"""Gym tools — §2.1 of TOOL_CALLING_DESIGN.md.

Each tool is `async def tool_name(conn, **kwargs) -> dict`, JSON-serialisable,
and returns {"error": "..."} on failure instead of raising.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta

from storage.models import (
    ExerciseSet,
    GymSession,
    get_last_sets_for_exercise,
    get_recent_sessions,
    insert_session,
    insert_set,
)

logger = logging.getLogger(__name__)

_PPL_CYCLE = ["push", "pull", "legs"]

# Structured session plans — target_sets/target_reps/notes per exercise.
# Ported from agents/gym.py:_SESSION_PLANS (free text) into the structured
# shape required by get_session_plan's return type (§2.1).
_SESSION_PLANS: dict[str, list[dict]] = {
    "push": [
        {"exercise": "bench press", "target_sets": 5, "target_reps": "8", "notes": None},
        {"exercise": "overhead press", "target_sets": 4, "target_reps": "8", "notes": None},
        {"exercise": "rope pulldowns", "target_sets": 4, "target_reps": "10", "notes": None},
        {"exercise": "DB lateral raises", "target_sets": 4, "target_reps": "15", "notes": None},
        {"exercise": "pec fly", "target_sets": 4, "target_reps": "8", "notes": "pick 1 isolation — alternatives: cable fly 3x10, incline DB bench 4x8"},
        {"exercise": "dips", "target_sets": 4, "target_reps": "10", "notes": "if time — alternatives: skullcrushers 4x8, ab finisher"},
    ],
    "pull": [
        {"exercise": "pull-ups", "target_sets": 4, "target_reps": "5-8", "notes": None},
        {"exercise": "bent over bar rows", "target_sets": 5, "target_reps": "10", "notes": None},
        {"exercise": "face pulls", "target_sets": 4, "target_reps": "10", "notes": None},
        {"exercise": "bar curls", "target_sets": 4, "target_reps": "10", "notes": None},
        {"exercise": "machine rows", "target_sets": 4, "target_reps": "8", "notes": "pick 1 row — alternatives: cable rows 4x8, T-bar rows 3x10"},
        {"exercise": "incline DB curls", "target_sets": 4, "target_reps": "10", "notes": "if time — alternative: cable delt fly 4x8"},
    ],
    "legs": [
        {"exercise": "Bulgarian split squats", "target_sets": 4, "target_reps": "10", "notes": "do these first — brutal"},
        {"exercise": "Smith squats", "target_sets": 5, "target_reps": "8", "notes": None},
        {"exercise": "Romanian deadlifts", "target_sets": 4, "target_reps": "10", "notes": None},
        {"exercise": "hamstring curls", "target_sets": 3, "target_reps": "8", "notes": None},
        {"exercise": "quad extensions", "target_sets": 4, "target_reps": "10", "notes": "pick 1 isolation — alternatives: calf raises 4x15, hip extensions 4x10"},
        {"exercise": "leg press", "target_sets": 4, "target_reps": "8", "notes": "if time — alternative: goblet squats 4x10"},
    ],
    "short": [
        {"exercise": "missed muscle", "target_sets": 0, "target_reps": "5-6 exercises", "notes": "one area, minimal rest"},
        {"exercise": "cardio", "target_sets": 0, "target_reps": "20-25 min", "notes": "intervals or tempo run"},
        {"exercise": "full-body circuit", "target_sets": 3, "target_reps": "8", "notes": "bench / rows / squats / press, move fast"},
        {"exercise": "weak point", "target_sets": 0, "target_reps": "varies", "notes": "arms, rear delts, calves tend to get dropped"},
    ],
    "run": [
        {"exercise": "easy run", "target_sets": 1, "target_reps": "20-30 min", "notes": "5:30-6:00/km, conversational pace — aerobic base"},
        {"exercise": "tempo run", "target_sets": 1, "target_reps": "15 min @ ~4:15/km", "notes": "5 min warmup jog + 5 min cooldown, comfortably hard"},
        {"exercise": "interval run", "target_sets": 8, "target_reps": "400m @ ~4:00/km, 90s rest", "notes": "5k-specific — directly improves 5k time"},
    ],
}


def _next_session_type(conn: sqlite3.Connection) -> str:
    """Return the next session type in the PPL cycle based on recent history.

    Ported from agents/gym.py:get_next_session_type.
    """
    for session in get_recent_sessions(conn, limit=10):
        if session["session_type"] in _PPL_CYCLE:
            last_idx = _PPL_CYCLE.index(session["session_type"])
            return _PPL_CYCLE[(last_idx + 1) % len(_PPL_CYCLE)]
    return "push"


async def log_exercise(
    conn: sqlite3.Connection,
    exercise_name: str,
    sets: int,
    reps: int,
    weight_kg: float | None = None,
    notes: str | None = None,
    session_type: str | None = None,
) -> dict:
    """Log one exercise, appending to today's session or creating a new one (§3.2d)."""
    try:
        today = date.today().isoformat()
        recent = get_recent_sessions(conn, limit=1)

        if recent and recent[0]["date"] == today:
            session_id = recent[0]["id"]
            resolved_type = recent[0]["session_type"]
        else:
            resolved_type = session_type or _next_session_type(conn)
            session_id = insert_session(conn, GymSession(date=today, session_type=resolved_type))

        insert_set(conn, ExerciseSet(
            session_id=session_id,
            exercise=exercise_name,
            weight_kg=weight_kg,
            sets=sets,
            reps=reps,
            notes=notes or "",
        ))

        return {
            "logged": True,
            "session_id": session_id,
            "session_type": resolved_type,
            "exercise": exercise_name,
            "sets": sets,
            "reps": reps,
            "weight_kg": weight_kg,
            "notes": notes,
        }
    except Exception as exc:
        logger.warning("log_exercise failed: %s", exc)
        return {"error": str(exc)}


async def get_last_session(conn: sqlite3.Connection, session_type: str) -> dict:
    """Return the most recent logged session of the given type, with all its exercises."""
    try:
        for session in get_recent_sessions(conn, limit=20):
            if session.get("session_type") == session_type:
                exercises = [
                    {
                        "exercise": s["exercise"],
                        "sets": s["sets"],
                        "reps": s["reps"],
                        "weight_kg": s["weight_kg"],
                        "notes": s["notes"] or None,
                    }
                    for s in session.get("sets", [])
                ]
                return {
                    "found": True,
                    "date": session["date"],
                    "session_type": session_type,
                    "exercises": exercises,
                }
        return {"found": False, "date": None, "session_type": session_type, "exercises": []}
    except Exception as exc:
        logger.warning("get_last_session failed: %s", exc)
        return {"error": str(exc)}


async def get_exercise_history(conn: sqlite3.Connection, exercise_name: str, limit: int = 5) -> dict:
    """Return the most recent logged sets for one exercise, newest first."""
    try:
        rows = get_last_sets_for_exercise(conn, exercise_name, limit=limit)
        return {
            "exercise": exercise_name,
            "entries": [
                {
                    "date": r["date"],
                    "sets": r["sets"],
                    "reps": r["reps"],
                    "weight_kg": r["weight_kg"],
                    "notes": r["notes"] or None,
                }
                for r in rows
            ],
        }
    except Exception as exc:
        logger.warning("get_exercise_history failed: %s", exc)
        return {"error": str(exc)}


async def get_next_session_type(conn: sqlite3.Connection) -> dict:
    """Return the next session type due in the push/pull/legs rotation."""
    try:
        session_type = _next_session_type(conn)
        idx = _PPL_CYCLE.index(session_type)
        return {"session_type": session_type, "cycle_position": f"{idx + 1}/{len(_PPL_CYCLE)}"}
    except Exception as exc:
        logger.warning("get_next_session_type failed: %s", exc)
        return {"error": str(exc)}


async def get_session_plan(conn: sqlite3.Connection, session_type: str) -> dict:
    """Return the static target exercise plan for a session type."""
    try:
        plan = _SESSION_PLANS.get(session_type)
        if plan is None:
            return {"error": f"unknown session_type: {session_type}"}
        return {"session_type": session_type, "exercises": plan}
    except Exception as exc:
        logger.warning("get_session_plan failed: %s", exc)
        return {"error": str(exc)}


async def get_weekly_gym_summary(conn: sqlite3.Connection) -> dict:
    """Return this week's (Monday-based) logged sessions with exercise counts."""
    try:
        today = date.today()
        week_start = (today - timedelta(days=today.weekday())).isoformat()
        sessions = get_recent_sessions(conn, limit=20)
        this_week = [s for s in sessions if s.get("date", "") >= week_start]
        return {
            "week_start": week_start,
            "sessions": [
                {
                    "date": s["date"],
                    "session_type": s["session_type"],
                    "exercise_count": len(s.get("sets", [])),
                }
                for s in reversed(this_week)
            ],
            "session_count": len(this_week),
        }
    except Exception as exc:
        logger.warning("get_weekly_gym_summary failed: %s", exc)
        return {"error": str(exc)}


# ── Tool schemas (OpenAI function-calling format) ───────────────────────────

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "log_exercise",
            "description": (
                "Log one completed exercise to today's gym session. Call this once per "
                "exercise mentioned in the user's message — e.g. for 'bench 80kg 5x5, OHP "
                "52.5kg 4x8' call this twice, once per movement. Automatically appends to "
                "today's open session if one exists (see open_session_today in the ambient "
                "context), or starts a new session."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "exercise_name": {
                        "type": "string",
                        "description": "Name of the exercise, e.g. 'bench press', 'rope pulldowns', '5k run'.",
                    },
                    "sets": {
                        "type": "integer",
                        "description": "Number of sets performed.",
                    },
                    "reps": {
                        "type": "integer",
                        "description": "Reps per set (or total reps for single-set entries like a run).",
                    },
                    "weight_kg": {
                        "type": ["number", "null"],
                        "description": "Working weight in kg. Use null for bodyweight exercises or runs.",
                    },
                    "notes": {
                        "type": ["string", "null"],
                        "description": "Form cues, 'failed last rep', pace/time for runs, or any other free note.",
                    },
                    "session_type": {
                        "type": ["string", "null"],
                        "enum": ["push", "pull", "legs", "short", "run", None],
                        "description": (
                            "Only set this when starting a brand-new session today and the type "
                            "isn't already clear from ambient context. Infer from the exercise: "
                            "bench/OHP/dips/flyes -> push; rows/pull-ups/curls/face pulls -> pull; "
                            "squats/RDLs/lunges/leg press -> legs."
                        ),
                    },
                },
                "required": ["exercise_name", "sets", "reps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_last_session",
            "description": (
                "Get every exercise logged in the most recent session of a given type "
                "(push/pull/legs/short/run), with weights, sets, reps and notes. Use this "
                "to answer 'what did I do last push day' or to work out progression targets "
                "(+2.5kg or +1 rep from last time, unless the notes show the lift was "
                "failed/missed, in which case hold)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_type": {
                        "type": "string",
                        "enum": ["push", "pull", "legs", "short", "run"],
                        "description": "Which session type to look up.",
                    },
                },
                "required": ["session_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_exercise_history",
            "description": (
                "Get the recent logged history for one specific exercise across sessions, "
                "newest first. Use this for progressive-overload questions about a single "
                "movement, e.g. 'how's my bench progressing'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "exercise_name": {
                        "type": "string",
                        "description": "Exercise name to look up, e.g. 'bench press'.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of past entries to return. Defaults to 5.",
                    },
                },
                "required": ["exercise_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_next_session_type",
            "description": (
                "Get the next session type due in the push/pull/legs rotation, based on the "
                "most recently logged PPL session. Use this when the user asks what session "
                "they're due, or wants a suggestion without specifying push/pull/legs."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_session_plan",
            "description": (
                "Get the target exercise plan (sets/reps per exercise) for a session type — "
                "push, pull, legs, short, or run. Use this to tell the user what's on today, "
                "typically after determining the type via get_next_session_type or from what "
                "the user asked for."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_type": {
                        "type": "string",
                        "enum": ["push", "pull", "legs", "short", "run"],
                        "description": "Which session plan to return.",
                    },
                },
                "required": ["session_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weekly_gym_summary",
            "description": (
                "Get a summary of this week's (Monday-based) logged gym sessions — dates, "
                "session types, and exercise counts. Use this for 'how many sessions this "
                "week' or 'how did training go this week'."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]
