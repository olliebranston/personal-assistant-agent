"""Tests for the morning briefing tool (tools/briefing.py) — §2.6."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from storage.models import (
    EXERCISE_SET_DDL,
    FOOD_LOG_DDL,
    GYM_SESSION_DDL,
    MEAL_PLAN_DDL,
    WEIGHT_LOG_DDL,
)
import tools.briefing as briefing_module
import tools.calendar as calendar_tools
import tools.news as news_tools
from tools.briefing import get_morning_briefing_data


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(GYM_SESSION_DDL)
    conn.execute(EXERCISE_SET_DDL)
    conn.execute(FOOD_LOG_DDL)
    conn.execute(WEIGHT_LOG_DDL)
    conn.execute(MEAL_PLAN_DDL)
    conn.commit()
    return conn


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value
    return _inner


def _async_raise(exc):
    async def _inner(*args, **kwargs):
        raise exc
    return _inner


# ── Fixtures ─────────────────────────────────────────────────────────────────

MOCK_CALENDAR = {
    "events": [
        {
            "summary": "Team standup",
            "start": "2026-06-17T09:00:00+01:00",
            "end": "2026-06-17T09:30:00+01:00",
            "location": "Zoom",
            "all_day": False,
            "calendar": "Social",
        }
    ]
}

MOCK_NEWS = {
    "chelsea": [
        {
            "title": "Chelsea sign new striker",
            "summary": "Big news.",
            "published_minutes_ago": 90,
            "link": "https://bbc.co.uk/1",
        }
    ],
    "world": [
        {"title": "World headline", "summary": "Something happened."}
    ],
    "horses": {
        "rate_limited": False,
        "entries": {
            "Astrazar": [
                {
                    "course": "Ascot",
                    "day_label": "today",
                    "off_time": "14:30",
                    "distance": "1m2f",
                    "going": "Good",
                    "race_class": "Class 4",
                    "jockey": "J Smith",
                    "form": "21-3",
                }
            ]
        },
    },
    "today_calendar": [],
}


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_morning_briefing_data_returns_all_expected_fields(monkeypatch):
    conn = _make_conn()
    monkeypatch.setattr(calendar_tools, "get_service", lambda: MagicMock(
        **{
            "calendarList.return_value.list.return_value.execute.return_value": {"items": []},
            "events.return_value.list.return_value.execute.return_value": {
                "items": [
                    {
                        "summary": "Team standup",
                        "start": {"dateTime": "2026-06-17T09:00:00+01:00"},
                        "end": {"dateTime": "2026-06-17T09:30:00+01:00"},
                        "location": "Zoom",
                    }
                ]
            },
        }
    ))
    monkeypatch.setattr(news_tools.news_svc, "fetch_chelsea_items", _async_return([]))
    monkeypatch.setattr(news_tools.news_svc, "fetch_world_news_items", _async_return(
        [{"title": "World headline", "summary": "Detail.", "published": 0, "link": ""}]
    ))
    monkeypatch.setattr(news_tools.news_svc, "fetch_all_horse_items", _async_return({}))

    result = await get_morning_briefing_data(conn)

    assert "date" in result
    assert "day_name" in result
    assert "calendar_today" in result
    assert "world_headlines" in result
    assert "gym" in result
    assert "nutrition" in result
    assert "chelsea" in result
    assert "horses" in result

    gym = result["gym"]
    assert "next_session_type" in gym
    assert "last_session_type" in gym
    assert "days_since_last_session" in gym

    nutrition = result["nutrition"]
    assert "yesterday_protein_g" in nutrition
    assert "yesterday_kcal" in nutrition
    assert "yesterday_protein_target_g" in nutrition
    assert "yesterday_kcal_target" in nutrition
    assert "today_kcal_target" in nutrition
    assert "is_training_day" in nutrition

    horses = result["horses"]
    assert "rate_limited" in horses
    assert "entries" in horses


@pytest.mark.asyncio
async def test_yesterday_nutrition_fetched_for_correct_date(monkeypatch):
    conn = _make_conn()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    food_logs_inserted = []

    async def _mock_get_daily_macros(c, date=None):
        food_logs_inserted.append(date)
        return {
            "protein_g": 185.0,
            "kcal": 2700.0,
            "protein_target": 230,
            "kcal_target": 2950,
            "is_weights_day": False,
        }

    monkeypatch.setattr(briefing_module, "get_daily_macros", _mock_get_daily_macros)
    monkeypatch.setattr(briefing_module, "get_calendar_events", _async_return({"error": "calendar_unavailable"}))
    monkeypatch.setattr(briefing_module, "get_news", _async_return(
        {"chelsea": [], "world": [], "horses": {"rate_limited": False, "entries": {}}, "today_calendar": []}
    ))
    monkeypatch.setattr(briefing_module, "get_next_session_type", _async_return({"session_type": "push", "cycle_position": "1/3"}))
    monkeypatch.setattr(briefing_module, "get_last_session", _async_return({"found": False, "date": None, "session_type": "push", "exercises": []}))

    result = await get_morning_briefing_data(conn)

    assert result["nutrition"]["yesterday_protein_g"] == 185.0
    assert result["nutrition"]["yesterday_kcal"] == 2700.0
    assert yesterday in food_logs_inserted


@pytest.mark.asyncio
async def test_horses_filtered_to_today_only(monkeypatch):
    conn = _make_conn()

    monkeypatch.setattr(briefing_module, "get_calendar_events", _async_return({"error": "calendar_unavailable"}))
    monkeypatch.setattr(briefing_module, "get_news", _async_return({
        "chelsea": [],
        "world": [],
        "horses": {
            "rate_limited": False,
            "entries": {
                # Runs today AND tomorrow — only the today race should survive.
                "Astrazar": [
                    {"course": "Ascot", "day_label": "today", "off_time": "14:30",
                     "distance": "1m2f", "going": "Good", "race_class": "Class 4"},
                    {"course": "York", "day_label": "tomorrow", "off_time": "15:00",
                     "distance": "1m", "going": "Soft", "race_class": "Class 3"},
                ],
                # Only racing tomorrow — should disappear entirely.
                "Magnatura": [
                    {"course": "Newmarket", "day_label": "tomorrow", "off_time": "13:00",
                     "distance": "7f", "going": "Good", "race_class": "Class 2"},
                ],
            },
        },
        "today_calendar": [],
    }))
    monkeypatch.setattr(briefing_module, "get_next_session_type", _async_return({"session_type": "push", "cycle_position": "1/3"}))
    monkeypatch.setattr(briefing_module, "get_last_session", _async_return({"found": False, "date": None, "session_type": "push", "exercises": []}))

    result = await get_morning_briefing_data(conn)

    entries = result["horses"]["entries"]
    assert set(entries.keys()) == {"Astrazar"}
    assert len(entries["Astrazar"]) == 1
    assert entries["Astrazar"][0]["course"] == "Ascot"
    assert "day_label" not in entries["Astrazar"][0]


@pytest.mark.asyncio
async def test_source_failure_returns_safe_defaults(monkeypatch):
    conn = _make_conn()

    monkeypatch.setattr(briefing_module, "get_calendar_events", _async_raise(RuntimeError("calendar down")))
    monkeypatch.setattr(briefing_module, "get_news", _async_raise(RuntimeError("news down")))
    monkeypatch.setattr(briefing_module, "get_next_session_type", _async_raise(RuntimeError("gym down")))
    monkeypatch.setattr(briefing_module, "get_last_session", _async_raise(RuntimeError("gym down")))
    monkeypatch.setattr(briefing_module, "get_daily_macros", _async_raise(RuntimeError("nutrition down")))

    result = await get_morning_briefing_data(conn)

    assert result["calendar_today"] == []
    assert result["world_headlines"] == []
    assert result["chelsea"] == []
    assert result["horses"] == {"rate_limited": False, "entries": {}}
    assert result["gym"]["next_session_type"] == "push"
    assert result["gym"]["last_session_type"] is None
    assert result["gym"]["days_since_last_session"] is None
    assert result["nutrition"]["yesterday_protein_g"] == 0.0
    assert result["nutrition"]["is_training_day"] is False
