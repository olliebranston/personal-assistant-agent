"""Tests for the tool-calling loop (services.openrouter.complete) and the
ambient context builder (tools.context.build_ambient_context).

Covers TOOL_CALLING_DESIGN.md §4.1 (tool-call loop), §4.2 (error convention),
and §3.3 (ambient context block).
"""

from __future__ import annotations

import datetime
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from agents.meal import CALORIE_TARGETS, PROTEIN_TARGET_G
from services import openrouter
from storage.models import EXERCISE_SET_DDL, FOOD_LOG_DDL, GYM_SESSION_DDL, WEIGHT_LOG_DDL
from tools.context import build_ambient_context

_TZ = ZoneInfo("Europe/London")


# ── Mock OpenRouter response helpers ────────────────────────────────────────


def _make_message(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _make_response(message):
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _make_tool_call(call_id, name, arguments):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


_CALCULATE_TOOL = [{
    "type": "function",
    "function": {
        "name": "calculate",
        "description": "Add two integers together.",
        "parameters": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    },
}]


# ── §4.1 tool-call loop ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_complete_runs_tool_call_loop(monkeypatch):
    """One tool_calls response, then a plain text response — loop terminates with the text."""
    tool_call = _make_tool_call("call_1", "calculate", '{"a": 2, "b": 2}')
    first = _make_response(_make_message(content=None, tool_calls=[tool_call]))
    second = _make_response(_make_message(content="The answer is 4.", tool_calls=None))

    create_mock = AsyncMock(side_effect=[first, second])
    monkeypatch.setattr(openrouter._client.chat.completions, "create", create_mock)

    executor_calls = []

    async def tool_executor(name, args):
        executor_calls.append((name, args))
        return {"result": args["a"] + args["b"]}

    result = await openrouter.complete(
        messages=[{"role": "user", "content": "What is 2 + 2?"}],
        tools=_CALCULATE_TOOL,
        tool_executor=tool_executor,
    )

    assert result == "The answer is 4."
    assert executor_calls == [("calculate", {"a": 2, "b": 2})]
    assert create_mock.call_count == 2

    # Second call should carry the tool result back as a role:"tool" message
    second_call_messages = create_mock.call_args_list[1].kwargs["messages"]
    tool_messages = [m for m in second_call_messages if m["role"] == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "call_1"
    assert json.loads(tool_messages[0]["content"]) == {"result": 4}


@pytest.mark.asyncio
async def test_complete_converts_tool_exception_to_error(monkeypatch):
    """A tool_executor that raises must not crash complete() — §4.2 error convention."""
    tool_call = _make_tool_call("call_1", "calculate", '{"a": 1, "b": 1}')
    first = _make_response(_make_message(content=None, tool_calls=[tool_call]))
    second = _make_response(_make_message(content="Sorted.", tool_calls=None))

    create_mock = AsyncMock(side_effect=[first, second])
    monkeypatch.setattr(openrouter._client.chat.completions, "create", create_mock)

    async def tool_executor(name, args):
        raise RuntimeError("boom")

    result = await openrouter.complete(
        messages=[{"role": "user", "content": "do the thing"}],
        tools=_CALCULATE_TOOL,
        tool_executor=tool_executor,
    )

    assert result == "Sorted."
    second_call_messages = create_mock.call_args_list[1].kwargs["messages"]
    tool_messages = [m for m in second_call_messages if m["role"] == "tool"]
    assert json.loads(tool_messages[0]["content"]) == {"error": "boom"}


@pytest.mark.asyncio
async def test_complete_stops_after_max_tool_iterations(monkeypatch):
    """A model that keeps calling tools forever must be cut off by max_tool_iterations."""
    tool_call = _make_tool_call("call_x", "calculate", '{"a": 1, "b": 1}')
    response = _make_response(_make_message(content=None, tool_calls=[tool_call]))

    create_mock = AsyncMock(return_value=response)
    monkeypatch.setattr(openrouter._client.chat.completions, "create", create_mock)

    async def tool_executor(name, args):
        return {"ok": True}

    result = await openrouter.complete(
        messages=[{"role": "user", "content": "loop forever"}],
        tools=_CALCULATE_TOOL,
        tool_executor=tool_executor,
        max_tool_iterations=2,
    )

    assert create_mock.call_count == 2
    assert isinstance(result, str) and result  # fallback string, not a crash


# ── Existing callers unaffected (tools=None) ────────────────────────────────


@pytest.mark.asyncio
async def test_complete_without_tools_is_unaffected(monkeypatch):
    """tools=None must behave exactly like a plain chat completion — no tool dispatch."""
    response = _make_response(_make_message(content="Hello there", tool_calls=None))
    create_mock = AsyncMock(return_value=response)
    monkeypatch.setattr(openrouter._client.chat.completions, "create", create_mock)

    result = await openrouter.complete(
        messages=[{"role": "user", "content": "hi"}],
        system="Be nice.",
        history=[{"role": "user", "content": "earlier message"}],
    )

    assert result == "Hello there"
    assert create_mock.call_count == 1
    _, kwargs = create_mock.call_args
    assert "tools" not in kwargs


# ── §3.3 ambient context block ──────────────────────────────────────────────


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(GYM_SESSION_DDL)
    conn.execute(EXERCISE_SET_DDL)
    conn.execute(FOOD_LOG_DDL)
    conn.execute(WEIGHT_LOG_DDL)
    conn.commit()
    return conn


def _today() -> str:
    return datetime.datetime.now(tz=_TZ).date().isoformat()


def _yesterday() -> str:
    return (datetime.datetime.now(tz=_TZ).date() - datetime.timedelta(days=1)).isoformat()


def test_build_ambient_context_with_data():
    conn = _make_conn()
    today, yesterday = _today(), _yesterday()

    conn.execute(
        "INSERT INTO food_logs (date, meal_slot, description, protein_g, kcal, source) "
        "VALUES (?, 'lunch', 'chicken and rice', 50.0, 500.0, 'usda')",
        (today,),
    )
    conn.execute(
        "INSERT INTO food_logs (date, meal_slot, description, protein_g, kcal, source) "
        "VALUES (?, 'shake', 'protein shake', 30.0, 300.0, 'usda')",
        (today,),
    )
    conn.execute(
        "INSERT INTO gym_sessions (date, session_type, notes) VALUES (?, 'pull', '')",
        (yesterday,),
    )
    conn.execute("INSERT INTO weight_logs (date, weight_kg) VALUES (?, 81.4)", (yesterday,))
    conn.execute("INSERT INTO weight_logs (date, weight_kg) VALUES (?, 81.0)", (today,))
    conn.commit()

    ctx = build_ambient_context(conn)

    assert ctx["today"] == today
    assert ctx["daily_macros"]["protein_g"] == 80.0
    assert ctx["daily_macros"]["kcal"] == 800.0
    assert ctx["daily_macros"]["protein_target_g"] == PROTEIN_TARGET_G
    # No weights session logged today -> rest-day target
    assert ctx["daily_macros"]["kcal_target"] == CALORIE_TARGETS["rest"]
    assert ctx["last_workout"] == {"date": yesterday, "session_type": "pull"}
    assert ctx["open_session_today"] is None
    assert ctx["latest_weight_kg"] == 81.0

    conn.close()


def test_build_ambient_context_open_session_today():
    conn = _make_conn()
    today = _today()

    conn.execute(
        "INSERT INTO gym_sessions (date, session_type, notes) VALUES (?, 'push', '')",
        (today,),
    )
    conn.commit()

    session_id = conn.execute(
        "SELECT id FROM gym_sessions WHERE date = ?", (today,)
    ).fetchone()["id"]

    ctx = build_ambient_context(conn)

    assert ctx["open_session_today"] == {"session_type": "push", "session_id": session_id}
    assert ctx["last_workout"] == {"date": today, "session_type": "push"}
    # A push/pull/legs session today -> weights-day target
    assert ctx["daily_macros"]["kcal_target"] == CALORIE_TARGETS["weights"]

    conn.close()


def test_build_ambient_context_empty_db_returns_safe_defaults():
    conn = _make_conn()

    ctx = build_ambient_context(conn)

    assert ctx["daily_macros"] == {
        "protein_g": 0.0,
        "kcal": 0.0,
        "protein_target_g": PROTEIN_TARGET_G,
        "kcal_target": CALORIE_TARGETS["rest"],
    }
    assert ctx["last_workout"] is None
    assert ctx["open_session_today"] is None
    assert ctx["latest_weight_kg"] is None

    conn.close()
