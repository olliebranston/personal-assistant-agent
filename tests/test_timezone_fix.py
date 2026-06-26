"""Tests for the Europe/London date-boundary fix (commit 3).

Reproduces the exact bug class fixed in tools/gym.py by commit fcd1d4b: a
server running in UTC computes "today" via bare date.today()/datetime.now(),
which is wrong for roughly an hour every night during BST (UTC+1) — e.g. at
00:30 Europe/London (BST) it's still 23:30 the previous day in UTC. This
file picks exactly that instant and asserts the UK calendar date wins.

Fully offline — no network, no OpenRouter, no real clock dependency.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

import agents.meal as agents_meal
import tools.gym as gym_tools
import tools.meal as meal_tools
from storage.models import (
    EXERCISE_SET_DDL,
    FOOD_LOG_DDL,
    GYM_SESSION_DDL,
    MEAL_PLAN_DDL,
    USER_FOOD_DDL,
    WEIGHT_LOG_DDL,
    get_food_logs_for_date,
)

# The "real" instant: 2026-06-30 23:30 UTC == 2026-07-01 00:30 Europe/London (BST).
_BOUNDARY_UTC = datetime(2026, 6, 30, 23, 30, tzinfo=ZoneInfo("UTC"))
_EXPECTED_UK_DATE = date(2026, 7, 1)
_WRONG_UTC_DATE = date(2026, 6, 30)


class _FixedClock(datetime):
    """Stand-in for datetime.datetime whose .now(tz) always returns the boundary instant."""

    @classmethod
    def now(cls, tz=None):
        return _BOUNDARY_UTC.astimezone(tz) if tz is not None else _BOUNDARY_UTC


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(GYM_SESSION_DDL)
    conn.execute(EXERCISE_SET_DDL)
    conn.execute(FOOD_LOG_DDL)
    conn.execute(WEIGHT_LOG_DDL)
    conn.execute(MEAL_PLAN_DDL)
    conn.execute(USER_FOOD_DDL)
    conn.commit()
    return conn


async def _fake_lookup_macros(query: str, quantity_g: float) -> dict:
    return {"description": query, "quantity_g": quantity_g, "protein_g": 10.0, "kcal": 100.0, "source": "reference"}


def test_tools_meal_today_uses_uk_date_not_utc_date(monkeypatch):
    monkeypatch.setattr(meal_tools, "datetime", _FixedClock)

    assert meal_tools._today() == _EXPECTED_UK_DATE
    assert meal_tools._today() != _WRONG_UTC_DATE


def test_agents_meal_today_uses_uk_date_not_utc_date(monkeypatch):
    monkeypatch.setattr(agents_meal, "datetime", _FixedClock)

    assert agents_meal._today() == _EXPECTED_UK_DATE
    assert agents_meal._today() != _WRONG_UTC_DATE


def test_tools_gym_weekly_summary_week_start_uses_uk_date(monkeypatch):
    monkeypatch.setattr(gym_tools, "datetime", _FixedClock)
    conn = _make_conn()

    import asyncio
    result = asyncio.run(gym_tools.get_weekly_gym_summary(conn))

    # 2026-07-01 is a Wednesday -> week start (Monday) is 2026-06-29.
    assert result["week_start"] == "2026-06-29"


@pytest.mark.asyncio
async def test_log_food_writes_under_uk_date_at_the_boundary(monkeypatch):
    monkeypatch.setattr(meal_tools, "datetime", _FixedClock)
    monkeypatch.setattr(meal_tools, "lookup_macros", _fake_lookup_macros)
    conn = _make_conn()

    await meal_tools.log_food(conn, food_name="late night snack", grams=50)

    logged_today = get_food_logs_for_date(conn, _EXPECTED_UK_DATE.isoformat())
    logged_wrong_day = get_food_logs_for_date(conn, _WRONG_UTC_DATE.isoformat())
    assert len(logged_today) == 1
    assert len(logged_wrong_day) == 0
