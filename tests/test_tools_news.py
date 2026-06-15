"""Tests for the news tool (tools/news.py) — TOOL_CALLING_DESIGN.md §2.4.

All external sources (RSS feeds, Racing API, Google Calendar) are mocked —
no real network calls.
"""

from __future__ import annotations

import time

import pytest

import tools.news as news_tools
from tools.news import get_news


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value
    return _inner


def _async_raise(exc):
    async def _inner(*args, **kwargs):
        raise exc
    return _inner


@pytest.mark.asyncio
async def test_chelsea_rss_failure_returns_empty_list_without_failing_tool(monkeypatch):
    world_items = [
        {
            "title": "World story",
            "summary": "Something happened.",
            "published": time.time(),
            "link": "https://bbc.co.uk/world",
        }
    ]
    horse_map = {
        "ASTRAZAR": [
            {
                "horse": "Astrazar",
                "horse_id": "h1",
                "day_label": "today",
                "course": "Ascot",
                "date": "2026-06-15",
                "off_time": "15:00",
                "race_name": "Handicap",
                "distance_f": "8.0",
                "going": "Soft",
                "race_class": "Class 3",
                "jockey": "T Jones",
                "form": "4-5-1",
            }
        ]
    }
    calendar_events = {
        "events": [
            {
                "summary": "Gym",
                "start": "2026-06-15T18:00:00+01:00",
                "end": "2026-06-15T19:00:00+01:00",
                "location": None,
                "all_day": False,
                "calendar": "Ollie",
            }
        ]
    }

    monkeypatch.setattr(news_tools.news_svc, "fetch_chelsea_items", _async_raise(RuntimeError("RSS down")))
    monkeypatch.setattr(news_tools.news_svc, "fetch_world_news_items", _async_return(world_items))
    monkeypatch.setattr(news_tools.news_svc, "fetch_all_horse_items", _async_return(horse_map))
    monkeypatch.setattr(news_tools, "get_calendar_events", _async_return(calendar_events))

    result = await get_news(conn=None)

    assert result["chelsea"] == []
    assert result["world"] == [{"title": "World story", "summary": "Something happened."}]
    assert "Astrazar" in result["horses"]["entries"]
    assert result["today_calendar"] == [
        {"summary": "Gym", "start_time": "2026-06-15T18:00:00+01:00", "location": None}
    ]


@pytest.mark.asyncio
async def test_horse_entries_include_formatted_distance(monkeypatch):
    horse_map = {
        "DIAMOND BAY": [
            {
                "horse": "Diamond Bay",
                "horse_id": "hb123",
                "day_label": "today",
                "course": "Newmarket",
                "date": "2026-06-15",
                "off_time": "14:30",
                "race_name": "Maiden Stakes",
                "distance_f": "10.0",
                "going": "Good",
                "race_class": "Class 4",
                "jockey": "J Smith",
                "form": "1-2-3",
            }
        ]
    }

    monkeypatch.setattr(news_tools.news_svc, "fetch_chelsea_items", _async_return([]))
    monkeypatch.setattr(news_tools.news_svc, "fetch_world_news_items", _async_return([]))
    monkeypatch.setattr(news_tools.news_svc, "fetch_all_horse_items", _async_return(horse_map))
    monkeypatch.setattr(news_tools, "get_calendar_events", _async_return({"error": "calendar_unavailable"}))

    result = await get_news(conn=None)

    horses = result["horses"]
    assert horses["rate_limited"] is False
    race = horses["entries"]["Diamond Bay"][0]
    assert race["distance"] == "1m2f"
    assert race["course"] == "Newmarket"
    assert race["day_label"] == "today"
    assert race["off_time"] == "14:30"
    assert race["going"] == "Good"
    assert race["race_class"] == "Class 4"
    assert race["jockey"] == "J Smith"
    assert race["form"] == "1-2-3"


@pytest.mark.asyncio
async def test_today_calendar_empty_when_calendar_unavailable(monkeypatch):
    monkeypatch.setattr(news_tools.news_svc, "fetch_chelsea_items", _async_return([]))
    monkeypatch.setattr(news_tools.news_svc, "fetch_world_news_items", _async_return([]))
    monkeypatch.setattr(news_tools.news_svc, "fetch_all_horse_items", _async_return({}))
    monkeypatch.setattr(news_tools, "get_calendar_events", _async_return({"error": "calendar_unavailable"}))

    result = await get_news(conn=None)

    assert result["today_calendar"] == []


@pytest.mark.asyncio
async def test_published_minutes_ago_calculated_relative_to_now(monkeypatch):
    chelsea_items = [
        {
            "title": "Chelsea sign new striker",
            "summary": "Big news for the Blues.",
            "published": time.time() - 600,  # 10 minutes ago
            "link": "https://bbc.co.uk/example",
        }
    ]

    monkeypatch.setattr(news_tools.news_svc, "fetch_chelsea_items", _async_return(chelsea_items))
    monkeypatch.setattr(news_tools.news_svc, "fetch_world_news_items", _async_return([]))
    monkeypatch.setattr(news_tools.news_svc, "fetch_all_horse_items", _async_return({}))
    monkeypatch.setattr(news_tools, "get_calendar_events", _async_return({"error": "calendar_unavailable"}))

    result = await get_news(conn=None)

    item = result["chelsea"][0]
    assert item["title"] == "Chelsea sign new striker"
    assert item["summary"] == "Big news for the Blues."
    assert item["published_minutes_ago"] == 10
    assert item["link"] == "https://bbc.co.uk/example"
