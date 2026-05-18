"""Telegram handler for calendar messages."""

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

import logging

import config
from agents import calendar as calendar_agent
from services import memory
from storage.db import get_connection

logger = logging.getLogger(__name__)

_USAGE = (
    "Calendar — what do you need?\n"
    "  what's on today          — today's events\n"
    "  what have I got this week — full week view\n"
    "  add dentist Friday 10am  — create an event\n"
    "  drinks at The Anchor Tue — social event (defaults to 7pm)"
)


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Receive a calendar-related Telegram message and reply with the agent's response."""
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
        response = await calendar_agent.handle(conn, text, user_id)
        memory.add(user_id, "user", text)
        memory.add(user_id, "assistant", response)
    except Exception as exc:
        logger.error("[calendar] %s", exc, exc_info=True)
        response = "Couldn't reach the calendar — try again in a moment."
    finally:
        conn.close()

    await update.message.reply_text(response)


def _extract_text(update: Update) -> str:
    """Strip any leading /calendar command prefix from the message text."""
    text = (update.message.text or "").strip()
    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ""
    return text
