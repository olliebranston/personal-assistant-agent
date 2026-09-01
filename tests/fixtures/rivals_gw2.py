"""Known-answer test fixture — real GW2 rival picks, FPL Rugby League (1342398).

PHASE3-ADDENDUM.md §0: "The squad data in §A is a frozen snapshot for unit
tests only. It must never be imported by anything in the production path,
and nothing at runtime may read these markdown/fixture files." Production
pulls rival squads live from the API every gameweek via
bot/fpl_jobs.py::_sync_passed_deadlines — this file exists purely so Step 4's
logic (league_eo, differentials, template holes) can be verified against
ground truth instead of a synthetic squad. tests/test_no_production_import_of_fixtures.py
enforces the "never imported outside tests/" rule structurally.

Real data, FPL API, 1 September 2026, after GW2. Format: element_id:multiplier.
multiplier 0 = benched, 1 = starting, 2 = captain, 3 = triple captain.
"""

from __future__ import annotations

RIVALS_GW2: dict[int, tuple[str, str]] = {
    1896251: ("Angus Robinson",  "496:1,418:1,8:1,10:1,124:1,426:2,368:1,427:1,165:1,411:1,346:1,109:0,127:0,534:0,304:0"),
    2846954: ("George Langran",  "109:1,418:1,4:1,229:1,8:1,43:1,426:2,237:1,481:1,411:1,165:1,497:0,259:0,290:0,194:0"),
    670639:  ("Archie Powell",   "496:1,173:1,8:1,423:1,426:1,427:1,398:1,557:1,411:2,165:1,346:1,109:0,237:0,502:0,259:0"),
    1180800: ("Arun Mehta",      "496:1,8:1,417:1,173:1,237:1,368:1,427:1,426:2,165:1,411:1,552:1,497:0,31:0,115:0,124:0"),
    2954812: ("Jack Lea-Jones",  "1:1,387:1,4:1,418:1,368:1,427:1,397:1,426:2,236:1,165:1,106:1,497:0,272:0,304:0,259:0"),
    5330114: ("Angus Smith",     "496:1,8:1,423:1,175:1,426:1,366:1,427:1,237:1,165:1,411:2,346:1,497:0,115:0,498:0,290:0"),
    6157646: ("Oscar Holt",      "1:1,423:1,8:1,499:1,175:1,237:1,366:1,40:1,379:1,165:1,411:2,497:0,557:0,204:0,481:0"),
}

OLLIE_GW2 = "109:1,200:1,112:1,204:1,127:1,335:1,426:1,40:1,346:1,411:2,165:1,497:0,201:0,533:0,488:0"

CHIPS_GW2: dict[int, str | None] = {}  # every entry: active_chip is null. Nobody has spent a chip.

# Resolved live against bootstrap-static on 1 Sept 2026 (§A: "resolve these at
# test-build time rather than hardcoding my mapping").
CALAFIORI = 8      # ARS
NDIAYE = 237       # EVE
MBEUMO = 427       # MUN
KINSKY = 496       # TOT
FERNANDES = 426
HAALAND = 411
JOAO_PEDRO = 165

# Oscar Holt (6157646) joined the mini-league standings late but has played
# since GW1 (started_event=1) — the case that breaks naive "backfill from
# first-seen" (§0). Verified live against entry/6157646/ on 1 Sept 2026.
OSCAR_HOLT_ENTRY_ID = 6157646
OSCAR_HOLT_STARTED_EVENT = 1


def parse_picks(spec: str) -> list[dict]:
    """'426:2,411:1,...' -> [{"element_id": 426, "multiplier": 2}, ...]."""
    rows = []
    for pair in spec.split(","):
        element_id, multiplier = pair.split(":")
        rows.append({"element_id": int(element_id), "multiplier": int(multiplier)})
    return rows


def squad_ids(spec: str) -> set[int]:
    return {row["element_id"] for row in parse_picks(spec)}
