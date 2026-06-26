"""Reminders tool — §2.5 of TOOL_CALLING_DESIGN.md.

create_reminder schedules a one-off Telegram notification via PTB's job queue.
The model resolves relative times itself (using ambient context) and passes
an absolute ISO 8601 datetime — no secondary LLM call needed.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from utils.telegram_format import send_formatted

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("Europe/London")


async def create_reminder(
    conn: sqlite3.Connection,
    telegram_context,
    chat_id: int,
    text: str,
    when: str,
) -> dict:
    """Schedule a one-off reminder. Returns {"scheduled": true, ...} or {"error": "time_in_past"}."""
    now = datetime.now(tz=_TZ)

    try:
        fire_at = datetime.fromisoformat(when)
        if fire_at.tzinfo is None:
            fire_at = fire_at.replace(tzinfo=_TZ)
    except (ValueError, TypeError) as exc:
        logger.warning("create_reminder: invalid 'when' value %r: %s", when, exc)
        return {"error": "invalid_time", "detail": str(exc)}

    if fire_at <= now:
        return {"error": "time_in_past"}

    delay = (fire_at - now).total_seconds()

    async def _fire(ctx):
        await send_formatted(ctx.bot, chat_id, f"Reminder: {text}")

    telegram_context.job_queue.run_once(_fire, when=delay)

    logger.info("Reminder scheduled: '%s' at %s (in %.0fs)", text, fire_at.isoformat(), delay)
    return {
        "scheduled": True,
        "text": text,
        "fire_at": fire_at.isoformat(),
    }


TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "create_reminder",
            "description": (
                "Schedule a one-off reminder for Ollie. Resolve relative times like "
                "'in 2 hours' or 'at 3pm' using the current datetime from ambient context, "
                "then pass the absolute ISO 8601 datetime as 'when'. If the time has already "
                "passed, say so instead of calling this."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Concise reminder text, e.g. 'call dentist', 'check the laundry'.",
                    },
                    "when": {
                        "type": "string",
                        "description": (
                            "Absolute ISO 8601 datetime, e.g. '2026-06-17T15:00:00'. "
                            "Resolve from ambient context current_time + today's date."
                        ),
                    },
                },
                "required": ["text", "when"],
            },
        },
    },
]
