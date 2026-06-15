"""Calendar tools — §2.3 of TOOL_CALLING_DESIGN.md.

Each tool is `async def tool_name(conn, **kwargs) -> dict`, JSON-serialisable,
and returns {"error": "..."} on failure instead of raising.
"""

from __future__ import annotations

import logging
import sqlite3

from services.google_calendar import create_event, get_service, list_events

logger = logging.getLogger(__name__)


async def get_calendar_events(conn: sqlite3.Connection, time_min: str, time_max: str) -> dict:
    """Return events across all calendars in [time_min, time_max], with an all_day flag."""
    try:
        service = get_service()
        events = list_events(service, time_min, time_max)
    except Exception as exc:
        logger.warning("get_calendar_events failed: %s", exc)
        return {"error": "calendar_unavailable"}

    return {
        "events": [
            {
                "summary": ev["summary"],
                "start": ev["start"],
                "end": ev["end"],
                "location": ev["location"] or None,
                "all_day": "T" not in ev["start"],
                "calendar": ev["calendar"],
            }
            for ev in events
        ]
    }


async def create_calendar_event(
    conn: sqlite3.Connection,
    summary: str,
    start: str,
    end: str,
    location: str = "",
    all_day: bool = False,
) -> dict:
    """Create a calendar event. Only call after Ollie has confirmed (§3.2c)."""
    try:
        service = get_service()
    except Exception as exc:
        logger.warning("create_calendar_event: auth failed: %s", exc)
        return {"error": "calendar_unavailable"}

    try:
        result = create_event(
            service, summary=summary, start=start, end=end, location=location, all_day=all_day
        )
    except Exception as exc:
        logger.warning("create_calendar_event: create failed: %s", exc)
        return {"error": "create_failed"}

    event = result["event"]
    return {
        "created": True,
        "summary": event.get("summary", summary),
        "start": event["start"].get("dateTime") or event["start"].get("date"),
        "end": event["end"].get("dateTime") or event["end"].get("date"),
        "location": event.get("location") or None,
        "calendar": result["calendar"],
    }


TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_calendar_events",
            "description": (
                "Check what's in Ollie's calendar for a time range — use this to answer "
                "'what's on today/this week', 'when's X', or to check for clashes before "
                "proposing a new event. time_min and time_max are ISO 8601 datetimes with "
                "a UTC offset, e.g. '2026-06-15T00:00:00+01:00'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "time_min": {
                        "type": "string",
                        "description": "Start of the window, ISO 8601 with UTC offset.",
                    },
                    "time_max": {
                        "type": "string",
                        "description": "End of the window, ISO 8601 with UTC offset.",
                    },
                },
                "required": ["time_min", "time_max"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": (
                "Create a calendar event. ONLY call this after Ollie has confirmed the "
                "event in his reply to your proposal — never call this speculatively. "
                "Always propose first (title, date/time or all-day range, location if "
                "known) and wait for his confirmation (e.g. 'yes', 'sounds right') in the "
                "next message before calling this."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Event title.",
                    },
                    "start": {
                        "type": "string",
                        "description": (
                            "Start. If all_day is false: ISO 8601 datetime without offset, "
                            "e.g. '2026-06-15T19:00:00' (Europe/London is applied "
                            "automatically). If all_day is true: date only, 'YYYY-MM-DD'."
                        ),
                    },
                    "end": {
                        "type": "string",
                        "description": (
                            "End. Same format as start. For all-day events, Google Calendar "
                            "uses an EXCLUSIVE end date — for a trip covering 11-18 Sep "
                            "inclusive, pass start='2026-09-11' and end='2026-09-19' (the "
                            "day after the last day), even though you tell Ollie '11-18 Sep'."
                        ),
                    },
                    "location": {
                        "type": "string",
                        "description": "Venue or address. Leave empty if not mentioned — never guess.",
                    },
                    "all_day": {
                        "type": "boolean",
                        "description": "True for all-day / date-range events with no specific time.",
                    },
                },
                "required": ["summary", "start", "end"],
            },
        },
    },
]
