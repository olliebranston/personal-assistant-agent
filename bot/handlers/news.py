"""Telegram handler for news and sports queries."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

import config
from agents import news as news_agent
from services import memory

logger = logging.getLogger(__name__)

_USAGE = (
    "News — just say:\n"
    "  news / what's the news    — Chelsea + your horses\n"
    "  chelsea news              — Chelsea FC only\n"
    "  racing / my horses        — your horses only"
)


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Receive a news-related Telegram message and reply with the agent's response."""
    user_id = update.effective_user.id
    if user_id != config.TELEGRAM_ALLOWED_USER_ID:
        return

    text = _extract_text(update) or "news"

    await update.effective_chat.send_action(ChatAction.TYPING)

    try:
        response = await news_agent.handle(text, user_id)
        memory.add(user_id, "user", text)
        memory.add(user_id, "assistant", response)
    except Exception as exc:
        logger.error("[news] %s", exc, exc_info=True)
        response = "Couldn't fetch the news — try again in a moment."

    await update.message.reply_text(response, parse_mode="Markdown")


def _extract_text(update: Update) -> str:
    """Strip any leading /news command prefix from the message text."""
    text = (update.message.text or "").strip()
    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ""
    return text
