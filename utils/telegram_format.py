"""Converts the markdown the LLM naturally produces into Telegram-safe HTML.

Messages were being sent with parse_mode=None almost everywhere, so any
**bold**/### heading syntax the model produced showed up as literal
asterisks/hashes instead of being rendered. HTML is used instead of
Telegram's MarkdownV2 because MarkdownV2 requires escaping a long list of
punctuation characters in plain text, which free-form LLM output makes easy
to get wrong; HTML only needs &, <, > escaped.
"""

from __future__ import annotations

import logging
import re

from telegram import Bot, Message
from telegram.error import BadRequest

logger = logging.getLogger(__name__)

_HEADER_RE = re.compile(r"^#{1,6}[ \t]+(.+)$", re.MULTILINE)
_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
_ITALIC_STAR_RE = re.compile(r"\*([^*\n]+)\*")
_ITALIC_UNDERSCORE_RE = re.compile(r"(?<![\w_])_([^_\n]+)_(?![\w_])")


def to_telegram_html(text: str) -> str:
    """Convert common markdown patterns to Telegram-supported HTML tags.

    Handles: **bold**, *bold*, _italic_, `code`, and #/##/### headers (bold
    line, hashes stripped). Bullets (-/•) and numbered lists pass through
    unchanged — Telegram doesn't need markup for those.
    """
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    with_headers = _HEADER_RE.sub(r"<b>\1</b>", escaped)
    with_code = _CODE_RE.sub(r"<code>\1</code>", with_headers)
    with_bold = _BOLD_RE.sub(r"<b>\1</b>", with_code)
    with_italic_star = _ITALIC_STAR_RE.sub(r"<i>\1</i>", with_bold)
    return _ITALIC_UNDERSCORE_RE.sub(r"<i>\1</i>", with_italic_star)


async def send_formatted(bot: Bot, chat_id: int, text: str) -> None:
    """Send text through to_telegram_html with parse_mode="HTML".

    Falls back to a plain-text send on a BadRequest (malformed HTML edge
    case) so a formatting glitch never drops a reply or crashes a scheduled
    job.
    """
    try:
        await bot.send_message(chat_id=chat_id, text=to_telegram_html(text), parse_mode="HTML")
    except BadRequest as exc:
        logger.warning("send_formatted: HTML parse failed (%s) — retrying as plain text", exc)
        await bot.send_message(chat_id=chat_id, text=text)


async def reply_formatted(message: Message, text: str) -> None:
    """Same conversion/fallback as send_formatted, but via Message.reply_text."""
    try:
        await message.reply_text(to_telegram_html(text), parse_mode="HTML")
    except BadRequest as exc:
        logger.warning("reply_formatted: HTML parse failed (%s) — retrying as plain text", exc)
        await message.reply_text(text)
