"""Running tools — §2.8 of TOOL_CALLING_DESIGN.md.

Each tool is `async def tool_name(conn, **kwargs) -> dict`, JSON-serialisable,
and returns {"error": "..."} on failure instead of raising.

Structured run data (distance/duration/pace) lives in run_logs, separate
from exercise_sets — sets/reps there are INTEGER and don't fit a run.
Run-strength/prehab work (jump squats, Nordic curls, etc.) is logged
through the existing log_exercises/exercise_sets path instead, same as any
other gym exercise.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta

import config
from storage.models import (
    GymSession,
    RunLog,
    get_recent_runs,
    insert_run,
    insert_session,
)

_TZ = config.TZ

logger = logging.getLogger(__name__)


def _format_pace(sec_per_km: float | None) -> str | None:
    if sec_per_km is None:
        return None
    minutes, seconds = divmod(round(sec_per_km), 60)
    return f"{minutes}:{seconds:02d}/km"


async def log_run(
    conn: sqlite3.Connection,
    run_type: str,
    distance_km: float | None = None,
    duration_min: float | None = None,
    perceived_effort: int | None = None,
    notes: str = "",
) -> dict:
    """Log a completed run.

    Also finds-or-creates today's gym_sessions row with session_type="run" —
    the same today-lookup pattern log_exercises uses — so a logged run
    naturally fills the PPL leg-slot rotation the same way a logged legs
    session does.
    """
    try:
        today = datetime.now(tz=_TZ).date().isoformat()

        avg_pace = None
        if distance_km and duration_min:
            avg_pace = (duration_min * 60) / distance_km

        run_id = insert_run(conn, RunLog(
            date=today,
            run_type=run_type,
            distance_km=distance_km,
            duration_min=duration_min,
            avg_pace_sec_per_km=avg_pace,
            perceived_effort=perceived_effort,
            notes=notes or "",
        ))

        row = conn.execute(
            "SELECT * FROM gym_sessions WHERE date = ? ORDER BY id DESC LIMIT 1",
            (today,),
        ).fetchone()
        if row:
            session_id = row["id"]
        else:
            session_id = insert_session(conn, GymSession(date=today, session_type="run"))

        return {
            "logged": True,
            "run_id": run_id,
            "session_id": session_id,
            "date": today,
            "run_type": run_type,
            "distance_km": distance_km,
            "duration_min": duration_min,
            "avg_pace_per_km": _format_pace(avg_pace),
            "perceived_effort": perceived_effort,
            "notes": notes or None,
        }
    except Exception as exc:
        logger.warning("log_run failed: %s", exc)
        return {"error": str(exc)}


async def get_recent_runs_tool(conn: sqlite3.Connection, run_type: str | None = None, limit: int = 10) -> dict:
    """Return the most recent logged runs, newest first, optionally filtered by run_type."""
    try:
        rows = get_recent_runs(conn, run_type=run_type, limit=limit)
        return {
            "runs": [
                {
                    "date": r["date"],
                    "run_type": r["run_type"],
                    "distance_km": r["distance_km"],
                    "duration_min": r["duration_min"],
                    "avg_pace_per_km": _format_pace(r["avg_pace_sec_per_km"]),
                    "perceived_effort": r["perceived_effort"],
                    "notes": r["notes"] or None,
                }
                for r in rows
            ],
        }
    except Exception as exc:
        logger.warning("get_recent_runs failed: %s", exc)
        return {"error": str(exc)}


async def get_weekly_running_summary(conn: sqlite3.Connection) -> dict:
    """Return this week's (Monday-based) logged runs — total distance and a count per type."""
    try:
        today = datetime.now(tz=_TZ).date()
        week_start = (today - timedelta(days=today.weekday())).isoformat()
        runs = get_recent_runs(conn, limit=50)
        this_week = [r for r in runs if r.get("date", "") >= week_start]

        by_type: dict[str, int] = {}
        for r in this_week:
            by_type[r["run_type"]] = by_type.get(r["run_type"], 0) + 1

        total_distance = sum(r["distance_km"] for r in this_week if r["distance_km"])

        return {
            "week_start": week_start,
            "runs": [
                {
                    "date": r["date"],
                    "run_type": r["run_type"],
                    "distance_km": r["distance_km"],
                    "avg_pace_per_km": _format_pace(r["avg_pace_sec_per_km"]),
                }
                for r in reversed(this_week)
            ],
            "run_count": len(this_week),
            "total_distance_km": round(total_distance, 1),
            "sessions_by_type": by_type,
        }
    except Exception as exc:
        logger.warning("get_weekly_running_summary failed: %s", exc)
        return {"error": str(exc)}


# ── Tool schemas (OpenAI function-calling format) ───────────────────────────

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "log_run",
            "description": (
                "Log a completed run — distance/duration/pace, not weights/reps. Use this "
                "for any actual run (easy/tempo/interval/long/race), not for run-strength/"
                "prehab work (jump squats, Nordic curls etc. — those go through "
                "log_exercises like any other gym exercise). Automatically attaches to "
                "today's gym session (or starts one), so a logged run fills the PPL "
                "leg-slot rotation the same way a logged legs session does."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "run_type": {
                        "type": "string",
                        "enum": ["easy", "tempo", "interval", "long", "race"],
                        "description": "The kind of run performed.",
                    },
                    "distance_km": {
                        "type": ["number", "null"],
                        "description": "Distance covered in km, if known.",
                    },
                    "duration_min": {
                        "type": ["number", "null"],
                        "description": "Duration in minutes, if known.",
                    },
                    "perceived_effort": {
                        "type": ["integer", "null"],
                        "description": "Optional RPE 1-10, only if Ollie mentioned how hard it felt.",
                    },
                    "notes": {
                        "type": ["string", "null"],
                        "description": "Any other free note — splits, weather, how it felt, etc.",
                    },
                },
                "required": ["run_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_runs",
            "description": (
                "Get recent logged runs, newest first, optionally filtered by run_type. Use "
                "this for 'how's my pace trending' or 'what was my last long run'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "run_type": {
                        "type": ["string", "null"],
                        "enum": ["easy", "tempo", "interval", "long", "race", None],
                        "description": "Only return runs of this type. Omit for all types.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of past runs to return. Defaults to 10.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weekly_running_summary",
            "description": (
                "Get a summary of this week's (Monday-based) logged runs — total distance, "
                "count per run type. Use this for 'how much have I run this week'."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]
