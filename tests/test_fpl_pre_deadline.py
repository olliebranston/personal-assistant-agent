"""Tests for the pre-deadline league analysis (W6, Oct 2026).

Ollie: "when I get my first message ahead of the gameweek transfer deadline
I want some good analysis on why I gained / lost on the group, and what I
need to change for my next week." The facts are computed deterministically
(tools.fpl.build_pre_deadline_facts); an AI summary may sit on top of them
but is only sent if every number in it comes from the facts
(bot.fpl_jobs._pre_deadline_summary). Everything here is offline — the FPL
client and complete() are monkeypatched.
"""

from __future__ import annotations

import sqlite3

import pytest

import bot.fpl_jobs as fpl_jobs
import config
import tools.fpl as fpl_tools
from storage.models import (
    GAMEWEEK_DDL,
    MY_HISTORY_DDL,
    MY_PICKS_DDL,
    NOTIFICATIONS_SENT_DDL,
    RIVAL_HISTORY_DDL,
    RIVAL_PICKS_DDL,
    RIVALS_DDL,
    mark_notification_sent,
    replace_my_picks,
    replace_rival_picks,
    sync_rivals_from_standings,
    upsert_my_history,
    upsert_rival_history,
)

_ME, _LEADER, _OTHER = 6748844, 111, 222

# 1 Haaland (FWD), 2 Palmer (MID), 3 Saka (MID), 4 Benchman (DEF),
# 5 Salah (MID), 6 Outgoer (DEF) — one club each.
_ELEMENTS = [
    {"id": 1, "web_name": "Haaland", "team": 1, "element_type": 4, "status": "a", "news": "", "now_cost": 150},
    {"id": 2, "web_name": "Palmer", "team": 2, "element_type": 3, "status": "a", "news": "", "now_cost": 100},
    {"id": 3, "web_name": "Saka", "team": 3, "element_type": 3, "status": "a", "news": "", "now_cost": 100},
    {"id": 4, "web_name": "Benchman", "team": 4, "element_type": 2, "status": "a", "news": "", "now_cost": 45},
    {"id": 5, "web_name": "Salah", "team": 5, "element_type": 3, "status": "a", "news": "", "now_cost": 130},
    {"id": 6, "web_name": "Outgoer", "team": 6, "element_type": 2, "status": "d", "news": "Knock",
     "chance_of_playing_next_round": 50, "now_cost": 45},
]
_LIVE_GW7 = {1: 4, 2: 12, 3: 10, 4: 6, 5: 15, 6: 2}


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for ddl in (GAMEWEEK_DDL, MY_PICKS_DDL, MY_HISTORY_DDL, NOTIFICATIONS_SENT_DDL,
                RIVALS_DDL, RIVAL_PICKS_DDL, RIVAL_HISTORY_DDL):
        conn.execute(ddl)
    conn.commit()
    return conn


def _seed(conn) -> None:
    # Me: captained Haaland (4x2), Palmer 12, Benchman 6 on the bench -> 20 pts.
    replace_my_picks(conn, 7, [
        {"element_id": 1, "position": 1, "is_captain": 1, "is_vice": 0, "multiplier": 2},
        {"element_id": 2, "position": 2, "is_captain": 0, "is_vice": 1, "multiplier": 1},
        {"element_id": 4, "position": 12, "is_captain": 0, "is_vice": 0, "multiplier": 0},
    ])
    upsert_my_history(conn, {"gw": 7, "points": 20, "total_points": 400, "overall_rank": 1, "bank": 0,
                             "team_value": 1000, "transfers": 1, "transfer_cost": 0, "chip": None,
                             "points_on_bench": 6})
    sync_rivals_from_standings(conn, [
        {"entry_id": _LEADER, "entry_name": "L", "player_name": "Leader", "started_event": 1},
        {"entry_id": _OTHER, "entry_name": "O", "player_name": "Other", "started_event": 1},
    ])
    # Leader: Haaland 4, Saka 10, Salah captain 15x2 -> 44. Other: Haaland (C) 8, Saka 10, Palmer 12 -> 30.
    replace_rival_picks(conn, 7, _LEADER, [
        {"element_id": 1, "multiplier": 1}, {"element_id": 3, "multiplier": 1}, {"element_id": 5, "multiplier": 2},
    ])
    replace_rival_picks(conn, 7, _OTHER, [
        {"element_id": 1, "multiplier": 2}, {"element_id": 3, "multiplier": 1}, {"element_id": 2, "multiplier": 1},
    ])
    upsert_rival_history(conn, {"gw": 7, "entry_id": _LEADER, "points": 44, "total_points": 500,
                                "rank": 1, "chip": None, "points_on_bench": 0})
    upsert_rival_history(conn, {"gw": 7, "entry_id": _OTHER, "points": 30, "total_points": 450,
                                "rank": 1, "chip": None, "points_on_bench": 0})
    mark_notification_sent(conn, 7, "history_final", "2026-10-21T10:00:00+00:00")


@pytest.fixture
def _wired(monkeypatch):
    monkeypatch.setattr(config, "FPL_ENABLED", True)
    monkeypatch.setattr(config, "FPL_TEAM_ID", _ME)
    monkeypatch.setattr(config, "FPL_LEAGUE_ID", 1342398)

    async def _bootstrap(force=False):
        return {"elements": _ELEMENTS, "teams": [], "events": []}

    async def _live(gw):
        return dict(_LIVE_GW7)

    async def _league(league_id):
        return {"league": {"name": "L"}, "standings": {"results": [
            {"entry": _LEADER, "player_name": "Leader", "rank": 1, "last_rank": 1, "total": 500, "event_total": 44},
            {"entry": _OTHER, "player_name": "Other", "rank": 2, "last_rank": 3, "total": 450, "event_total": 30},
            {"entry": _ME, "player_name": "Ollie", "rank": 3, "last_rank": 2, "total": 400, "event_total": 20},
        ]}}

    async def _transfers(team_id):
        return [{"event": 7, "element_in": 2, "element_out": 6, "element_in_cost": 100,
                 "element_out_cost": 45, "time": "2026-10-16T10:00:00Z"}]

    monkeypatch.setattr(fpl_tools.fpl_client, "bootstrap", _bootstrap)
    monkeypatch.setattr(fpl_tools.fpl_client, "live", _live)
    monkeypatch.setattr(fpl_tools.fpl_client, "league", _league)
    monkeypatch.setattr(fpl_tools.fpl_client, "transfers", _transfers)

    conn = _make_conn()
    _seed(conn)
    return conn


# ── Deterministic facts ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_facts_cover_points_rank_and_league_average(_wired):
    facts = await fpl_tools.build_pre_deadline_facts(_wired)

    assert facts["gw"] == 7
    assert facts["my_points"] == 20
    assert facts["league_average"] == pytest.approx(31.3, abs=0.05)  # (20+44+30)/3
    assert (facts["league_rank"], facts["league_rank_before"], facts["league_size"]) == (3, 2, 3)
    assert facts["total_gap_to_leader"] == -100
    assert facts["total_gap_to_above"] == -50


@pytest.mark.asyncio
async def test_facts_decompose_the_gap_to_the_leader(_wired):
    facts = await fpl_tools.build_pre_deadline_facts(_wired)

    vs = facts["comparisons"][0]
    assert vs["name"] == "Leader"
    assert vs["gap"] == -24
    causes = {d["cause"]: d["delta"] for d in vs["causes"]}
    assert causes["captain"] == -22  # Haaland 4x2 vs Salah 15x2
    assert sum(causes.values()) == -24  # the causes always add back to the real gap


@pytest.mark.asyncio
async def test_facts_cover_captain_bench_and_transfers(_wired):
    facts = await fpl_tools.build_pre_deadline_facts(_wired)

    assert facts["captain"] == {"name": "Haaland", "points": 4, "multiplier": 2,
                                "best_starter": "Palmer", "best_starter_points": 12}
    assert facts["bench_points"] == 6
    assert facts["transfers"] == [{"in": "Palmer", "in_points": 12, "out": "Outgoer", "out_points": 2}]
    assert facts["transfer_net"] == 10
    assert facts["hit_cost"] == 0


@pytest.mark.asyncio
async def test_facts_flag_league_template_players_ollie_does_not_own(_wired):
    facts = await fpl_tools.build_pre_deadline_facts(_wired)

    # Saka is owned by both rivals, Ollie doesn't own him; Salah only by one.
    assert facts["template_holes"] == [{"name": "Saka", "rivals_owning": 2, "rivals": 2}]


@pytest.mark.asyncio
async def test_facts_error_when_no_gameweek_is_final_yet(monkeypatch, _wired):
    _wired.execute("DELETE FROM notifications_sent")
    _wired.commit()

    facts = await fpl_tools.build_pre_deadline_facts(_wired)

    assert "error" in facts


@pytest.mark.asyncio
async def test_format_facts_reads_as_a_short_report(_wired):
    facts = await fpl_tools.build_pre_deadline_facts(_wired)

    text = "\n".join(fpl_tools.format_pre_deadline_facts(facts))

    assert "GW7" in text
    assert "20 pts" in text
    assert "3rd of 3" in text and "down 1" in text
    assert "Leader" in text and "-24" in text
    assert "Palmer in (12)" in text and "Outgoer out (2)" in text
    assert "Saka" in text


# ── AI summary guard ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_is_used_when_every_number_comes_from_the_facts(monkeypatch, _wired):
    facts = await fpl_tools.build_pre_deadline_facts(_wired)

    async def fake_complete(messages, system="", **kwargs):
        return "Lost 24 to the Leader, almost all on the captaincy (-22). Salah's 15 was the swing."

    monkeypatch.setattr(fpl_jobs, "complete", fake_complete)

    summary = await fpl_jobs._pre_deadline_summary(facts)

    assert summary.startswith("Lost 24")


@pytest.mark.asyncio
async def test_summary_with_an_invented_number_is_dropped(monkeypatch, _wired):
    facts = await fpl_tools.build_pre_deadline_facts(_wired)

    async def fake_complete(messages, system="", **kwargs):
        return "Captain cost you 37 points — get Salah, he'll score 19."

    monkeypatch.setattr(fpl_jobs, "complete", fake_complete)

    assert await fpl_jobs._pre_deadline_summary(facts) is None


@pytest.mark.asyncio
async def test_summary_failure_returns_none(monkeypatch, _wired):
    facts = await fpl_tools.build_pre_deadline_facts(_wired)

    async def broken_complete(messages, system="", **kwargs):
        raise RuntimeError("OpenRouter down")

    monkeypatch.setattr(fpl_jobs, "complete", broken_complete)

    assert await fpl_jobs._pre_deadline_summary(facts) is None


# ── Sent as its own message ahead of the T-24h recommendation ───────────────


class _FakeBot:
    def __init__(self):
        self.messages: list[str] = []

    async def send_message(self, chat_id, text, parse_mode=None):
        self.messages.append(text)


class _FakeContext:
    def __init__(self, bot):
        self.bot = bot


@pytest.mark.asyncio
async def test_analysis_message_includes_robins_take(monkeypatch, _wired):
    async def fake_complete(messages, system="", **kwargs):
        return "Captaincy was the swing: -22 against the Leader."

    monkeypatch.setattr(fpl_jobs, "complete", fake_complete)
    bot = _FakeBot()

    await fpl_jobs._send_pre_deadline_analysis(_FakeContext(bot), _wired)

    assert len(bot.messages) == 1
    assert "GW7" in bot.messages[0]
    assert "Robin's take" in bot.messages[0]
    assert "-22" in bot.messages[0]


@pytest.mark.asyncio
async def test_analysis_message_falls_back_to_facts_when_the_summary_fails(monkeypatch, _wired):
    async def broken_complete(messages, system="", **kwargs):
        raise RuntimeError("OpenRouter down")

    monkeypatch.setattr(fpl_jobs, "complete", broken_complete)
    bot = _FakeBot()

    await fpl_jobs._send_pre_deadline_analysis(_FakeContext(bot), _wired)

    assert len(bot.messages) == 1
    assert "Robin's take" not in bot.messages[0]
    assert "GW7" in bot.messages[0]


@pytest.mark.asyncio
async def test_no_analysis_message_without_final_data(monkeypatch, _wired):
    _wired.execute("DELETE FROM notifications_sent")
    _wired.commit()
    bot = _FakeBot()

    await fpl_jobs._send_pre_deadline_analysis(_FakeContext(bot), _wired)

    assert bot.messages == []
