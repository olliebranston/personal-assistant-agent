"""Entry point. Initialises the database, registers handlers, and starts polling."""

import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters

import config
import services.state as state_svc
from agents.router import classify
from bot.handlers import gym as gym_handler
from bot.handlers import meal as meal_handler
from services import memory
from services.openrouter import complete
from storage.db import init_db

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_GENERAL_SYSTEM = (
    "You are Ollie's personal assistant — a sharp, knowledgeable friend. "
    "Be concise and direct. Plain prose. No sycophancy, no filler. "
    "You cover gym training, nutrition, calendar, and news. "
    "Answer what's asked. If it's clearly a gym or meal question use that knowledge directly. "
    "Use conversation history for context on follow-ups."
)


async def _general_response(user_id: int, text: str) -> str:
    """Handle messages that don't match a specific agent domain.

    Uses conversation history so follow-up questions work naturally —
    "and the week before?" after a history query makes sense in context.
    Falls back to a friendly error if the LLM call fails.
    """
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
    """Catch-all handler for free-text messages.

    Rejects non-allowed users before any LLM call, classifies the domain,
    dispatches to the right handler, or falls back to a general LLM response.
    """
    user_id = update.effective_user.id
    if user_id != config.TELEGRAM_ALLOWED_USER_ID:
        return

    text = (update.message.text or "").strip()
    if not text:
        return

    await update.effective_chat.send_action(ChatAction.TYPING)

    # If there's a pending confirmation, bypass the domain classifier and route directly.
    pending = state_svc.get(user_id)
    if pending:
        if pending.get("type") == "food_log":
            await meal_handler.handle(update, context)
            return
        if pending.get("type") == "session_offered":
            await gym_handler.handle(update, context)
            return

    domain = await classify(text)
    logger.info("Routed '%s' → %s", text[:60], domain)

    if domain == "gym":
        await gym_handler.handle(update, context)
    elif domain == "meal":
        await meal_handler.handle(update, context)
    else:
        response = await _general_response(user_id, text)
        await update.message.reply_text(response)


async def error_handler(update: object, context) -> None:
    """Log any unhandled exception that bubbles up from a handler."""
    logger.error("Unhandled error for update %s: %s", update, context.error, exc_info=context.error)


def main() -> None:
    init_db()
    logger.info("Database ready.")

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("gym", gym_handler.handle))
    app.add_handler(CommandHandler("meal", meal_handler.handle))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_message))
    app.add_error_handler(error_handler)

    logger.info("Starting polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
