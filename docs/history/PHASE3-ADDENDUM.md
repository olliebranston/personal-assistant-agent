# Phase 3 addendum — real data, known-answer tests, and one extra feature

**Status: implemented** — the fixtures and tests described below exist
(`tests/fixtures/rivals_gw2.py`, `tests/test_no_production_import_of_fixtures.py`).

Append to `PHASE3-BRIEF.md`. Everything below is **real data pulled from the live
API on 1 Sept 2026**, after GW2. Use it as checked-in test fixtures so Phase 3 is
verified against ground truth rather than synthetic squads.

---

## 0. Read this before anything else: fixtures are tests, never a data source

The squad data in §A is a **frozen snapshot for unit tests only**. It must never
be imported by anything in the production path, and nothing at runtime may read
these markdown files.

**Production pulls rival squads live from the API every gameweek**, exactly as it
does Ollie's own. Rivals make transfers, change captains and reorder benches every
week; a static file would be wrong within days and — worse — would be *silently*
wrong, since nothing would fail.

Enforce it structurally: the fixture lives under `tests/fixtures/`, and a test
asserts that no module outside `tests/` imports it. If that's awkward in this
codebase, at minimum put a comment at the top of the file saying so.

### Weekly sync requirements

- **When:** as part of the existing post-deadline sync in
  `bot/fpl_jobs.py::_sync_passed_deadlines`. Once a gameweek's deadline passes,
  every rival's picks for that gameweek are public and immutable — fetch once,
  store, never refetch.
- **What:** for each entry in `leagues-classic/1342398/standings/`, call
  `entry/{id}/event/{gw}/picks/` and `entry/{id}/history/`. Seven rivals, one
  gameweek — trivial load. Cache by `(gw, entry_id)`; a row that exists is final.
- **Membership is re-read every week, not cached.** Managers join and leave
  mid-season. Refresh the `rivals` table from the standings each sync, mark
  departures inactive rather than deleting them (their historical picks stay
  meaningful), and pick up newcomers automatically.
- **Backfill on first run**, and whenever a new rival appears: walk every
  completed gameweek from that entry's `started_event` forward. **Drive backfill
  off `started_event`, not off when they first showed up in the standings** —
  a manager can join a mini-league in September having played since GW1, and
  their earlier gameweeks are fully retrievable.
- **Failure is per-rival, not per-sync.** One entry 404ing must not abort the
  others. Log it, store what succeeded, retry next tick.

### Track rival transfers week to week

Ollie explicitly wants to see rivals' squads *change*, not just their current
state. Once two gameweeks of `rival_picks` exist this is a diff:

```python
def rival_transfers(entry_id, gw) -> dict:
    """{'in': [element_ids], 'out': [element_ids]} — set difference between
    this gameweek's 15 and last gameweek's 15 for one rival."""
```

Surface it in the weekly review: *"Angus Robinson brought in Calafiori and sold
Ndiaye. Three of your seven rivals bought Mbeumo this week."* A player being
bought by several rivals at once is the strongest early signal available in a
league this small, and it is invisible from global transfer numbers.

Note `entry/{id}/transfers/` also exists and gives exact prices and timestamps —
use it in preference to the diff where available, and keep the diff as the
fallback for a rival whose transfer log is unavailable.

---

## A. Known-answer test fixture — GW2 rival picks

```python
# tests/fixtures/rivals_gw2.py
# Real data, FPL API, 1 Sept 2026. Format: element_id:multiplier
# multiplier 0 = benched, 1 = starting, 2 = captain, 3 = triple captain

RIVALS_GW2 = {
    1896251: ("Angus Robinson",  "496:1,418:1,8:1,10:1,124:1,426:2,368:1,427:1,165:1,411:1,346:1,109:0,127:0,534:0,304:0"),
    2846954: ("George Langran",  "109:1,418:1,4:1,229:1,8:1,43:1,426:2,237:1,481:1,411:1,165:1,497:0,259:0,290:0,194:0"),
    670639:  ("Archie Powell",   "496:1,173:1,8:1,423:1,426:1,427:1,398:1,557:1,411:2,165:1,346:1,109:0,237:0,502:0,259:0"),
    1180800: ("Arun Mehta",      "496:1,8:1,417:1,173:1,237:1,368:1,427:1,426:2,165:1,411:1,552:1,497:0,31:0,115:0,124:0"),
    2954812: ("Jack Lea-Jones",  "1:1,387:1,4:1,418:1,368:1,427:1,397:1,426:2,236:1,165:1,106:1,497:0,272:0,304:0,259:0"),
    5330114: ("Angus Smith",     "496:1,8:1,423:1,175:1,426:1,366:1,427:1,237:1,165:1,411:2,346:1,497:0,115:0,498:0,290:0"),
    6157646: ("Oscar Holt",      "1:1,423:1,8:1,499:1,175:1,237:1,366:1,40:1,379:1,165:1,411:2,497:0,557:0,204:0,481:0"),
}

OLLIE_GW2 = "109:1,200:1,112:1,204:1,127:1,335:1,426:1,40:1,346:1,411:2,165:1,497:0,201:0,533:0,488:0"

CHIPS_GW2 = {}   # every entry: active_chip is null. Nobody has spent a chip.
```

### Expected values — assert these exactly

With `N = 7` rivals and `league_eo = Σ(multiplier) / N × 100`:

| Element | Player | Owned by | Started by | Captained by | **league_eo** |
|---|---|---|---|---|---|
| 426 | B. Fernandes | 6/7 | 6 | **4** | **143%** |
| 411 | Haaland | 6/7 | 6 | **3** | **129%** |
| 165 | João Pedro | **7/7** | 7 | 0 | **100%** |
| 434 → 43 | Calafiori (ARS) | 6/7 | 6 | 0 | **86%** |
| — | Mbeumo (MUN) | 5/7 | 5 | 0 | **71%** |
| — | Ndiaye (EVE) | 5/7 | 4 | 0 | **57%** |
| — | Kinsky (TOT) | 4/7 | 4 | 0 | **57%** |

*(Resolve the element ids for Calafiori, Mbeumo, Ndiaye and Kinsky from
bootstrap-static at test-build time rather than hardcoding my mapping — I read
those names off a snapshot and the ids should be confirmed against the live feed.)*

Additional assertions worth pinning:

- `league_eo(165, gw=2) == 100.0` — João Pedro is owned by **every** rival and
  captained by none. A player at exactly 100% EO with zero captaincy is a useful
  edge case: owning him is mandatory, captaining him is pure differential.
- Players Ollie owns that **no** rival owns: `{200 Lacroix, 112 Van Hecke,
  335 Stach, 201 Muñoz, 533 Mukiele, 488 Sangaré}` — six. The "my differentials"
  query must return exactly this set for GW2.
- Players owned by ≥4 rivals that Ollie does **not** own: Calafiori, Mbeumo,
  Ndiaye, Kinsky. The "league template holes" query must return exactly these.

### Two real edge cases in this data — test both

1. **Oscar Holt (6157646) joined the mini-league late but has played since GW1.**
   His `started_event` is 1 and `entry/6157646/history/` returns GW1 (59 pts) and
   GW2 (78 pts). He was simply absent from the standings when the league was first
   read on 20 Aug. This is the case that breaks naive backfill: a rival appearing
   in the standings for the first time in week *n* may have a full history behind
   them. **Backfill from `started_event`, never from first-seen date.** Verified
   against the live API on 1 Sept 2026.
2. **`league_eo` can exceed 100%.** Fernandes is at 143%. Anyone who writes this
   assuming a 0–100 range will produce a broken progress bar or a clamped value.
   It is a *weighted* ownership, and captaincy is what pushes it past 100.

---

## B. Extra feature — the post-gameweek differential report

**This is the addition I'd make to the brief.** Everything in Step 4 as written
feeds *forward* into next week's decision. Nothing closes the loop backwards, and
the backwards loop is where the learning is.

After lockdown each gameweek, decompose the gap to each rival into its causes:

```python
{
  "gw": 2,
  "my_points": 77,
  "league_average": 106,
  "vs_leader": {"name": "Angus Robinson", "points": 127, "gap": -50},
  "decomposition": [
    {"cause": "captain",  "delta": -20,
     "detail": "You captained Haaland (13). He captained Fernandes (23). You owned both."},
    {"cause": "bench",    "delta": -14,
     "detail": "Mukiele scored 9 from your bench."},
    {"cause": "squad",    "delta": -16,
     "detail": "He owns Calafiori and Mbeumo; you own Lacroix and Stach."}
  ]
}
```

The arithmetic is straightforward once `rival_picks` exists: captain delta is
(your captain's score × 2) minus (theirs × 2); bench delta is your
`points_on_bench` minus theirs; squad delta is the residual.

**Why it matters more than it looks.** The whole diagnosis of this season so far —
that roughly 40 of a 68-point gap came from captaincy and bench, not squad
quality — took me a session of manual API calls to work out. It should be a line
in Tuesday's review message, automatically, every week. That is the difference
between a system that recommends things and one that teaches you where you're
actually losing.

**Acceptance:** replaying GW2 produces a captain delta of −20 against Angus
Robinson and identifies Mukiele's 9 bench points.

---

## C. A correction to carry into the strategy

I told you the two managers above you both captained Fernandes and implied you'd
missed a consensus. Having now pulled all seven squads, that was overstated:

| Captained Fernandes (23 pts) | Captained Haaland (13 pts) |
|---|---|
| Angus Robinson (1st) | Archie Powell (3rd) |
| George Langran (2nd) | Angus Smith (6th) |
| Arun Mehta (4th) | Oscar Holt (7th) |
| Jack Lea-Jones (5th) | **Ollie (8th)** |

**The league split four-four.** You didn't miss something everyone else saw — you
lost a genuine coin flip. Half the league lost the same one.

That changes the lesson. The fix isn't "listen to the consensus", because there
wasn't one. It's that captaincy is a high-variance decision worth an explicit
weekly thought, and that when it's close the system should *say* it's close rather
than presenting a coin flip as settled. Which is what Step 2 of the brief does.

**What the data does show as a genuine gap:** you're missing two players that most
of your league owns — **Calafiori (6/7 rivals, 86% league EO)** and **Mbeumo
(5/7, 71%)**. Those are real template holes *within your league*, invisible to
global ownership figures, and exactly what Phase 3 is built to surface. Worth
considering on Thursday, not today.

Meanwhile you hold **six players nobody else in the league owns**. Per the
doctrine that's fine — they're all in the £4.5–6.0m bracket where differentiating
is cheap — but six is a lot of independent risk, and it's worth knowing you've
drifted further from your league than the global ownership numbers suggested.