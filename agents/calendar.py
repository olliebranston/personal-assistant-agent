"""Calendar agent: event queries and creation with mandatory confirmation.

Rule: ALWAYS confirm before creating — format: "I'll add: [name], [date], [time], [location if known] — shall I?"
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import services.state as state_svc
from services import memory as memory_svc
from services.google_calendar import create_event, get_service, list_events
from services.openrouter import complete

_TZ = ZoneInfo("Europe/London")

_AFFIRMATIVES = frozenset({
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "correct",
    "go ahead", "perfect", "good", "fine", "do it", "add it", "confirm",
})

# ── System prompts ────────────────────────────────────────────────────────────

_ROUTER_SYSTEM = """\
Classify the user's calendar message. Reply ONLY with valid JSON — no prose.

{"action": "query"}                                          — asking about existing events or schedule
{"action": "create"}                                         — adding, scheduling, or booking a new event
{"action": "clarify", "question": "<one short question>"}    — intent genuinely unclear
"""

_QUERY_SYSTEM = """\
Extract the time window the user wants to search. Reply ONLY with valid JSON — no prose.

Current London time: {now}

{{"time_min": "<ISO 8601 with UTC offset>", "time_max": "<ISO 8601 with UTC offset>"}}

Rules:
- "today" → start of today to 23:59 today
- "tomorrow" → start of tomorrow to 23:59 tomorrow
- "this week" → now to end of Sunday 23:59
- "next week" → next Monday 00:00 to next Sunday 23:59
- "weekend" → coming Saturday 00:00 to Sunday 23:59
- "this month" → today to last day of the current month 23:59
- For a specific date, use that date 00:00 to 23:59
- Carry the UTC offset from the current London time shown above
"""

_CREATE_SYSTEM = """\
Extract event details from the user's message. Reply ONLY with valid JSON — no prose.

Current London time: {now}
Tomorrow's date: {tomorrow}

{{
  "summary": "<event title, concise>",
  "start": "<ISO 8601 datetime, no offset, e.g. 2024-06-15T19:00:00>",
  "end":   "<ISO 8601 datetime, no offset, e.g. 2024-06-15T20:00:00>",
  "location": "<venue or address, or empty string if not mentioned>"
}}

Rules:
- If no time is specified for a social event (pub, drinks, dinner, party, match, gig, birthday), default start to 19:00.
- If no time is specified for a daytime event (dentist, meeting, gym, appointment), default start to 09:00.
- If no end time given, set end = start + 1 hour.
- If no year given, assume the next future occurrence of that date.
- "tomorrow" means {tomorrow}.
- "next [weekday]" means the coming occurrence of that day.
- Never guess a location — leave it empty if not explicitly mentioned.
- If the date is not stated in the current message, look for it in the conversation history above.
"""

# ── Public entry point ────────────────────────────────────────────────────────


async def handle(conn: sqlite3.Connection, text: str, user_id: int = 0) -> str:
    """Classify the message and dispatch to query or create flow.

    Checks for a pending event_create confirmation first — same pattern as the
    food log flow in the meal agent.
    """
    hist = memory_svc.get(user_id)

    pending = state_svc.get(user_id)
    if pending and pending.get("type") == "event_create":
        return await _confirm_create(text, user_id, pending, hist)

    raw = await complete([{"role": "user", "content": text}], system=_ROUTER_SYSTEM, history=hist)
    try:
        intent = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, ValueError):
        return "What do you need — checking your schedule or adding an event?"

    action = intent.get("action")
    if action == "query":
        return await _query(text, hist)
    if action == "create":
        return await _stage_create(text, user_id, hist)
    if action == "clarify":
        return intent.get("question", "What do you need — checking your schedule or adding an event?")
    return "What do you need — checking your schedule or adding an event?"


# ── Private helpers ───────────────────────────────────────────────────────────


async def _query(text: str, hist: list[dict] | None = None) -> str:
    """Extract a time range from the message, fetch events, format conversationally."""
    now = _london_now()
    system = _QUERY_SYSTEM.format(now=now.isoformat())

    raw = await complete([{"role": "user", "content": text}], system=system, history=hist)
    try:
        parsed = json.loads(_extract_json(raw))
        time_min = parsed["time_min"]
        time_max = parsed["time_max"]
    except (json.JSONDecodeError, KeyError, ValueError):
        # Fall back to the rest of today
        time_min = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        time_max = now.replace(hour=23, minute=59, second=59, microsecond=0).isoformat()

    try:
        service = get_service()
        events = list_events(service, time_min, time_max)
    except FileNotFoundError as exc:
        return str(exc)
    except Exception as exc:
        return f"Couldn't reach Google Calendar — try again in a moment. ({type(exc).__name__})"

    if not events:
        return "Nothing in the calendar for that window."

    lines = [f"{len(events)} event{'s' if len(events) != 1 else ''}:"]
    for ev in events:
        time_str = _format_event_time(ev["start"])
        loc_str = f" @ {ev['location']}" if ev["location"] else ""
        lines.append(f"  {time_str}: {ev['summary']}{loc_str}")
    return "\n".join(lines)


async def _stage_create(text: str, user_id: int, hist: list[dict] | None = None) -> str:
    """Parse event details via LLM, stage in state, return confirmation prompt."""
    now = _london_now()
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    system = _CREATE_SYSTEM.format(now=now.isoformat(), tomorrow=tomorrow)

    raw = await complete([{"role": "user", "content": text}], system=system, history=hist)
    try:
        parsed = json.loads(_extract_json(raw))
        summary = parsed["summary"]
        start = parsed["start"]
        end = parsed["end"]
        location = parsed.get("location", "")
    except (json.JSONDecodeError, KeyError, ValueError):
        return (
            "Couldn't parse the event details. "
            "Try: 'add dentist Friday 10am' or 'drinks at The Anchor Tuesday 7pm'"
        )

    state_svc.set_state(user_id, {
        "type": "event_create",
        "summary": summary,
        "start": start,
        "end": end,
        "location": location,
    })

    date_str = _format_event_time(start)
    loc_part = f", {location}" if location else ""
    return f"I'll add: {summary}, {date_str}{loc_part} — shall I?"


async def _confirm_create(text: str, user_id: int, pending: dict, hist: list[dict] | None = None) -> str:
    """Handle the yes/cancel/adjust response to a staged event confirmation."""
    text_lower = text.lower().strip()

    if any(w in text_lower for w in ("cancel", "forget", "never mind", "nevermind", "don't", "dont", "no thanks", "nope", "no")):
        state_svc.clear(user_id)
        return "No problem, nothing added."

    words = set(text_lower.split())
    is_yes = bool(words & _AFFIRMATIVES) and len(text.split()) <= 6

    if is_yes:
        try:
            service = get_service()
            create_event(
                service,
                summary=pending["summary"],
                start=pending["start"],
                end=pending["end"],
                location=pending.get("location", ""),
            )
        except FileNotFoundError as exc:
            state_svc.clear(user_id)
            return str(exc)
        except Exception as exc:
            state_svc.clear(user_id)
            return f"Couldn't create the event — try again. ({type(exc).__name__})"

        state_svc.clear(user_id)
        date_str = _format_event_time(pending["start"])
        return f"Done. {pending['summary']} added for {date_str}."

    # Treat anything else as an adjustment — re-parse as a new create request.
    state_svc.clear(user_id)
    return await _stage_create(text, user_id, hist)


def _london_now() -> datetime:
    return datetime.now(tz=_TZ)


def _format_event_time(dt_str: str) -> str:
    """Format an ISO 8601 datetime or date string for human-readable display."""
    if not dt_str:
        return "unknown time"
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_TZ)
        else:
            dt = dt.astimezone(_TZ)
        # "Tue 17 Jun, 7:00 pm"
        return dt.strftime("%#d %b (%a), %#I:%M %p").replace("AM", "am").replace("PM", "pm")
    except ValueError:
        pass
    try:
        d = datetime.strptime(dt_str[:10], "%Y-%m-%d")
        return d.strftime("%#d %b (%a)")
    except ValueError:
        return dt_str


def _extract_json(text: str) -> str:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group() if match else text
