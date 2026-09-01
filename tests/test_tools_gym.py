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
    log_exercises,
    normalize_exercise_name,
)


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(GYM_SESSION_DDL)
    conn.execute(EXERCISE_SET_DDL)
    conn.commit()
    return conn


# ── log_exercises ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_log_exercises_creates_session_when_none_today():
    conn = _make_conn()

    result = await log_exercises(conn, exercises=[
        {"exercise_name": "bench press", "sets": 5, "reps": 5, "weight_kg": 80.0},
    ])

    assert result["logged"] is True
    assert result["session_type"] == "push"  # default fallback with no history
    assert result["exercises"][0]["exercise"] == "bench press"

    sessions = get_recent_sessions(conn)
    assert len(sessions) == 1
    assert sessions[0]["date"] == date.today().isoformat()
    assert len(sessions[0]["sets"]) == 1


@pytest.mark.asyncio
async def test_log_exercises_appends_to_existing_session_today():
    conn = _make_conn()
    today = date.today().isoformat()
    session_id = insert_session(conn, GymSession(date=today, session_type="push"))

    result = await log_exercises(conn, exercises=[
        {"exercise_name": "overhead press", "sets": 4, "reps": 8, "weight_kg": 52.5},
    ])

    assert result["logged"] is True
    assert result["session_id"] == session_id
    assert result["session_type"] == "push"

    sessions = get_recent_sessions(conn)
    assert len(sessions) == 1  # appended, not a new session
    assert len(sessions[0]["sets"]) == 1


@pytest.mark.asyncio
async def test_log_exercises_inserts_all_items_from_one_call():
    # Regression test: multi-exercise messages used to rely on the model
    # self-issuing one tool call per exercise, and a second exercise could
    # silently never get logged. A single atomic call must insert every item.
    conn = _make_conn()

    result = await log_exercises(conn, exercises=[
        {"exercise_name": "bench press", "sets": 5, "reps": 5, "weight_kg": 80.0},
        {"exercise_name": "overhead press", "sets": 4, "reps": 8, "weight_kg": 52.5},
    ])

    assert result["logged"] is True
    assert {e["exercise"] for e in result["exercises"]} == {"bench press", "overhead press"}

    sessions = get_recent_sessions(conn)
    assert len(sessions) == 1
    assert len(sessions[0]["sets"]) == 2


@pytest.mark.asyncio
async def test_log_exercises_creates_exactly_one_session_for_multiple_items():
    conn = _make_conn()

    await log_exercises(conn, exercises=[
        {"exercise_name": "bench press", "sets": 5, "reps": 5, "weight_kg": 80.0},
        {"exercise_name": "overhead press", "sets": 4, "reps": 8, "weight_kg": 52.5},
        {"exercise_name": "dips", "sets": 4, "reps": 10, "weight_kg": None},
    ])

    sessions = get_recent_sessions(conn)
    assert len(sessions) == 1
    assert len(sessions[0]["sets"]) == 3


@pytest.mark.asyncio
async def test_log_exercises_captures_warmup_kg():
    # warmup_kg was silently dropped when logging moved off the old
    # single-shot parser — the DB column/dataclass field always supported it,
    # only the tool interface had stopped exposing it.
    conn = _make_conn()

    result = await log_exercises(conn, exercises=[
        {"exercise_name": "bench press", "sets": 5, "reps": 8, "weight_kg": 70.0, "warmup_kg": 40.0},
    ])

    assert result["exercises"][0]["warmup_kg"] == 40.0
    sessions = get_recent_sessions(conn)
    assert sessions[0]["sets"][0]["warmup_kg"] == 40.0


@pytest.mark.asyncio
async def test_log_exercises_empty_list_errors():
    conn = _make_conn()

    result = await log_exercises(conn, exercises=[])

    assert "error" in result


@pytest.mark.asyncio
async def test_log_exercises_malformed_item_writes_nothing():
    # Found by adversarial review: insert_set commits per row, so a naive
    # loop that fails partway through a list would leave earlier items
    # durably logged while the call as a whole reports an error — breaking
    # the "single atomic call" guarantee log_exercises is meant to provide.
    conn = _make_conn()

    result = await log_exercises(conn, exercises=[
        {"exercise_name": "bench press", "sets": 5, "reps": 5, "weight_kg": 80.0},
        {"exercise_name": "overhead press", "weight_kg": 52.5},  # missing "sets"/"reps"
    ])

    assert "error" in result
    assert get_recent_sessions(conn) == []


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


@pytest.mark.asyncio
async def test_progression_finds_history_logged_under_a_known_alias():
    # Reproduces a real bug: a set logged as "OHP" (the shorthand main.py's
    # own system prompt tells the model to use) was invisible to progression
    # lookups for "overhead press" (the canonical name used in
    # _SESSION_PLANS) because get_last_sets_for_exercise did an exact-string
    # match with no alias awareness — get_session_plan showed weight_kg=null
    # for OHP even with real logged history under the "OHP" name.
    conn = _make_conn()
    s1 = insert_session(conn, GymSession(date="2026-06-15", session_type="push"))
    insert_set(conn, ExerciseSet(session_id=s1, exercise="OHP", sets=4, reps=8, weight_kg=52.5))

    result = await get_exercise_progression(conn, exercise_name="overhead press")

    assert result["found"] is True
    assert result["basis"]["weight_kg"] == 52.5

    plan = await get_session_plan(conn, session_type="push")
    ohp = next(ex for ex in plan["exercises"] if ex["exercise"] == "overhead press")
    assert ohp["basis"] == "progression"
    assert ohp["weight_kg"] == 52.5

    # And the reverse direction: querying by the shorthand also finds sets
    # logged under the canonical full name.
    conn2 = _make_conn()
    s2 = insert_session(conn2, GymSession(date="2026-06-15", session_type="push"))
    insert_set(conn2, ExerciseSet(session_id=s2, exercise="overhead press", sets=4, reps=8, weight_kg=60.0))

    reverse = await get_exercise_progression(conn2, exercise_name="OHP")
    assert reverse["found"] is True
    assert reverse["basis"]["weight_kg"] == 60.0


@pytest.mark.asyncio
async def test_exercise_history_finds_sets_logged_under_a_known_alias():
    conn = _make_conn()
    s1 = insert_session(conn, GymSession(date="2026-06-15", session_type="push"))
    insert_set(conn, ExerciseSet(session_id=s1, exercise="OHP", sets=4, reps=8, weight_kg=52.5))

    result = await get_exercise_history(conn, exercise_name="overhead press")

    assert len(result["entries"]) == 1
    assert result["entries"][0]["weight_kg"] == 52.5


# ── normalize_exercise_name / exercise-name matching ────────────────────────


def test_normalize_exercise_name_expands_known_abbreviations():
    assert normalize_exercise_name("incline db bench") == normalize_exercise_name("incline dumbbell bench")
    assert normalize_exercise_name("OHP") == normalize_exercise_name("overhead press")
    assert normalize_exercise_name("rdl") == normalize_exercise_name("romanian deadlift")


def test_normalize_exercise_name_strips_punctuation_and_case():
    assert normalize_exercise_name("Bench-Press!") == normalize_exercise_name("bench press")


@pytest.mark.asyncio
async def test_get_exercise_progression_matches_across_db_abbreviation():
    # Reproduces the reported bug directly: a session logged as "incline
    # dumbbell bench" was invisible to progression lookups made as "incline
    # db bench" — get_last_sets_for_exercise did an exact string match with
    # no abbreviation awareness, so progressive-overload silently fell back
    # to a static, weight-less plan for what is really the same exercise.
    conn = _make_conn()
    s1 = insert_session(conn, GymSession(date="2026-06-15", session_type="push"))
    insert_set(conn, ExerciseSet(session_id=s1, exercise="incline dumbbell bench", sets=4, reps=8, weight_kg=32.5))

    result = await get_exercise_progression(conn, exercise_name="incline db bench")

    assert result["found"] is True
    assert result["basis"]["weight_kg"] == 32.5


@pytest.mark.asyncio
async def test_get_exercise_progression_fuzzy_match_does_not_conflate_similar_exercises():
    # Found by adversarial review: "incline dumbbell bench" and "incline DB
    # curls" (normalized: "incline dumbbell curls") share enough characters
    # to score 0.818 on a plain difflib ratio — comfortably matched by an 0.8
    # cutoff despite being different exercises (a bench press vs a curl).
    # The cutoff must be high enough to reject this specific near-miss.
    conn = _make_conn()
    s1 = insert_session(conn, GymSession(date="2026-06-15", session_type="pull"))
    insert_set(conn, ExerciseSet(session_id=s1, exercise="incline DB curls", sets=4, reps=10, weight_kg=14.0))

    result = await get_exercise_progression(conn, exercise_name="incline dumbbell bench")

    assert result["found"] is False


@pytest.mark.asyncio
async def test_get_exercise_progression_does_not_cross_match_unrelated_exercises():
    # Guard against the fuzzy-matching fallback overreaching — "bench press"
    # and "leg press" are different exercises and must not be conflated just
    # because they share a word.
    conn = _make_conn()
    s1 = insert_session(conn, GymSession(date="2026-06-15", session_type="legs"))
    insert_set(conn, ExerciseSet(session_id=s1, exercise="leg press", sets=4, reps=8, weight_kg=120.0))

    result = await get_exercise_progression(conn, exercise_name="bench press")

    assert result["found"] is False


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


@pytest.mark.asyncio
async def test_get_session_plan_merges_weights_for_exercises_with_history():
    # Root cause confirmed by Ollie: a session-plan request only ever returned
    # static sets/reps, never weights, because the LLM didn't reliably chain
    # a get_exercise_progression call per exercise. get_session_plan now does
    # that merge itself, in-process, so weights are always included.
    conn = _make_conn()
    s1 = insert_session(conn, GymSession(date="2026-06-01", session_type="push"))
    insert_set(conn, ExerciseSet(session_id=s1, exercise="bench press", sets=4, reps=8, weight_kg=80.0))

    result = await get_session_plan(conn, session_type="push")

    bench = next(ex for ex in result["exercises"] if ex["exercise"] == "bench press")
    assert bench["basis"] == "progression"
    assert bench["weight_kg"] == 80.0
    assert (bench["sets"], bench["reps"]) == (4, 10)

    # An exercise with no logged history at all falls back to the static plan.
    ohp = next(ex for ex in result["exercises"] if ex["exercise"] == "overhead press")
    assert ohp["basis"] == "static"
    assert ohp["weight_kg"] is None
    assert ohp["sets"] == 4
    assert ohp["reps"] == "8"


@pytest.mark.asyncio
async def test_get_session_plan_orders_compound_bodyweight_movement_first():
    # Explicit rule (Gym-CONTEXT.md "Exercise Ordering Rule"): each session
    # opens with its signature compound/bodyweight movement, never left to
    # the LLM to reorder per session.
    conn = _make_conn()

    push = await get_session_plan(conn, session_type="push")
    assert push["exercises"][0]["exercise"] == "dips"

    pull = await get_session_plan(conn, session_type="pull")
    assert pull["exercises"][0]["exercise"] == "pull-ups"

    legs = await get_session_plan(conn, session_type="legs")
    assert legs["exercises"][0]["exercise"] == "Bulgarian split squats"


@pytest.mark.asyncio
async def test_get_session_plan_orders_compounds_before_isolation():
    conn = _make_conn()

    push = await get_session_plan(conn, session_type="push")
    push_order = [ex["exercise"] for ex in push["exercises"]]
    # Compounds (dips, bench press, overhead press) all precede isolation
    # (pec fly, DB lateral raises, rope pulldowns).
    assert push_order.index("overhead press") < push_order.index("pec fly")
    assert push_order.index("overhead press") < push_order.index("DB lateral raises")
    assert push_order.index("overhead press") < push_order.index("rope pulldowns")

    legs = await get_session_plan(conn, session_type="legs")
    legs_order = [ex["exercise"] for ex in legs["exercises"]]
    # leg press is a compound, multi-joint movement — must precede the
    # true single-joint isolation finishers (hamstring curls, quad extensions).
    assert legs_order.index("leg press") < legs_order.index("hamstring curls")
    assert legs_order.index("leg press") < legs_order.index("quad extensions")


@pytest.mark.asyncio
async def test_get_session_plan_short_and_run_types_are_not_weight_merged():
    conn = _make_conn()

    result = await get_session_plan(conn, session_type="run")

    assert result["session_type"] == "run"
    for ex in result["exercises"]:
        assert "basis" not in ex
        assert "target_sets" in ex
        assert "target_reps" in ex


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
