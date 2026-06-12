"""Top-level intent router — classifies which domain a free-text message belongs to."""

from __future__ import annotations

import json
import re

from services import memory as memory_svc
from services.openrouter import complete

_SYSTEM = """\
Classify the user's message into one domain. Reply ONLY with valid JSON — no prose.

{"domain": "gym"}      — workouts, exercises, lifting, running, sessions, training, bench, squat, OHP, pull day, push day, legs
{"domain": "meal"}     — food, eating, logging meals, macros, protein, calories, nutrition, recipes, shopping,
                         corrections to food already logged, anything about what was eaten or how much protein
{"domain": "calendar"} — events, schedule, appointments, meetings, reminders, dates, times, bookings.
                         Also classify here if the message reads like a forwarded event invite or plan,
                         e.g. "everyone meeting at The Anchor Thursday 8pm" or "match day Saturday 3pm kick-off"
{"domain": "news"}     — news, football, Chelsea, horse racing, sports results, transfers
{"domain": "unknown"}  — anything that doesn't fit the above

Tricky cases — classify these correctly:
  "Log 15g more protein"            → meal  (adjusting nutrition data, not a gym log)
  "Actually make it 300g"           → meal  (food portion correction)
  "What did I eat yesterday"        → meal
  "How did I do this week"          → meal  (weekly macro summary)
  "Give me pull day"                → gym
  "Switch to legs today"            → gym
  "I want to do push"               → gym
  "Short session today"             → gym
  "How much protein left"           → meal
  "That should be 200g not 150g"    → meal  (food correction)
  "Log it" after a food discussion  → meal

If conversation history is shown above and the message is a short follow-up (under 5 words),
use the domain of the most recent exchange to disambiguate.
"""

# Per-user last-known domain — used as fallback when classifier returns "unknown"
_last_domain: dict[int, str] = {}


def get_last_domain(user_id: int) -> str | None:
    return _last_domain.get(user_id)


def set_last_domain(user_id: int, domain: str) -> None:
    if domain not in ("unknown",):
        _last_domain[user_id] = domain


async def classify(text: str, user_id: int = 0) -> str:
    """Return the domain name this message belongs to.

    Classifies purely on message content — no conversation history passed, as that
    biases the LLM toward the previous domain for unrelated messages.
    Falls back to the last known domain only for very short follow-ups (≤3 words)
    that are genuinely context-dependent (e.g. "yes", "cancel", "done").

    Returns one of: 'gym' | 'meal' | 'calendar' | 'news' | 'unknown'.
    """
    raw = await complete(
        [{"role": "user", "content": text}],
        system=_SYSTEM,
    )
    try:
        data = json.loads(_extract_json(raw))
        domain = data.get("domain", "unknown")
    except (json.JSONDecodeError, ValueError):
        domain = "unknown"

    # Only fall back to last domain for very short ambiguous messages
    if domain == "unknown" and len(text.split()) <= 3 and user_id:
        last = get_last_domain(user_id)
        if last:
            domain = last

    return domain


def _extract_json(text: str) -> str:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group() if match else text
