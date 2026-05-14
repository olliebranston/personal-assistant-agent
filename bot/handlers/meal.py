"""Telegram handler for meal and nutrition messages.

Registered in main.py as a /meal CommandHandler and called by the message
router when free-text intent is classified as meal/nutrition.
"""

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

import config
from agents import meal as meal_agent
from storage.db import get_connection

_USAGE = (
    "Meal — what do you need?\n"
    "  had chicken and rice    — log food\n"
    "  summary                 — today's protein and calories\n"
    "  how much left           — remaining macros\n"
    "  suggest breakfast       — meal suggestion"
)


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Receive a meal-related Telegram message and reply with the agent's response."""
    if update.effective_user.id != config.TELEGRAM_ALLOWED_USER_ID:
        return

    text = _extract_text(update)
    if not text:
        await update.message.reply_text(_USAGE)
        return

    await update.effective_chat.send_action(ChatAction.TYPING)

    conn = get_connection()
    try:
        response = await meal_agent.handle(conn, text)
    except Exception as exc:
        print(f"[meal handler] {exc}")
        response = "Meal agent hit an error — try again."
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
