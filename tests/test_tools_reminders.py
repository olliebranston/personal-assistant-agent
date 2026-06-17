"""Tests for the reminders tool (tools/reminders.py) — §2.5."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from tools.reminders import create_reminder

_TZ = ZoneInfo("Europe/London")


def _make_context():
    ctx = MagicMock()
    ctx.job_queue.run_once = MagicMock()
    return ctx


@pytest.mark.asyncio
async def test_create_reminder_schedules_job_and_returns_scheduled_true():
    telegram_context = _make_context()
    fire_at = datetime.now(tz=_TZ) + timedelta(hours=2)

    result = await create_reminder(
        conn=None,
        telegram_context=telegram_context,
        chat_id=12345,
        text="call dentist",
        when=fire_at.isoformat(),
    )

    assert result["scheduled"] is True
    assert result["text"] == "call dentist"
    assert "fire_at" in result
    telegram_context.job_queue.run_once.assert_called_once()


@pytest.mark.asyncio
async def test_create_reminder_returns_error_for_time_in_past():
    telegram_context = _make_context()
    past = datetime.now(tz=_TZ) - timedelta(hours=1)

    result = await create_reminder(
        conn=None,
        telegram_context=telegram_context,
        chat_id=12345,
        text="call dentist",
        when=past.isoformat(),
    )

    assert result == {"error": "time_in_past"}
    telegram_context.job_queue.run_once.assert_not_called()
