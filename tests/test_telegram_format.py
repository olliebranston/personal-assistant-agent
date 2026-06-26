"""Tests for utils/telegram_format.py — no Telegram or network calls."""

from __future__ import annotations

import pytest
from telegram.error import BadRequest

from utils.telegram_format import reply_formatted, send_formatted, to_telegram_html


# ── to_telegram_html ──────────────────────────────────────────────────────────


def test_bold_double_asterisk():
    assert to_telegram_html("**bold**") == "<b>bold</b>"


def test_italic_single_asterisk():
    assert to_telegram_html("*italic*") == "<i>italic</i>"


def test_italic_underscore():
    assert to_telegram_html("_italic_") == "<i>italic</i>"


def test_underscore_inside_word_not_converted():
    # snake_case identifiers must not be mangled into <i> tags.
    assert to_telegram_html("the snake_case_var stays as is") == "the snake_case_var stays as is"


def test_code_span():
    assert to_telegram_html("`code`") == "<code>code</code>"


def test_header_levels_become_bold_line():
    assert to_telegram_html("### Header\ntext") == "<b>Header</b>\ntext"
    assert to_telegram_html("# Header\ntext") == "<b>Header</b>\ntext"


def test_html_special_chars_are_escaped():
    assert to_telegram_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_bullets_and_numbered_lists_pass_through():
    text = "- first\n- second\n1. one\n2. two"
    assert to_telegram_html(text) == text


def test_mixed_message_end_to_end():
    text = "### Logged\n- **bench press**: 80kg\n- _rest day_ tomorrow"
    expected = "<b>Logged</b>\n- <b>bench press</b>: 80kg\n- <i>rest day</i> tomorrow"
    assert to_telegram_html(text) == expected


# ── send_formatted / reply_formatted fallback on BadRequest ──────────────────


class _FakeBot:
    def __init__(self, fail_first: bool):
        self.fail_first = fail_first
        self.calls: list[dict] = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.calls.append({"chat_id": chat_id, "text": text, "parse_mode": parse_mode})
        if self.fail_first and parse_mode == "HTML":
            raise BadRequest("Can't parse entities")


class _FakeMessage:
    def __init__(self, fail_first: bool):
        self.fail_first = fail_first
        self.calls: list[dict] = []

    async def reply_text(self, text, parse_mode=None):
        self.calls.append({"text": text, "parse_mode": parse_mode})
        if self.fail_first and parse_mode == "HTML":
            raise BadRequest("Can't parse entities")


@pytest.mark.asyncio
async def test_send_formatted_uses_html_parse_mode():
    bot = _FakeBot(fail_first=False)

    await send_formatted(bot, 123, "**bold**")

    assert len(bot.calls) == 1
    assert bot.calls[0]["parse_mode"] == "HTML"
    assert bot.calls[0]["text"] == "<b>bold</b>"


@pytest.mark.asyncio
async def test_send_formatted_falls_back_to_plain_text_on_bad_request():
    bot = _FakeBot(fail_first=True)

    await send_formatted(bot, 123, "**bold**")

    assert len(bot.calls) == 2
    assert bot.calls[0]["parse_mode"] == "HTML"
    assert bot.calls[1]["parse_mode"] is None
    assert bot.calls[1]["text"] == "**bold**"  # original, unconverted text


@pytest.mark.asyncio
async def test_reply_formatted_falls_back_to_plain_text_on_bad_request():
    message = _FakeMessage(fail_first=True)

    await reply_formatted(message, "*italic*")

    assert len(message.calls) == 2
    assert message.calls[0]["parse_mode"] == "HTML"
    assert message.calls[1]["parse_mode"] is None
    assert message.calls[1]["text"] == "*italic*"
