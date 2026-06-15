"""Entry point. Initialises the database, registers handlers, and starts polling."""

import asyncio
import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters

import config
import services.state as state_svc
from utils import log_scrubber
from agents.router import classify, set_last_domain
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

_TZ = ZoneInfo("Europe/London")

_GENERAL_SYSTEM = (
    "You are Robin — Ollie's personal assistant. "
    "Talk like a sharp, switched-on friend who knows training, nutrition, scheduling, and sports inside out. "
    "Direct, informal, never robotic. No waffle, no filler, no 'great question!'. "
    "Dry humour where it fits — never forced. "
    "Answer what's asked. One or two sentences is usually enough. "
    "Use conversation history to understand follow-ups without asking Ollie to repeat himself."
)

_GYM_TOOLCALL_SYSTEM = """\
You are Robin — Ollie's personal assistant for training. Talk like a sharp, \
switched-on friend who knows training inside out: direct, informal, never \
robotic. No waffle, no filler, no "great question!". Dry humour where it \
fits — never forced. You're not a coach and not sycophantic — give it \
straight, including when something wasn't great.

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

AMBIENT CONTEXT
Every message starts with a JSON block containing: today's date, day name, \
current time, today's macros so far plus targets, last_workout, \
open_session_today, and latest_weight_kg. Use these facts directly — don't \
call a tool to re-fetch something already in that block.

Use conversation history to understand follow-ups without asking Ollie to \
repeat himself. Answer what's asked — one or two sentences is usually \
enough.\
"""

_REMINDER_SYSTEM = """\
Extract the reminder from the user's message. Reply ONLY with valid JSON — no prose.

Current London time: {now}

{{"text": "<what to remind about>", "datetime": "<ISO 8601 datetime, e.g. 2026-06-13T14:00:00>"}}

Rules:
- "in 2 hours" → now + 2 hours
- "tomorrow morning" → tomorrow at 08:00
- "at 3pm" → today at 15:00 (or tomorrow if 3pm has passed)
- "tomorrow at X" → tomorrow at X
- text should be concise: "call dentist", "check the laundry", "take medication"
"""


async def _set_reminder(update: Update, context, text: str) -> str:
    """Parse a reminder request and schedule a one-off job."""
    now = datetime.now(tz=_TZ)
    system = _REMINDER_SYSTEM.format(now=now.isoformat())

    raw = await complete([{"role": "user", "content": text}], system=system)

    try:
        import json
        parsed = json.loads(re.search(r"\{.*\}", raw, re.DOTALL).group())
        reminder_text = parsed["text"]
        reminder_dt = datetime.fromisoformat(parsed["datetime"])
        if reminder_dt.tzinfo is None:
            reminder_dt = reminder_dt.replace(tzinfo=_TZ)
    except Exception as exc:
        logger.warning("Reminder parse failed: %s — raw: %s", exc, raw)
        return "Couldn't parse that reminder. Try: 'remind me at 3pm to call the dentist'"

    if reminder_dt <= now:
        return "That time has already passed. Give me a future time."

    delay = (reminder_dt - now).total_seconds()
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    async def _fire_reminder(ctx):
        await ctx.bot.send_message(chat_id=chat_id, text=f"Reminder: {reminder_text}")

    context.job_queue.run_once(_fire_reminder, when=delay)

    time_str = reminder_dt.strftime("%H:%M")
    date_str = reminder_dt.strftime("%a %d %b")
    today_str = now.strftime("%a %d %b")
    when_str = f"today at {time_str}" if date_str == today_str else f"{date_str} at {time_str}"
    return f"Reminder set for {when_str} — '{reminder_text}'."


async def _handle_gym_tool_calling(update: Update, context, text: str) -> None:
    """Tool-calling path for gym messages, backed by the gym tool registry (§2.1/§4.3).

    Everything else still goes through agents/router.py during this transition (§7 step 5).
    """
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    conn = get_connection()
    try:
        ambient_context = build_ambient_context(conn)
        history = memory.get(user_id)
        registry = build_tool_registry(conn, context, chat_id)

        reply = await complete(
            messages=[
                {"role": "system", "content": json.dumps(ambient_context)},
                {"role": "user", "content": text},
            ],
            system=_GYM_TOOLCALL_SYSTEM,
            history=history,
            tools=registry.schemas,
            tool_executor=registry.execute,
        )
    finally:
        conn.close()

    memory.add(user_id, "user", text)
    memory.add(user_id, "assistant", reply)
    await update.message.reply_text(reply)


async def _general_response(user_id: int, text: str) -> str:
    hist = memory.get(user_id)
    try:
        response = await complete(
            [{"role": "user", "content": text}],
            system=_GENERAL_SYSTEM,
            history=hist,
        )
    except Exception as exc:
        logger.error("General response failed: %s", exc)
        return "Something went wrong on my end — try again."

    memory.add(user_id, "user", text)
    memory.add(user_id, "assistant", response)
    return response


async def route_message(update: Update, context) -> None:
    user_id = update.effective_user.id
    if user_id != config.TELEGRAM_ALLOWED_USER_ID:
        return

    text = (update.message.text or "").strip()
    if not text:
        return

    await update.effective_chat.send_action(ChatAction.TYPING)

    pending = state_svc.get(user_id)
    if pending:
        if pending.get("type") == "food_log":
            await meal_handler.handle(update, context)
            return
        if pending.get("type") == "session_offered":
            await gym_handler.handle(update, context)
            return
        if pending.get("type") == "event_create":
            await calendar_handler.handle(update, context)
            return

    domain = await classify(text, user_id=user_id)
    logger.info("Routed '%s' → %s", text[:60], domain)

    if domain == "gym":
        set_last_domain(user_id, "gym")
        await _handle_gym_tool_calling(update, context, text)
    elif domain == "meal":
        set_last_domain(user_id, "meal")
        await meal_handler.handle(update, context)
    elif domain == "calendar":
        set_last_domain(user_id, "calendar")
        await calendar_handler.handle(update, context)
    elif domain == "news":
        set_last_domain(user_id, "news")
        await news_handler.handle(update, context)
    elif domain == "reminder":
        response = await _set_reminder(update, context, text)
        await update.message.reply_text(response)
    else:
        response = await _general_response(user_id, text)
        await update.message.reply_text(response)


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
