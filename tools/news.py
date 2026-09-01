"""News tool — §2.4 of TOOL_CALLING_DESIGN.md.

get_news is `async def get_news(conn) -> dict`, JSON-serialisable, and never
raises — any individual source failure is reported with empty data so the
rest of the response is unaffected.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time

from services import news as news_svc
from tools.calendar import get_today_calendar_summary

logger = logging.getLogger(__name__)


async def _safe_fetch(coro, default, name: str):
    try:
        return await coro
    except Exception as exc:
        logger.warning("get_news: %s fetch failed: %s", name, exc)
        return default


def _format_chelsea(items: list[dict]) -> list[dict]:
    now = time.time()
    return [
        {
            "title": item["title"],
            "summary": item.get("summary") or None,
            "published_minutes_ago": int((now - item["published"]) / 60),
            "link": item.get("link", ""),
        }
        for item in items
    ]


def _format_world(items: list[dict]) -> list[dict]:
    return [
        {"title": item["title"], "summary": item.get("summary") or None}
        for item in items
    ]


def _format_horses(horse_map: dict[str, list[dict]]) -> dict:
    if horse_map.get("_rate_limited"):
        return {"rate_limited": True, "entries": {}}

    entries: dict[str, list[dict]] = {}
    for horse_key, races in horse_map.items():
        if horse_key.startswith("_"):
            continue
        entries[horse_key.title()] = [
            {
                "course": race.get("course", ""),
                "day_label": race.get("day_label", ""),
                "off_time": race.get("off_time", ""),
                "distance": news_svc._fmt_dist(race.get("distance_f", "")),
                "going": race.get("going", ""),
                "race_class": race.get("race_class", ""),
                "jockey": race.get("jockey", ""),
                "form": race.get("form", ""),
            }
            for race in races
        ]
    return {"rate_limited": False, "entries": entries}


async def get_news(conn: sqlite3.Connection) -> dict:
    """Chelsea FC news, world headlines, racing entries for Ollie's horses, and today's calendar."""
    chelsea_items, world_items, horse_map = await asyncio.gather(
        _safe_fetch(news_svc.fetch_chelsea_items(), [], "chelsea"),
        _safe_fetch(news_svc.fetch_world_news_items(), [], "world"),
        _safe_fetch(news_svc.fetch_all_horse_items(), {}, "horses"),
    )

    return {
        "chelsea": _format_chelsea(chelsea_items),
        "world": _format_world(world_items),
        "horses": _format_horses(horse_map),
        "today_calendar": await get_today_calendar_summary(conn),
    }


TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_news",
            "description": (
                "Get the latest Chelsea FC news, world news headlines, racing entries "
                "for Ollie's horses (today and tomorrow), and what's on today's "
                "calendar. Call this for any news, sports, racing, or Chelsea request "
                "— e.g. 'what's the news', 'any Chelsea news', 'are my horses running "
                "today', 'what's on today'."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]
