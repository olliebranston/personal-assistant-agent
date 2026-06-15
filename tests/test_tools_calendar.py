"""Tests for the calendar tools (tools/calendar.py) — TOOL_CALLING_DESIGN.md §2.3.

All tests mock the Google Calendar API service object — no real API calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tools.calendar import create_calendar_event, get_calendar_events


def _mock_service_for_list(calendar_items: list[dict], event_items: list[dict]) -> MagicMock:
    service = MagicMock()
    service.calendarList.return_value.list.return_value.execute.return_value = {
        "items": calendar_items
    }
    service.events.return_value.list.return_value.execute.return_value = {
        "items": event_items
    }
    return service


def _mock_service_for_insert(calendar_items: list[dict], created_event: dict) -> MagicMock:
    service = MagicMock()
    service.calendarList.return_value.list.return_value.execute.return_value = {
        "items": calendar_items
    }
    service.events.return_value.insert.return_value.execute.return_value = created_event
    return service


# ── get_calendar_events ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_calendar_events_all_day_true_for_date_only(monkeypatch):
    service = _mock_service_for_list(
        calendar_items=[{"id": "primary", "summary": "Ollie"}],
        event_items=[
            {
                "summary": "Spain trip",
                "start": {"date": "2026-09-11"},
                "end": {"date": "2026-09-19"},
            }
        ],
    )
    monkeypatch.setattr("tools.calendar.get_service", lambda: service)

    result = await get_calendar_events(
        conn=None,
        time_min="2026-09-01T00:00:00+01:00",
        time_max="2026-09-30T23:59:59+01:00",
    )

    assert "error" not in result
    assert len(result["events"]) == 1
    event = result["events"][0]
    assert event["summary"] == "Spain trip"
    assert event["start"] == "2026-09-11"
    assert event["end"] == "2026-09-19"
    assert event["all_day"] is True
    assert event["location"] is None
    assert event["calendar"] == "Ollie"


@pytest.mark.asyncio
async def test_get_calendar_events_all_day_false_for_datetime(monkeypatch):
    service = _mock_service_for_list(
        calendar_items=[{"id": "primary", "summary": "Ollie"}],
        event_items=[
            {
                "summary": "Dentist",
                "start": {"dateTime": "2026-06-16T10:00:00+01:00"},
                "end": {"dateTime": "2026-06-16T10:30:00+01:00"},
                "location": "Smile Clinic",
            }
        ],
    )
    monkeypatch.setattr("tools.calendar.get_service", lambda: service)

    result = await get_calendar_events(
        conn=None,
        time_min="2026-06-16T00:00:00+01:00",
        time_max="2026-06-16T23:59:59+01:00",
    )

    assert "error" not in result
    event = result["events"][0]
    assert event["start"] == "2026-06-16T10:00:00+01:00"
    assert event["all_day"] is False
    assert event["location"] == "Smile Clinic"


@pytest.mark.asyncio
async def test_get_calendar_events_returns_error_on_auth_failure(monkeypatch):
    def _raise():
        raise FileNotFoundError("token.json not found")

    monkeypatch.setattr("tools.calendar.get_service", _raise)

    result = await get_calendar_events(
        conn=None,
        time_min="2026-06-16T00:00:00+01:00",
        time_max="2026-06-16T23:59:59+01:00",
    )

    assert result == {"error": "calendar_unavailable"}


# ── create_calendar_event ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_calendar_event_all_day_false_builds_datetime_body(monkeypatch):
    service = _mock_service_for_insert(
        calendar_items=[{"id": "social-id", "summary": "Social"}, {"id": "primary", "summary": "Ollie"}],
        created_event={
            "summary": "Dentist",
            "start": {"dateTime": "2026-06-16T10:00:00", "timeZone": "Europe/London"},
            "end": {"dateTime": "2026-06-16T10:30:00", "timeZone": "Europe/London"},
        },
    )
    monkeypatch.setattr("tools.calendar.get_service", lambda: service)

    result = await create_calendar_event(
        conn=None,
        summary="Dentist",
        start="2026-06-16T10:00:00",
        end="2026-06-16T10:30:00",
        location="",
        all_day=False,
    )

    assert result["created"] is True
    assert result["summary"] == "Dentist"
    assert result["start"] == "2026-06-16T10:00:00"
    assert result["end"] == "2026-06-16T10:30:00"
    assert result["calendar"] == "Social"

    _, insert_kwargs = service.events.return_value.insert.call_args
    assert insert_kwargs["calendarId"] == "social-id"

    _, kwargs = service.events.return_value.insert.call_args
    body = kwargs["body"]
    assert body["start"] == {"dateTime": "2026-06-16T10:00:00", "timeZone": "Europe/London"}
    assert body["end"] == {"dateTime": "2026-06-16T10:30:00", "timeZone": "Europe/London"}


@pytest.mark.asyncio
async def test_create_calendar_event_all_day_true_builds_date_body(monkeypatch):
    service = _mock_service_for_insert(
        calendar_items=[{"id": "primary", "summary": "Ollie"}],
        created_event={
            "summary": "Spain trip",
            "start": {"date": "2026-09-11"},
            "end": {"date": "2026-09-19"},
        },
    )
    monkeypatch.setattr("tools.calendar.get_service", lambda: service)

    result = await create_calendar_event(
        conn=None,
        summary="Spain trip",
        start="2026-09-11",
        end="2026-09-19",
        location="",
        all_day=True,
    )

    assert result["created"] is True
    assert result["start"] == "2026-09-11"
    assert result["end"] == "2026-09-19"

    _, kwargs = service.events.return_value.insert.call_args
    body = kwargs["body"]
    assert body["start"] == {"date": "2026-09-11"}
    assert body["end"] == {"date": "2026-09-19"}
    assert "timeZone" not in body["start"]
    assert "timeZone" not in body["end"]


@pytest.mark.asyncio
async def test_create_calendar_event_returns_error_on_create_failure(monkeypatch):
    service = MagicMock()
    service.calendarList.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "primary", "summary": "Ollie"}]
    }
    service.events.return_value.insert.return_value.execute.side_effect = RuntimeError("boom")
    monkeypatch.setattr("tools.calendar.get_service", lambda: service)

    result = await create_calendar_event(
        conn=None,
        summary="Dentist",
        start="2026-06-16T10:00:00",
        end="2026-06-16T10:30:00",
    )

    assert result == {"error": "create_failed"}
