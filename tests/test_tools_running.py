"""Tests for the running tools (tools/running.py) — TOOL_CALLING_DESIGN.md §2.8."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from storage.models import EXERCISE_SET_DDL, GYM_SESSION_DDL, RUN_LOG_DDL
from tools.running import (
    get_recent_runs_tool,
    get_weekly_running_summary,
    log_run,
)


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(GYM_SESSION_DDL)
    conn.execute(EXERCISE_SET_DDL)
    conn.execute(RUN_LOG_DDL)
    conn.commit()
    return conn


# ── log_run ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_log_run_computes_pace_from_distance_and_duration():
    conn = _make_conn()

    result = await log_run(conn, run_type="tempo", distance_km=5.0, duration_min=25.0)

    assert result["logged"] is True
    assert result["distance_km"] == 5.0
    assert result["duration_min"] == 25.0
    assert result["avg_pace_per_km"] == "5:00/km"


@pytest.mark.asyncio
async def test_log_run_without_distance_or_duration_has_no_pace():
    conn = _make_conn()

    result = await log_run(conn, run_type="run-strength", notes="prehab session")

    assert result["logged"] is True
    assert result["avg_pace_per_km"] is None


@pytest.mark.asyncio
async def test_log_run_creates_todays_gym_session_as_run_type():
    conn = _make_conn()

    result = await log_run(conn, run_type="easy", distance_km=5.0, duration_min=30.0)

    row = conn.execute("SELECT * FROM gym_sessions WHERE id = ?", (result["session_id"],)).fetchone()
    assert row["session_type"] == "run"
    assert row["date"] == date.today().isoformat()


@pytest.mark.asyncio
async def test_log_run_appends_to_already_open_session_today():
    conn = _make_conn()

    first = await log_run(conn, run_type="easy", distance_km=5.0, duration_min=30.0)
    second = await log_run(conn, run_type="interval", distance_km=3.0, duration_min=15.0)

    assert second["session_id"] == first["session_id"]
    session_count = conn.execute("SELECT COUNT(*) AS c FROM gym_sessions").fetchone()["c"]
    assert session_count == 1


# ── get_recent_runs ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_recent_runs_filters_by_type():
    conn = _make_conn()
    await log_run(conn, run_type="easy", distance_km=5.0, duration_min=30.0)
    await log_run(conn, run_type="race", distance_km=5.0, duration_min=24.5)

    result = await get_recent_runs_tool(conn, run_type="race")

    assert len(result["runs"]) == 1
    assert result["runs"][0]["run_type"] == "race"


@pytest.mark.asyncio
async def test_get_recent_runs_returns_newest_first():
    conn = _make_conn()
    await log_run(conn, run_type="easy", distance_km=5.0, duration_min=30.0)
    await log_run(conn, run_type="tempo", distance_km=6.0, duration_min=32.0)

    result = await get_recent_runs_tool(conn)

    assert [r["run_type"] for r in result["runs"]] == ["tempo", "easy"]


# ── get_weekly_running_summary ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_weekly_running_summary_counts_this_week_only():
    conn = _make_conn()
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    last_week = week_start - timedelta(days=1)

    conn.execute(
        "INSERT INTO run_logs (date, run_type, distance_km, duration_min) VALUES (?, ?, ?, ?)",
        (week_start.isoformat(), "easy", 5.0, 30.0),
    )
    conn.execute(
        "INSERT INTO run_logs (date, run_type, distance_km, duration_min) VALUES (?, ?, ?, ?)",
        (last_week.isoformat(), "long", 15.0, 90.0),
    )
    conn.commit()

    result = await get_weekly_running_summary(conn)

    assert result["week_start"] == week_start.isoformat()
    assert result["run_count"] == 1
    assert result["total_distance_km"] == 5.0
    assert result["sessions_by_type"] == {"easy": 1}
