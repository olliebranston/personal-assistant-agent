# Robin — Feature Backlog & Ideas (captured 2026-08-17)

## Quick fixes — scoped, hand to Claude Code directly (good Ralph-loop candidates)
1. **[DONE] Breakfast/lunch/dinner "same meal" bug.** Fixed — `meal_slot` is
   now explicit-only (never inferred from time-of-day/order), and
   `tools/meal.py:repeat_meal` matches strictly on `meal_slot`, never
   recency, with a regression test guarding against an intervening snack
   being mistaken for the meal being repeated.
2. **[DONE] Gym exercise ordering rule.** Explicit in `Gym-CONTEXT.md` and
   structurally enforced by `tools/gym.py:_SESSION_PLANS`'s ordered lists,
   not left to the LLM to infer per session.
3. **Weight-logging reliability.** Status unconfirmed — `log_weight` has
   input validation and test coverage, but whether the original
   intermittent-failure symptom still occurs in practice needs Ollie's own
   confirmation (a static code review alone can't settle this one).

## Needs a dedicated brainstorming session + new/expanded context doc first
4. **Marathon training plan (target: April 2027) + sub-20 5k + VO2 max
   base-building**, integrated with the existing 3-day PPL split. Real
   periodization design problem — training-phase concept needed, not a
   bolt-on to `Gym-CONTEXT.md`. Likely output: new `Running-CONTEXT.md`
   or substantially expanded `Gym-CONTEXT.md`. Still not started.
5. **[DONE] Fantasy Premier League agent.** Built — `FPL-CONTEXT.md` +
   `PHASE1-BRIEF.md`/`PHASE2-BRIEF.md`/`PHASE3-BRIEF.md` +
   `tools/fpl.py`/`services/fpl_*.py`/`bot/fpl_jobs.py`, live in production
   (see `FPL-STATUS.md` for current season state).

## Parked / later-stage (per own framing at capture time)
6. **Newsletter reader/summarizer.** Needs its own design pass first —
   how newsletters reach the bot (forwarding? Gmail API polling?).
7. **Nutrition/exercise/recovery correlation** ("under-fuelling for this
   training load" style flags). Depends on the marathon/running program
   (#4) existing first, since that defines what "correct recovery" means
   for a given week.

## Process note
- User wants to use the "Ralph" agentic-loop technique (Geoffrey
  Huntley's pattern: loop Claude Code against a persistent plan/task file
  until it works through a scoped backlog) more in VS Code + Claude Code.
  Good fit for bucket 1 (quick fixes); NOT recommended for bucket 2 items
  (marathon plan, FPL agent) — those need human judgment/design before
  there's a task list worth looping against.

## Architecture note (recurring point of confusion, worth not re-litigating)
Robin already has the separated data layer the user pictured wanting to
build — SQLite (`assistant.db`), schema/CRUD in `storage/models.py`,
queried by the bot via `tools/*.py`. The VCN/instance is the hosting
layer (where the code runs), not a data-storage concept — these are two
different things that got conflated in an August 2026 conversation.