"""Tests for the tool registry (tools/registry.py) — §4.3 of TOOL_CALLING_DESIGN.md.

This is the hub every domain's tools are dispatched through; a typo in a
dispatch key or a wrong functools.partial binding would silently break one
tool while leaving the rest fine, and nothing else in the test suite
exercises build_tool_registry directly.
"""

from __future__ import annotations

import sqlite3

import pytest

from storage.models import (
    EXERCISE_SET_DDL,
    FOOD_LOG_DDL,
    GYM_SESSION_DDL,
    MEAL_PLAN_DDL,
    USER_FOOD_DDL,
    WEIGHT_LOG_DDL,
)
from tools.registry import build_tool_registry


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


@pytest.fixture(autouse=True)
def _patch_lookup_macros(monkeypatch):
    async def _fake_lookup(query, quantity_g):
        return {"description": query, "quantity_g": quantity_g, "protein_g": 10.0, "kcal": 100.0, "source": "reference"}
    monkeypatch.setattr("tools.meal.lookup_macros", _fake_lookup)


def test_every_schema_name_has_a_dispatch_entry():
    """A tool the LLM can see but can't call (schema present, dispatch missing)
    would silently fail with 'unknown tool' every time it's invoked."""
    registry = build_tool_registry(_make_conn())
    schema_names = {schema["function"]["name"] for schema in registry.schemas}
    dispatch_names = set(registry.dispatch.keys())

    assert schema_names == dispatch_names


def test_dispatch_has_no_duplicate_schema_names():
    registry = build_tool_registry(_make_conn())
    names = [schema["function"]["name"] for schema in registry.schemas]

    assert len(names) == len(set(names))


@pytest.mark.asyncio
async def test_execute_dispatches_to_the_correct_tool():
    conn = _make_conn()
    registry = build_tool_registry(conn)

    result = await registry.execute("get_daily_macros", {})

    assert "protein_g" in result
    assert "kcal_target" in result


@pytest.mark.asyncio
async def test_execute_unknown_tool_returns_error_without_raising():
    registry = build_tool_registry(_make_conn())

    result = await registry.execute("not_a_real_tool", {})

    assert result == {"error": "unknown tool: not_a_real_tool"}


@pytest.mark.asyncio
async def test_execute_log_food_turn_totals_accumulate_within_one_registry():
    """The turn_totals dict build_tool_registry creates is bound into log_food
    via functools.partial — this exercises that actual wiring (tools/meal.py's
    own tests call log_food directly and can't catch a binding mistake here)."""
    conn = _make_conn()
    registry = build_tool_registry(conn)

    first = await registry.execute("log_food", {"food_name": "oats", "grams": 100})
    second = await registry.execute("log_food", {"food_name": "oats", "grams": 100})

    assert second["turn_totals"]["protein_g"] == pytest.approx(
        first["protein_g"] + second["protein_g"]
    )


@pytest.mark.asyncio
async def test_execute_create_reminder_receives_bound_context_and_chat_id():
    class _FakeJobQueue:
        def run_once(self, *args, **kwargs):
            return None

    class _FakeContext:
        job_queue = _FakeJobQueue()

    conn = _make_conn()
    registry = build_tool_registry(conn, telegram_context=_FakeContext(), chat_id=12345)

    result = await registry.execute("create_reminder", {
        "text": "test",
        "when": "2099-01-01T00:00:00",
    })

    assert result.get("scheduled") is True
