# Robin — Feature Backlog & Ideas (captured 2026-08-17)

## Quick fixes — scoped, hand to Claude Code directly (good Ralph-loop candidates)
1. **Breakfast/lunch/dinner "same meal" bug.** Suspected root cause: meal
   type may be inferred (time-of-day/order) rather than explicitly tagged
   at logging time, breaking when snacks/extra meals are logged. Have
   Claude Code check `tools/meal.py` / logging flow before assuming a fix.
2. **Gym exercise ordering rule.** Sessions should start with
   compound/bodyweight/multi-muscle movements (dips, pull-ups, squats)
   and end with isolation work (rope pulldowns, etc.). Add as an explicit
   rule in `Gym-CONTEXT.md` and to whatever assembles the session order.
3. **Weight-logging reliability.** Improved but still intermittently
   fails. Needs diagnosis against real recent failure cases (check logs),
   not a guess-and-patch.

## Needs a dedicated brainstorming session + new/expanded context doc first
4. **Marathon training plan (target: April 2027) + sub-20 5k + VO2 max
   base-building**, integrated with the existing 3-day PPL split. Real
   periodization design problem — training-phase concept needed, not a
   bolt-on to `Gym-CONTEXT.md`. Likely output: new `Running-CONTEXT.md`
   or substantially expanded `Gym-CONTEXT.md`.
5. **Fantasy Premier League agent.** New domain: team strategy, transfer
   recommendations, chip/power-up timing across a season, persistent
   "where are we in the plan" memory. Needs a live-data integration
   answer (how the agent gets real FPL prices/fixtures/injuries) before
   strategy design is meaningful. New context doc + new agent module.

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