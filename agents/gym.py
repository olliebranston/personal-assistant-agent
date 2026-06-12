"""Gym routine agent: workout suggestions, session logging, progressive overload queries."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, timedelta

import services.state as state_svc
from services.openrouter import complete
from storage.models import (
    ExerciseSet,
    GymSession,
    get_last_sets_for_exercise,
    get_recent_sessions,
    insert_session,
    insert_set,
)

_PPL_CYCLE = ["push", "pull", "legs"]

# Key exercises to pull history for per session type.
_KEY_EXERCISES: dict[str, list[str]] = {
    "push": ["bench press", "overhead press"],
    "pull": ["bent over bar rows", "pull-ups"],
    "legs": ["squats", "romanian deadlifts"],
}

# Slimmed-down plans: 1 main compound, 2–3 accessories, pick-1 isolation, 2 alternatives.
_SESSION_PLANS: dict[str, str] = {
    "push": (
        "TODAY — PUSH (Chest, Shoulders, Triceps)\n"
        "• bench press 5×8\n"
        "• OHP 4×8\n"
        "• rope pulldowns 4×10\n"
        "• DB lateral raises 4×15\n"
        "\n"
        "PICK 1 ISOLATION\n"
        "• pec fly 4×8  ·  cable fly 3×10  ·  incline DB bench 4×8\n"
        "\n"
        "IF TIME\n"
        "• dips 4×10  ·  skullcrushers 4×8  ·  ab finisher"
    ),
    "pull": (
        "TODAY — PULL (Back, Biceps, Rear Delts)\n"
        "• pull-ups 4×5–8\n"
        "• bent over bar rows 5×10\n"
        "• face pulls 4×10\n"
        "• bar curls 4×10\n"
        "\n"
        "PICK 1 ROW\n"
        "• machine rows 4×8  ·  cable rows 4×8  ·  T-bar rows 3×10\n"
        "\n"
        "IF TIME\n"
        "• incline DB curls 4×10  ·  cable delt fly 4×8"
    ),
    "legs": (
        "TODAY — LEGS (Quads, Hamstrings, Glutes, Calves)\n"
        "• Bulgarian split squats 4×10  ← do these first, they're brutal\n"
        "• Smith squats 5×8\n"
        "• Romanian deadlifts 4×10\n"
        "• hamstring curls 3×8\n"
        "\n"
        "PICK 1 ISOLATION\n"
        "• quad extensions 4×10  ·  calf raises 4×15  ·  hip extensions 4×10\n"
        "\n"
        "IF TIME\n"
        "• leg press 4×8  ·  goblet squats 4×10"
    ),
    "short": (
        "TODAY — SHORT SESSION (<30 mins)\n"
        "\n"
        "PICK ONE FOCUS\n"
        "• missed muscle — 5–6 exercises, one area, minimal rest\n"
        "• cardio — 20–25 min run (intervals or tempo)\n"
        "• full-body circuit — bench / rows / squats / press, 3×8, move fast\n"
        "• weak point — arms, rear delts, calves tend to get dropped"
    ),
}

# ── System prompts ────────────────────────────────────────────────────────────

_ROUTER_SYSTEM = """\
Classify the user's gym message into exactly one action. Reply ONLY with valid JSON — no prose.

{"action": "suggest"}                                                      — wants a workout suggestion or next session
{"action": "suggest", "override": "push|pull|legs|short"}                 — explicitly requests a specific session type
{"action": "log"}                                                          — logging a completed workout
{"action": "history", "exercise": "<name or empty string>"}               — wants exercise history / progressive overload data
{"action": "week"}                                                         — wants a summary of this week's gym sessions
{"action": "clarify", "question": "<one short question>"}                 — intent unclear

Override examples:
  "give me pull day"       → {"action": "suggest", "override": "pull"}
  "switch to legs"         → {"action": "suggest", "override": "legs"}
  "I want to do push"      → {"action": "suggest", "override": "push"}
  "short session today"    → {"action": "suggest", "override": "short"}

Week examples:
  "how did I do this week" → {"action": "week"}
  "how many sessions"      → {"action": "week"}
  "weekly gym summary"     → {"action": "week"}
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

_AFFIRMATIVES = frozenset({
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "go", "done",
    "ready", "absolutely", "let's go", "lets go",
})

# ── Public entry point ────────────────────────────────────────────────────────


async def handle(conn: sqlite3.Connection, text: str, user_id: int = 0) -> str:
    """Classify the user's gym message and dispatch to the appropriate function."""
    pending = state_svc.get(user_id)
    if pending and pending.get("type") == "session_offered":
        state_svc.clear(user_id)
        words = set(text.lower().split())
        if words & _AFFIRMATIVES and len(text.split()) <= 5:
            return (
                "Nice. Send the lifts — I'll log them.\n"
                "e.g. bench 80kg 5×5, OHP 52.5kg 4×8, dips BW 4×10"
            )

    raw = await complete([{"role": "user", "content": text}], system=_ROUTER_SYSTEM)

    try:
        intent = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, ValueError):
        return "Session plan, log your lifts, or check history — what do you need?"

    action = intent.get("action")

    if action == "suggest":
        override = intent.get("override", "").strip().lower() or None
        return await _suggest_next_session(conn, user_id, override_type=override)
    if action == "log":
        return await _log_workout(conn, text)
    if action == "history":
        exercise = intent.get("exercise", "").strip()
        return await _query_history(conn, exercise)
    if action == "week":
        return _week_summary(conn)
    if action == "clarify":
        return intent.get("question", "Session plan, log your lifts, or check history?")
    return "Session plan, log your lifts, or check history?"


# ── Private helpers ───────────────────────────────────────────────────────────


def get_next_session_type(conn: sqlite3.Connection) -> str:
    """Return the next session type in the PPL cycle based on recent history."""
    for session in get_recent_sessions(conn, limit=10):
        if session["session_type"] in _PPL_CYCLE:
            last_idx = _PPL_CYCLE.index(session["session_type"])
            return _PPL_CYCLE[(last_idx + 1) % len(_PPL_CYCLE)]
    return "push"


def _get_last_session_of_type(conn: sqlite3.Connection, session_type: str) -> dict | None:
    """Return the most recent logged session matching session_type, or None."""
    for session in get_recent_sessions(conn, limit=20):
        if session.get("session_type") == session_type:
            return session
    return None


def _format_last_session(session: dict) -> str:
    """Format a past session as a bulleted block with an all-caps heading."""
    try:
        days_ago = (date.today() - date.fromisoformat(session["date"])).days
        age = f"{days_ago} day{'s' if days_ago != 1 else ''} ago" if days_ago > 0 else "today"
    except (ValueError, KeyError):
        age = session.get("date", "?")

    lines = [f"LAST {session['session_type'].upper()} · {age}"]
    for ex in session.get("sets", []):
        weight = f"{ex['weight_kg']}kg" if ex.get("weight_kg") is not None else "BW"
        warmup = f" (warm-up {ex['warmup_kg']}kg)" if ex.get("warmup_kg") else ""
        lines.append(f"• {ex['exercise']} — {weight}{warmup} {ex['sets']}×{ex['reps']}")

    if len(lines) == 1:
        lines.append("• no exercises recorded")

    return "\n".join(lines)


def _get_progression_hints(conn: sqlite3.Connection, session_type: str) -> list[str]:
    """Pull last logged weight for key exercises and suggest +2.5kg or +1 rep."""
    hints = []
    for ex in _KEY_EXERCISES.get(session_type, []):
        rows = get_last_sets_for_exercise(conn, ex, limit=1)
        if not rows:
            continue
        r = rows[0]
        weight = r["weight_kg"]
        notes = (r.get("notes") or "").lower()
        failed = any(w in notes for w in ("fail", "missed", "short", "couldn't", "only"))

        if weight is None:
            next_reps = r["reps"] if failed else r["reps"] + 1
            hints.append(
                f"  {ex.title()}: BW {r['sets']}×{r['reps']} last time ({r['date']}) → aim {r['sets']}×{next_reps} today"
            )
        else:
            next_weight = weight if failed else round((weight + 2.5) * 2) / 2
            suffix = " (same weight — didn't nail it last time)" if failed else ""
            hints.append(
                f"  {ex.title()}: {weight}kg last time ({r['date']}) → try {next_weight}kg{suffix}"
            )
    return hints


async def _suggest_next_session(
    conn: sqlite3.Connection,
    user_id: int = 0,
    override_type: str | None = None,
) -> str:
    """Return the exercise plan for the next PPL session with last-session recap + progression hints."""
    session_type = override_type if override_type in (*_PPL_CYCLE, "short") else get_next_session_type(conn)

    parts: list[str] = []

    # Show the full last logged session of this type at the top
    last = _get_last_session_of_type(conn, session_type)
    if last:
        parts.append(_format_last_session(last))
        parts.append("")

    # Progression hints slot in before the plan
    hints = _get_progression_hints(conn, session_type)
    if hints:
        parts.append("TARGETS")
        parts.extend(hints)
        parts.append("")

    parts.append(_SESSION_PLANS[session_type])
    parts.append("\nSend the lifts when you're done.")

    state_svc.set_state(user_id, {"type": "session_offered", "session_type": session_type})

    return "\n".join(parts)


async def _log_workout(conn: sqlite3.Connection, text: str) -> str:
    """Parse free-text workout log via LLM, save to DB, return confirmation."""
    raw = await complete([{"role": "user", "content": text}], system=_LOG_PARSER_SYSTEM)

    try:
        parsed = json.loads(_extract_json(raw))
    except (json.JSONDecodeError, ValueError):
        return "Couldn't parse that — try: bench 80kg 5×5, incline DB 30kg 4×8, dips 4×10"

    exercises = parsed.get("exercises", [])
    if not exercises:
        return "No exercises found. Format: bench 80kg 5×5, OHP 50kg 4×8"

    session_type = parsed.get("session_type", "short")
    today = date.today().isoformat()
    session_id = insert_session(conn, GymSession(date=today, session_type=session_type))

    lines = [f"{session_type.title()} session logged — {today}:\n"]
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
        warmup_str = f" (warmup {warmup}kg)" if warmup is not None else ""
        note_str = f"  {notes}" if notes else ""
        lines.append(
            f"  {ex.get('exercise', 'unknown').title()}  {weight_str}{warmup_str}  {sets}×{reps}{note_str}"
        )

    lines.append("\nLogged to your session history.")
    return "\n".join(lines)


async def _query_history(conn: sqlite3.Connection, exercise: str) -> str:
    """Return the last 5 logged sets for an exercise."""
    if not exercise:
        return "Which exercise? e.g. 'bench history' or 'squat last'"

    rows = get_last_sets_for_exercise(conn, exercise, limit=5)
    if not rows:
        return f"Nothing logged for '{exercise}' yet."

    lines = [f"{exercise.title()} — last {len(rows)} session(s):"]
    for r in rows:
        weight_str = f"{r['weight_kg']}kg" if r["weight_kg"] is not None else "BW"
        warmup_str = f" (warmup {r['warmup_kg']}kg)" if r.get("warmup_kg") else ""
        note_str = f"  {r['notes']}" if r.get("notes") else ""
        lines.append(
            f"  {r['date']}  {weight_str}{warmup_str}  {r['sets']}×{r['reps']}{note_str}"
        )

    return "\n".join(lines)


def _week_summary(conn: sqlite3.Connection) -> str:
    """Return this week's gym sessions — dates, types, and key lifts."""
    today = date.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()

    sessions = get_recent_sessions(conn, limit=20)
    this_week = [s for s in sessions if s.get("date", "") >= week_start]

    if not this_week:
        return f"Nothing logged this week yet (started {week_start})."

    lines = [f"THIS WEEK · {len(this_week)} session{'s' if len(this_week) != 1 else ''}"]

    for session in reversed(this_week):  # chronological order
        try:
            day_name = date.fromisoformat(session["date"]).strftime("%A")
        except ValueError:
            day_name = session["date"]
        lines.append(f"\n{day_name.upper()} — {session['session_type'].upper()}")
        for ex in session.get("sets", []):
            weight = f"{ex['weight_kg']}kg" if ex.get("weight_kg") is not None else "BW"
            lines.append(f"• {ex['exercise']} — {weight} {ex['sets']}×{ex['reps']}")

    next_type = get_next_session_type(conn)
    lines.append(f"\nNext up: {next_type.title()} day.")
    return "\n".join(lines)


def _extract_json(text: str) -> str:
    """Extract the first {...} block from an LLM response."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group() if match else text
