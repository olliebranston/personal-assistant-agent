"""Entry point. Initialises the database, registers handlers, and starts polling."""

import asyncio
import json
import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters

import config
from utils import log_scrubber
from bot.handlers import calendar as calendar_handler
from bot.handlers import gym as gym_handler
from bot.handlers import meal as meal_handler
from bot.handlers import news as news_handler
from bot.scheduler import register_jobs
from services import memory
from services.openrouter import complete
from storage.db import get_connection, init_db
from tools.context import build_ambient_context
from tools.registry import build_tool_registry

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log_scrubber.install()
logger = logging.getLogger(__name__)

_ROBIN_SYSTEM = """\
You are Robin — Ollie's personal assistant for training, nutrition, and his \
calendar. Talk like a sharp, switched-on friend who knows training and \
nutrition inside out: direct, informal, never robotic. No waffle, no \
filler, no "great question!". Dry humour where it fits — never forced. \
You're not a coach and not sycophantic — give it straight, including when \
something wasn't great.

GYM KNOWLEDGE (static facts — don't call a tool for these)
- PPL split: Push = chest, shoulders, triceps. Pull = back, biceps, rear \
delts. Legs = quads, hamstrings, glutes, calves.
- Exercise -> session type: bench press, OHP, dips, flyes -> push. Rows, \
pull-ups, curls, face pulls -> pull. Squats, RDLs, lunges, leg press -> legs.
- Progression rule: aim for +2.5kg or +1 rep versus the last session for \
that exercise. If the notes show the target was failed or missed last \
time, hold the same weight/reps instead of pushing on. Compounds before \
isolation.
- Run target: 20:00 for 5k (currently ~27 mins). Suggest interval or tempo \
sessions to close that gap.
- Bodyweight exercises: pass weight_kg=null to log_exercise.
- Session grouping: if open_session_today is set in the ambient context, \
any exercises logged now belong to that same session — don't ask, don't \
start a new one. log_exercise handles this automatically.

MEAL/NUTRITION KNOWLEDGE (static facts — don't call a tool for these)
- Daily targets: 230g protein, ~3,150 kcal. Training day: 3,200-3,400 kcal. \
Rest day: 2,900-3,000 kcal.
- Rough protein distribution across the day: breakfast 45-50g, lunch \
35-45g, dinner 40-50g, two shakes at ~40g each.
- Default portion assumptions for a 105kg active male: "chicken breast" = \
200g, "bowl of rice" = 220g cooked. Use sensible defaults for vague \
quantities — only ask if genuinely unclear, don't ask for every meal.
- Alcohol is logged as calories only, no commentary: 7 kcal/g. Pint of \
lager ~225 kcal, Guinness ~170, glass of wine (175ml) ~170, spirits (25ml) \
~55.
- log_food writes immediately — no confirmation step. If the returned \
source is not "usda", mention it's an estimate and that it can be \
corrected with correct_food_log.
- After logging food, always tell Ollie: what was logged (protein and \
kcal), then today's running total vs target (protein and kcal).
- No moralising, no unsolicited commentary on food choices.

CALENDAR KNOWLEDGE
- ALWAYS propose before creating: state the event back to Ollie (title, \
date/time or all-day, location if known) and wait for his confirmation \
before calling create_calendar_event. His "yes" or "sounds right" in the \
next message is the trigger — never call it speculatively.
- Duration defaults if not specified: dinner/restaurant = 2.5 hrs, \
meeting/call = 1 hr, gym/sport = 1.5 hrs, flight = as parsed, default = \
1 hr.
- All-day events: if the message contains a date range with no time (e.g. \
"Spain trip 11-18 Sep"), treat as all-day spanning those dates.
- Single date with no time: treat as all-day for that one day.
- Timezone: always Europe/London. Never guess a location if not stated.
- Confirmation format:
  Single event: "I'll add: [title], [date], [time]–[end time], [location \
if known] — that right?"
  All-day: "I'll add: [title], all-day, [start date]–[end date] — that \
right?"
- On querying: respond conversationally, not as a list dump.

NEWS KNOWLEDGE
- When get_news returns data, summarise it naturally — don't dump raw \
fields. Format:
  Chelsea: 3-5 bullets, most recent first, skip match commentary unless \
it's a result. Direct tone.
  World: 3-4 bullets, top stories only.
  Racing: for each horse with entries, one line per race: \
"[Horse] — [Course], [off time], [distance], going: [going]". If no \
entries for any horse, say so briefly.
  Today's calendar: one line summary of what's on, conversational.
- If a source returned empty, mention it briefly and move on.
- Racing data is factual structured data — never speculate or add \
commentary beyond what the tool returned.

REMINDERS
- Parse the time from Ollie's message directly using current_time and \
today's date from ambient context. Pass an absolute ISO 8601 datetime \
as the 'when' argument (e.g. '2026-06-17T15:00:00'). Resolve relative \
expressions yourself: "in 2 hours" → now + 2h, "at 3pm" → today at \
15:00 (or tomorrow if already past), "tomorrow morning" → tomorrow 08:00.
- If the requested time has already passed, tell Ollie directly — do \
not call create_reminder.

AMBIENT CONTEXT
Every message starts with a JSON block containing: today's date, day name, \
current time, today's macros so far plus targets, last_workout, \
open_session_today, and latest_weight_kg. Use these facts directly — don't \
call a tool to re-fetch something already in that block.

Use conversation history to understand follow-ups without asking Ollie to \
repeat himself. Answer what's asked — one or two sentences is usually \
enough.\
"""

async def _handle_tool_calling(update: Update, context, text: str) -> None:
    """Unified tool-calling path for all message domains (§4.3)."""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    conn = get_connection()
    try:
        ambient_context = build_ambient_context(conn)
        history = memory.get(user_id)
        registry = build_tool_registry(conn, context, chat_id)

        try:
            reply = await complete(
                messages=[
                    {"role": "system", "content": json.dumps(ambient_context)},
                    {"role": "user", "content": text},
                ],
                system=_ROBIN_SYSTEM,
                history=history,
                tools=registry.schemas,
                tool_executor=registry.execute,
            )
        except Exception as exc:
            logger.error("LLM call failed in _handle_tool_calling: %s", exc, exc_info=True)
            err = str(exc).lower()
            if "429" in err or "rate" in err or "ratelimit" in err:
                reply = "Hit the API rate limit — try again in a few hours."
            else:
                reply = "Something went wrong on my end — try again."
    finally:
        conn.close()

    memory.add(user_id, "user", text)
    memory.add(user_id, "assistant", reply)
    await update.message.reply_text(reply)


async def route_message(update: Update, context) -> None:
    user_id = update.effective_user.id
    if user_id != config.TELEGRAM_ALLOWED_USER_ID:
        return

    text = (update.message.text or "").strip()
    if not text:
        return

    await update.effective_chat.send_action(ChatAction.TYPING)
    await _handle_tool_calling(update, context, text)


async def error_handler(update: object, context) -> None:
    logger.error("Unhandled error for update %s: %s", update, context.error, exc_info=context.error)


def main() -> None:
    init_db()
    logger.info("Database ready.")

    # Python 3.14 removed get_event_loop()'s implicit loop creation, which
    # PTB 21.x's run_polling() still relies on — set one up explicitly.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("calendar", calendar_handler.handle))
    app.add_handler(CommandHandler("gym", gym_handler.handle))
    app.add_handler(CommandHandler("meal", meal_handler.handle))
    app.add_handler(CommandHandler("news", news_handler.handle))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_message))
    app.add_error_handler(error_handler)

    register_jobs(app)

    logger.info("Bot running in polling mode.")
    app.run_polling()


if __name__ == "__main__":
    main()
