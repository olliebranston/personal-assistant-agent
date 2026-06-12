"""News and sports agent: Chelsea FC news + horse owner race entries."""

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


def _format_horse_entries(horse_map: dict[str, list[dict]]) -> str:
    """Format Racing API entry data as factual bullets — no LLM, nothing to hallucinate."""
    if not horse_map:
        return "No entries found for today or tomorrow."

    lines = []
    for horse_key, entries in horse_map.items():
        display = horse_key.title()
        for entry in entries:
            day = entry.get("day_label", entry.get("date", ""))
            course = entry.get("course", "")
            off = entry.get("off_time", "")
            dist = news_svc._fmt_dist(entry.get("distance_f", ""))
            going = entry.get("going", "")
            race_class = entry.get("race_class", "")
            jockey = entry.get("jockey", "")
            form = entry.get("form", "")

            parts = [f"{course} {day}"]
            if off:
                parts.append(f"off {off}")
            if dist:
                parts.append(dist)
            if going:
                parts.append(going)
            if race_class:
                parts.append(race_class)

            line = f"• {display} — {', '.join(parts)}"
            if jockey:
                line += f" ({jockey})"
            if form:
                line += f" — form: {form}"
            lines.append(line)

    return "\n".join(lines)


async def handle(text: str, user_id: int = 0) -> str:
    """Fetch Chelsea news and horse racing entries, return formatted Telegram response."""
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
    # Structured data only — no LLM, no hallucinations.
    # Free plan covers today + tomorrow racecards. Historical results need Pro plan.
    horse_section = _format_horse_entries(horse_map)
    sections.append(f"*Your horses (today & tomorrow)*\n{horse_section}")

    return "\n\n".join(sections)
