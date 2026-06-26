# Robin — Personal Assistant Bot

## Project Overview
Telegram bot (python-telegram-bot, polling mode) acting as a personal
assistant for gym, nutrition, calendar, and news. Single LLM orchestrator
(`services/openrouter.py:complete`) via OpenRouter, using tool-calling —
domain logic lives in deterministic `tools/*.py` modules, not in prompts.
SQLite storage (`assistant.db`), schema/CRUD in `storage/models.py`.

See `CONTEXT.md`, `Gym-CONTEXT.md`, `Mealplan-CONTEXT.md` for domain
knowledge (goals, macro targets, training split) and `TOOL_CALLING_DESIGN.md`
for the tool-calling architecture.

## Stack
- python-telegram-bot v21 (polling)
- OpenRouter via the `openai` SDK — model: `openai/gpt-4o-mini` (see `.env`)
- SQLite (`storage/db.py`, `storage/models.py`)
- Google Calendar API (OAuth), USDA FoodData Central, BBC/Sky RSS, The Racing API

## Critical Rules
- **No OpenRouter API calls in the pytest suite. All automated tests must
  run fully offline using mocks.** Gym/meal/news/nutrition logic is
  deterministic Python — test it directly with seeded in-memory SQLite and
  monkeypatched HTTP/USDA calls. End-to-end checks that genuinely involve
  the LLM (does a reply read naturally, does formatting render correctly)
  are manual smoke tests only — run `python main.py` locally and read the
  output. Never write an automated test that calls OpenRouter.
- All dates/times are Europe/London (`ZoneInfo("Europe/London")`) — never
  bare `date.today()`/`datetime.now()` without that tzinfo. The server may
  not run in UK time; a past bug (commit `fcd1d4b`) came from exactly this.
- Never hardcode nutrition values in new code — go through
  `services/nutrition.lookup_macros` (user calibration table → USDA →
  fallback table) so values stay verifiable.

## Common Commands
- Run tests: `python -m pytest tests/ -v`
- Start bot locally: `python main.py`
