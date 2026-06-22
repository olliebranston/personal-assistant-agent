"""Tests for the gym tools (tools/gym.py) — TOOL_CALLING_DESIGN.md §2.1."""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from storage.models import (
    EXERCISE_SET_DDL,
    GYM_SESSION_DDL,
    ExerciseSet,
    GymSession,
    get_recent_sessions,
    insert_session,
    insert_set,
)
from tools.gym import (
    get_exercise_history,
    get_exercise_progression,
    get_last_session,
    get_next_session_type,
    get_session_plan,
    get_weekly_gym_summary,
    log_exercise,
)


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(GYM_SESSION_DDL)
    conn.execute(EXERCISE_SET_DDL)
    conn.commit()
    return conn


# ── log_exercise ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_log_exercise_creates_session_when_none_today():
    conn = _make_conn()

    result = await log_exercise(conn, exercise_name="bench press", sets=5, reps=5, weight_kg=80.0)

    assert result["logged"] is True
    assert result["session_type"] == "push"  # default fallback with no history
    assert result["exercise"] == "bench press"

    sessions = get_recent_sessions(conn)
    assert len(sessions) == 1
    assert sessions[0]["date"] == date.today().isoformat()
    assert len(sessions[0]["sets"]) == 1


@pytest.mark.asyncio
async def test_log_exercise_appends_to_existing_session_today():
    conn = _make_conn()
    today = date.today().isoformat()
    session_id = insert_session(conn, GymSession(date=today, session_type="push"))

    result = await log_exercise(conn, exercise_name="overhead press", sets=4, reps=8, weight_kg=52.5)

    assert result["logged"] is True
    assert result["session_id"] == session_id
    assert result["session_type"] == "push"

    sessions = get_recent_sessions(conn)
    assert len(sessions) == 1  # appended, not a new session
    assert len(sessions[0]["sets"]) == 1


# ── get_last_session ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_last_session_returns_all_exercises():
    conn = _make_conn()
    today = date.today().isoformat()
    session_id = insert_session(conn, GymSession(date=today, session_type="push"))
    insert_set(conn, ExerciseSet(session_id=session_id, exercise="bench press", sets=5, reps=5, weight_kg=80.0))
    insert_set(conn, ExerciseSet(session_id=session_id, exercise="overhead press", sets=4, reps=8, weight_kg=52.5))

    result = await get_last_session(conn, session_type="push")

    assert result["found"] is True
    assert result["date"] == today
    assert len(result["exercises"]) == 2
    assert {e["exercise"] for e in result["exercises"]} == {"bench press", "overhead press"}


@pytest.mark.asyncio
async def test_get_last_session_not_found():
    conn = _make_conn()

    result = await get_last_session(conn, session_type="legs")

    assert result == {"found": False, "date": None, "session_type": "legs", "exercises": []}


# ── get_exercise_history ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_exercise_history_descending_date_order():
    conn = _make_conn()
    older, newer = "2026-06-01", "2026-06-08"
    s1 = insert_session(conn, GymSession(date=older, session_type="push"))
    s2 = insert_session(conn, GymSession(date=newer, session_type="push"))
    insert_set(conn, ExerciseSet(session_id=s1, exercise="bench press", sets=5, reps=5, weight_kg=77.5))
    insert_set(conn, ExerciseSet(session_id=s2, exercise="bench press", sets=5, reps=5, weight_kg=80.0))

    result = await get_exercise_history(conn, exercise_name="bench press")

    assert result["exercise"] == "bench press"
    assert [e["date"] for e in result["entries"]] == [newer, older]
    assert result["entries"][0]["weight_kg"] == 80.0


# ── get_exercise_progression ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_progression_advances_within_cycle_same_weight():
    # 3x10 @ 66kg -> next is 4x8 @ 66kg (sets up, reps down, weight held).
    conn = _make_conn()
    s1 = insert_session(conn, GymSession(date="2026-06-01", session_type="pull"))
    insert_set(conn, ExerciseSet(session_id=s1, exercise="rope pulldowns", sets=3, reps=10, weight_kg=66.0))

    result = await get_exercise_progression(conn, exercise_name="rope pulldowns")

    assert result["found"] is True
    assert result["recommended_weight_kg"] == 66.0
    assert (result["recommended_sets"], result["recommended_reps"]) == (4, 8)


@pytest.mark.asyncio
async def test_progression_jump_to_heavier_weight_restarts_cycle_there():
    # Recommended 3x10@66, but Ollie logged 4x8@75 instead -> basis becomes
    # 75kg, next is 4x10 @ 75kg (continue the cycle at the new weight).
    conn = _make_conn()
    s1 = insert_session(conn, GymSession(date="2026-06-01", session_type="pull"))
    insert_set(conn, ExerciseSet(session_id=s1, exercise="rope pulldowns", sets=4, reps=8, weight_kg=75.0))

    result = await get_exercise_progression(conn, exercise_name="rope pulldowns")

    assert result["recommended_weight_kg"] == 75.0
    assert (result["recommended_sets"], result["recommended_reps"]) == (4, 10)


@pytest.mark.asyncio
async def test_progression_completing_top_of_cycle_bumps_weight():
    conn = _make_conn()
    s1 = insert_session(conn, GymSession(date="2026-06-01", session_type="pull"))
    insert_set(conn, ExerciseSet(session_id=s1, exercise="rope pulldowns", sets=4, reps=10, weight_kg=75.0))

    result = await get_exercise_progression(conn, exercise_name="rope pulldowns")

    assert result["recommended_weight_kg"] == 77.5
    assert (result["recommended_sets"], result["recommended_reps"]) == (3, 8)


@pytest.mark.asyncio
async def test_progression_does_not_regress_on_a_lighter_logged_session():
    # Historical max is 4x8@75kg (an earlier session). A later, lighter
    # 3x8@75kg log (off day) must NOT pull the recommendation backwards —
    # it should still be computed from the 4x8@75kg max: next = 4x10@75kg.
    conn = _make_conn()
    s1 = insert_session(conn, GymSession(date="2026-06-01", session_type="pull"))
    insert_set(conn, ExerciseSet(session_id=s1, exercise="rope pulldowns", sets=4, reps=8, weight_kg=75.0))
    s2 = insert_session(conn, GymSession(date="2026-06-08", session_type="pull"))
    insert_set(conn, ExerciseSet(session_id=s2, exercise="rope pulldowns", sets=3, reps=8, weight_kg=75.0))

    result = await get_exercise_progression(conn, exercise_name="rope pulldowns")

    assert result["recommended_weight_kg"] == 75.0
    assert (result["recommended_sets"], result["recommended_reps"]) == (4, 10)


@pytest.mark.asyncio
async def test_progression_no_weighted_history_returns_not_found():
    conn = _make_conn()

    result = await get_exercise_progression(conn, exercise_name="pull-ups")

    assert result == {"exercise": "pull-ups", "found": False}


# ── get_next_session_type ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_next_session_type_returns_correct_next():
    conn = _make_conn()
    insert_session(conn, GymSession(date=date.today().isoformat(), session_type="pull"))

    result = await get_next_session_type(conn)

    assert result == {"session_type": "legs", "cycle_position": "3/3"}


@pytest.mark.asyncio
async def test_get_next_session_type_defaults_to_push_with_no_history():
    conn = _make_conn()

    result = await get_next_session_type(conn)

    assert result == {"session_type": "push", "cycle_position": "1/3"}


# ── get_session_plan ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_session_plan_returns_plan_for_known_type():
    conn = _make_conn()

    result = await get_session_plan(conn, session_type="push")

    assert result["session_type"] == "push"
    assert any(ex["exercise"] == "bench press" for ex in result["exercises"])


@pytest.mark.asyncio
async def test_get_session_plan_errors_on_unknown_type():
    conn = _make_conn()

    result = await get_session_plan(conn, session_type="cardio")

    assert "error" in result


# ── get_weekly_gym_summary ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_weekly_gym_summary_counts_sessions_this_week():
    conn = _make_conn()
    today = date.today().isoformat()
    session_id = insert_session(conn, GymSession(date=today, session_type="push"))
    insert_set(conn, ExerciseSet(session_id=session_id, exercise="bench press", sets=5, reps=5, weight_kg=80.0))

    result = await get_weekly_gym_summary(conn)

    assert result["session_count"] == 1
    assert result["sessions"][0]["date"] == today
    assert result["sessions"][0]["exercise_count"] == 1
