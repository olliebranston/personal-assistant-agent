"""Morning briefing tool — §2.6 of TOOL_CALLING_DESIGN.md.

get_morning_briefing_data assembles all data for the morning briefing in one
shot, calling the existing tool functions directly (not via the registry).
Each source is wrapped so a failure in one doesn't abort the rest.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from agents.meal import CALORIE_TARGETS, PROTEIN_TARGET_G
from tools.calendar import get_calendar_events
from tools.gym import get_last_session, get_next_session_type
from tools.meal import get_daily_macros
from tools.news import get_news

logger = logging.getLogger(__name__)

_TZ = ZoneInfo("Europe/London")

_DEFAULT_NUTRITION = {
    "yesterday_protein_g": 0.0,
    "yesterday_kcal": 0.0,
    "yesterday_protein_target_g": PROTEIN_TARGET_G,
    "yesterday_kcal_target": CALORIE_TARGETS["rest"],
    "today_kcal_target": CALORIE_TARGETS["rest"],
    "is_training_day": False,
}

_DEFAULT_GYM = {
    "next_session_type": "push",
    "last_session_type": None,
    "days_since_last_session": None,
}

_DEFAULT_HORSES = {"rate_limited": False, "entries": {}}


async def get_morning_briefing_data(conn: sqlite3.Connection) -> dict:
    """Assemble all data needed for the morning briefing. Never raises."""
    now = datetime.now(tz=_TZ)
    today = now.date()
    yesterday = (today - timedelta(days=1)).isoformat()

    time_min = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    time_max = now.replace(hour=23, minute=59, second=0, microsecond=0).isoformat()

    # ── Calendar (today) ──────────────────────────────────────────────────────
    calendar_today: list[dict] = []
    try:
        cal_result = await get_calendar_events(conn, time_min=time_min, time_max=time_max)
        if "error" not in cal_result:
            calendar_today = [
                {"summary": ev["summary"], "start_time": ev["start"], "location": ev["location"]}
                for ev in cal_result.get("events", [])
            ]
    except Exception as exc:
        logger.warning("get_morning_briefing_data: calendar failed: %s", exc)

    # ── News bundle (world + chelsea + horses) ────────────────────────────────
    world_headlines: list[dict] = []
    chelsea: list[dict] = []
    horses: dict = _DEFAULT_HORSES.copy()
    try:
        news_result = await get_news(conn)
        world_headlines = news_result.get("world", [])
        chelsea = [
            {
                "title": c["title"],
                "summary": c.get("summary"),
                "published_minutes_ago": c["published_minutes_ago"],
            }
            for c in news_result.get("chelsea", [])
        ]
        raw_horses = news_result.get("horses", _DEFAULT_HORSES)
        horses = {
            "rate_limited": raw_horses.get("rate_limited", False),
            "entries": {
                horse: today_races
                for horse, races in raw_horses.get("entries", {}).items()
                # Morning briefing only ever shows races happening today —
                # tomorrow's entries would be ambiguous without a date on
                # the line, so they're dropped here rather than displayed.
                if (today_races := [
                    {
                        "course": r.get("course", ""),
                        "off_time": r.get("off_time", ""),
                        "distance": r.get("distance", ""),
                        "going": r.get("going", ""),
                        "race_class": r.get("race_class", ""),
                    }
                    for r in races
                    if r.get("day_label") == "today"
                ])
            },
        }
    except Exception as exc:
        logger.warning("get_morning_briefing_data: news failed: %s", exc)

    # ── Gym ───────────────────────────────────────────────────────────────────
    gym: dict = _DEFAULT_GYM.copy()
    try:
        next_result = await get_next_session_type(conn)
        next_type = next_result.get("session_type", "push")
        last_result = await get_last_session(conn, next_type)
        if last_result.get("found") and last_result.get("date"):
            days_since = (today - date.fromisoformat(last_result["date"])).days
            gym = {
                "next_session_type": next_type,
                "last_session_type": next_type,
                "days_since_last_session": days_since,
            }
        else:
            gym = {
                "next_session_type": next_type,
                "last_session_type": None,
                "days_since_last_session": None,
            }
    except Exception as exc:
        logger.warning("get_morning_briefing_data: gym failed: %s", exc)

    # ── Nutrition (yesterday totals + today targets) ──────────────────────────
    nutrition: dict = _DEFAULT_NUTRITION.copy()
    try:
        yesterday_macros = await get_daily_macros(conn, date=yesterday)
        today_macros = await get_daily_macros(conn)
        nutrition = {
            "yesterday_protein_g": yesterday_macros.get("protein_g", 0.0),
            "yesterday_kcal": yesterday_macros.get("kcal", 0.0),
            "yesterday_protein_target_g": yesterday_macros.get("protein_target", PROTEIN_TARGET_G),
            "yesterday_kcal_target": yesterday_macros.get("kcal_target", CALORIE_TARGETS["rest"]),
            "today_kcal_target": today_macros.get("kcal_target", CALORIE_TARGETS["rest"]),
            "is_training_day": today_macros.get("is_weights_day", False),
        }
    except Exception as exc:
        logger.warning("get_morning_briefing_data: nutrition failed: %s", exc)

    return {
        "date": today.isoformat(),
        "day_name": now.strftime("%A"),
        "calendar_today": calendar_today,
        "world_headlines": world_headlines,
        "gym": gym,
        "nutrition": nutrition,
        "chelsea": chelsea,
        "horses": horses,
    }


TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_morning_briefing_data",
            "description": (
                "Fetch all morning briefing data in one call: today's calendar events, "
                "world news headlines, Chelsea FC news, horse racing entries for Ollie's "
                "horses, gym next session type and days since last session, and yesterday's "
                "nutrition totals vs targets. Call this when Ollie asks for his briefing, "
                "morning update, or daily summary."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]
