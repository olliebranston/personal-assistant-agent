"""Tests for what main._handle_tool_calling keeps in conversation memory.

Memory used to hold only the user's text and Robin's reply, never which tools
ran — so on a follow-up message the model couldn't tell an exercise or food
had already been logged, and sometimes logged it again. Every test here is
offline: complete() is replaced by a scripted fake that drives the real tool
registry, so no OpenRouter call is ever made.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

import main
from services import memory
from storage.models import (
    EXERCISE_SET_DDL,
    FOOD_LOG_DDL,
    GYM_SESSION_DDL,
    MEAL_PLAN_DDL,
    RUN_LOG_DDL,
    USER_FOOD_DDL,
    WEIGHT_LOG_DDL,
)

_UID = 4242


class _NoCloseConn:
    """Wraps a shared in-memory connection so main's conn.close() doesn't
    destroy it between the two turns of a test."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for ddl in (GYM_SESSION_DDL, EXERCISE_SET_DDL, RUN_LOG_DDL, FOOD_LOG_DDL,
                WEIGHT_LOG_DDL, MEAL_PLAN_DDL, USER_FOOD_DDL):
        conn.execute(ddl)
    conn.commit()
    return conn


def _update(text: str):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=_UID),
        effective_chat=SimpleNamespace(id=_UID),
        message=SimpleNamespace(text=text),
    )


@pytest.fixture
def harness(monkeypatch):
    conn = _make_conn()
    memory._store.pop(_UID, None)
    monkeypatch.setattr(main, "get_connection", lambda: _NoCloseConn(conn))

    async def _no_reply(message, text):
        return None

    monkeypatch.setattr(main, "reply_formatted", _no_reply)
    yield conn
    memory._store.pop(_UID, None)


@pytest.mark.asyncio
async def test_turn_that_logs_leaves_a_note_of_what_was_done(monkeypatch, harness):
    async def fake_complete(messages, system, history, tools, tool_executor):
        await tool_executor("log_exercises", {
            "exercises": [{"exercise_name": "dips", "sets": 4, "reps": 10}],
            "session_type": "push",
        })
        return "Logged dips."

    monkeypatch.setattr(main, "complete", fake_complete)

    await main._handle_tool_calling(_update("dips 4x10"), None, "dips 4x10")

    history = memory.get(_UID)
    assert [m["role"] for m in history] == ["user", "assistant", "system"]
    assert "dips 4x10" in history[-1]["content"]


@pytest.mark.asyncio
async def test_next_turn_sees_the_note_in_its_history(monkeypatch, harness):
    seen_histories = []

    async def fake_complete(messages, system, history, tools, tool_executor):
        seen_histories.append(list(history))
        if len(seen_histories) == 1:
            await tool_executor("log_food", {"food_name": "chicken breast", "grams": 200})
        return "ok"

    async def fake_lookup(query, quantity_g):
        return {"description": query, "quantity_g": quantity_g, "protein_g": 62.0,
                "kcal": 330.0, "source": "reference"}

    monkeypatch.setattr(main, "complete", fake_complete)
    monkeypatch.setattr("tools.meal.lookup_macros", fake_lookup)

    await main._handle_tool_calling(_update("200g chicken"), None, "200g chicken")
    await main._handle_tool_calling(_update("and some rice"), None, "and some rice")

    notes = [m["content"] for m in seen_histories[1] if m["role"] == "system"]
    assert any("200g chicken breast" in n for n in notes)


@pytest.mark.asyncio
async def test_turn_with_only_reads_adds_no_note(monkeypatch, harness):
    async def fake_complete(messages, system, history, tools, tool_executor):
        await tool_executor("get_session_plan", {"session_type": "push"})
        return "Here's push."

    monkeypatch.setattr(main, "complete", fake_complete)

    await main._handle_tool_calling(_update("what's push"), None, "what's push")

    assert [m["role"] for m in memory.get(_UID)] == ["user", "assistant"]
