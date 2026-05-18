"""Google Calendar API wrapper. Handles OAuth token refresh, event creation, and event listing."""

from __future__ import annotations

import logging
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

import config

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
_TZ = "Europe/London"

# Preferred calendar names for event creation, in priority order.
_WRITE_PREFERENCE = ["Social", "Personal"]


def get_service():
    """Load token.json and return an authenticated Google Calendar service.

    Refreshes silently if the token is expired. Raises FileNotFoundError with
    an actionable message if token.json is missing so the agent can relay it.
    """
    token_path = Path(config.GOOGLE_TOKEN_FILE)
    if not token_path.exists():
        raise FileNotFoundError(
            f"token.json not found at '{token_path}'. "
            "Run 'python scripts/auth_google.py' to authenticate first."
        )

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json())
        logger.debug("Google credentials refreshed.")

    return build("calendar", "v3", credentials=creds)


def list_events(service, time_min: str, time_max: str, max_results: int = 15) -> list[dict]:
    """Return events across all user calendars in the given time window.

    Queries every calendar in the user's list so events on Social, Personal,
    etc. are all included — not just primary.

    time_min / time_max: RFC 3339 strings, e.g. '2024-01-15T00:00:00+00:00'.
    Each result dict: {summary, start, end, location, calendar}.
    """
    try:
        cal_list = service.calendarList().list().execute().get("items", [])
    except Exception as exc:
        logger.warning("Could not fetch calendar list, falling back to primary: %s", exc)
        cal_list = [{"id": "primary", "summary": "primary"}]

    all_events: list[dict] = []
    for cal in cal_list:
        cal_id = cal["id"]
        cal_name = cal.get("summary", cal_id)
        try:
            result = (
                service.events()
                .list(
                    calendarId=cal_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
        except Exception as exc:
            logger.warning("Skipping calendar '%s': %s", cal_name, exc)
            continue

        for item in result.get("items", []):
            start = item["start"].get("dateTime") or item["start"].get("date", "")
            end = item["end"].get("dateTime") or item["end"].get("date", "")
            all_events.append({
                "summary": item.get("summary", "(No title)"),
                "start": start,
                "end": end,
                "location": item.get("location", ""),
                "calendar": cal_name,
            })

    # Sort by start time across all calendars, deduplicate by (summary, start).
    seen: set[tuple] = set()
    unique: list[dict] = []
    for ev in sorted(all_events, key=lambda e: e["start"]):
        key = (ev["summary"], ev["start"])
        if key not in seen:
            seen.add(key)
            unique.append(ev)

    return unique[:max_results]


def create_event(service, summary: str, start: str, end: str, location: str = "") -> dict:
    """Create a timed event on the first available preferred calendar.

    Calendar priority: Social → Personal → primary.
    start / end: ISO 8601 datetime strings without offset, e.g. '2024-01-15T19:00:00'.
    The Europe/London timezone is applied server-side so BST/GMT is handled correctly.
    Returns the created event resource dict from the API.
    """
    calendar_id = _find_calendar_id(service, _WRITE_PREFERENCE)

    body: dict = {
        "summary": summary,
        "start": {"dateTime": start, "timeZone": _TZ},
        "end": {"dateTime": end, "timeZone": _TZ},
    }
    if location:
        body["location"] = location

    event = service.events().insert(calendarId=calendar_id, body=body).execute()
    logger.info("Event '%s' created on calendar '%s' (id: %s)", summary, calendar_id, event.get("id"))
    return event


def _find_calendar_id(service, preferred_names: list[str]) -> str:
    """Return the ID of the first calendar matching a preferred name, else 'primary'."""
    try:
        items = service.calendarList().list().execute().get("items", [])
        for name in preferred_names:
            for cal in items:
                if cal.get("summary", "").strip().lower() == name.lower():
                    return cal["id"]
    except Exception as exc:
        logger.warning("Calendar list lookup failed: %s. Using primary.", exc)
    return "primary"
