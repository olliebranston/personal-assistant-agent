"""Entry point. Initialises the database, registers handlers, and starts in webhook mode."""

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters

import config
import services.state as state_svc
from utils import log_scrubber
from agents.router import classify
from bot.handlers import calendar as calendar_handler
from bot.handlers import gym as gym_handler
from bot.handlers import meal as meal_handler
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
    "You are Ollie's personal assistant — a sharp, knowledgeable friend. "
    "Be concise and direct. Plain prose. No sycophancy, no filler. "
    "You cover gym training, nutrition, calendar, and news. "
    "Answer what's asked. If it's clearly a gym or meal question use that knowledge directly. "
    "Use conversation history for context on follow-ups."
)

_ptb_app: Application | None = None


async def _general_response(user_id: int, text: str) -> str:
    """Handle messages that don't match a specific agent domain."""
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
    """Catch-all handler for free-text messages."""
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

    domain = await classify(text)
    logger.info("Routed '%s' → %s", text[:60], domain)

    if domain == "gym":
        await gym_handler.handle(update, context)
    elif domain == "meal":
        await meal_handler.handle(update, context)
    elif domain == "calendar":
        await calendar_handler.handle(update, context)
    else:
        response = await _general_response(user_id, text)
        await update.message.reply_text(response)


async def error_handler(update: object, context) -> None:
    logger.error("Unhandled error for update %s: %s", update, context.error, exc_info=context.error)


# ── FastAPI app with PTB lifecycle ────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the Telegram bot (handlers + scheduler) on startup; stop on shutdown.

    Uses PTB's async context manager so initialize() and shutdown() are called
    automatically. app.start() kicks off the update dispatcher and job queue.
    """
    global _ptb_app
    init_db()
    logger.info("Database ready.")

    _ptb_app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    _ptb_app.add_handler(CommandHandler("calendar", calendar_handler.handle))
    _ptb_app.add_handler(CommandHandler("gym", gym_handler.handle))
    _ptb_app.add_handler(CommandHandler("meal", meal_handler.handle))
    _ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_message))
    _ptb_app.add_error_handler(error_handler)

    register_jobs(_ptb_app)

    await _ptb_app.bot.set_webhook(url=config.WEBHOOK_URL)
    logger.info("Webhook registered: %s", config.WEBHOOK_URL)

    async with _ptb_app:
        await _ptb_app.start()
        logger.info("Bot running in webhook mode.")
        yield
        await _ptb_app.stop()
        logger.info("Bot stopped.")


web_app = FastAPI(lifespan=lifespan)


@web_app.post("/webhook")
async def webhook(request: Request) -> Response:
    """Receive a Telegram update and hand it to the PTB dispatcher."""
    data = await request.json()
    update = Update.de_json(data, _ptb_app.bot)
    await _ptb_app.update_queue.put(update)
    return Response(status_code=200)


@web_app.get("/health")
async def health() -> Response:
    return Response(status_code=200)


@web_app.get("/")
async def root() -> Response:
    return Response(status_code=200)


def main() -> None:
    uvicorn.run(web_app, host="0.0.0.0", port=config.PORT)


if __name__ == "__main__":
    main()
