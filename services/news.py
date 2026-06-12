"""News and sports data service.

Data sources:
- Chelsea FC: BBC Sport RSS (48-hour window), Sky Sports fallback
- Horse racing: The Racing API free tier — today/tomorrow racecards scanned for
  Ollie's horses. Historical results require a Pro Plan upgrade.

All results cached in memory for 1 hour.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from email.utils import parsedate_to_datetime

import feedparser
import httpx
from bs4 import BeautifulSoup

import config

logger = logging.getLogger(__name__)

# ── In-memory cache ───────────────────────────────────────────────────────────

_CACHE: dict[str, tuple[float, object]] = {}
_CACHE_TTL = 3600  # 1 hour


def _get_cache(key: str):
    entry = _CACHE.get(key)
    if entry and time.time() - entry[0] < _CACHE_TTL:
        return entry[1]
    return None


def _set_cache(key: str, value) -> None:
    _CACHE[key] = (time.time(), value)


# ── Constants ─────────────────────────────────────────────────────────────────

BBC_CHELSEA_RSS = "https://feeds.bbci.co.uk/sport/football/chelsea/rss.xml"
_SKY_CHELSEA_RSS = "https://www.skysports.com/rss/12040"

_RACING_API_BASE = "https://api.theracingapi.com/v1"

HORSES = [
    "DIAMOND BAY",
    "SHADY BAY",
    "LAUGHTERINTHERAIN",
    "BRAVE LEADER",
    "ASTRAZAR",
    "MAGNATURA",
    "ABUNDANT",
    "BRAVE COUNTRY",
    "SO TEMPTING",
    "PASSING THOUGHT",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

_LIVE_TITLE_PREFIXES = ("goal!", "half-time:", "full-time:", "live:", "ht:", "ft:")
_CHELSEA_MAX_AGE_SEC = 48 * 3600


# ── Chelsea FC ────────────────────────────────────────────────────────────────

async def fetch_chelsea_items() -> list[dict]:
    """BBC Sport Chelsea RSS → filtered list from last 48 hours. Sky Sports fallback.

    Each item: {title, summary, published (epoch float), link}.
    """
    cached = _get_cache("chelsea")
    if cached is not None:
        return cached  # type: ignore[return-value]

    items: list[dict] = []
    try:
        items = await _fetch_chelsea_from_url(BBC_CHELSEA_RSS)
        if not items:
            logger.info("BBC Chelsea RSS returned 0 items — trying Sky Sports fallback")
            items = await _fetch_chelsea_from_url(_SKY_CHELSEA_RSS)
    except Exception as exc:
        logger.warning("Chelsea RSS fetch failed: %s", exc)

    _set_cache("chelsea", items)
    return items


async def _fetch_chelsea_from_url(url: str) -> list[dict]:
    """Fetch and parse one Chelsea RSS URL. Returns [] on any failure."""
    items: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=_HEADERS)
            resp.raise_for_status()
        feed = feedparser.parse(resp.content)  # bytes avoids encoding mismatches
        logger.debug("Chelsea RSS %s: bozo=%s entries=%d", url, feed.bozo, len(feed.entries))
        if feed.bozo:
            logger.warning("Chelsea RSS malformed (%s): %s", url, feed.bozo_exception)
        now = time.time()

        for entry in feed.entries:
            published: float = now
            try:
                pub_str = entry.get("published") or ""
                if pub_str:
                    published = parsedate_to_datetime(pub_str).timestamp()
            except Exception:
                pass

            if now - published > _CHELSEA_MAX_AGE_SEC:
                continue

            title = entry.get("title", "")
            if title.lower().startswith(_LIVE_TITLE_PREFIXES):
                continue

            summary = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text()
            items.append({
                "title": title,
                "summary": summary,
                "published": published,
                "link": entry.get("link", ""),
            })
    except Exception as exc:
        logger.warning("Chelsea RSS fetch failed (%s): %s", url, exc)

    return items


# ── Horse racing — The Racing API (free tier) ─────────────────────────────────
#
# Free plan: /v1/racecards/free?day=today|tomorrow
# Each racecard includes all runners with horse name, horse_id, jockey, form, going.
# We scan every race for name matches against the HORSES list.
#
# Not available on free plan:
#   - Horse search by name (/v1/horses/search — Standard Plan)
#   - Historical results (/v1/horses/{id}/results — Pro Plan)
#
# Upgrade path: theracingapi.com → Standard/Pro plan unlocks search + results.


def _racing_auth() -> httpx.BasicAuth | None:
    """Return BasicAuth for the Racing API, or None if credentials are not configured."""
    if config.RACING_API_USERNAME and config.RACING_API_PASSWORD:
        return httpx.BasicAuth(config.RACING_API_USERNAME, config.RACING_API_PASSWORD)
    return None


def _normalize_horse_name(name: str) -> str:
    """Strip country code suffix and return uppercase. 'Diamond Bay (GB)' → 'DIAMOND BAY'."""
    return re.sub(r"\s*\([^)]+\)\s*$", "", name).strip().upper()


def _fmt_dist(dist_f_str: str) -> str:
    """Convert furlongs to human-readable distance. '10.0' → '1m2f', '7.5' → '7.5f'."""
    try:
        f = float(dist_f_str)
    except (ValueError, TypeError):
        return ""
    miles, rem = divmod(f, 8)
    if miles == 0:
        return f"{rem:g}f"
    elif rem == 0:
        return f"{int(miles)}m"
    else:
        return f"{int(miles)}m{rem:g}f"


async def _fetch_racecard_entries(day: str, auth: httpx.BasicAuth) -> tuple[list[dict], bool]:
    """Fetch one day's racecards and return (runners_found, rate_limited).

    Returns ([], True) on 429 — no retry, quota exhaustion won't recover in seconds.
    Returns ([], False) on other errors or when no matches found.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{_RACING_API_BASE}/racecards/free",
                params={"day": day},
                auth=auth,
            )

        if resp.status_code == 429:
            logger.warning("Racing API rate limited on %s — daily quota likely exhausted", day)
            return [], True

        resp.raise_for_status()
        data = resp.json()

    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            logger.warning("Racing API rate limited on %s (caught via raise_for_status)", day)
            return [], True
        logger.warning("Racing API HTTP error on %s: %s", day, exc)
        return [], False
    except Exception as exc:
        logger.warning("Racing API racecard fetch (%s) failed: %s", day, exc)
        return [], False

    found = []
    for race in data.get("racecards", []):
        for runner in race.get("runners", []):
            normalized = _normalize_horse_name(runner.get("horse", ""))
            if normalized in HORSES:
                found.append({
                    "horse_key": normalized,
                    "horse": runner.get("horse", ""),
                    "horse_id": runner.get("horse_id", ""),
                    "day_label": day,
                    "course": race.get("course", ""),
                    "date": race.get("date", ""),
                    "off_time": race.get("off_time", ""),
                    "race_name": race.get("race_name", ""),
                    "distance_f": race.get("distance_f", ""),
                    "going": race.get("going", ""),
                    "race_class": race.get("race_class", ""),
                    "jockey": runner.get("jockey", ""),
                    "form": runner.get("form", ""),
                })
    return found, False


async def fetch_all_horse_items() -> dict[str, list[dict]]:
    """Scan today's and tomorrow's free racecards for our horses.

    Returns {horse_key: [entry, ...]} — only horses with at least one entry.
    Each entry has: horse, horse_id, day_label, course, date, off_time,
    race_name, distance_f, going, race_class, jockey, form.
    """
    cached = _get_cache("horse_entries")
    if cached is not None:
        return cached  # type: ignore[return-value]

    result: dict[str, list[dict]] = {}

    auth = _racing_auth()
    if auth is None:
        logger.warning("Racing API credentials not configured — skipping horse entries")
        _set_cache("horse_entries", result)
        return result

    # Sequential — free plan is rate-limited to 1 req/s
    today_entries, today_limited = await _fetch_racecard_entries("today", auth)
    if today_limited:
        _set_cache("horse_entries", {"_rate_limited": True})
        return {"_rate_limited": True}  # type: ignore[return-value]

    await asyncio.sleep(1.1)
    tomorrow_entries, tomorrow_limited = await _fetch_racecard_entries("tomorrow", auth)
    if tomorrow_limited:
        # Still use today's data if we have it
        for entry in today_entries:
            result.setdefault(entry["horse_key"], []).append(entry)
        _set_cache("horse_entries", result)
        return result

    for entry in today_entries + tomorrow_entries:
        result.setdefault(entry["horse_key"], []).append(entry)

    logger.info(
        "Racing API: found %d entries for %d horse(s) across today/tomorrow",
        sum(len(v) for v in result.values()),
        len(result),
    )
    _set_cache("horse_entries", result)
    return result
