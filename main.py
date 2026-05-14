"""Entry point. Initialises the database, registers handlers, and starts polling."""

import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters

import config
from agents.router import classify
from bot.handlers import gym as gym_handler
from bot.handlers import meal as meal_handler
from storage.db import init_db

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def route_message(update: Update, context) -> None:
    """Catch-all handler for free-text messages.

    Rejects non-allowed users first (before any LLM call), then classifies
    the message domain and dispatches to the appropriate agent handler.
    """
    if update.effective_user.id != config.TELEGRAM_ALLOWED_USER_ID:
        return  # silent — don't reveal the bot to strangers

    text = (update.message.text or "").strip()
    if not text:
        return

    # Show typing immediately — classify() makes an LLM call that takes 1–3s.
    await update.effective_chat.send_action(ChatAction.TYPING)

    domain = await classify(text)
    logger.info("Routed '%s' → %s", text[:60], domain)

    if domain == "gym":
        await gym_handler.handle(update, context)
    elif domain == "meal":
        await meal_handler.handle(update, context)
    else:
        await update.message.reply_text(
            "Not sure which agent handles that. Try /gym or /meal."
        )


async def error_handler(update: object, context) -> None:
    """Log any unhandled exception that bubbles up from a handler."""
    logger.error("Unhandled error for update %s: %s", update, context.error, exc_info=context.error)


def main() -> None:
    # Create tables on first run; safe to call every startup (IF NOT EXISTS).
    init_db()
    logger.info("Database ready.")

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Command handlers — registered first so /gym and /meal are never misrouted.
    app.add_handler(CommandHandler("gym", gym_handler.handle))
    app.add_handler(CommandHandler("meal", meal_handler.handle))

    # Catch-all for free-text messages — must come after command handlers.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_message))

    app.add_error_handler(error_handler)

    logger.info("Starting polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
