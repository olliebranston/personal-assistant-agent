# Tool-Calling Architecture — Design Document

Status: **draft for review — no implementation yet**

## 1. Overview

### 1.1 Current pattern

```
Telegram message
  → main.route_message()
  → agents.router.classify(text)         [LLM call #1: domain classification]
  → bot/handlers/<domain>.handle()
  → agents/<domain>.handle()
  → agent-internal LLM call(s)            [LLM call #2..N: sub-classify / parse]
  → deterministic DB/API code
  → formatted string reply
```

Every domain re-implements its own mini router (`_ROUTER_SYSTEM` in `gym.py`,
`meal.py`, `calendar.py`), its own confirmation state machine
(`services/state.py`), and its own ad-hoc "is this a follow-up" heuristics
(`agents/router.py`'s `_last_domain` fallback). Conversation history
(`services/memory.py`) is only passed to the calendar agent and the general
fallback chat — everywhere else the model sees a single message in isolation.

### 1.2 Target pattern

```
Telegram message
  → main.route_message()
  → build ambient context block (§3.3)     [no LLM call — DB reads only]
  → load last-N history (§3.1)
  → services.openrouter.complete(
        messages=[ambient context, history..., user message],
        system=<single combined system prompt, §3.4>,
        tools=<full tool catalog, §2>,
    )
  → tool-call loop (§4.1): model calls 0..N tools, each tool returns
    structured data, fed back as role:"tool" messages
  → model composes final natural-language reply
  → reply sent to Telegram, (user msg, reply) appended to memory
```

One LLM call per turn (plus however many tool-calls the model chooses to
make within that single `complete()` invocation — those don't count as
separate "turns"). Domain classification, sub-parsing, and confirmation
state machines are replaced by: the model's own tool selection, structured
tool returns, and conversation history.

### 1.3 What stays exactly the same

- SQLite schema and all of `storage/models.py` — no DDL changes except the
  one optional addition flagged in §5.6.
- `services/google_calendar.py`, `services/nutrition.py`, `services/news.py`
  — called from new `tools/` wrappers instead of from `agents/`.
- `bot/scheduler.py` — scheduled jobs remain deterministic, non-LLM (§4.4).
- `data/recipes.py`, `config.py`, `utils/log_scrubber.py`.

---

## 2. Tool Catalog

21 tools, grouped by domain. For each: signature with types, return shape,
what it maps to in the current code, and any behaviour notes. "Maps to"
references are the *source of logic* to port, not files that survive
unchanged.

All tools are `async def tool_name(conn, **kwargs) -> dict`. `conn` is a
single SQLite connection opened once per incoming message in
`route_message()` and threaded through the whole tool-call loop (mirrors how
each `agents/*.handle()` opens its own connection today, just hoisted one
level up).

### 2.1 Gym tools

#### `log_exercise(exercise_name: str, weight_kg: float | null, sets: int, reps: int, notes: str | null = null) -> dict`

- **Returns:** `{"logged": true, "session_id": int, "session_type": str, "exercise": str, "sets": int, "reps": int, "weight_kg": float|null, "notes": str|null}`
- **Maps to:** `agents/gym.py:_log_workout` + `storage/models.py:insert_session`/`insert_set`. Today a whole session (multiple exercises) is parsed from one message via `_LOG_PARSER_SYSTEM` and written in one go; the tool-calling model instead calls `log_exercise` once per exercise it identifies in the message.
- **Notes:** Needs a "find or create today's session" step — see §3.2(d) for how the tool decides whether to append to an existing `gym_session` row for today or create a new one, and how it infers `session_type` when not explicitly stated.

#### `get_last_session(session_type: str) -> dict`

- **Returns:** `{"found": bool, "date": str|null, "session_type": str, "exercises": [{"exercise": str, "sets": int, "reps": int, "weight_kg": float|null, "notes": str|null}]}`
- **Maps to:** `agents/gym.py:_get_last_session_of_type` (data already structured this way internally — `_format_last_session` is the formatting step that gets dropped; the model formats the reply itself).

#### `get_session_plan(session_type: str) -> dict`

- **Returns:** `{"session_type": str, "exercises": [{"exercise": str, "target_sets": int, "target_reps": str, "notes": str|null}]}`
- **Maps to:** the static `_SESSION_PLANS` dict in `agents/gym.py`. New tool — needed so the model can answer "what's on legs day" or open a session by telling the user the plan, without that logic living in a prompt.

#### `get_next_session_type() -> dict`

- **Returns:** `{"session_type": str, "cycle_position": str}`
- **Maps to:** `agents/gym.py:get_next_session_type` (PPL cycle logic against `_PPL_CYCLE` + last logged session). New tool, used by `get_morning_briefing_data` today and now also directly callable ("what session am I due?").

#### `get_weekly_gym_summary() -> dict`

- **Returns:** `{"week_start": str, "sessions": [{"date": str, "session_type": str, "exercise_count": int}], "session_count": int}`
- **Maps to:** `agents/gym.py:_week_summary` (currently formats text; tool returns the underlying `get_recent_sessions` data shaped for the model).

### 2.2 Meal & nutrition tools

#### `log_food(food_name: str, grams: float, meal_slot: str | null = null) -> dict`

- **Returns:** `{"logged": true, "id": int, "food_name": str, "grams": float, "calories": float, "protein_g": float, "carbs_g": float, "fat_g": float, "source": "usda"|"fallback"|"estimate", "meal_slot": str|null}`
- **Maps to:** `agents/meal.py:_log_food` + `services/nutrition.py:lookup_macros` + `storage/models.py:insert_food_log`.
- **Behaviour change (flagged, §5.1):** today this is staged via `services/state.py` ("food_log" pending confirmation) unless auto-confirmed. In the new design, `log_food` **writes immediately** and returns the computed macros; the model relays them in its reply ("Logged 150g chicken breast — 247 kcal / 46g protein"). Mistakes are fixed via `correct_food_log` after the fact.

#### `get_food_log(date: str) -> dict`

- **Returns:** `{"date": str, "entries": [{"id": int, "food_name": str, "grams": float, "calories": float, "protein_g": float, "carbs_g": float, "fat_g": float, "meal_slot": str|null, "logged_at": str}]}`
- **Maps to:** new tool — no direct existing wrapper, but the data is exactly `storage/models.py:get_food_logs_for_date`. Required so the model can (a) find the right row for `correct_food_log` ("that should be 200g not 150g" — model needs the `id`), and (b) implement "repeat yesterday's meal" by reading yesterday's entries (see §5.2).

#### `correct_food_log(food_name: str, field: str, new_value: str | float) -> dict`

- **Returns:** `{"updated": true, "entry": {"id": int, "food_name": str, "grams": float, "calories": float, "protein_g": float, "carbs_g": float, "fat_g": float, "meal_slot": str|null}}` or `{"error": "no matching entry found for today"}`
- **Maps to:** `agents/meal.py:_correct_log` + `storage/models.py:update_food_log`. `field` is one of `grams|food_name|meal_slot`; for `grams`/`food_name` changes the tool re-runs `lookup_macros` to recompute calories/protein/carbs/fat, matching today's behaviour.

#### `get_daily_macros(date: str) -> dict`

- **Returns:** `{"date": str, "calories": float, "protein_g": float, "carbs_g": float, "fat_g": float, "target_calories": int, "target_protein_g": int, "remaining_calories": float, "remaining_protein_g": float, "is_weights_day": bool}`
- **Maps to:** `storage/models.py:get_daily_totals` + `agents/meal.py:_get_calorie_target` + `_remaining_macros`.

#### `get_weekly_macro_summary() -> dict`

- **Returns:** `{"week_start": str, "days": [{"date": str, "calories": float, "protein_g": float}], "avg_calories": float, "avg_protein_g": float}`
- **Maps to:** `agents/meal.py:_week_summary` + `storage/models.py:get_week_logs` (structured, not formatted).

#### `get_recipe(recipe_name: str) -> dict`

- **Returns:** `{"found": bool, "name": str, "slug": str, "category": str, "time_mins": int, "protein_g": int, "ingredients": [{"item": str, "qty": float|str, "unit": str|null}], "method": [str]}`
- **Maps to:** `data/recipes.py:find_recipe` (currently fed into `format_recipe` for a text reply; tool returns the raw dict and the model formats it).

#### `suggest_meal(meal_type: str) -> dict`

- **Returns:** `{"meal_type": str, "suggestion": str, "recipe_slug": str|null, "rotation_day": str|null}`
- **Maps to:** `agents/meal.py:_suggest_meal` + breakfast/lunch/dinner rotation dicts + `get_lunch_rotation`/`get_breakfast`. `meal_type` ∈ `breakfast|lunch|dinner`.

#### `log_weight(weight_kg: float) -> dict`

- **Returns:** `{"logged": true, "date": str, "weight_kg": float, "trend_kg_per_week": float|null}`
- **Maps to:** `agents/meal.py:_handle_weight` + `storage/models.py:log_weight` (trend computed via `get_weight_history`, same as `_weight_trend`).

#### `get_weight_trend() -> dict`

- **Returns:** `{"entries": [{"date": str, "weight_kg": float}], "trend_kg_per_week": float|null, "latest_weight_kg": float|null}`
- **Maps to:** `agents/meal.py:_weight_trend` + `storage/models.py:get_weight_history`.

#### `generate_meal_plan(week_start: str) -> dict`

- **Returns:** `{"week_start": str, "days": {"<weekday>": {"breakfast": str, "lunch": str, "dinner": str}}}`
- **Maps to:** `agents/meal.py:_generate_week_plan`, used by the Friday scheduled job (`build_friday_summary`).

#### `get_shopping_list(week_start: str) -> dict`

- **Returns:** `{"week_start": str, "items": [{"item": str, "qty": float|str, "unit": str|null}]}`
- **Maps to:** `agents/meal.py:_derive_shopping_list`, derives from `generate_meal_plan` output minus `PANTRY_STAPLES`.

### 2.3 Calendar tools

#### `get_calendar_events(time_min: str, time_max: str) -> dict`

- **Returns:** `{"events": [{"summary": str, "start": str, "end": str, "location": str|null, "all_day": bool, "calendar": str}]}`
- **Maps to:** `services/google_calendar.py:list_events` (already returns near this shape; tool adds an `all_day` flag derived from whether the API gave `date` vs `dateTime`).

#### `create_calendar_event(summary: str, start: str, end: str, location: str | null = null, all_day: bool = false) -> dict`

- **Returns:** `{"created": true, "event_id": str, "summary": str, "start": str, "end": str, "location": str|null, "calendar": str}` or `{"error": "..."}`
- **Maps to:** `services/google_calendar.py:create_event`. **Requires extension** — currently only builds `{"dateTime": ..., "timeZone": "Europe/London"}`; needs an `all_day` branch building `{"date": "YYYY-MM-DD"}` instead (§5.3).
- **Behaviour change:** today's mandatory confirm-before-create via `services/state.py` (`event_create` pending state) is replaced by a **propose-then-wait** pattern driven by the system prompt + conversation history (§3.2c) — the model proposes the event in plain text first, and only calls this tool after the user confirms in their next message.

### 2.4 News tool

#### `get_news() -> dict`

- **Returns:** `{"chelsea": [{"title": str, "summary": str|null, "published": float}], "world": [{"title": str, "summary": str|null}], "horses_today": [...], "horses_upcoming": [...]}`
- **Maps to:** `services/news.py:fetch_chelsea_items`, `fetch_world_news_items`, `fetch_all_horse_items` (via `asyncio.gather`, same as `agents/news.py:handle` does today).
- **Behaviour change:** today's flow makes 2 *additional* LLM summarisation calls (`_CHELSEA_SYSTEM`, `_WORLD_SYSTEM`) on top of the router classification. The tool returns **raw item data**; the single top-level model call composes the summary as part of its normal reply. Net effect: 3 LLM calls → 1.

### 2.5 Reminders

#### `create_reminder(text: str, when: str) -> dict`

- **Returns:** `{"created": true, "text": str, "fire_at": str}` or `{"error": "that time has already passed"}`
- **Maps to:** `main.py:_set_reminder` (the JSON-parsing LLM call this function makes today is no longer needed — the top-level model already extracts `text`/`when` as tool arguments; `when` is an ISO 8601 datetime string the model resolves itself using the date-math rules in the system prompt, §3.4).
- **Notes:** the only tool needing access to `context.job_queue` and `chat_id` — these are closure-bound when the per-request tool registry is built in `route_message()` (§4.3), breaking the otherwise-uniform `(conn, **kwargs)` signature. Implementation: `tools/reminders.py` exports a factory `make_create_reminder(context, chat_id)` returning the actual async tool function.

### 2.6 Composite / briefing

#### `get_morning_briefing_data() -> dict`

- **Returns:** `{"date": str, "day_name": str, "calendar_events": [str], "gym_targets": [str], "horses_today": [str], "chelsea_headline": str|null, "world_headlines": [str], "breakfast_suggestion": str, "calorie_target": int, "is_weekend": bool}`
- **Maps to:** the data-gathering portion of `bot/scheduler.py:_morning_briefing` — `_get_today_calendar_events`, `_get_gym_targets`, `_get_horses_today`, `_get_chelsea_headline`, `_get_world_headlines`, `meal_agent.get_breakfast`. Used both by the scheduled job (§4.4) and on-demand if the user asks "give me my briefing".

---

## 3. Context System

### 3.1 Conversation history (last-N)

- **Current:** `services/memory.py`, `maxlen=6` (3 user/assistant pairs), in-memory only, passed to the LLM in 2 of 6 code paths (calendar agent, general fallback).
- **Proposed:** bump to **`maxlen=10`** (5 pairs), passed on **every** `complete()` call (the whole point of the redesign — see §1.2).
- **What gets stored:** only the user-facing turns — the incoming user message and the model's *final* natural-language reply. Intermediate `tool_calls` / `role:"tool"` messages from the tool-call loop (§4.1) are **not** persisted to `services/memory` — they're regenerated fresh each turn from the ambient context block (§3.3) and a new tool-call loop. Rationale: tool results can go stale fast (macro totals, calendar events) and persisting them risks the model citing outdated numbers from 5 messages ago instead of calling the tool again.
- **Why 10, not 6 or 20:** multi-step flows (logging several exercises, a meal correction following a log, a calendar proposal + confirmation) typically span 2-4 user/assistant pairs. 5 pairs comfortably covers "propose → confirm" plus a couple of follow-ups without ballooning the prompt — each pair is now also slightly heavier (the assistant's reply may reference tool results inline). Revisit if prompt size becomes a problem with a smaller model's context window.

### 3.2 Session detection ("is this a continuation?")

Today this is handled by three separate, partial mechanisms: `services/state.py` (explicit pending-confirmation state machines for food logs, gym session offers, and calendar event creation) and `agents/router.py`'s per-user `_last_domain` fallback for short follow-ups. All three go away. Replacements:

**(a) Conversation history does most of the work.** With the model seeing the last 5 exchanges on every call, "yes", "log it", "actually make it 300g" are interpretable in context without any router/state machinery — the model has the full prior exchange in front of it.

**(b) Ambient context surfaces "is there an open gym session today?"** (§3.3) so the model knows whether `log_exercise` should append to today's session or start a new one.

**(c) Confirmation flows become propose-then-wait, governed by the system prompt.** For `create_calendar_event` (and any other action worth double-checking), the system prompt instructs the model to state the proposed action in plain language and wait for an affirmative reply before calling the tool — exactly the "yes/no" pattern that exists today, but driven by conversation history instead of `services/state.py`. No code-level state machine is needed because the proposal *is* the model's previous message, sitting right there in history.

**(d) Gym "open session" heuristic — no schema change required.** `log_exercise`'s implementation queries `storage/models.get_recent_sessions` for a `gym_session` row dated today; if one exists, append the new `exercise_set` to it (inferring `session_type` from that row if the model didn't specify one); if not, create a new session row, using `session_type` from the tool argument or falling back to `get_next_session_type()`. This mirrors the *effect* of today's `_log_workout`, which already groups same-day logs into one session — just decomposed into per-exercise calls. A `status` column (open/closed) was considered but isn't needed: "today's session" is an unambiguous enough proxy, and is simpler (§5.6 notes this as the one schema change considered and rejected by default).

### 3.3 Ambient structured context block

A block of **structured, always-current** data, rebuilt from the DB on every incoming message (no caching across messages — cheap, all local SQLite reads) and injected as a separate message (not part of conversation history, so it never goes stale *within* history — each turn gets a fresh one). Contents:

```json
{
  "today": "2026-06-14",
  "day_name": "Sunday",
  "current_time": "14:32",
  "daily_macros": {"calories": 1820, "protein_g": 142, "target_calories": 2950, "target_protein_g": 230},
  "last_workout": {"date": "2026-06-12", "session_type": "pull"},
  "open_session_today": null,
  "latest_weight_kg": 81.4
}
```

- `daily_macros` → `get_daily_macros(today)` data, so "how much protein have I got left" often needs **zero** tool calls.
- `last_workout` / `open_session_today` → support §3.2(d) without a tool round-trip.
- `latest_weight_kg` → small, cheap, frequently relevant to meal/training questions.
- Calendar events and news are **not** included here — they require external API calls (Google Calendar, RSS/racing feeds), which aren't "cheap local reads" and would slow down *every* message even when irrelevant. These stay tool calls (`get_calendar_events`, `get_news`).

### 3.4 System prompt shape (summary)

```
[Persona — "You are Robin..." tone, from today's _GENERAL_SYSTEM]
[Profile & constants — protein/kcal targets, progression rule, 5k goal, dinner policy]
[Date-math rules — for calendar/reminder/history date resolution, from _QUERY_SYSTEM/_CREATE_SYSTEM/_REMINDER_SYSTEM]
[Confirmation rule — propose-then-wait for create_calendar_event, §3.2c]
[Session-continuation rule — use open_session_today / last_workout from ambient context, §3.2d]
[News-summarisation rules — from _CHELSEA_SYSTEM/_WORLD_SYSTEM]
---
[Ambient context block — §3.3, regenerated per message, as a system or tool-role message]
[Last 10 history messages — §3.1]
[Current user message]
```

Tool definitions themselves (§2, 21 tools) are passed via the API's native
`tools=[...]` parameter, not inlined into the prompt text — this is what
lets the model do structured argument-extraction natively instead of via
`_extract_json` + a hand-rolled JSON schema in prose.

---

## 4. Tool Execution Model

### 4.1 The tool-call loop

`services/openrouter.py:complete()` gains a loop. New signature:

```python
async def complete(
    messages: list[dict],
    system: str = "",
    history: list[dict] | None = None,
    tools: list[dict] | None = None,
    tool_executor: Callable[[str, dict], Awaitable[dict]] | None = None,
    max_attempts: int = 3,
    max_tool_iterations: int = 5,
) -> str:
```

`tools=None` (the default) preserves exactly today's behaviour — every
existing call site (`agents/router.py`, the per-domain agents during
migration, `_set_reminder`, `_general_response`) keeps working unchanged.
This is what makes the migration incremental (§7).

When `tools` is supplied:

```
full_messages = [system] + history + messages
loop up to max_tool_iterations times:
    response = chat.completions.create(model=..., messages=full_messages, tools=tools)
    msg = response.choices[0].message
    if msg.tool_calls:
        full_messages.append(msg)  # assistant turn with tool_calls
        for call in msg.tool_calls:
            args = json.loads(call.function.arguments)
            result = await tool_executor(call.function.name, args)
            full_messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result),
            })
        continue  # let the model see tool results and respond/call again
    return msg.content  # plain text — done
# max_tool_iterations exceeded
logger.warning("Tool loop exceeded %d iterations", max_tool_iterations)
return msg.content or "Sorry, I got stuck working on that — try rephrasing."
```

`max_tool_iterations=5` is a safety cap against runaway loops (a tool that
always errors and a model that keeps retrying it). 5 covers realistic
multi-tool turns (e.g. log 3 exercises + get_weekly_gym_summary +
final reply = 4 round trips) with headroom.

### 4.2 Tool error convention

Every tool function returns `{"error": "<human-readable message>"}` on
failure instead of raising — e.g. `get_recipe("nonexistent")` returns
`{"found": false}` (a non-error "not found"), while a USDA API timeout in
`log_food` returns `{"error": "nutrition lookup failed, try again"}`. The
model sees this in the `role:"tool"` message and can explain the problem to
Ollie or try a fallback, rather than the whole turn crashing.

The dispatch wrapper in `tool_executor` additionally catches *any*
unexpected exception from a tool implementation and converts it to
`{"error": str(exc)}` — one buggy tool can't take down the whole message
handler. This mirrors the existing per-handler `try/except` blocks in
`agents/*.py`, just centralised in one place.

### 4.3 Tool registry & per-request wiring

A new `tools/` package, one module per domain (`tools/gym.py`,
`tools/meal.py`, `tools/calendar.py`, `tools/news.py`,
`tools/reminders.py`, `tools/briefing.py`), each exporting:

- the async tool implementations (`(conn, **kwargs) -> dict`, except
  `create_reminder`, §2.5), and
- a `TOOL_SCHEMAS: list[dict]` — OpenAI-format function-calling schemas
  (name, description, JSON-schema parameters) for registration.

`main.py:route_message()` becomes roughly:

```python
conn = get_connection()
try:
    context_block = build_ambient_context(conn)        # §3.3
    history = memory.get(user_id)                       # §3.1, maxlen=10
    registry = build_tool_registry(conn, context, chat_id)  # binds create_reminder
    reply = await complete(
        messages=[context_block, {"role": "user", "content": text}],
        system=SYSTEM_PROMPT,                            # §3.4
        history=history,
        tools=registry.schemas,
        tool_executor=registry.execute,
    )
finally:
    conn.close()
memory.add(user_id, "user", text)
memory.add(user_id, "assistant", reply)
await update.message.reply_text(reply)
```

`build_tool_registry` is the one place that knows about the `create_reminder`
special case (§2.5) — it closes over `context` (for `job_queue`) and
`chat_id`, while every other tool just gets `conn`.

### 4.4 Scheduled jobs & `get_morning_briefing_data()`

Two options for how `bot/scheduler.py:_morning_briefing` consumes the new
`get_morning_briefing_data()` tool:

- **Option A — stay fully deterministic (no LLM call).** The job calls
  `get_morning_briefing_data()` directly and formats the message with the
  same `"\n".join(sections)` logic it uses today. Zero behaviour change,
  zero new failure modes on a time-critical 07:45 job, easiest to test.
- **Option B — one composition LLM call.** The job calls
  `get_morning_briefing_data()`, then makes a single *no-tools* `complete()`
  call with that structured data + a "write today's briefing" system prompt,
  for more natural/varied phrasing day to day.

**Recommendation: Option A.** The morning briefing's value is being
reliable and scannable at 07:45 every day; LLM composition adds latency,
cost, and a new way for the job to fail (API outage = no briefing) for a
purely cosmetic upgrade. `get_morning_briefing_data()` is still useful
on-demand (Ollie asks "give me my briefing" mid-morning), where it goes
through the normal tool-calling loop and gets composed naturally anyway.
Revisit Option B later if the static format becomes a genuine complaint.

The other six scheduled jobs (`_midmorning_checkin`, `_lunch_prompt`,
`_evening_dinner_prompt`, `_end_of_day_summary`, `_friday_meal_plan`,
`_sunday_batch_cook`) are unaffected by the redesign — none call the LLM
today and none need to. As a pure internal-consistency refactor (not
required, can be deferred past §7's migration), they could call the new
`tools/meal.py` / `tools/gym.py` functions instead of `agents/meal.py` /
`agents/gym.py` helpers once those exist, since the underlying logic moves
there anyway.

---

## 5. Decisions for Your Review

These are the judgment calls made while drafting the catalog above. Flagging
each explicitly per your instruction — none of these are implemented yet.

### 5.1 `log_food`: immediate write vs. staged confirmation

**Today:** `agents/meal.py` stages a parsed food log via `services/state.py`
and waits for "yes" before writing — except for an "auto-confirmed" path
(per the recent "Show per-item breakdown in auto-confirmed food logs"
commit), which already writes immediately and just *shows* the breakdown.

**Proposed:** `log_food` always writes immediately and returns the computed
macros; the model relays them ("Logged 150g chicken breast — 247 kcal /
46g protein"). A misparse (wrong food or grams) is fixed with
`correct_food_log` after the fact.

**Tradeoff:** removes a confirmation round-trip, but a misparse becomes a
follow-up correction instead of a pre-write catch. Given the auto-confirm
path already exists and corrections are common/cheap, **I'd recommend
immediate-write** — it's also simpler (no `services/state.py` dependency at
all, §6). Flagging because it's a visible UX change either way.

### 5.2 "Repeat yesterday's meal": exact copy vs. re-derived

**Today:** `_repeat_yesterday_meal` presumably re-logs yesterday's
food/grams.

**Composition approach (as designed above):** `get_food_log(yesterday)` →
`log_food(food_name, grams)` for each entry. This re-runs
`lookup_macros`, which could return slightly different values than what was
logged yesterday (USDA data drift, or fallback-vs-API differences).

**Alternative:** a dedicated `repeat_meal(date, meal_slot)` tool that copies
`food_log` rows verbatim — including the *already-computed* macro values —
guaranteeing exact parity with today's behaviour. This would be a 22nd tool.

**My take:** USDA macro values for the same food/gram amount essentially
never change day-to-day, so composition's drift risk is theoretical. I'd
skip the dedicated tool unless you've seen this drift matter in practice —
but it's a cheap addition if you want exact parity guaranteed.

### 5.3 `create_calendar_event` all-day support — required code change

`services/google_calendar.py:create_event` currently only builds
`{"dateTime": ..., "timeZone": "Europe/London"}`. Supporting `all_day=true`
needs a small additive branch: when `all_day`, build
`{"start": {"date": "YYYY-MM-DD"}, "end": {"date": "YYYY-MM-DD"}}` (no
`dateTime`/`timeZone`). This is the **one place** the tool catalog needs new
capability beyond wrapping what exists — small, but worth flagging since
it's not "purely" a restructure.

### 5.4 Does `config.OPENROUTER_MODEL` support tool calling?

Default is `"openrouter/free"`. Many free-tier OpenRouter models don't
reliably support OpenAI-style `tools`/`tool_choice` (some silently ignore
`tools`, some hallucinate malformed `tool_calls`). **Before implementation,
this needs checking** against whatever model is actually configured in
`.env` — may need to pin a tool-calling-capable model (e.g. a Claude or
GPT-4o-mini variant via OpenRouter) for this code path specifically, even if
other paths keep using the free model. This is the single highest-risk
unknown in this whole plan — worth resolving first, since it determines
whether the tool-call loop (§4.1) is viable at all on the current model.

### 5.5 Scheduled jobs: deterministic vs. LLM composition

Covered in §4.4 — recommendation is to keep all 7 scheduled jobs fully
deterministic (Option A), at least initially.

### 5.6 Gym session grouping: heuristic vs. schema change

Covered in §3.2(d). Recommendation is the "today's `gym_session` row, if any"
heuristic — no DDL change. A `status` (open/closed) column was considered
(e.g. to let "I'm done for today" explicitly close a session) but adds a
migration and a new failure mode (forgetting to close → tomorrow's first set
appends to yesterday's "open" session under the date heuristic anyway, so
the heuristic is actually *more* robust here, not less). Not recommending
this column.

---

## 6. What Becomes Obsolete / What's Reused

### Removed entirely

- `agents/router.py` — `classify`, `get_last_domain`, `set_last_domain`,
  `_SYSTEM`. The model does intent routing itself via tool selection.
- `services/state.py` — all pending-confirmation state machines, replaced
  by conversation-history-driven propose-then-wait (§3.2c).
- The per-domain `_ROUTER_SYSTEM` / sub-parser prompts in `agents/gym.py`,
  `agents/meal.py`, `agents/calendar.py` (`_QUERY_SYSTEM`, `_CREATE_SYSTEM`,
  `_LOG_PARSER_SYSTEM`, `_FOOD_PARSER_SYSTEM`, `_CORRECT_PARSER_SYSTEM`,
  `_RECIPE_EXTRACT_SYSTEM`, `_WEIGHT_EXTRACT_SYSTEM`) — their *logic*
  (date math, target rules, etc.) gets folded into the single system prompt
  (§3.4) or into tool implementations directly.
- `agents/news.py:handle` — its two summarisation LLM calls (`_CHELSEA_SYSTEM`,
  `_WORLD_SYSTEM`) are dropped; `get_news()` returns raw items (§2.4).
- `bot/handlers/*.py` (`calendar.py`, `gym.py`, `meal.py`, `news.py`) — the
  `/calendar`, `/gym`, `/meal`, `/news` command handlers collapse into
  `route_message`, which now handles everything uniformly (a `/gym` command
  can just feed the literal text "gym" into the same pipeline).
- `main.py:_set_reminder` — logic moves into `tools/reminders.py:create_reminder`.

### Reused as-is

- `storage/db.py`, `storage/models.py` — unchanged, called from `tools/`
  instead of `agents/`.
- `services/nutrition.py`, `services/news.py` — unchanged.
- `services/google_calendar.py` — unchanged except the `all_day` addition
  to `create_event` (§5.3).
- `data/recipes.py`, `config.py`, `utils/log_scrubber.py` — unchanged.
- `bot/scheduler.py` — unchanged structure (§4.4); optionally repoints
  helper calls from `agents/*` to `tools/*` later.

### Restructured

- `services/memory.py` — same module, `maxlen` 6 → 10 (§3.1).
- `services/openrouter.py` — `complete()` gains `tools`/`tool_executor`/loop
  (§4.1), backward-compatible (`tools=None` ⇒ today's behaviour).
- `main.py` — `route_message` rewritten per §4.3; `main()`'s handler
  registration shrinks (no more per-domain command handlers, see above).

### New

- `tools/gym.py`, `tools/meal.py`, `tools/calendar.py`, `tools/news.py`,
  `tools/reminders.py`, `tools/briefing.py` — 21 tool implementations +
  `TOOL_SCHEMAS`.
- `tools/registry.py` — aggregates schemas + dispatch, builds the
  per-request `tool_executor` (binds `create_reminder`'s `context`/`chat_id`).
- Ambient context block builder (§3.3) — likely `tools/context.py` or a
  function in `tools/registry.py`.
- The single combined system prompt (§3.4) — likely `tools/prompts.py`.

---

## 7. Suggested Migration Order

1. **Extend `services/openrouter.complete()`** with the tool-call loop
   (§4.1-4.2). Purely additive — `tools=None` keeps every existing call site
   working unchanged. Can be merged and deployed with zero behaviour change.
2. **Resolve §5.4** (model tool-calling support) — spike this first, since
   it gates everything else.
3. **Build `tools/` one domain at a time**, each tool testable standalone
   against the current DB/services before any prompt wiring.
4. **Build the ambient context block (§3.3)** and bump `services/memory`
   to `maxlen=10`.
5. **Wire `route_message` to the new loop for one domain first** (suggest
   gym — smallest catalog, no confirmation flows), with `agents/router.py`
   still handling everything else as a fallback during transition.
6. **Cut over remaining domains** (meal, calendar, news, reminders),
   validating propose-then-wait (§3.2c) carefully for calendar.
7. **Remove** `agents/router.py`, `services/state.py`, `bot/handlers/*.py`,
   and the old `agents/*.py` `handle()`/prompt constants.
8. *(Optional, deferred)* repoint `bot/scheduler.py` helpers from `agents/*`
   to `tools/*` for consistency — no behaviour change.

