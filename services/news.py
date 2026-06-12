"""News and sports data service: Chelsea FC RSS + horse racing news.

Data sources:
- Chelsea FC: BBC Sport RSS (48-hour window, live match updates excluded)
- Horses: Racing Post search (best-effort, Cloudflare may block) → Google News RSS fallback

All results cached in memory for 1 hour to avoid hammering feeds.
"""

from __future__ import annotations

import asyncio
import logging
import time
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import feedparser
import httpx
from bs4 import BeautifulSoup

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
_RACING_POST_SEARCH = "https://www.racingpost.com/horses/search/results/?q={}"
_GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={}&hl=en-GB&gl=GB&ceid=GB:en"

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

# Title-level patterns that indicate live match commentary rather than news
_LIVE_TITLE_PREFIXES = ("goal!", "half-time:", "full-time:", "live:", "ht:", "ft:")

_CHELSEA_MAX_AGE_SEC = 48 * 3600  # 48 hours


# ── Chelsea FC ────────────────────────────────────────────────────────────────

async def fetch_chelsea_items() -> list[dict]:
    """BBC Sport Chelsea RSS → filtered list of news items from the last 48 hours.

    Each item: {title, summary, published (epoch float), link}.
    Returns [] if the feed is unreachable.
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
    """Fetch and parse a Chelsea RSS feed URL. Returns list of items (may be empty)."""
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


# ── Horse racing ──────────────────────────────────────────────────────────────
#
# TODO: Replace the Racing Post / Google News approach below with The Racing API.
#
# Sign up at theracingapi.com/register for a free API key, then:
#   1. Add RACING_API_KEY to config.py and .env
#   2. Implement _lookup_horse_id(name, client) → str | None
#      GET https://api.theracingapi.com/v1/horses/search?name={name}
#      Auth: httpx.BasicAuth(RACING_API_KEY, "x")
#   3. Implement fetch_horse_entries(horse_id, client) and fetch_horse_results(horse_id, client)
#      GET /v1/entries?horse_id={id}&start_date={today}
#      GET /v1/results?horse_id={id}&start_date={14d_ago}&end_date={today}
#   4. Replace fetch_all_horse_items() with fetch_all_horse_data() that returns
#      {horse_name: {entries: [...], results: [...]}} — no LLM summarisation needed.
#   5. Update agents/news.py to format structured data directly (no _RACING_SYSTEM LLM call).
#   6. Remove fetch_horse_items, _try_racing_post, _try_google_news and their constants below.
#
# Horse IDs are stable — cache them permanently in a module-level dict with no TTL.

async def fetch_horse_items(horse: str) -> list[dict]:
    """Fetch recent news for one horse. Tries Racing Post, falls back to Google News RSS.

    Each item: {title, summary, published, link, source}.
    Returns [] if nothing found.
    """
    cache_key = f"horse:{horse}"
    cached = _get_cache(cache_key)
    if cached is not None:
        return cached  # type: ignore[return-value]

    items = await _try_racing_post(horse)
    if not items:
        items = await _try_google_news(horse)

    _set_cache(cache_key, items)
    return items


async def _try_racing_post(horse: str) -> list[dict]:
    """Attempt Racing Post horse search. Returns [] if blocked or nothing parseable."""
    url = _RACING_POST_SEARCH.format(quote_plus(horse))
    try:
        async with httpx.AsyncClient(
            timeout=10.0, headers=_HEADERS, follow_redirects=True
        ) as client:
            resp = await client.get(url)
            if resp.status_code in (403, 429, 503):
                logger.debug("Racing Post blocked (HTTP %d) for %s", resp.status_code, horse)
                return []
            resp.raise_for_status()

        if any(s in resp.text for s in ("Just a moment", "cf-browser-verification", "Please Wait")):
            logger.debug("Racing Post Cloudflare challenge for %s", horse)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        items: list[dict] = []

        for selector in (
            ".RC-horsesSearch__card",
            "[data-test-id='horse-search-result']",
            ".js-horse-search-result",
            "a.RC-horsesSearch__link",
        ):
            for el in soup.select(selector)[:3]:
                text = el.get_text(separator=" ", strip=True)
                link_el = el if el.name == "a" else el.select_one("a[href]")
                href = (link_el or {}).get("href", "")
                link = f"https://www.racingpost.com{href}" if href.startswith("/") else href
                if horse.split()[0].lower() in text.lower():
                    items.append({
                        "title": text[:200],
                        "summary": "",
                        "published": "",
                        "link": link,
                        "source": "racingpost",
                    })
            if items:
                break

        return items
    except Exception as exc:
        logger.debug("Racing Post error for %s: %s", horse, exc)
        return []


async def _try_google_news(horse: str) -> list[dict]:
    """Google News RSS search for a horse. Returns [] on failure."""
    query = quote_plus(f"{horse} horse racing")
    url = _GOOGLE_NEWS_RSS.format(query)
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=_HEADERS)
            resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        items: list[dict] = []
        first_word = horse.split()[0].lower()
        for entry in feed.entries[:6]:
            title = entry.get("title", "")
            if first_word not in title.lower():
                continue  # skip obvious name collisions
            summary = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text()
            items.append({
                "title": title,
                "summary": summary[:300],
                "published": entry.get("published", ""),
                "link": entry.get("link", ""),
                "source": "google_news",
            })
        return items
    except Exception as exc:
        logger.warning("Google News fetch for %s failed: %s", horse, exc)
        return []


async def fetch_all_horse_items() -> dict[str, list[dict]]:
    """Fetch news for all horses concurrently.

    Returns {horse_name: [items]} — only horses that have at least one item.
    """
    results = await asyncio.gather(
        *[fetch_horse_items(horse) for horse in HORSES],
        return_exceptions=True,
    )
    return {
        horse: result
        for horse, result in zip(HORSES, results)
        if isinstance(result, list) and result
    }
