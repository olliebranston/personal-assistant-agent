"""Gym tools — §2.1 of TOOL_CALLING_DESIGN.md.

Each tool is `async def tool_name(conn, **kwargs) -> dict`, JSON-serialisable,
and returns {"error": "..."} on failure instead of raising.
"""

from __future__ import annotations

import difflib
import logging
import re
import sqlite3
from datetime import datetime, timedelta

import config
from storage.models import (
    ExerciseSet,
    GymSession,
    delete_exercise_set,
    get_distinct_exercise_names,
    get_last_sets_for_exercise,
    get_recent_sessions,
    get_set_by_id,
    get_sets_for_date,
    insert_session,
    insert_set,
    update_exercise_set,
)

_TZ = config.TZ

logger = logging.getLogger(__name__)

_PPL_CYCLE = ["push", "pull", "legs"]

# Progressive-overload cycle: sets x reps advance through these four steps in
# order, at a fixed weight, before the weight increases and the cycle resets.
# e.g. 3x10 @ 66kg -> next is 4x8 @ 66kg; completing 4x10 @ 66kg -> next is
# 3x8 @ 68.5kg.
_PROGRESSION_CYCLE = [(3, 8), (3, 10), (4, 8), (4, 10)]
_PROGRESSION_INCREMENT_KG = 2.5


def _cycle_rank(sets: int, reps: int) -> int:
    """Highest cycle step that (sets, reps) dominates (>= sets and >= reps), or -1."""
    best = -1
    for i, (s, r) in enumerate(_PROGRESSION_CYCLE):
        if sets >= s and reps >= r:
            best = i
    return best


# Common shorthand tokens expanded before matching, so a set logged as "OHP"
# or "incline db bench" matches history logged as "overhead press" or
# "incline dumbbell bench" — get_last_sets_for_exercise otherwise does an
# exact, case-insensitive match with no abbreviation awareness. Extend this
# as more shorthand turns out to need it.
_TOKEN_ALIASES: dict[str, str] = {
    "db": "dumbbell",
    "bb": "barbell",
    "ohp": "overhead press",
    "rdl": "romanian deadlift",
    "sldl": "stiff leg deadlift",
    "bor": "bent over row",
    "ez": "ez bar",
    "bw": "bodyweight",
}

# 0.8 was found to false-positive-match distinct exercises that happen to
# share most of their words (e.g. "incline dumbbell bench" vs "incline DB
# curls" scores 0.818) — 0.9 comfortably excludes that (max similarity across
# all real _SESSION_PLANS exercise pairs is 0.70) while still catching plain
# typos/pluralisation (e.g. "romanian deadlift" vs "romanian deadlifts" scores
# 0.971).
_FUZZY_MATCH_CUTOFF = 0.9

# Exercises Ollie has logged under more than one name — the first entry of
# each group is the canonical _SESSION_PLANS name. An explicit list rather
# than a looser fuzzy cutoff: "DB bench" vs "DB bench press" scores well below
# 0.9, but lowering the cutoff conflates genuinely different exercises (see
# above). Variants come from the real logged names on the VM (Oct 2026 audit).
# A group's members are never fuzzy-matched to anything outside the group.
_EXERCISE_ALIAS_GROUPS: list[tuple[str, ...]] = [
    ("DB bench press", "DB press", "DB bench", "dumbbell bench press", "flat DB press"),
    ("machine raises", "machine lateral raises"),
    ("overhead tricep extensions", "overhead rope extensions", "overhead extensions"),
    ("rope pulldowns", "rope pull downs"),
    ("bent over DB rows", "DB bent over rows", "bent over rows"),
    ("incline DB bicep curls", "incline DB curls"),
    ("rope pull-ups", "rope pull ups", "rope pullups"),
]


def normalize_exercise_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace, and expand known
    shorthand tokens — 'incline db bench' and 'incline dumbbell bench'
    normalize identically, as do 'OHP' and 'overhead press'."""
    cleaned = re.sub(r"[^a-z0-9\s]", " ", name.lower())
    expanded: list[str] = []
    for token in cleaned.split():
        expanded.extend(_TOKEN_ALIASES.get(token, token).split())
    return " ".join(expanded)


# normalized variant -> normalized canonical, built once from the groups above.
_ALIAS_INDEX: dict[str, str] = {
    normalize_exercise_name(variant): normalize_exercise_name(group[0])
    for group in _EXERCISE_ALIAS_GROUPS
    for variant in group
}


def _exercise_key(name: str) -> str:
    """The identity two logged names must share to count as the same
    exercise: the normalized name, folded onto its alias group if it has one."""
    normalized = normalize_exercise_name(name)
    return _ALIAS_INDEX.get(normalized, normalized)


def _known_exercise_vocabulary(conn: sqlite3.Connection) -> list[str]:
    """Every exercise name worth matching against: real logged history first
    (what a lookup actually needs to find), backstopped by the static
    _SESSION_PLANS names for exercises never yet logged."""
    plan_names = [ex["exercise"] for plan in _SESSION_PLANS.values() for ex in plan]
    seen: set[str] = set()
    vocabulary: list[str] = []
    for name in [*get_distinct_exercise_names(conn), *plan_names]:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            vocabulary.append(name)
    return vocabulary


def _resolve_canonical_exercise(conn: sqlite3.Connection, name: str) -> str | None:
    """Find the literal, already-known exercise name (as actually logged or
    planned) that name most likely refers to — via normalized-form exact
    match first, then a conservative fuzzy match. Returns None if nothing
    resolves confidently (caller still tries the raw name as a fallback)."""
    target = _exercise_key(name)
    normalized_map: dict[str, str] = {}
    for candidate in _known_exercise_vocabulary(conn):
        normalized_map.setdefault(_exercise_key(candidate), candidate)

    if target in normalized_map:
        return normalized_map[target]
    if target in _ALIAS_INDEX.values():
        return None  # an explicit alias group is matched exactly, never fuzzily

    close = difflib.get_close_matches(target, list(normalized_map.keys()), n=1, cutoff=_FUZZY_MATCH_CUTOFF)
    return normalized_map[close[0]] if close else None


def _exercise_name_candidates(conn: sqlite3.Connection, name: str) -> list[str]:
    """All names that should be treated as the same exercise for history
    lookups: the given name, its resolved canonical form (if any), and every
    logged name sharing an alias group with either — so a lookup for a
    shorthand/abbreviation finds sets logged under the full name and vice
    versa, and "DB bench press" finds sets logged as "DB bench"."""
    candidates = {name}
    canonical = _resolve_canonical_exercise(conn, name)
    if canonical:
        candidates.add(canonical)
    keys = {_exercise_key(c) for c in candidates}
    for logged in get_distinct_exercise_names(conn):
        if _exercise_key(logged) in keys:
            candidates.add(logged)
    return list(candidates)


def _get_history_across_aliases(conn: sqlite3.Connection, exercise_name: str, limit: int) -> list[dict]:
    """get_last_sets_for_exercise, but merged across all known aliases of exercise_name."""
    seen_ids: set[int] = set()
    merged: list[dict] = []
    for candidate in _exercise_name_candidates(conn, exercise_name):
        for row in get_last_sets_for_exercise(conn, candidate, limit=limit):
            if row["id"] not in seen_ids:
                seen_ids.add(row["id"])
                merged.append(row)
    merged.sort(key=lambda r: (r["date"], r["id"]), reverse=True)
    return merged[:limit]

# Structured session plans — target_sets/target_reps/notes per exercise.
# Ported from agents/gym.py:_SESSION_PLANS (free text) into the structured
# shape required by get_session_plan's return type (§2.1).
_SESSION_PLANS: dict[str, list[dict]] = {
    # Ollie's standard push/pull days from Oct 2026, in the order he does
    # them. 3x8 (the bottom of _PROGRESSION_CYCLE) is only the fallback for
    # an exercise with no history — get_session_plan uses logged history
    # wherever it exists. Swaps on the day are fine; this is the default.
    "push": [
        {"exercise": "dips", "target_sets": 4, "target_reps": "10", "notes": None},
        {"exercise": "DB bench press", "target_sets": 3, "target_reps": "8", "notes": None},
        {"exercise": "pec fly", "target_sets": 3, "target_reps": "8", "notes": None},
        {"exercise": "machine raises", "target_sets": 3, "target_reps": "8", "notes": None},
        {"exercise": "overhead tricep extensions", "target_sets": 3, "target_reps": "8", "notes": None},
        {"exercise": "rope pulldowns", "target_sets": 3, "target_reps": "8", "notes": None},
    ],
    "pull": [
        {"exercise": "pull-ups", "target_sets": 4, "target_reps": "5-8", "notes": None},
        {"exercise": "bent over DB rows", "target_sets": 3, "target_reps": "8", "notes": None},
        {"exercise": "delt fly", "target_sets": 3, "target_reps": "8", "notes": None},
        {"exercise": "face pulls", "target_sets": 3, "target_reps": "8", "notes": None},
        {"exercise": "incline DB bicep curls", "target_sets": 3, "target_reps": "8", "notes": None},
        {"exercise": "rope pull-ups", "target_sets": 3, "target_reps": "8", "notes": None},
    ],
    "legs": [
        {"exercise": "Bulgarian split squats", "target_sets": 4, "target_reps": "10", "notes": "do these first — brutal"},
        {"exercise": "Smith squats", "target_sets": 5, "target_reps": "8", "notes": None},
        {"exercise": "Romanian deadlifts", "target_sets": 4, "target_reps": "10", "notes": None},
        {"exercise": "leg press", "target_sets": 4, "target_reps": "8", "notes": "alternative: goblet squats 4x10"},
        {"exercise": "hamstring curls", "target_sets": 3, "target_reps": "8", "notes": None},
        {"exercise": "quad extensions", "target_sets": 4, "target_reps": "10", "notes": "pick 1 isolation — alternatives: calf raises 4x15, hip extensions 4x10"},
    ],
    "short": [
        {"exercise": "missed muscle", "target_sets": 0, "target_reps": "5-6 exercises", "notes": "one area, minimal rest"},
        {"exercise": "cardio", "target_sets": 0, "target_reps": "20-25 min", "notes": "intervals or tempo run"},
        {"exercise": "full-body circuit", "target_sets": 3, "target_reps": "8", "notes": "bench / rows / squats / press, move fast"},
        {"exercise": "weak point", "target_sets": 0, "target_reps": "varies", "notes": "arms, rear delts, calves tend to get dropped"},
    ],
    "run": [
        {"exercise": "easy run", "target_sets": 1, "target_reps": "30-45 min",
         "notes": "~5:50-6:15/km, conversational — aerobic base"},
        {"exercise": "long run", "target_sets": 1, "target_reps": "60-90 min",
         "notes": "~5:45-6:15/km, easy effort — Saturday slot, builds toward marathon base"},
        {"exercise": "tempo run", "target_sets": 1, "target_reps": "20 min @ ~5:10/km",
         "notes": "5 min warmup jog + 5 min cooldown, comfortably hard"},
        {"exercise": "interval run", "target_sets": 6, "target_reps": "800m @ ~4:35-4:40/km, 2 min rest",
         "notes": "VO2max work — Sunday slot, directly targets the sub-20 5k gap"},
        {"exercise": "run-strength", "target_sets": 3, "target_reps": "8-12",
         "notes": ("prehab/injury-proofing for the leg-slot substitution: jump squats "
                   "(start bodyweight, add load gradually), Nordic hamstring curls, "
                   "single-leg RDLs, calf raises, planks/Pallof press for lower back — "
                   "see Running-CONTEXT.md for progression order")},
    ],
}

# Half of what used to always be a leg day is now a run (cardio or a
# run-strength/prehab session) — the 3rd PPL slot alternates between the two
# instead of always resolving to "legs", per Ollie's injury-caution request
# to cut leg-day frequency (docs/context/Running-CONTEXT.md).
_LEG_SLOT_TYPES = ("legs", "run")


def _next_leg_slot_type(conn: sqlite3.Connection) -> str:
    """Alternate the rotation's 3rd slot between legs and run, based on
    whichever was logged most recently at that slot."""
    for session in get_recent_sessions(conn, limit=10):
        if session["session_type"] in _LEG_SLOT_TYPES:
            return "legs" if session["session_type"] == "run" else "run"
    return "run"  # no leg-slot history yet — lead with run/prehab, not a heavy leg day


def _next_session_type(conn: sqlite3.Connection) -> str:
    """Return the next session type in the PPL cycle based on recent history.

    Ported from agents/gym.py:get_next_session_type. The 3rd slot ("legs")
    is resolved through _next_leg_slot_type, which alternates it with "run".
    """
    for session in get_recent_sessions(conn, limit=10):
        st = session["session_type"]
        if st in _PPL_CYCLE or st == "run":
            slot = "legs" if st == "run" else st
            last_idx = _PPL_CYCLE.index(slot)
            next_slot = _PPL_CYCLE[(last_idx + 1) % len(_PPL_CYCLE)]
            return _next_leg_slot_type(conn) if next_slot == "legs" else next_slot
    return "push"


def _todays_session_row(conn: sqlite3.Connection, today: str, session_type: str | None) -> sqlite3.Row | None:
    """Which of today's sessions a log joins (Ollie's rule, Oct 2026): a run
    and a gym session on the same day stay separate, while different gym
    types on one day merge. session_type="run" joins today's run session;
    any gym type joins today's non-run session; None (type unknown) keeps
    the original behaviour of appending to today's latest session."""
    if session_type is None:
        clause, params = "", (today,)
    elif session_type == "run":
        clause, params = " AND session_type = 'run'", (today,)
    else:
        clause, params = " AND session_type != 'run'", (today,)
    return conn.execute(
        f"SELECT * FROM gym_sessions WHERE date = ?{clause} ORDER BY id DESC LIMIT 1",
        params,
    ).fetchone()


def _same_weight(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return round(float(a), 2) == round(float(b), 2)


def _set_view(row: dict) -> dict:
    """The model-facing shape of one logged exercise row — log_id included so
    a follow-up correct/delete can target it exactly."""
    return {
        "log_id": row["id"],
        "exercise": row["exercise"],
        "sets": row["sets"],
        "reps": row["reps"],
        "weight_kg": row["weight_kg"],
    }


async def log_exercises(
    conn: sqlite3.Connection,
    exercises: list[dict],
    session_type: str | None = None,
    allow_duplicates: bool = False,
) -> dict:
    """Log one or more exercises to today's session in a single atomic call
    (§3.2d), appending to it or creating a new one.

    Takes every exercise from the user's message as a list — even a single
    exercise is a 1-item list — so a multi-exercise message can't silently
    lose an item to the model choosing to call this fewer times than there
    were exercises (the previous one-exercise-per-call design's failure mode).

    An exercise already in today's session (same exercise after aliases) is
    not written again unless allow_duplicates is set: an identical item is
    reported in skipped_duplicates, one with different numbers is held in
    needs_confirmation for Ollie to say whether it's a correction or extra
    work. The model had been re-sending earlier messages' exercises — and
    logging corrections or the day's plan as new rows — doubling sessions up.
    The reply carries session_so_far, the whole session, so each answer can
    show everything logged today rather than just this message.
    """
    try:
        if not exercises:
            return {"error": "exercises list is empty"}

        # Validate every item before writing any of them — insert_set commits
        # per row, so without this an item failing partway through the list
        # would leave earlier items durably logged while the call as a whole
        # reports an error, breaking the "single atomic call" guarantee.
        for i, item in enumerate(exercises):
            missing = [k for k in ("exercise_name", "sets", "reps") if k not in item]
            if missing:
                return {"error": f"exercises[{i}] is missing required field(s): {', '.join(missing)}"}

        today = datetime.now(tz=_TZ).date().isoformat()

        row = _todays_session_row(conn, today, session_type)
        if row:
            session_id = row["id"]
            resolved_type = row["session_type"]
            existing = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM exercise_sets WHERE session_id = ?", (session_id,)
                ).fetchall()
            ]
        else:
            resolved_type = session_type or _next_session_type(conn)
            session_id = None  # created only once there's something to write
            existing = []

        logged = []
        skipped = []
        held = []
        for item in exercises:
            exercise_name = item["exercise_name"]
            sets = item["sets"]
            reps = item["reps"]
            weight_kg = item.get("weight_kg")
            warmup_kg = item.get("warmup_kg")
            notes = item.get("notes")

            same_exercise = [e for e in existing if _exercise_key(e["exercise"]) == _exercise_key(exercise_name)]
            if same_exercise and not allow_duplicates:
                as_given = {"exercise": exercise_name, "sets": sets, "reps": reps, "weight_kg": weight_kg}
                if any(
                    e["sets"] == sets and e["reps"] == reps and _same_weight(e["weight_kg"], weight_kg)
                    for e in same_exercise
                ):
                    skipped.append(as_given)
                else:
                    # Already logged today with different numbers: in the
                    # VM data this was a correction or a plan logged as done,
                    # never genuine extra work — so ask rather than write.
                    held.append({**as_given, "already_logged": _set_view(same_exercise[-1])})
                continue

            if session_id is None:
                session_id = insert_session(conn, GymSession(date=today, session_type=resolved_type))
            log_id = insert_set(conn, ExerciseSet(
                session_id=session_id,
                exercise=exercise_name,
                weight_kg=weight_kg,
                warmup_kg=warmup_kg,
                sets=sets,
                reps=reps,
                notes=notes or "",
            ))
            logged.append({
                "log_id": log_id,
                "exercise": exercise_name,
                "sets": sets,
                "reps": reps,
                "weight_kg": weight_kg,
                "warmup_kg": warmup_kg,
                "notes": notes,
            })

        session_so_far = [
            _set_view(dict(r)) for r in conn.execute(
                "SELECT * FROM exercise_sets WHERE session_id = ? ORDER BY id", (session_id,)
            ).fetchall()
        ] if session_id is not None else []

        result = {
            "logged": bool(logged),
            "session_id": session_id,
            "session_type": resolved_type,
            "exercises": logged,
            "skipped_duplicates": skipped,
            "needs_confirmation": held,
            "session_so_far": session_so_far,
        }
        if held:
            result["note"] = (
                "needs_confirmation items were NOT logged — each is already logged today with "
                "different numbers. Ask Ollie: a correction (correct_exercise_log with that "
                "log_id) or a genuine extra entry (log_exercises again with allow_duplicates)?"
            )
        elif skipped and not logged:
            result["note"] = "every exercise was already logged today — nothing new written"
        return result
    except Exception as exc:
        logger.warning("log_exercises failed: %s", exc)
        return {"error": str(exc)}


def _find_logged_set(
    conn: sqlite3.Connection,
    log_id: int | None,
    exercise_name: str,
    date: str | None,
) -> tuple[dict | None, dict | None]:
    """Resolve which logged row a correct/delete means: (row, None) when it's
    unambiguous, else (None, reply) where reply is an error or — when a name
    matches several rows — the candidates, so the model asks Ollie rather
    than guessing (same rule as delete_food_log)."""
    if log_id is not None:
        row = get_set_by_id(conn, int(log_id))
        if row is None:
            return None, {"error": f"no logged exercise with id {log_id}"}
        return row, None
    if not exercise_name:
        return None, {"error": "give a log_id or an exercise_name"}

    day = date or datetime.now(tz=_TZ).date().isoformat()
    names = {n.lower() for n in _exercise_name_candidates(conn, exercise_name)}
    key = _exercise_key(exercise_name)
    matches = [
        r for r in get_sets_for_date(conn, day)
        if r["exercise"].lower() in names or _exercise_key(r["exercise"]) == key
    ]
    if not matches:
        return None, {"error": f"no '{exercise_name}' logged on {day}"}
    if len(matches) > 1:
        return None, {
            "candidates": [_set_view(r) for r in matches],
            "message": "more than one entry matches — ask Ollie which one, then retry with its log_id",
        }
    return matches[0], None


_UNSET = object()  # distinguishes "not given" from an explicit null (bodyweight)


async def correct_exercise_log(
    conn: sqlite3.Connection,
    log_id: int | None = None,
    exercise_name: str = "",
    date: str | None = None,
    new_exercise_name: str | None = None,
    sets: int | None = None,
    reps: int | None = None,
    weight_kg=_UNSET,
    warmup_kg=_UNSET,
    notes: str | None = None,
) -> dict:
    """Fix one logged exercise in place — only the fields given change."""
    try:
        row, reply = _find_logged_set(conn, log_id, exercise_name, date)
        if reply is not None:
            return reply

        changes: dict = {}
        if new_exercise_name:
            changes["exercise"] = new_exercise_name
        if sets is not None:
            changes["sets"] = sets
        if reps is not None:
            changes["reps"] = reps
        if weight_kg is not _UNSET:
            changes["weight_kg"] = weight_kg
        if warmup_kg is not _UNSET:
            changes["warmup_kg"] = warmup_kg
        if notes is not None:
            changes["notes"] = notes
        if not changes:
            return {"error": "nothing to change — give at least one new value"}

        update_exercise_set(conn, row["id"], changes)
        after = get_set_by_id(conn, row["id"])
        return {
            "updated": True,
            "date": row["date"],
            "before": _set_view(row),
            "after": _set_view(after),
        }
    except Exception as exc:
        logger.warning("correct_exercise_log failed: %s", exc)
        return {"error": str(exc)}


async def delete_exercise_log(
    conn: sqlite3.Connection,
    log_id: int | None = None,
    exercise_name: str = "",
    date: str | None = None,
) -> dict:
    """Remove one logged exercise — for a duplicate or something that was
    never meant to be logged. Never guesses between several matches."""
    try:
        row, reply = _find_logged_set(conn, log_id, exercise_name, date)
        if reply is not None:
            return reply

        session_removed = delete_exercise_set(conn, row["id"])
        return {
            "deleted": True,
            "date": row["date"],
            "removed": _set_view(row),
            "session_removed": session_removed,
        }
    except Exception as exc:
        logger.warning("delete_exercise_log failed: %s", exc)
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
        rows = _get_history_across_aliases(conn, exercise_name, limit=limit)
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


async def get_exercise_progression(conn: sqlite3.Connection, exercise_name: str) -> dict:
    """Compute the next sets/reps/weight recommendation for one exercise.

    Progression rule: sets x reps advance through 3x8 -> 3x10 -> 4x8 -> 4x10
    at a fixed weight; completing 4x10 bumps weight by +2.5kg and resets to
    3x8. The recommendation is always based on the most advanced (weight,
    cycle-step) ever logged for this exercise — not just the last entry — so
    a one-off lighter or lower-rep session doesn't regress the target. A
    logged weight higher than any seen before immediately becomes the new
    basis, restarting the cycle at that weight.
    """
    try:
        rows = _get_history_across_aliases(conn, exercise_name, limit=100)
        weighted = [r for r in rows if r.get("weight_kg") is not None]
        if not weighted:
            return {"exercise": exercise_name, "found": False}

        best_row = None
        best_key: tuple[float, int] | None = None
        for row in weighted:
            rank = _cycle_rank(row["sets"], row["reps"])
            key = (row["weight_kg"], rank)
            if best_key is None or key > best_key:
                best_key = key
                best_row = row

        weight, rank = best_key
        if rank >= len(_PROGRESSION_CYCLE) - 1:
            next_weight = round(weight + _PROGRESSION_INCREMENT_KG, 2)
            next_sets, next_reps = _PROGRESSION_CYCLE[0]
        else:
            next_weight = weight
            next_sets, next_reps = _PROGRESSION_CYCLE[max(rank + 1, 0)]

        return {
            "exercise": exercise_name,
            "found": True,
            "basis": {
                "date": best_row["date"],
                "weight_kg": weight,
                "sets": best_row["sets"],
                "reps": best_row["reps"],
            },
            "recommended_weight_kg": next_weight,
            "recommended_sets": next_sets,
            "recommended_reps": next_reps,
        }
    except Exception as exc:
        logger.warning("get_exercise_progression failed: %s", exc)
        return {"error": str(exc)}


async def get_next_session_type(conn: sqlite3.Connection) -> dict:
    """Return the next session type due in the push/pull/legs rotation."""
    try:
        session_type = _next_session_type(conn)
        # "run" occupies the 3rd (legs) slot when the leg-slot alternation
        # lands there — display it at that position rather than raising.
        idx = _PPL_CYCLE.index("legs" if session_type == "run" else session_type)
        return {"session_type": session_type, "cycle_position": f"{idx + 1}/{len(_PPL_CYCLE)}"}
    except Exception as exc:
        logger.warning("get_next_session_type failed: %s", exc)
        return {"error": str(exc)}


async def get_session_plan(conn: sqlite3.Connection, session_type: str) -> dict:
    """Return the exercise plan for a session type, with weights already computed.

    For push/pull/legs, each exercise's sets/reps/weight_kg come from
    get_exercise_progression (computed in-process, not a separate tool call)
    when the exercise has weighted history; otherwise they fall back to the
    static target_sets/target_reps with weight_kg=null. short/run sessions
    aren't weight-tracked and are returned as-is from the static plan.
    """
    try:
        plan = _SESSION_PLANS.get(session_type)
        if plan is None:
            return {"error": f"unknown session_type: {session_type}"}

        if session_type not in _PPL_CYCLE:
            return {"session_type": session_type, "exercises": plan}

        exercises = []
        for ex in plan:
            progression = await get_exercise_progression(conn, ex["exercise"])
            if progression.get("found"):
                exercises.append({
                    "exercise": ex["exercise"],
                    "sets": progression["recommended_sets"],
                    "reps": progression["recommended_reps"],
                    "weight_kg": progression["recommended_weight_kg"],
                    "notes": ex["notes"],
                    "basis": "progression",
                })
            else:
                exercises.append({
                    "exercise": ex["exercise"],
                    "sets": ex["target_sets"],
                    "reps": ex["target_reps"],
                    "weight_kg": None,
                    "notes": ex["notes"],
                    "basis": "static",
                })
        return {"session_type": session_type, "exercises": exercises}
    except Exception as exc:
        logger.warning("get_session_plan failed: %s", exc)
        return {"error": str(exc)}


async def get_weekly_gym_summary(conn: sqlite3.Connection) -> dict:
    """Return this week's (Monday-based) logged sessions with exercise counts."""
    try:
        today = datetime.now(tz=_TZ).date()
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
            "name": "log_exercises",
            "description": (
                "Log one or more exercises Ollie has COMPLETED, to today's gym session, in a "
                "SINGLE call. Only call this when he reports doing them — never for a "
                "question ('what did I do on X last time?', 'what weight for X?') or a plan. "
                "Pass only the exercises from his latest message — never re-send ones from "
                "earlier messages; they're already logged (see open_session_today / "
                "session_so_far). Every exercise in the message goes in one list — even a "
                "single exercise is a 1-item list; e.g. 'DB bench 30kg 3x8, pec fly 50kg "
                "3x8' is one call with two items. Appends to today's session or starts one. "
                "Returns session_so_far (everything logged today, with log_ids) — use it to "
                "show the whole session. An exercise already logged today is NOT written "
                "again: identical ones come back in skipped_duplicates; ones with different "
                "numbers come back in needs_confirmation — ask Ollie whether it's a "
                "correction (correct_exercise_log with that log_id) or genuine extra work."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "exercises": {
                        "type": "array",
                        "description": "Every exercise from this message, one item each, in the order performed.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "exercise_name": {
                                    "type": "string",
                                    "description": "Name of the exercise, e.g. 'bench press', 'rope pulldowns', '5k run'.",
                                },
                                "sets": {
                                    "type": "integer",
                                    "description": "Number of working sets performed.",
                                },
                                "reps": {
                                    "type": "integer",
                                    "description": "Reps per set (or total reps for single-set entries like a run).",
                                },
                                "weight_kg": {
                                    "type": ["number", "null"],
                                    "description": "Working weight in kg. Use null for bodyweight exercises or runs.",
                                },
                                "warmup_kg": {
                                    "type": ["number", "null"],
                                    "description": (
                                        "Warm-up weight, only if Ollie noted one — 'sXX' or "
                                        "'warm-up set of Xkg' notation, e.g. 's40 70kg 5x8' means "
                                        "warmup_kg=40, weight_kg=70. Null if no warm-up was mentioned."
                                    ),
                                },
                                "notes": {
                                    "type": ["string", "null"],
                                    "description": "Form cues, 'failed last rep', pace/time for runs, or any other free note.",
                                },
                            },
                            "required": ["exercise_name", "sets", "reps"],
                        },
                    },
                    "session_type": {
                        "type": ["string", "null"],
                        "enum": ["push", "pull", "legs", "short", "run", None],
                        "description": (
                            "Set this whenever the type is clear — infer it from the exercises "
                            "(dips/presses/flyes/raises/tricep work -> push; rows/pull-ups/curls/"
                            "face pulls/delt fly -> pull; squats/RDLs/lunges/leg press -> legs). "
                            "It decides which of today's sessions these join: 'run' (run-strength/"
                            "prehab work) joins today's run session; push/pull/legs/short join "
                            "today's gym session — a run and gym work on the same day stay "
                            "separate sessions, different gym types on one day merge."
                        ),
                    },
                    "allow_duplicates": {
                        "type": "boolean",
                        "description": (
                            "Only true when Ollie confirms an exercise reported in "
                            "skipped_duplicates or needs_confirmation really was extra work "
                            "on top of what's logged, not a correction."
                        ),
                    },
                },
                "required": ["exercises"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "correct_exercise_log",
            "description": (
                "Fix an exercise already logged — wrong weight/sets/reps/name, e.g. "
                "'that pec fly was 55kg not 50'. Only the fields you pass change. Prefer "
                "log_id (from session_so_far or a [done: ...] note) since it's exact. With "
                "only a name, several matches come back as candidates instead of an edit — "
                "ask Ollie which one. Works on earlier days via date. Never use "
                "log_exercises to 'fix' a log — that adds a second entry."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "log_id": {"type": ["integer", "null"], "description": "Exact entry id, if known."},
                    "exercise_name": {
                        "type": "string",
                        "description": "Name to match, only used if log_id is omitted.",
                    },
                    "date": {
                        "type": ["string", "null"],
                        "description": "YYYY-MM-DD the entry was logged on. Omit for today.",
                    },
                    "new_exercise_name": {"type": ["string", "null"], "description": "Rename the entry."},
                    "sets": {"type": ["integer", "null"]},
                    "reps": {"type": ["integer", "null"]},
                    "weight_kg": {"type": ["number", "null"], "description": "New weight; null = bodyweight."},
                    "warmup_kg": {"type": ["number", "null"]},
                    "notes": {"type": ["string", "null"]},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_exercise_log",
            "description": (
                "Remove a logged exercise entirely — a duplicate, or something logged by "
                "mistake ('delete that', 'I didn't do the face pulls'). NEVER use this to "
                "fix a wrong value — that's correct_exercise_log. Prefer log_id. With only "
                "a name, several matches come back as candidates and nothing is deleted — "
                "ask Ollie which one. Works on earlier days via date."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "log_id": {"type": ["integer", "null"], "description": "Exact entry id, if known."},
                    "exercise_name": {
                        "type": "string",
                        "description": "Name to match, only used if log_id is omitted.",
                    },
                    "date": {
                        "type": ["string", "null"],
                        "description": "YYYY-MM-DD the entry was logged on. Omit for today.",
                    },
                },
                "required": [],
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
                "to answer 'what did I do last push day'. For progression targets on a "
                "specific exercise, use get_exercise_progression instead — it computes the "
                "next sets/reps/weight directly."
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
                "newest first. Use this for 'what did I do on X last time?', including for "
                "an exercise Ollie is swapping in today, and for progress questions like "
                "'how's my bench progressing'. Read-only — asking never logs anything."
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
            "name": "get_exercise_progression",
            "description": (
                "Get the next recommended sets/reps/weight for one specific exercise, "
                "computed from logged history (not left to you to calculate). Call this "
                "for EVERY exercise before telling Ollie his targets for today's session — "
                "don't recommend weights from get_session_plan's static targets alone. "
                "Also use it directly for 'what weight should I do on bench today'. Returns "
                "found=false if the exercise has no weighted history yet (use the static "
                "target from get_session_plan instead)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "exercise_name": {
                        "type": "string",
                        "description": "Exercise name, matching how it's logged, e.g. 'bench press'.",
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
                "most recently logged PPL session. The 3rd slot alternates between 'legs' and "
                "'run' (cardio run or run-strength/prehab) rather than always being legs — may "
                "return 'run' instead of 'legs'. Use this when the user asks what session "
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
                "Get the exercise plan for a session type — push, pull, legs, short, or run. "
                "For push/pull/legs, sets/reps/weight_kg per exercise are already computed from "
                "logged history (same as get_exercise_progression would return) — present them "
                "directly, don't call get_exercise_progression again for exercises already in "
                "this result. weight_kg is null where there's no weighted history yet (new "
                "exercise) — use the static target in that case. Exercises are returned in "
                "the order they should be performed (compound/multi-muscle movements first, "
                "isolation last) — present them in that order, don't re-sort or group by "
                "muscle yourself. Use this single call to tell the user what's on today, "
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
