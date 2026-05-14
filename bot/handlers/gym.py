"""Telegram handler for gym messages."""

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

import config
from agents import gym as gym_agent
from services import memory
from storage.db import get_connection

_USAGE = (
    "Gym — what do you need?\n"
    "  next session    — today's workout plan\n"
    "  log bench 80kg 5×5, dips 4×10    — log a session\n"
    "  bench history   — progressive overload data"
)


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Receive a gym-related Telegram message and reply with the agent's response."""
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
        response = await gym_agent.handle(conn, text, user_id)
        memory.add(user_id, "user", text)
        memory.add(user_id, "assistant", response)
    except Exception as exc:
        print(f"[gym handler] {exc}")
        response = "Something went wrong — try again."
    finally:
        conn.close()

    await update.message.reply_text(response)


def _extract_text(update: Update) -> str:
    """Strip any leading /command prefix from the message text."""
    text = (update.message.text or "").strip()
    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ""
    return text
