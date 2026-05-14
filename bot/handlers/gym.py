"""Telegram handler for gym messages.

Receives updates from python-telegram-bot, enforces the single-user lockdown,
calls agents.gym.handle(), and sends the reply.

Registered in main.py as both a /gym CommandHandler and called by the message
router when free-text intent is classified as gym.
"""

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

import config
from agents import gym as gym_agent
from storage.db import get_connection

_USAGE = (
    "Gym — what do you need?\n"
    "  next session    — today's workout plan\n"
    "  log bench 80kg 5×5, dips 4×10    — log a session\n"
    "  bench history   — progressive overload data"
)


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Receive a gym-related Telegram message and reply with the agent's response.

    Flow:
      1. Reject any user who isn't TELEGRAM_ALLOWED_USER_ID (silent — no reply).
      2. Extract text, stripping /gym command prefix if present.
      3. Return a usage hint if the message is empty after stripping.
      4. Send TYPING action while the LLM call runs.
      5. Open DB connection → agents.gym.handle() → close connection.
      6. Reply with the response string.
    """
    if update.effective_user.id != config.TELEGRAM_ALLOWED_USER_ID:
        return  # silent — don't reveal the bot exists to other users

    text = _extract_text(update)
    if not text:
        await update.message.reply_text(_USAGE)
        return

    await update.effective_chat.send_action(ChatAction.TYPING)

    conn = get_connection()
    try:
        response = await gym_agent.handle(conn, text)
    except Exception as exc:
        print(f"[gym handler] {exc}")
        response = "Gym agent hit an error — try again."
    finally:
        conn.close()

    await update.message.reply_text(response)


def _extract_text(update: Update) -> str:
    """Return message text with any leading /command prefix stripped.

    Examples:
      '/gym log bench 80kg 5×5'  →  'log bench 80kg 5×5'
      '/gym'                     →  ''
      'bench history'            →  'bench history'
    """
    text = (update.message.text or "").strip()
    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ""
    return text
