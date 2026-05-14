"""Gym routine agent: workout suggestions, session logging, progressive overload queries."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date

from services.openrouter import complete
from storage.models import (
    ExerciseSet,
    GymSession,
    get_last_sets_for_exercise,
    get_recent_sessions,
    insert_session,
    insert_set,
)

# Push→pull→legs cycle. 'short' sessions are excluded — they don't advance the cycle.
_PPL_CYCLE = ["push", "pull", "legs"]

# Exercise plans per session type, sourced directly from Gym-CONTEXT.md.
_SESSION_PLANS: dict[str, str] = {
    "push": (
        "PUSH — Chest, Shoulders, Triceps\n"
        "\n"
        "Compounds:\n"
        "  Bench press        5×8   (~60–70kg working, target 100kg)\n"
        "  Incline DB bench   4×8\n"
        "  Chest press machine 4×8\n"
        "  Dips               4×10\n"
        "\n"
        "Shoulders:\n"
        "  OHP                4×8\n"
        "  DB lateral raises  4×15\n"
        "  Cable/machine laterals 3×10  (rotate with DB raises)\n"
        "\n"
        "Triceps:\n"
        "  Rope pulldowns     4×8–10\n"
        "  Skullcrushers      4×8\n"
        "  Tricep extension   3×10  (drop set candidate)\n"
        "\n"
        "Chest isolation — pick 1–2:\n"
        "  Pec fly machine 4×8 / Cable pec fly 3×10 / Close-grip DB bench 4×8\n"
        "\n"
        "Ab finisher: 2–3 of crunches, sit-ups, plank"
    ),
    "pull": (
        "PULL — Back, Biceps, Rear Delts\n"
        "\n"
        "Vertical pulls:\n"
        "  Pull-ups/chin-ups  4×5–8\n"
        "  Cable pulldowns    4×8–10\n"
        "\n"
        "Rows:\n"
        "  Bent over bar rows  5×10\n"
        "  Single DB rows      4×10  (pull to hip, not chest)\n"
        "  Machine rows        4×8   (shoulder-width grip)\n"
        "  Cable rows          4×8   (shoulder-width grip)\n"
        "  T-bar rows          3×8–10  (rotate in for variety)\n"
        "\n"
        "Rear delts (don't skip):\n"
        "  Face pulls         4×10\n"
        "  Cable delt fly     4×8\n"
        "  Machine delt fly   4×10  (rotate)\n"
        "\n"
        "Biceps:\n"
        "  Bar curls          4×10\n"
        "  Incline DB curls   4×10  (long head stretch)\n"
        "  EZ bar curls       4×8   (rotate with bar curls)\n"
        "  Machine/DB curls   3×8–10  (drop set candidate)\n"
        "\n"
        "Back accessories (rotate in, not every session):\n"
        "  Shrugs 3×10 / Upright rows 4×10 / DB pullover 3×10"
    ),
    "legs": (
        "LEGS — Quads, Hamstrings, Glutes, Calves\n"
        "\n"
        "Compounds (do Bulgarians early — they're brutal):\n"
        "  Smith squats           5×8   (~80–100kg working, target 150kg)\n"
        "  Bulgarian split squats 4×10\n"
        "  Leg press              4×8\n"
        "  Goblet squats          4×10  (rotate as higher-rep finisher)\n"
        "\n"
        "Hamstrings / posterior chain:\n"
        "  Romanian deadlifts     4×10  (hip hinge, bar close, soft knee)\n"
        "  Bent-over bell lifts   5×8   (brace hard)\n"
        "  Hamstring curls        3×8\n"
        "  Lunges                 3×10 each leg\n"
        "\n"
        "Isolation:\n"
        "  Quad extensions        4×8–10\n"
        "  Hip extensions/abductors 4×10\n"
        "  Calf raises            4×15  (slow eccentric)\n"
        "\n"
        "Time-tight? Core four: Squats + Bulgarians + RDLs + hamstring curls"
    ),
    "short": (
        "SHORT SESSION (<30 mins) — pick one focus:\n"
        "\n"
        "  Missed muscle group  — 5–6 exercises, one area, minimal rest\n"
        "  Cardio               — 20–25 min run (intervals or tempo; counts toward 5k goal)\n"
        "  Full-body circuit    — Bench / rows / squats / press, 3×8 each, move fast\n"
        "  Weak point           — arms, rear delts, calves tend to get dropped"
    ),
}

# ── System prompts ────────────────────────────────────────────────────────────

_ROUTER_SYSTEM = """\
Classify the user's gym message into exactly one action. Reply ONLY with valid JSON — no prose.

{"action": "suggest"}                                          — wants a workout suggestion or next session
{"action": "log"}                                              — logging a completed workout
{"action": "history", "exercise": "<name or empty string>"}   — wants exercise history / progressive overload data
{"action": "clarify", "question": "<one short question>"}     — intent unclear
"""

_LOG_PARSER_SYSTEM = """\
Parse a gym workout log into structured JSON. Reply ONLY with valid JSON — no prose.

{
  "session_type": "push|pull|legs|short",
  "exercises": [
    {
      "exercise": "<name>",
      "weight_kg": <number or null for bodyweight>,
      "warmup_kg": <number or null>,
      "sets": <integer>,
      "reps": <integer>,
      "notes": "<form notes, drop sets, missed reps — or empty string>"
    }
  ]
}

Rules:
- Infer session_type from exercises (bench/OHP/dips→push, rows/pull-ups/curls→pull, squats/RDLs→legs).
- Convert lbs to kg if specified (×0.4536), round to 1 decimal.
- "5x5" or "5×5" → sets=5, reps=5.
- If reps varied (8,8,7), use the first number as target reps.
- warmup_kg only if user noted a warm-up weight (e.g. "s40").
- Omit exercises you cannot parse.
"""

# ── Public entry point ────────────────────────────────────────────────────────


async def handle(conn: sqlite3.Connection, text: str, user_id: int = 0) -> str:
    """Classify the user's gym message and dispatch to the appropriate function."""
    raw = await complete([{"role": "user", "content": text}], system=_ROUTER_SYSTEM)

    try:
        intent = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, ValueError):
        return (
            "Didn't catch that — are you logging a workout, "
            "asking for a suggestion, or checking exercise history?"
        )

    action = intent.get("action")

    if action == "suggest":
        return await _suggest_next_session(conn)
    if action == "log":
        return await _log_workout(conn, text)
    if action == "history":
        exercise = intent.get("exercise", "").strip()
        return await _query_history(conn, exercise)
    if action == "clarify":
        return intent.get(
            "question",
            "Log a session, get a workout suggestion, or check history?",
        )
    return "Log a session, get a workout suggestion, or check history?"


# ── Private helpers ───────────────────────────────────────────────────────────


def _get_next_session_type(conn: sqlite3.Connection) -> str:
    """Return the next session type in the PPL cycle based on recent history.

    Walks back through recent sessions and finds the most recent push/pull/legs.
    Short sessions are skipped — they don't advance the cycle.
    Returns 'push' if there's no history yet.
    """
    for session in get_recent_sessions(conn, limit=10):
        if session["session_type"] in _PPL_CYCLE:
            last_idx = _PPL_CYCLE.index(session["session_type"])
            return _PPL_CYCLE[(last_idx + 1) % len(_PPL_CYCLE)]
    return "push"


async def _suggest_next_session(conn: sqlite3.Connection) -> str:
    """Return the formatted exercise plan for the next PPL session."""
    session_type = _get_next_session_type(conn)
    return f"Next up: {session_type.upper()}\n\n{_SESSION_PLANS[session_type]}"


async def _log_workout(conn: sqlite3.Connection, text: str) -> str:
    """Parse free-text workout log via LLM, save to DB, return a confirmation summary."""
    raw = await complete([{"role": "user", "content": text}], system=_LOG_PARSER_SYSTEM)

    try:
        parsed = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, ValueError):
        return (
            "Couldn't parse that. Try: 'bench 80kg 5×5, incline DB 30kg 4×8, dips 4×10'"
        )

    exercises = parsed.get("exercises", [])
    if not exercises:
        return "No exercises found. Try: 'bench 80kg 5×5, OHP 50kg 4×8'"

    session_type = parsed.get("session_type", "short")
    today = date.today().isoformat()
    session_id = insert_session(conn, GymSession(date=today, session_type=session_type))

    lines = [f"Logged — {session_type.upper()} ({today})\n"]
    for ex in exercises:
        weight = ex.get("weight_kg")
        warmup = ex.get("warmup_kg")
        sets = ex.get("sets", 0)
        reps = ex.get("reps", 0)
        notes = ex.get("notes", "")

        insert_set(conn, ExerciseSet(
            session_id=session_id,
            exercise=ex.get("exercise", "unknown"),
            weight_kg=weight,
            warmup_kg=warmup,
            sets=sets,
            reps=reps,
            notes=notes,
        ))

        weight_str = f"{weight}kg" if weight is not None else "BW"
        warmup_str = f" (s{warmup}kg)" if warmup is not None else ""
        note_str = f"  [{notes}]" if notes else ""
        lines.append(
            f"  {ex.get('exercise', 'unknown')}  {weight_str}{warmup_str}  {sets}×{reps}{note_str}"
        )

    return "\n".join(lines)


async def _query_history(conn: sqlite3.Connection, exercise: str) -> str:
    """Return the last 5 logged sets for an exercise, formatted for display."""
    if not exercise:
        return "Which exercise? e.g. 'bench history' or 'squat last'"

    rows = get_last_sets_for_exercise(conn, exercise, limit=5)
    if not rows:
        return f"No logged sets for '{exercise}' yet."

    lines = [f"{exercise.title()} — last {len(rows)} logged set(s):"]
    for r in rows:
        weight_str = f"{r['weight_kg']}kg" if r["weight_kg"] is not None else "BW"
        warmup_str = f" (s{r['warmup_kg']}kg)" if r.get("warmup_kg") else ""
        note_str = f"  [{r['notes']}]" if r.get("notes") else ""
        lines.append(
            f"  {r['date']}  {weight_str}{warmup_str}  {r['sets']}×{r['reps']}{note_str}"
        )

    return "\n".join(lines)


def _extract_json(text: str) -> str:
    """Extract the first {...} block from an LLM response.

    LLMs occasionally wrap JSON in prose despite explicit instructions. This
    makes downstream json.loads calls robust to that.
    """
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group() if match else text
