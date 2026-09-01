# Phase 1 brief — Robin FPL module, adherence layer

**Status: implemented.** Kept as a historical build spec — see
`FPL-STATUS.md` for current state.

**Paste-in prompt for Claude Code:**
> Read `FPL-CONTEXT.md` and `PHASE1-BRIEF.md` in this repo, then implement Phase 1
> as specified. Match the existing Robin module conventions for config, logging,
> Telegram sending and systemd units — read an existing module first and follow it
> rather than inventing a new pattern. Ask me before deviating from the schema.

---

## Purpose

Phase 1 exists to make Ollie **never miss a deadline and never field a broken
team**. That is the entire goal. Per §0 of the context doc, this is where most of
the points are — not in the model.

## Non-goals (do not build these yet)

- ❌ No expected-points model
- ❌ No MILP optimiser (`fpl_squad_v0.py` exists as a Phase 2 prototype — leave it alone)
- ❌ No transfer recommendations
- ❌ No LLM in the decision path
- ❌ No mini-league EO engine

If a task starts to look like Phase 2, stop and flag it. The failure mode for this
project is building the interesting part first.

---

## Config

Add to Robin's existing config mechanism (env or config file — follow the house
pattern, do not introduce a new one):

```
FPL_TEAM_ID=<Ollie's entry id>
FPL_LEAGUE_ID=<the mini-league id>
FPL_ENABLED=true
```

Neither ID is a secret, but keep them in config, not source.

---

## 1. `fpl_client.py` — the data layer

A thin, well-behaved client for `https://fantasy.premierleague.com/api/`.

**Endpoints needed in Phase 1:**

| Method | Endpoint | Purpose |
|---|---|---|
| `bootstrap()` | `bootstrap-static/` | Players, prices, teams, **gameweek deadlines** |
| `fixtures(gw=None)` | `fixtures/` | Kickoff times, FDR |
| `entry(team_id)` | `entry/{id}/` | Team metadata |
| `entry_history(team_id)` | `entry/{id}/history/` | Points, rank, bank, team value, chips used |
| `picks(team_id, gw)` | `entry/{id}/event/{gw}/picks/` | **The actual squad** — only available after the deadline |
| `league(league_id)` | `leagues-classic/{id}/standings/` | Mini-league table |

**Requirements — these are the bits people get wrong:**

1. **Cache `bootstrap-static` on disk**, TTL 1 hour. It's ~3MB. Never call it twice
   in one job run. Everything else is small.
2. **One `requests.Session`**, a descriptive `User-Agent`, a 20s timeout, and
   exponential backoff on 429/5xx (3 attempts, then give up loudly). The API is
   unofficial and undocumented — being a polite client is the price of using it.
3. **Prices are in tenths.** `now_cost: 155` is £15.5m. Wrap this in a helper so
   the conversion happens in exactly one place.
4. **Validate the schema on load.** Assert the keys you depend on exist and raise a
   clear error naming the missing field. The API changes between seasons without
   notice, and a silent `KeyError` at 18:00 on a Friday is the worst possible
   outcome. Write a `verify_schema()` that the health check calls.
5. **`picks()` returns 404 before a deadline passes.** Handle that as a normal
   state, not an error.
6. Times: `deadline_time` and `kickoff_time` are UTC ISO strings. Parse to
   timezone-aware datetimes. Display in `Europe/London`. Never do naive datetime
   arithmetic here — the season crosses a DST boundary in late October.

---

## 2. State — SQLite

Follow Robin's existing DB conventions if there is one; otherwise a dedicated
`fpl.db`.

```sql
-- Gameweek calendar, refreshed from bootstrap-static
CREATE TABLE gameweeks (
    gw              INTEGER PRIMARY KEY,
    deadline_utc    TEXT NOT NULL,
    is_current      INTEGER DEFAULT 0,
    is_next         INTEGER DEFAULT 0,
    finished        INTEGER DEFAULT 0,
    data_checked    INTEGER DEFAULT 0   -- lockdown passed, scores final
);

-- What Ollie actually had, read back from the API after each deadline
CREATE TABLE my_picks (
    gw              INTEGER,
    element_id      INTEGER,
    position        INTEGER,            -- 1..15, bench order
    is_captain      INTEGER,
    is_vice         INTEGER,
    multiplier      INTEGER,
    PRIMARY KEY (gw, element_id)
);

CREATE TABLE my_history (
    gw              INTEGER PRIMARY KEY,
    points          INTEGER,
    total_points    INTEGER,
    overall_rank    INTEGER,
    bank            INTEGER,            -- tenths
    team_value      INTEGER,            -- tenths
    transfers       INTEGER,
    transfer_cost   INTEGER,
    chip            TEXT
);

-- Player snapshot, so we can detect flags and price moves between runs
CREATE TABLE player_snapshots (
    taken_at        TEXT,
    element_id      INTEGER,
    now_cost        INTEGER,
    status          TEXT,
    chance_next     INTEGER,
    news            TEXT,
    PRIMARY KEY (taken_at, element_id)
);

-- Which reminders have fired, so we never double-send or spam on retry
CREATE TABLE notifications_sent (
    gw              INTEGER,
    kind            TEXT,               -- 'T24','T3','T45','T15','review'
    sent_at         TEXT,
    PRIMARY KEY (gw, kind)
);

-- Ollie acknowledging he's done his transfers, so the ladder stops nagging
CREATE TABLE acknowledgements (
    gw              INTEGER PRIMARY KEY,
    acked_at        TEXT
);
```

**The important design point (context doc §4.5):** after each deadline, read
`picks()` and write it to `my_picks`. Robin learns what Ollie actually did from
the API — it never depends on him reporting it. Build this in Phase 1 even though
nothing consumes it yet; Phases 2 and 3 are built on top of it.

---

## 3. The reminder ladder

The core of Phase 1. Schedule off `deadline_utc` from the `gameweeks` table, not
hardcoded times — deadlines move constantly (Friday nights, midweek rounds,
international breaks).

| Trigger | Content | Suppression |
|---|---|---|
| **T−24h** | Deadline time. Current squad. Any player flagged (`status != 'a'`). Free transfers available. Bank. Chip status + weeks remaining in the current chip set. | Always sends |
| **T−3h** | Team news sweep: any owned player whose `status` or `news` changed since the T−24h snapshot. | **Only sends if something changed.** Silence is a feature. |
| **T−45m** | "Deadline in 45 minutes." List flagged players. Ask for acknowledgement. | Skipped if `acknowledgements` has this GW |
| **T−15m** | Hard nudge, same content. | Skipped if acknowledged |
| **Deadline +10m** | Silent: fetch `picks()`, write `my_picks`, write `my_history`. | No message |
| **Lockdown +1h** | GW review: final points, overall rank, mini-league position and movement. | Only after `data_checked` is true |
| **Weekly Wed 19:00** | Chip status. From GW14, escalating warning that the first set expires at the GW19 deadline (13:30 GMT, 2 Jan 2027). | Always |

**Rules:**
- Every send checks `notifications_sent` first. A cron that fires twice must not
  send twice.
- Acknowledgement: a `/fpl done` command (or an inline button) writes to
  `acknowledgements` and silences T−45m/T−15m for that GW.
- **Do not post a "final" GW review before lockdown.** Lockdown is now 09:00 the
  day after the gameweek's last match; bonus and DefCon are still provisional
  until then (context doc §1).

Implement scheduling to match Robin's existing pattern — if the other modules use
systemd timers, use systemd timers. A single job running every 5 minutes that
checks "what's due?" against the gameweeks table is simpler and more robust than
one timer per notification. Prefer that.

---

## 4. Telegram commands

| Command | Output |
|---|---|
| `/fpl` | Current squad with prices and flags, points this GW, overall rank, mini-league position, next deadline with countdown |
| `/fpl team` | Squad only, formatted by position, captain and vice marked |
| `/fpl league` | Mini-league table with movement since last GW |
| `/fpl chips` | Which chips are used, which remain, days until the set expires |
| `/fpl done` | Acknowledge — stops the nagging for this gameweek |

Formatting: fixed-width blocks for tables, and keep messages readable on a phone.
Prices as `£6.0m`, never `60`.

---

## 5. Acceptance criteria

Phase 1 is done when all of these are true:

- [ ] `/fpl` returns the real squad, pulled live from the API
- [ ] `verify_schema()` passes, and fails loudly with a named field if the API changes
- [ ] Deadlines are read from the API, not hardcoded, and render correctly in `Europe/London`
- [ ] The reminder ladder fires at the right times against a **simulated** deadline (write a test that injects a fake `deadline_utc` — do not wait a week to find out it's broken)
- [ ] Running the scheduler job twice in a row sends exactly one message
- [ ] `/fpl done` suppresses T−45m and T−15m
- [ ] After a deadline passes, `my_picks` and `my_history` populate automatically with no user input
- [ ] `bootstrap-static` is fetched at most once per hour across all jobs
- [ ] A 429 or a 500 from the API produces a logged warning and a retry, not a crash and not a bad message

Last one is the one to actually verify by hand: **the module must never send a
message it isn't sure about.** Failing silently and logging is better than sending
Ollie a confidently wrong squad at T−15m.

---

## 6. What Phase 2 will need from you

Don't build these, but don't design them out either:

- `player_snapshots` is the basis of price-change alerts
- `my_picks` history is the basis of "what did his choices cost vs the recommendation"
- The client's `league()` method is the entry point to the mini-league EO engine —
  it returns rival entry IDs, and `picks(rival_id, gw)` works on any public team

Keep `fpl_client.py` free of business logic. It fetches and validates; nothing else.
