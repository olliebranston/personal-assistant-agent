"""Ambient structured context block (§3.3 of TOOL_CALLING_DESIGN.md).

Built fresh from SQLite on every incoming message — cheap, local-only, no
external API calls. Injected as a separate message alongside conversation
history so the model always has today's date, macro totals, and training
state without needing a tool call for the common cases.
"""

from __future__ import annotations

import datetime
import logging
import sqlite3
from zoneinfo import ZoneInfo

from agents.meal import CALORIE_TARGETS, PROTEIN_TARGET_G
from storage.models import get_daily_totals, get_latest_weight, get_recent_sessions

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("Europe/London")
_WEIGHTS_SESSION_TYPES = ("push", "pull", "legs")


def build_ambient_context(conn: sqlite3.Connection) -> dict:
    """Return the ambient context block described in §3.3.

    Every sub-section is wrapped so a single failing query can't take down
    the whole block — this runs on every message and must not raise.
    """
    now = datetime.datetime.now(tz=_TZ)
    today = now.date().isoformat()

    return {
        "today": today,
        "day_name": now.strftime("%A"),
        "current_time": now.strftime("%H:%M"),
        "daily_macros": _daily_macros(conn, today),
        "last_workout": _last_workout(conn),
        "open_session_today": _open_session_today(conn, today),
        "latest_weight_kg": _latest_weight(conn),
    }


def _daily_macros(conn: sqlite3.Connection, today: str) -> dict:
    try:
        totals = get_daily_totals(conn, today)
        kcal_target = CALORIE_TARGETS["weights"] if _is_weights_day(conn, today) else CALORIE_TARGETS["rest"]
        return {
            "protein_g": totals["protein_g"],
            "kcal": totals["kcal"],
            "protein_target_g": PROTEIN_TARGET_G,
            "kcal_target": kcal_target,
        }
    except Exception as exc:
        logger.debug("Ambient context: daily_macros failed: %s", exc)
        return {
            "protein_g": 0.0,
            "kcal": 0.0,
            "protein_target_g": PROTEIN_TARGET_G,
            "kcal_target": CALORIE_TARGETS["rest"],
        }


def _is_weights_day(conn: sqlite3.Connection, today: str) -> bool:
    for session in get_recent_sessions(conn, limit=5):
        if session["date"] == today and session["session_type"] in _WEIGHTS_SESSION_TYPES:
            return True
    return False


def _last_workout(conn: sqlite3.Connection) -> dict | None:
    try:
        sessions = get_recent_sessions(conn, limit=1)
        if not sessions:
            return None
        return {"date": sessions[0]["date"], "session_type": sessions[0]["session_type"]}
    except Exception as exc:
        logger.debug("Ambient context: last_workout failed: %s", exc)
        return None


def _open_session_today(conn: sqlite3.Connection, today: str) -> dict | None:
    try:
        for session in get_recent_sessions(conn, limit=5):
            if session["date"] == today:
                return {"session_type": session["session_type"], "session_id": session["id"]}
        return None
    except Exception as exc:
        logger.debug("Ambient context: open_session_today failed: %s", exc)
        return None


def _latest_weight(conn: sqlite3.Connection) -> float | None:
    try:
        latest = get_latest_weight(conn)
        return latest["weight_kg"] if latest else None
    except Exception as exc:
        logger.debug("Ambient context: latest_weight failed: %s", exc)
        return None
