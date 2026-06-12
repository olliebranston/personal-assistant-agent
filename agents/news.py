"""News and sports agent: Chelsea FC news + horse owner updates."""

from __future__ import annotations

import asyncio
import logging

from services import news as news_svc
from services.openrouter import complete

logger = logging.getLogger(__name__)

_HORSE_DETAILS = {
    "DIAMOND BAY":       "8yo gelding, Tom Ward, 8 wins",
    "SHADY BAY":         "5yo mare, Tom Ward, 4 wins",
    "LAUGHTERINTHERAIN": "3yo filly, Ed Walker",
    "BRAVE LEADER":      "3yo gelding, Tom Ward",
    "ASTRAZAR":          "3yo gelding, Ed Walker, 2 wins",
    "MAGNATURA":         "3yo gelding, Tom Ward, 2 wins",
    "ABUNDANT":          "3yo gelding, Tom Ward, 1 win",
    "BRAVE COUNTRY":     "2yo colt, Ed Walker",
    "SO TEMPTING":       "2yo filly, Tom Ward",
    "PASSING THOUGHT":   "3yo filly (leased), Ed Walker, 2 wins",
}

_CHELSEA_SYSTEM = """\
Summarise these Chelsea FC news items for Ollie. 3–5 bullets max.
Cover: transfers, signings, manager news, injuries, contract news, club decisions.
Skip: match commentary, goal notifications, anything a match report already covers.
Tone: direct, no padding, no "according to sources". One bullet per item, starting with •.
If nothing is actually newsworthy, reply with exactly:
No significant Chelsea news in the last 48 hours.
"""

_RACING_SYSTEM = """\
Summarise horse racing news for Ollie, who is the owner or part-owner of these horses.
Use ownership tone: "Diamond Bay runs at Newbury on Saturday" — never "is reported to run".

For each horse where the articles mention a specific race entry, result, odds, or trainer comment:
write one concise line. Skip horses where articles are vague or contain no racing specifics.

Output format — one bullet per horse with concrete news, no preamble:
• Diamond Bay — runs at Newbury Saturday 14 June
• Shady Bay — won at Ascot on Tuesday, returned 4/1

If no horse has concrete news, reply with exactly one line:
No specific updates for your horses.
"""


async def handle(text: str, user_id: int = 0) -> str:
    """Fetch Chelsea and horse racing news, return formatted Telegram response."""
    chelsea_items, horse_map = await asyncio.gather(
        news_svc.fetch_chelsea_items(),
        news_svc.fetch_all_horse_items(),
    )

    sections: list[str] = []

    # ── Chelsea ────────────────────────────────────────────────────────────────
    if chelsea_items:
        raw = "\n".join(
            f"• {item['title']}: {item['summary']}"
            for item in chelsea_items
        )
        try:
            chelsea_out = await complete(
                [{"role": "user", "content": raw}],
                system=_CHELSEA_SYSTEM,
            )
            sections.append(f"*Chelsea FC*\n{chelsea_out.strip()}")
        except Exception as exc:
            logger.error("[news] Chelsea LLM call failed: %s", exc)
            sections.append("*Chelsea FC*\nCouldn't summarise news right now.")
    else:
        sections.append("*Chelsea FC*\nNo news in the last 48 hours.")

    # ── Horses ─────────────────────────────────────────────────────────────────
    # Google News RSS removed (hallucinated results). Racing API integration pending.
    sections.append(
        "*Your horses*\nNo verified data available — Racing API integration coming. "
        "Sign up at theracingapi.com and share the key to enable this."
    )

    return "\n\n".join(sections)
