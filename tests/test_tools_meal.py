"""Tests for the meal/nutrition tools (tools/meal.py) — TOOL_CALLING_DESIGN.md §2.2."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from storage.models import (
    EXERCISE_SET_DDL,
    FOOD_LOG_DDL,
    GYM_SESSION_DDL,
    MEAL_PLAN_DDL,
    USER_FOOD_DDL,
    WEIGHT_LOG_DDL,
    get_food_logs_for_date,
    log_weight as db_log_weight,
)
from tools.meal import (
    correct_food_log,
    get_daily_macros,
    get_food_log,
    get_weight_trend,
    log_food,
    log_weight,
    set_user_food_macros,
)


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
    """Stand-in for services.nutrition.lookup_macros using the real fallback table,
    without making a network call to USDA."""
    from services.nutrition import _fallback_lookup

    fallback = _fallback_lookup(query)
    if fallback:
        protein_per_100g, kcal_per_100g = fallback
        scale = quantity_g / 100.0
        return {
            "description": query,
            "quantity_g": quantity_g,
            "protein_g": round(protein_per_100g * scale, 1),
            "kcal": round(kcal_per_100g * scale, 0),
            "source": "reference",
        }
    return {
        "description": query,
        "quantity_g": quantity_g,
        "protein_g": 0.0,
        "kcal": 0.0,
        "source": "estimated",
    }


@pytest.fixture(autouse=True)
def _patch_lookup_macros(monkeypatch):
    monkeypatch.setattr("tools.meal.lookup_macros", _fake_lookup_macros)


# ── log_food ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_log_food_writes_immediately_and_returns_correct_macros():
    conn = _make_conn()
    today = date.today().isoformat()

    result = await log_food(conn, food_name="Greek yoghurt", grams=200, meal_slot="breakfast")

    assert result["logged"] is True
    assert result["protein_g"] == 20.0  # 10.0g/100g * 200g
    assert result["kcal"] == 118.0      # 59.0kcal/100g * 200g
    assert result["meal_slot"] == "breakfast"

    logs = get_food_logs_for_date(conn, today)
    assert len(logs) == 1
    assert logs[0]["description"] == "200g Greek yoghurt"
    assert logs[0]["protein_g"] == 20.0
    assert logs[0]["kcal"] == 118.0

    assert result["daily_totals"]["protein_g"] == 20.0
    assert result["daily_totals"]["kcal"] == 118.0
    assert result["daily_totals"]["protein_target"] == 230
    assert result["daily_totals"]["kcal_target"] == 2950  # rest day, no gym session today


@pytest.mark.asyncio
async def test_log_food_returns_source_field():
    conn = _make_conn()

    reference = await log_food(conn, food_name="oats", grams=80)
    assert reference["source"] == "reference"

    estimated = await log_food(conn, food_name="some completely unknown food xyz", grams=100)
    assert estimated["source"] == "estimated"
    assert estimated["protein_g"] == 0.0
    assert estimated["kcal"] == 0.0
    assert estimated["needs_input"] is True
    assert "needs_input" not in reference


# ── user_foods calibration table ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_log_food_uses_user_override_and_never_calls_lookup_macros(monkeypatch):
    from storage.models import upsert_user_food

    conn = _make_conn()
    upsert_user_food(conn, "my weird protein bar", 30.0, 200.0, "2026-06-01T00:00:00")

    async def _raise_if_called(*args, **kwargs):
        raise AssertionError("lookup_macros should not be called when a user override exists")

    monkeypatch.setattr("tools.meal.lookup_macros", _raise_if_called)

    result = await log_food(conn, food_name="my weird protein bar", grams=50)

    assert result["source"] == "user_defined"
    assert result["protein_g"] == 15.0  # 30.0g/100g * 50g
    assert result["kcal"] == 100.0      # 200.0kcal/100g * 50g


@pytest.mark.asyncio
async def test_set_user_food_macros_stores_and_fixes_todays_entry():
    conn = _make_conn()

    logged = await log_food(conn, food_name="some completely unknown food xyz", grams=100)
    assert logged["needs_input"] is True

    result = await set_user_food_macros(
        conn, food_name="some completely unknown food xyz",
        protein_per_100g=20.0, kcal_per_100g=150.0,
    )

    assert result["stored"] is True
    assert result["updated_log"]["protein_g"] == 20.0  # 20.0g/100g * 100g
    assert result["updated_log"]["kcal"] == 150.0
    assert result["daily_totals"]["protein_g"] == 20.0


@pytest.mark.asyncio
async def test_set_user_food_macros_calibration_is_used_on_next_log(monkeypatch):
    conn = _make_conn()
    await log_food(conn, food_name="some completely unknown food xyz", grams=100)
    await set_user_food_macros(
        conn, food_name="some completely unknown food xyz",
        protein_per_100g=20.0, kcal_per_100g=150.0,
    )

    async def _raise_if_called(*args, **kwargs):
        raise AssertionError("lookup_macros should not be called once calibrated")

    monkeypatch.setattr("tools.meal.lookup_macros", _raise_if_called)

    result = await log_food(conn, food_name="some completely unknown food xyz", grams=50)

    assert result["source"] == "user_defined"
    assert result["protein_g"] == 10.0  # 20.0g/100g * 50g


@pytest.mark.asyncio
async def test_set_user_food_macros_falls_back_to_parsing_legacy_row_with_null_grams():
    # Simulates a pre-migration row (grams/food_name columns NULL) — the only
    # path that still needs to parse the description string.
    conn = _make_conn()
    conn.execute(
        "INSERT INTO food_logs (date, meal_slot, description, protein_g, kcal, source) "
        "VALUES (?, 'other', '150g mystery meat', 0.0, 0.0, 'estimated')",
        (date.today().isoformat(),),
    )
    conn.commit()

    result = await set_user_food_macros(
        conn, food_name="mystery meat", protein_per_100g=20.0, kcal_per_100g=200.0,
    )

    assert result["updated_log"]["protein_g"] == 30.0  # 20.0g/100g * 150g (parsed from description)
    assert result["updated_log"]["kcal"] == 300.0


# ── correct_food_log ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_correct_food_log_updates_quantity_and_reruns_lookup():
    conn = _make_conn()
    today = date.today().isoformat()

    await log_food(conn, food_name="Greek yoghurt", grams=200, meal_slot="breakfast")

    result = await correct_food_log(conn, food_name="yoghurt", field="quantity_g", new_value=300)

    assert result["updated"] is True
    assert result["before"]["protein_g"] == 20.0
    assert result["before"]["kcal"] == 118.0
    assert result["after"]["protein_g"] == 30.0   # 10.0g/100g * 300g
    assert result["after"]["kcal"] == 177.0       # 59.0kcal/100g * 300g
    assert result["after"]["source"] == "reference"
    assert result["after"]["description"] == "300g Greek yoghurt"

    assert result["daily_totals"]["protein_g"] == 30.0
    assert result["daily_totals"]["kcal"] == 177.0

    logs = get_food_logs_for_date(conn, today)
    assert len(logs) == 1
    assert logs[0]["description"] == "300g Greek yoghurt"


@pytest.mark.asyncio
async def test_correct_food_log_empty_food_name_matches_most_recent():
    conn = _make_conn()

    await log_food(conn, food_name="Greek yoghurt", grams=200, meal_slot="breakfast")
    await log_food(conn, food_name="oats", grams=80, meal_slot="breakfast")

    result = await correct_food_log(conn, food_name="", field="quantity_g", new_value=100)

    assert result["updated"] is True
    assert result["before"]["description"] == "80g oats"
    assert result["after"]["description"] == "100g oats"
    assert result["after"]["protein_g"] == 17.0   # 17.0g/100g * 100g
    assert result["after"]["kcal"] == 389.0       # 389.0kcal/100g * 100g


# ── get_food_log ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_food_log_returns_all_entries_for_date_with_totals():
    conn = _make_conn()
    today = date.today().isoformat()

    await log_food(conn, food_name="Greek yoghurt", grams=200, meal_slot="breakfast")
    await log_food(conn, food_name="oats", grams=80, meal_slot="breakfast")

    result = await get_food_log(conn, date=today)

    assert result["date"] == today
    assert len(result["entries"]) == 2
    descriptions = {e["description"] for e in result["entries"]}
    assert descriptions == {"200g Greek yoghurt", "80g oats"}

    assert result["totals"]["protein_g"] == 20.0 + 13.6   # 17.0g/100g * 80g = 13.6
    assert result["totals"]["kcal"] == 118.0 + 311.0      # 389.0kcal/100g * 80g = 311.2 -> 311.0


# ── get_daily_macros ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_daily_macros_returns_correct_targets():
    conn = _make_conn()

    result = await get_daily_macros(conn)

    assert result["protein_target"] == 230
    assert result["kcal_target"] == 2950  # rest day, no gym session today
    assert result["is_weights_day"] is False
    assert result["protein_g"] == 0.0
    assert result["kcal"] == 0.0


# ── log_weight ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_log_weight_rejects_values_outside_range():
    conn = _make_conn()

    too_light = await log_weight(conn, weight_kg=30)
    assert "error" in too_light

    too_heavy = await log_weight(conn, weight_kg=300)
    assert "error" in too_heavy

    ok = await log_weight(conn, weight_kg=104.2)
    assert ok["logged"] is True
    assert ok["weight_kg"] == 104.2


# ── get_weight_trend ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_weight_trend_returns_entries_newest_first():
    conn = _make_conn()
    today = date.today()

    db_log_weight(conn, (today - timedelta(days=14)).isoformat(), 100.0)
    db_log_weight(conn, (today - timedelta(days=7)).isoformat(), 102.0)
    db_log_weight(conn, today.isoformat(), 104.0)

    result = await get_weight_trend(conn)

    assert [e["date"] for e in result["entries"]] == [
        today.isoformat(),
        (today - timedelta(days=7)).isoformat(),
        (today - timedelta(days=14)).isoformat(),
    ]
    assert result["latest_weight_kg"] == 104.0
    assert result["trend_kg_per_week"] == 2.0
