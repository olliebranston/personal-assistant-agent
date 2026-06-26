"""Tests for the Chelsea RSS fetch/filter logic (services/news.py).

No real network calls — httpx is monkeypatched to return a constructed feed.
"""

from __future__ import annotations

import pytest

import services.news as news_svc
from services.news import _is_chelsea_relevant, fetch_chelsea_items


_MIXED_FEED_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>BBC Sport - Chelsea</title>
    <item>
      <title>Chelsea eye Lacroix as part of defensive rebuild</title>
      <description>Chelsea step up their efforts to sign a new defender.</description>
      <link>https://www.bbc.co.uk/sport/football/articles/chelsea1</link>
      <pubDate>%(now)s</pubDate>
    </item>
    <item>
      <title>England seek wickets to contain New Zealand on day two</title>
      <description>Cricket update from Trent Bridge.</description>
      <link>https://www.skysports.com/cricket/example</link>
      <pubDate>%(now)s</pubDate>
    </item>
    <item>
      <title>Wimbledon draw: Draper, Raducanu confirmed</title>
      <description>Tennis news ahead of the grand slam.</description>
      <link>https://www.skysports.com/tennis/example</link>
      <pubDate>%(now)s</pubDate>
    </item>
  </channel>
</rss>"""


def _build_feed_xml() -> bytes:
    from email.utils import formatdate

    now = formatdate(usegmt=True).encode()
    return _MIXED_FEED_XML % {b"now": now}


class _FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self) -> None:
        pass


class _FakeAsyncClient:
    def __init__(self, content: bytes):
        self._content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, headers=None):
        return _FakeResponse(self._content)


# ── _is_chelsea_relevant ──────────────────────────────────────────────────────


def test_is_chelsea_relevant_true_for_chelsea_title():
    assert _is_chelsea_relevant("Chelsea sign new striker", "") is True


def test_is_chelsea_relevant_true_for_chelsea_in_summary_only():
    assert _is_chelsea_relevant("Transfer news roundup", "Chelsea among the clubs interested.") is True


def test_is_chelsea_relevant_false_for_unrelated_sport():
    assert _is_chelsea_relevant("England seek wickets on day two", "Cricket update.") is False


def test_is_chelsea_relevant_case_insensitive():
    assert _is_chelsea_relevant("CHELSEA confirm signing", "") is True


# ── fetch_chelsea_items filtering (mocked HTTP, no network) ──────────────────


@pytest.mark.asyncio
async def test_fetch_chelsea_items_filters_out_non_chelsea_entries(monkeypatch):
    news_svc._CACHE.clear()
    feed_xml = _build_feed_xml()
    monkeypatch.setattr(news_svc.httpx, "AsyncClient", lambda **kwargs: _FakeAsyncClient(feed_xml))

    items = await fetch_chelsea_items()

    assert len(items) == 1
    assert "Chelsea" in items[0]["title"]


@pytest.mark.asyncio
async def test_fetch_chelsea_items_falls_back_to_sky_on_bbc_miss(monkeypatch):
    news_svc._CACHE.clear()
    empty_xml = b"<?xml version='1.0'?><rss><channel></channel></rss>"
    sky_xml = _build_feed_xml()

    calls = []

    def _client_factory(**kwargs):
        calls.append(kwargs)
        content = empty_xml if len(calls) == 1 else sky_xml
        return _FakeAsyncClient(content)

    monkeypatch.setattr(news_svc.httpx, "AsyncClient", _client_factory)

    items = await fetch_chelsea_items()

    assert len(calls) == 2  # BBC tried first, then Sky fallback
    assert len(items) == 1
    assert "Chelsea" in items[0]["title"]
