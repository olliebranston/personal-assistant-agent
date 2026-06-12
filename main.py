"""Entry point. Initialises the database, registers handlers, and starts polling."""

import logging

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
from storage.db import init_db

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log_scrubber.install()
logger = logging.getLogger(__name__)

_GENERAL_SYSTEM = (
    "You are Robin — Ollie's personal assistant. "
    "Talk like a sharp, switched-on friend who knows training, nutrition, scheduling, and sports inside out. "
    "Direct, informal, never robotic. No waffle, no filler, no 'great question!'. "
    "Dry humour where it fits — never forced. "
    "Answer what's asked. One or two sentences is usually enough. "
    "Use conversation history to understand follow-ups without asking Ollie to repeat himself."
)


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
        await gym_handler.handle(update, context)
    elif domain == "meal":
        set_last_domain(user_id, "meal")
        await meal_handler.handle(update, context)
    elif domain == "calendar":
        set_last_domain(user_id, "calendar")
        await calendar_handler.handle(update, context)
    elif domain == "news":
        set_last_domain(user_id, "news")
        await news_handler.handle(update, context)
    else:
        response = await _general_response(user_id, text)
        await update.message.reply_text(response)


async def error_handler(update: object, context) -> None:
    logger.error("Unhandled error for update %s: %s", update, context.error, exc_info=context.error)


def main() -> None:
    init_db()
    logger.info("Database ready.")

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
