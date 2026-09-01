# Next build steps — Claude Code brief

**Written 1 Sept 2026, after GW2.** Context: `FPL-CONTEXT.md`, `FPL-STATUS.md`,
`GW3-ACTIONS.md`. Prior specs: `FPL-PHASE1-BRIEF.md`, `FPL-PHASE2-BRIEF.md`.

Four steps, in strict order. Step 0 blocks everything — the code is written and
tested but is not running, so it has delivered zero points so far.

**Paste-in prompt:**
> Read `FPL-CONTEXT.md`, `FPL-STATUS.md` and this file. Work through the steps in
> order. Do not start a later step until the earlier one is verified. Step 0 is a
> deployment problem, not a coding problem — diagnose before changing any code.

---

## Step 0 — Get it actually running (blocker)

Two symptoms: no Telegram messages at any point across two gameweeks, and FPL
questions answered with generic LLM waffle instead of tool calls. One cause
explains both: **the live bot is running an old build with neither the scheduled
jobs nor the FPL tools registered.**

Most likely the Phase 1/2 work was committed on the Windows machine and never
pulled onto the Oracle box.

Diagnose on the **server**, not locally:

```bash
ls -la services/fpl_client.py tools/fpl.py bot/fpl_jobs.py   # is the code there?
grep FPL .env                                                 # is config there?
systemctl status robin --no-pager
journalctl -u robin --since "3 days ago" | grep -i fpl
```

The success signal is a log line reading `FPL jobs registered: 5-min adherence
tick...`. Until that appears, nothing else in this document matters.

**Acceptance:** `/fpl` on Telegram returns the real 15-man squad with live prices,
and the T−24h message arrives unprompted before the GW3 deadline.

**Then add a deployment check so this can't recur silently:** on startup, log the
git commit hash and whether `FPL_ENABLED` is set. A one-line `systemctl status`
check should be able to answer "is the FPL module live?" without archaeology.

---

## Step 1 — Put the starting XI and bench order in the output

**This is the highest-value change in this document, and it's a gap in my Phase 2
spec, not a bug in the implementation.**

The MILP already solves for the starting XI and captain — `start` and `captain`
are decision variables. But the §4 output contract only surfaced `transfers` and
`captain`. So Robin computes the optimal XI every week and never tells Ollie.

The cost of that omission is measurable: **22 points left on the bench across GW1
and GW2**, including Mukiele's 9 in GW2. That is more than the entire transfer
gain the optimiser is likely to generate in two months.

Extend the recommendation object:

```python
"lineup": {
  "xi": [
    {"element": 109, "pos": "GK",  "fixture": "LEE (H)", "difficulty": 2},
    ...                                    # 11 entries, ordered GK/DEF/MID/FWD
  ],
  "bench": [                               # ordered 1,2,3 then GK
    {"element": 40, "order": 1, "fixture": "ARS (A)", "difficulty": 5},
    ...
  ],
  "changes_from_current": [                # only what he must actually click
    {"in": 533, "out": 40, "reason": "Rogers faces Arsenal away (5); Mukiele has started the last two"}
  ],
  "formation": "4-4-2"
}
```

**`changes_from_current` is the important field.** Don't make him diff two
fifteen-man lists — tell him the two or three swaps to make. If there are none,
say "no lineup changes" explicitly, because that is also useful information.

The XI is computed from `my_picks` (what he actually has) versus the solver's
`start` variables, so it works even in a gameweek with no transfers.

**Acceptance:**
- The T−24h message names the XI, the bench in order, and the specific swaps.
- A player with `status != 'a'` never appears in the recommended XI.
- Running against GW2's actual squad and fixtures would have put Mukiele in the XI.

---

## Step 2 — Make captaincy a first-class weekly decision

Captaincy cost 20 points in a single gameweek — he owned both Fernandes and
Haaland and captained the wrong one. It is the biggest single lever in the game
and currently it gets one line in the message.

Upgrade the `captain` block:

```python
"captain": {
  "pick": 411,
  "xp": 7.2,
  "alternatives": [
    {"element": 426, "xp": 5.9, "fixture": "EVE (A)", "difficulty": 3},
  ],
  "vice": 426,
  "rationale": "Home to a promoted side, difficulty 2, on penalties.",
  "margin": "clear"          # "clear" | "close" | "coin-flip"
}
```

Two rules:

1. **`margin` drives the prose.** When the top two candidates are within 1.5 xP,
   say so out loud — "this is close, here's the case for each" — rather than
   presenting a marginal call as settled. Last time I called it "a dead heat,
   narrowly Haaland" and that framing was right; the message should do the same.
2. **Vice-captain is chosen independently**, not as the second-best captain.
   Prefer a candidate whose fixture is on a different day to the captain's, so a
   late benching is covered.

**Acceptance:** the message always names the captain, the runner-up with its xP,
and whether the gap is clear or close.

---

## Step 3 — Retime the main briefing to Thursday evening

Ollie picks his team **the day before the deadline, typically Thursday**, to catch
late training-ground injuries. The current T−24h is deadline-relative, so:

- Friday 18:30 deadline → briefing Thursday 18:30 ✓
- Saturday 11:00 deadline → briefing Friday 11:00 ✗ mid-workday, easy to miss
- Midweek deadline → briefing at some arbitrary hour ✗

Change the main briefing trigger to: **the later of (a) 18:00 on the Thursday
before the deadline, or (b) T−48h — but never later than T−20h.** In practice that
gives a consistent Thursday-evening slot for weekend deadlines and still fires
sensibly for midweek rounds.

Keep the rest of the ladder deadline-relative and unchanged: T−3h team-news sweep
(silent unless something changed), T−45m and T−15m nudges, `/fpl done` to silence
them.

**Acceptance:** for a Saturday 11:00 deadline, the main briefing fires Thursday
evening, not Friday morning. Test with an injected fake deadline; don't wait a
week to find out.

---

## Step 4 — Phase 3: the mini-league engine

Only after steps 0–3 are verified.

This is the feature that would have caught the GW2 captaincy error. Both managers
above Ollie captained Fernandes; he captained Haaland. Nothing in the system knew
that, because nothing looks at rivals' teams.

### Data

`leagues-classic/1342398/standings/` gives the seven rival entry ids. Then
`entry/{id}/event/{gw}/picks/` for each — seven calls a gameweek, available once
each deadline passes. Cache per gameweek; picks never change retrospectively.

New tables:

```sql
CREATE TABLE rivals (
    entry_id     INTEGER PRIMARY KEY,
    entry_name   TEXT,
    player_name  TEXT
);

CREATE TABLE rival_picks (
    gw           INTEGER,
    entry_id     INTEGER,
    element_id   INTEGER,
    multiplier   INTEGER,     -- 0 bench, 1 starting, 2 captain, 3 TC
    PRIMARY KEY (gw, entry_id, element_id)
);

CREATE TABLE rival_history (
    gw           INTEGER,
    entry_id     INTEGER,
    points       INTEGER,
    total_points INTEGER,
    rank         INTEGER,
    chip         TEXT,
    PRIMARY KEY (gw, entry_id)
);
```

**Key everything on `entry_id`.** Two rivals had near-identical team names
("Red Djed Redemption" / "Red Djed redemption") — matching on name will break.

### Computation

```python
def league_eo(element_id, gw) -> float:
    """Effective ownership within the mini-league, not the global field.
    Σ(multiplier) across rivals ÷ number of rivals, as a percentage."""
```

Global ownership is the wrong denominator in a seven-man league. If five of six
rivals own Haaland, captaining him gains nothing *here* however popular he is
nationally.

Feed `league_eo` into the captain decision as the tiebreaker when `margin` is
close, replacing global ownership. Per `FPL-CONTEXT.md` §2.3: if the top xP
candidate leads by 2+ points, captain him regardless; under 1.5, let league EO
decide.

### Mode switching

Drive risk appetite off actual league position and gameweeks remaining
(`FPL-CONTEXT.md` §2.1):

| Position | GWs left | Mode |
|---|---|---|
| Behind | > 15 | **Neutral** — play the highest-xP team, ignore ownership. Currently correct: 36 gameweeks left, a 68-point gap is nothing |
| Behind | < 10 | **Chase** — favour low league-EO picks, accept variance |
| Leading | < 10 | **Protect** — converge on rivals' squads; own what they own |

Do not let it flip to chase mode in September. A 68-point deficit over 36 weeks is
noise, and chasing early is how a bad season becomes a catastrophic one.

### New output

Add to the weekly briefing:
- League table with movement since last gameweek
- **Chips used by each rival** — currently nobody has spent one, and knowing that
  is worth more than any tip
- Players owned by 4+ rivals that Ollie doesn't own (template risk *within* the league)
- Captain choices of the managers above him, once the deadline has passed

**Acceptance:**
- `league_eo(411, 2)` returns the real figure computed from rivals' GW2 picks.
- Replaying GW2 flags that both managers above Ollie captained element 426.
- Rival syncing survives a manager joining or leaving mid-season.
- Mode stays "neutral" when given a 68-point deficit with 36 gameweeks remaining.

---

## What not to build yet

Phase 4 (Dixon-Coles) and Phase 5 (chip-planner MILP) stay last. The v0 model is
not what's costing points — an unrun system, an unstated lineup and an unrevisited
captaincy are. Fix those first.
