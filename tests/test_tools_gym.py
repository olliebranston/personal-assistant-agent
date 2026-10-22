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
    correct_exercise_log,
    delete_exercise_log,
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

    # (OHP left the push plan in Oct 2026, so the plan-level half of this
    # check now lives in test_plan_finds_history_logged_under_variant_names.)

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
async def test_get_next_session_type_returns_run_with_no_leg_slot_history():
    # With no legs/run history yet, the leg-slot alternator leads with "run"
    # (prehab/running first, not a heavy leg day) — not "legs".
    conn = _make_conn()
    insert_session(conn, GymSession(date=date.today().isoformat(), session_type="pull"))

    result = await get_next_session_type(conn)

    assert result == {"session_type": "run", "cycle_position": "3/3"}


@pytest.mark.asyncio
async def test_get_next_session_type_defaults_to_push_with_no_history():
    conn = _make_conn()

    result = await get_next_session_type(conn)

    assert result == {"session_type": "push", "cycle_position": "1/3"}


@pytest.mark.asyncio
async def test_get_next_session_type_alternates_legs_to_run():
    # push -> pull -> legs -> push -> pull -> next leg-slot should be "run",
    # alternating away from the "legs" that was last logged at that slot.
    conn = _make_conn()
    for session_type in ["push", "pull", "legs", "push", "pull"]:
        insert_session(conn, GymSession(date=date.today().isoformat(), session_type=session_type))

    result = await get_next_session_type(conn)

    assert result == {"session_type": "run", "cycle_position": "3/3"}


@pytest.mark.asyncio
async def test_get_next_session_type_alternates_run_to_legs():
    # push -> pull -> run -> push -> pull -> next leg-slot should be "legs",
    # alternating away from the "run" that was last logged at that slot.
    conn = _make_conn()
    for session_type in ["push", "pull", "run", "push", "pull"]:
        insert_session(conn, GymSession(date=date.today().isoformat(), session_type=session_type))

    result = await get_next_session_type(conn)

    assert result == {"session_type": "legs", "cycle_position": "3/3"}


# ── get_session_plan ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_session_plan_returns_plan_for_known_type():
    conn = _make_conn()

    result = await get_session_plan(conn, session_type="push")

    assert result["session_type"] == "push"
    assert any(ex["exercise"] == "DB bench press" for ex in result["exercises"])


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
    insert_set(conn, ExerciseSet(session_id=s1, exercise="DB bench press", sets=4, reps=8, weight_kg=32.5))

    result = await get_session_plan(conn, session_type="push")

    bench = next(ex for ex in result["exercises"] if ex["exercise"] == "DB bench press")
    assert bench["basis"] == "progression"
    assert bench["weight_kg"] == 32.5
    assert (bench["sets"], bench["reps"]) == (4, 10)

    # An exercise with no logged history at all falls back to the static
    # plan — 3x8, the bottom of the progression cycle (Ollie, Oct 2026).
    raises = next(ex for ex in result["exercises"] if ex["exercise"] == "machine raises")
    assert raises["basis"] == "static"
    assert raises["weight_kg"] is None
    assert raises["sets"] == 3
    assert raises["reps"] == "8"


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
    # Compounds (dips, DB bench press) all precede isolation.
    for isolation in ("pec fly", "machine raises", "overhead tricep extensions", "rope pulldowns"):
        assert push_order.index("DB bench press") < push_order.index(isolation)

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


# ── Oct 2026 routine + logging safeguards ───────────────────────────────────
# Ollie's new standard push/pull days, plus the guards against the bugs seen
# in daily use: re-sent exercises logged twice, no way to fix a wrong log,
# and history logged under a slightly different name not reaching the plan.


@pytest.mark.asyncio
async def test_push_and_pull_plans_are_the_oct_2026_routine():
    conn = _make_conn()

    push = await get_session_plan(conn, session_type="push")
    pull = await get_session_plan(conn, session_type="pull")

    assert [ex["exercise"] for ex in push["exercises"]] == [
        "dips", "DB bench press", "pec fly", "machine raises",
        "overhead tricep extensions", "rope pulldowns",
    ]
    assert [ex["exercise"] for ex in pull["exercises"]] == [
        "pull-ups", "bent over DB rows", "delt fly", "face pulls",
        "incline DB bicep curls", "rope pull-ups",
    ]


@pytest.mark.asyncio
async def test_plan_finds_history_logged_under_variant_names():
    # The names below are exactly how these exercises were logged on the VM
    # (audit, 22 Oct 2026). Without the alias table every one of them showed
    # as static/no weight in the new plan.
    conn = _make_conn()
    s1 = insert_session(conn, GymSession(date="2026-10-17", session_type="push"))
    for name, kg in [
        ("DB bench", 30.0), ("machine lateral raises", 40.0),
        ("overhead rope extensions", 25.0), ("rope pull downs", 30.0),
    ]:
        insert_set(conn, ExerciseSet(session_id=s1, exercise=name, sets=3, reps=10, weight_kg=kg))
    s2 = insert_session(conn, GymSession(date="2026-10-20", session_type="pull"))
    for name, kg in [
        ("DB Bent Over Rows", 26.0), ("incline DB curls", 12.0), ("rope pull ups", 45.0),
    ]:
        insert_set(conn, ExerciseSet(session_id=s2, exercise=name, sets=3, reps=10, weight_kg=kg))

    push = {ex["exercise"]: ex for ex in (await get_session_plan(conn, "push"))["exercises"]}
    pull = {ex["exercise"]: ex for ex in (await get_session_plan(conn, "pull"))["exercises"]}

    for plan, name, kg in [
        (push, "DB bench press", 30.0), (push, "machine raises", 40.0),
        (push, "overhead tricep extensions", 25.0), (push, "rope pulldowns", 30.0),
        (pull, "bent over DB rows", 26.0), (pull, "incline DB bicep curls", 12.0),
        (pull, "rope pull-ups", 45.0),
    ]:
        assert plan[name]["basis"] == "progression", name
        assert plan[name]["weight_kg"] == kg, name


@pytest.mark.asyncio
async def test_alias_table_keeps_incline_db_press_separate_from_db_bench_press():
    conn = _make_conn()
    s1 = insert_session(conn, GymSession(date="2026-10-17", session_type="push"))
    insert_set(conn, ExerciseSet(session_id=s1, exercise="incline db press", sets=3, reps=8, weight_kg=26.0))

    result = await get_exercise_progression(conn, exercise_name="DB bench press")

    assert result["found"] is False


@pytest.mark.asyncio
async def test_log_exercises_skips_an_exercise_already_logged_identically_today():
    # The model sometimes re-sends earlier messages' exercises on a follow-up
    # (two Sept push sessions had 12 rows for 6 exercises).
    conn = _make_conn()

    await log_exercises(conn, exercises=[
        {"exercise_name": "dips", "sets": 4, "reps": 10, "weight_kg": None},
        {"exercise_name": "DB bench", "sets": 3, "reps": 8, "weight_kg": 30.0},
    ])
    second = await log_exercises(conn, exercises=[
        {"exercise_name": "dips", "sets": 4, "reps": 10, "weight_kg": None},
        {"exercise_name": "DB bench press", "sets": 3, "reps": 8, "weight_kg": 30.0},
        {"exercise_name": "pec fly", "sets": 3, "reps": 8, "weight_kg": 50.0},
    ])

    assert [e["exercise"] for e in second["exercises"]] == ["pec fly"]
    assert {s["exercise"] for s in second["skipped_duplicates"]} == {"dips", "DB bench press"}
    assert len(get_recent_sessions(conn)[0]["sets"]) == 3


@pytest.mark.asyncio
async def test_log_exercises_allow_duplicates_logs_a_genuine_repeat():
    conn = _make_conn()
    await log_exercises(conn, exercises=[{"exercise_name": "dips", "sets": 4, "reps": 10}])

    second = await log_exercises(
        conn, exercises=[{"exercise_name": "dips", "sets": 4, "reps": 10}], allow_duplicates=True,
    )

    assert len(second["exercises"]) == 1
    assert len(get_recent_sessions(conn)[0]["sets"]) == 2


@pytest.mark.asyncio
async def test_log_exercises_returns_whole_session_so_far_with_ids():
    conn = _make_conn()
    await log_exercises(conn, exercises=[{"exercise_name": "dips", "sets": 4, "reps": 10}])

    second = await log_exercises(conn, exercises=[
        {"exercise_name": "pec fly", "sets": 3, "reps": 8, "weight_kg": 50.0},
    ])

    so_far = second["session_so_far"]
    assert [e["exercise"] for e in so_far] == ["dips", "pec fly"]
    assert all(isinstance(e["log_id"], int) for e in so_far)
    assert second["exercises"][0]["log_id"] == so_far[1]["log_id"]


@pytest.mark.asyncio
async def test_gym_exercises_never_join_todays_run_session():
    # Ollie's rule (Oct 2026): a run and a gym session on the same day are
    # separate sessions — a morning run must not swallow evening pull work.
    conn = _make_conn()
    today = date.today().isoformat()
    run_id = insert_session(conn, GymSession(date=today, session_type="run"))

    result = await log_exercises(conn, exercises=[
        {"exercise_name": "pull-ups", "sets": 4, "reps": 8},
    ], session_type="pull")

    assert result["session_id"] != run_id
    assert result["session_type"] == "pull"


@pytest.mark.asyncio
async def test_run_strength_work_still_joins_todays_run_session():
    conn = _make_conn()
    today = date.today().isoformat()
    run_id = insert_session(conn, GymSession(date=today, session_type="run"))

    result = await log_exercises(conn, exercises=[
        {"exercise_name": "jump squats", "sets": 3, "reps": 8},
    ], session_type="run")

    assert result["session_id"] == run_id


@pytest.mark.asyncio
async def test_different_gym_types_on_the_same_day_merge():
    conn = _make_conn()
    today = date.today().isoformat()
    push_id = insert_session(conn, GymSession(date=today, session_type="push"))

    result = await log_exercises(conn, exercises=[
        {"exercise_name": "face pulls", "sets": 3, "reps": 10, "weight_kg": 20.0},
    ], session_type="pull")

    assert result["session_id"] == push_id


# ── correct_exercise_log / delete_exercise_log ──────────────────────────────


@pytest.mark.asyncio
async def test_correct_exercise_log_by_id_updates_only_given_fields():
    conn = _make_conn()
    logged = await log_exercises(conn, exercises=[
        {"exercise_name": "pec fly", "sets": 3, "reps": 8, "weight_kg": 50.0},
    ])
    log_id = logged["exercises"][0]["log_id"]

    result = await correct_exercise_log(conn, log_id=log_id, weight_kg=55.0)

    assert result["updated"] is True
    assert result["before"]["weight_kg"] == 50.0
    assert result["after"]["weight_kg"] == 55.0
    assert result["after"]["sets"] == 3
    row = get_recent_sessions(conn)[0]["sets"][0]
    assert (row["weight_kg"], row["sets"], row["reps"]) == (55.0, 3, 8)


@pytest.mark.asyncio
async def test_correct_exercise_log_by_name_on_an_earlier_date():
    conn = _make_conn()
    s1 = insert_session(conn, GymSession(date="2026-10-17", session_type="push"))
    insert_set(conn, ExerciseSet(session_id=s1, exercise="DB bench", sets=3, reps=8, weight_kg=30.0))

    result = await correct_exercise_log(conn, exercise_name="DB bench press", date="2026-10-17", reps=10)

    assert result["updated"] is True
    assert result["after"]["reps"] == 10


@pytest.mark.asyncio
async def test_correct_exercise_log_with_ambiguous_name_returns_candidates():
    conn = _make_conn()
    await log_exercises(conn, exercises=[
        {"exercise_name": "dips", "sets": 4, "reps": 10},
        {"exercise_name": "dips", "sets": 3, "reps": 12, "weight_kg": 10.0},
    ])

    result = await correct_exercise_log(conn, exercise_name="dips", reps=8)

    assert "updated" not in result
    assert len(result["candidates"]) == 2


@pytest.mark.asyncio
async def test_delete_exercise_log_by_id_removes_the_row():
    conn = _make_conn()
    logged = await log_exercises(conn, exercises=[
        {"exercise_name": "dips", "sets": 4, "reps": 10},
        {"exercise_name": "pec fly", "sets": 3, "reps": 8, "weight_kg": 50.0},
    ])

    result = await delete_exercise_log(conn, log_id=logged["exercises"][1]["log_id"])

    assert result["deleted"] is True
    assert [s["exercise"] for s in get_recent_sessions(conn)[0]["sets"]] == ["dips"]


@pytest.mark.asyncio
async def test_deleting_the_only_exercise_removes_the_empty_session():
    # An empty session would otherwise still count as a workout in the
    # push/pull/legs rotation.
    conn = _make_conn()
    logged = await log_exercises(conn, exercises=[{"exercise_name": "dips", "sets": 4, "reps": 10}])

    await delete_exercise_log(conn, log_id=logged["exercises"][0]["log_id"])

    assert get_recent_sessions(conn) == []


@pytest.mark.asyncio
async def test_delete_exercise_log_ambiguous_name_deletes_nothing():
    conn = _make_conn()
    await log_exercises(conn, exercises=[
        {"exercise_name": "dips", "sets": 4, "reps": 10},
        {"exercise_name": "dips", "sets": 3, "reps": 12, "weight_kg": 10.0},
    ])

    result = await delete_exercise_log(conn, exercise_name="dips")

    assert "deleted" not in result
    assert len(result["candidates"]) == 2
    assert len(get_recent_sessions(conn)[0]["sets"]) == 2


@pytest.mark.asyncio
async def test_delete_exercise_log_unknown_name_errors():
    conn = _make_conn()
    await log_exercises(conn, exercises=[{"exercise_name": "dips", "sets": 4, "reps": 10}])

    result = await delete_exercise_log(conn, exercise_name="squats")

    assert "error" in result
