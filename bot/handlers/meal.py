"""Telegram handler for meal and nutrition messages."""

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

import logging

import config
from agents import meal as meal_agent
from services import memory
from storage.db import get_connection

logger = logging.getLogger(__name__)

_USAGE = (
    "Meal — what do you need?\n"
    "  had chicken and rice    — log food\n"
    "  summary                 — today's protein and calories\n"
    "  how much left           — remaining macros\n"
    "  suggest breakfast       — meal suggestion"
)


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Receive a meal-related Telegram message and reply with the agent's response."""
    user_id = update.effective_user.id
    if user_id != config.TELEGRAM_ALLOWED_USER_ID:
        return

    text = _extract_text(update)
    if not text:
        await update.message.reply_text(_USAGE)
        return

    await update.effective_chat.send_action(ChatAction.TYPING)

    conn = get_connection()
    try:
        response = await meal_agent.handle(conn, text, user_id)
        memory.add(user_id, "user", text)
        memory.add(user_id, "assistant", response)
    except Exception as exc:
        logger.error("[meal] %s", exc, exc_info=True)
        response = "Couldn't reach the AI — try again in a moment."
    finally:
        conn.close()

    await update.message.reply_text(response)


def _extract_text(update: Update) -> str:
    """Strip any leading /meal command prefix from the message text."""
    text = (update.message.text or "").strip()
    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ""
    return text
