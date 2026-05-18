"""Top-level intent router — classifies which domain a free-text message belongs to."""

from __future__ import annotations

import json
import re

from services.openrouter import complete

_SYSTEM = """\
Classify the user's message into one domain. Reply ONLY with valid JSON — no prose.

{"domain": "gym"}      — workouts, exercises, lifting, running, sessions, training, bench, squat
{"domain": "meal"}     — food, eating, logging meals, macros, protein, calories, nutrition, recipes, shopping
{"domain": "calendar"} — events, schedule, appointments, meetings, reminders, dates, times, bookings.
                         Also classify here if the message reads like a forwarded event invite or plan,
                         e.g. "everyone meeting at The Anchor Thursday 8pm" or "match day Saturday 3pm kick-off"
{"domain": "news"}     — news, football, Chelsea, horse racing, sports results, transfers
{"domain": "unknown"}  — anything that doesn't fit the above
"""


async def classify(text: str) -> str:
    """Return the domain name this message belongs to.

    Returns one of: 'gym' | 'meal' | 'calendar' | 'news' | 'unknown'.
    Falls back to 'unknown' on any parsing failure — callers handle that case.
    """
    raw = await complete([{"role": "user", "content": text}], system=_SYSTEM)
    try:
        data = json.loads(_extract_json(raw))
        return data.get("domain", "unknown")
    except (json.JSONDecodeError, ValueError):
        return "unknown"


def _extract_json(text: str) -> str:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group() if match else text
