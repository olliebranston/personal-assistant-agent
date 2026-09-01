# Phase 2 brief — recommendation engine

**Status: implemented.** Kept as a historical build spec — see
`FPL-STATUS.md` for current state.

**Paste-in prompt for Claude Code:**
> Read `FPL-CONTEXT.md`, `PHASE1-BRIEF.md` (for what already exists) and this file,
> then implement Phase 2. Build on the Phase 1 modules — `services/fpl_client.py`,
> `tools/fpl.py`, `bot/fpl_jobs.py`, the six tables in `storage/models.py`. Do not
> duplicate anything that already exists there. Ask before deviating from the
> output contract in §4.

---

## Scope

Turn Phase 1's status reporting into **a weekly recommendation with three options
and a stated pick**. Adds an expected-points model, a transfer optimiser, a
blank/double gameweek detector, and a validator between the solver and the message.

**Still not in scope:** mini-league effective ownership (Phase 3), Dixon-Coles
(Phase 4). Keep the v0 model crude and move on.

---

## 1. `services/fpl_calendar.py` — blank and double gameweek detection

**Detect, never hardcode.** The FPL `fixtures/` endpoint is updated as matches are
rescheduled, so blanks and doubles are derivable from live data and become visible
weeks before any tipster writes about them.

```python
def gameweek_shape(fixtures, gw) -> dict:
    """Per-team fixture count for a gameweek.
    Returns {'blanks': [team_ids with 0], 'doubles': [team_ids with 2+],
             'counts': {team_id: n}, 'total_fixtures': n}
    A normal round is 10 fixtures, every team once."""
```

Call it across the whole season on every bootstrap refresh, cache the result, and
surface changes. When a gameweek's shape changes from normal, that's a
**notification-worthy event** — it's the earliest possible warning and it's how the
chip plan stays current.

### What the calendar looks like today (20 Aug 2026)

Verified against the published fixture list: **all 38 gameweeks currently have
exactly 10 fixtures and every team plays exactly 38 times.** There are no blanks or
doubles built into the base schedule. Every one that appears this season will come
from a reschedule.

Mapping the published FA Cup dates onto the gameweek calendar:

| FA Cup round | Date | Nearest gameweeks | Collision? |
|---|---|---|---|
| Third round | Sat 9 Jan 2027 | GW20 (6 Jan), GW21 (16 Jan) | No — free weekend |
| Fourth round | Sat 13 Feb 2027 | GW25 (10 Feb), GW26 (20 Feb) | No — free weekend |
| Fifth round | Sat 6 Mar 2027 | GW28 (3 Mar), GW29 (13 Mar) | No — free weekend |
| Quarter-finals | Sat 3 Apr 2027 | GW30 (20 Mar), GW31 (10 Apr) | No — free weekend |
| **Semi-finals** | **Sat 24 Apr 2027** | **GW33 is Sat 24 Apr** | **Direct collision** |
| Final | Sat 22 May 2027 | GW37 is Sun 23 May | Adjacent — finalists likely move |

**Conclusions to encode in the chip planner:**

1. **GW33 is the standout blank candidate.** Four semi-finalists plus their four
   scheduled opponents — up to eight teams — will have no fixture. This is
   identifiable today and it is the natural home for the **second-set Free Hit**.
2. **The doubles follow.** GW33's postponed fixtures get replayed in the run-in,
   most likely across GW34–37. That window is where the **second-set Bench Boost
   and Triple Captain** belong.
3. **GW37 (Sun 23 May) sits the day after the FA Cup final.** Expect the two
   finalists' fixtures to move, creating a second, smaller blank.
4. **Nothing to save first-set chips for.** No blank or double is plausible before
   the GW19 deadline (13:30 GMT, 2 Jan 2027) when the first set expires. This
   confirms §2.7 of the context doc: spend them on fixture swings, not on doubles
   that won't come.
5. **GW17–20 is four gameweeks in twelve days** (26 Dec, 30 Dec, 2 Jan, 6 Jan),
   plus a midweek GW13/GW14 pairing (2 and 5 Dec). Heavy rotation, squad depth
   matters — a good window for a first-set Bench Boost, and a hard deadline
   reminder that the first set dies on 2 January.
6. **The EFL Cup final is the other blank candidate** and its date isn't confirmed
   in our sources. The detector will catch it; don't guess it.

Treat every item above as a *prior*, not a fact. The detector is the source of
truth and it should overrule this table the moment the fixture feed disagrees.

---

## 2. `services/fpl_xp.py` — expected points

Port the heuristic from `fpl_squad_v0.py` to run off live `bootstrap-static` rather
than CSVs. **Same maths. Do not improve it here** — improving it is Phase 4, and
doing it now will eat the time budget for the parts that matter.

```
avail       = 1.0 if status == 'a' else chance_of_playing_next_round / 100
reliability = 0.55 + 0.45 * min(1, minutes / 2500)
fixture     = 1.0 + (3.0 - avg_fdr_over_horizon) * 0.10
xp          = points_per_game * reliability * fixture * avail
```

Two additions the CSV version couldn't make:

- **Blank/double aware.** A team with two fixtures in a gameweek gets roughly
  double xP for that week; zero fixtures gets zero. Take the counts from
  `fpl_calendar.gameweek_shape()`. This is what makes chip planning work at all.
- **Log every prediction** to a `xp_predictions` table (gw, element_id, xp,
  model_version). Phase 4 cannot be evaluated without this, and the honest
  question — *are we beating "just use points_per_game"?* — needs the history.

---

## 3. `services/fpl_optimiser.py` — the transfer MILP

Extends the squad solver in `fpl_squad_v0.py` from *pick 15 from scratch* to
*evolve the squad you have*.

**State in:** current 15 (from `my_picks`), bank, free transfers available,
selling prices, chips remaining.

### Test fixture for the transfer solver

`picks()` returns 404 until the GW1 deadline passes (Fri 21 Aug, 18:30 BST), so
until then the transfer solver has no live squad to work from. Use this — Ollie's
actual GW1 squad, element ids resolved against the live feed — as a checked-in
fixture so development isn't blocked:

```python
GW1_SQUAD = [           # element_id, name, pos, now_cost (tenths)
    (109, "Verbruggen",    "GK",   45),
    (497, "Dubravka",      "GK",   40),
    (200, "Lacroix",       "DEF",  60),
    (201, "Muñoz",         "DEF",  55),
    (533, "Mukiele",       "DEF",  55),
    (112, "Van Hecke",     "DEF",  50),
    (204, "Mitchell",      "DEF",  45),
    (426, "B.Fernandes",   "MID", 120),
    ( 40, "Rogers",        "MID",  75),
    (335, "Stach",         "MID",  60),
    (127, "Gomez",         "MID",  50),
    (488, "Sangaré",       "MID",  50),
    (411, "Haaland",       "FWD", 155),
    (165, "João Pedro",    "FWD",  75),
    (346, "Calvert-Lewin", "FWD",  60),
]                        # total 995 = £99.5m, bank £0.5m
```

Captain 411 (Haaland), vice 426 (B. Fernandes). Chelsea: 200, 40, 165 — exactly
three, so the club cap is live and any incoming Chelsea player must displace one.
That makes this a good adversarial fixture for the 3-per-club constraint.

Prices are from the August snapshot; the live feed is authoritative once
`picks()` starts responding. Treat this fixture as scaffolding, not as truth —
delete it from the production path once real picks are syncing.

**Decision variables**, per player per gameweek over a 4–5 gameweek horizon:
buy, sell, own, start, captain.

**Hard constraints:**
- 15 players, 2/5/5/3, max 3 per club, legal XI formation
- `bank + Σ selling_price(sold) ≥ Σ now_cost(bought)`
- Free transfers roll, capped at 5. Transfers beyond the free allowance cost 4 points
- **Selling price is not current price.** You keep half of any rise, rounded down
  to £0.1m, and eat the whole of any fall. Get this from the API's
  `selling_price` in the picks response — do not recompute it, you'll get it wrong
- Player preferences as constraints: forced inclusions, exclusions, `min_club`

**Objective:** maximise Σ discounted xP over the horizon minus transfer costs.

**Bias against action.** Per §2.4 of the context doc, a hit needs a projected gain
over the horizon of **more than 6 points**, not 4. Implement as a 2-point penalty
on top of the 4, so the solver only recommends a hit when it's clearly worth it.
"Roll the transfer" must be a live and frequent answer.

---

## 4. Output contract — three options, one recommendation

This is the part to get exactly right. Every recommendation is a structured object
before it is prose:

```python
{
  "gameweek": 2,
  "deadline_local": "Sat 29 Aug, 11:00 BST",
  "recommended": "hold",          # id of the chosen option
  "options": [
    {"id": "hold",     "label": "No transfer",
     "transfers": [], "xp_delta": 0.0, "hit": 0,
     "rationale": "Bank the transfer; two rolled transfers opens up the Gabriel move in GW4."},
    {"id": "single",   "label": "Stach → Anderson",
     "transfers": [{"out": 412, "in": 233}], "xp_delta": +1.8, "hit": 0,
     "rationale": "Anderson clears the 12-action DefCon threshold most weeks."},
    {"id": "aggressive","label": "Stach → Anderson, Mitchell → Gabriel (-4)",
     "transfers": [...], "xp_delta": +2.9, "hit": 4,
     "rationale": "Below the 6-point bar for a hit — listed for completeness, not advised."}
  ],
  "captain": {"pick": 302, "alternatives": [233], "rationale": "..."},
  "chip": {"play": null, "plan": "Wildcard GW5-9; first set expires 2 Jan"},
  "warnings": ["Lacroix flagged: knock, 75% chance of playing"]
}
```

**Always include a `hold` option**, even when it isn't recommended. The single most
valuable thing this system can tell Ollie in a given week is "do nothing."

**`recommended` is chosen by the solver, not the LLM.**

### The validator — `services/fpl_validate.py`

Between solver and message. Every option is re-checked against live API data:

- Every player id exists and is the price we think it is
- The resulting 15 is legal: budget, 2/5/5/3, max 3 per club
- Money works: `bank + Σ selling_price ≥ Σ buy_price`
- No incoming player is flagged unavailable without it being in `warnings`
- The captain is in the starting XI

**If validation fails, the message does not send.** Log the failure and send a
plain error. A confidently wrong recommendation at T−45m is worse than no
recommendation — this rule is the whole point of the architecture.

---

## 5. LLM in the message path — allowed, with a leash

The LLM writes the prose from the validated object. It may reorder, compress and
explain. It may **not**:

- name a player not in the object
- state a price, xP figure or points total not in the object
- change which option is recommended
- invent a rationale — it paraphrases the solver's, it doesn't author one

Put this in the system prompt as a hard rule, and pass the object as structured
JSON rather than pre-rendered text so there's nothing to "improve."

Also **relax the Phase 1 guard**: the current prompt tells Robin to decline
recommendations because they aren't built. That comes out — but keep the refusal
for anything genuinely outside scope (mini-league EO until Phase 3 lands).

---

## 6. Natural language

Phase 1 already routes `/fpl` through the tool-calling pipeline, so plain-English
questions already reach the tools. Phase 2 should widen the surface, not add
command parsing:

| What Ollie says | Should reach |
|---|---|
| "when should I use my chips" | `get_fpl_chips` + calendar + chip planner |
| "what should I do this week" | `get_fpl_recommendation` |
| "any blanks coming up" | `get_fpl_calendar` |
| "should I captain Haaland" | `get_fpl_recommendation`, captain section |
| "I've done the transfers" | `fpl_acknowledge` |
| "get me Palmer in" | `get_fpl_recommendation` with a forced inclusion |

That last one matters. A stated preference becomes a **solver constraint**, and the
reply quotes the cost: *"Forcing Palmer in costs 1.4 xP over the next four weeks.
Here's the best squad that includes him."* Preferences persist in a
`preferences` table with an expiry, so an October whim doesn't quietly distort the
squad in March.

---

## 7. T−24h message, upgraded

Replaces the Phase 1 status dump:

1. Deadline and countdown
2. **Recommendation** — the chosen option, stated plainly, with its reasoning
3. The other two options, one line each with xP delta
4. Captain and vice, with the alternative named
5. Chip status: play one this week, and where the plan stands
6. Warnings: flagged players, price falls in the squad tonight
7. Blank/double alerts if the shape of any upcoming gameweek changed this week

---

## 8. Acceptance criteria

- [ ] `gameweek_shape()` returns 10 fixtures and one per team for all 38 gameweeks against today's fixture list, and correctly reports blanks/doubles against a synthetic rescheduled fixture set
- [ ] **`fpl_xp.xp()` matches the §2 formula on hand-computed cases.** Assert exact values for three or four players with known inputs — e.g. `ppg=6.7, minutes=3065, avg_fdr=2.83, status='a'` → `reliability=1.0, fixture=1.017, xp=6.81`. This is the real regression on the maths; the previous version of this criterion asked the optimiser to reproduce a specific 15-man squad, which was wrong — see the note below
- [ ] From-scratch mode (empty squad, £100.0m) returns a **legal** 15: 2/5/5/3, ≤3 per club, ≤£100.0m, valid XI formation, captain in the XI

> **Why "reproduce the GW1 squad" is not a valid test.** Two reasons. First, the
> Phase 2 optimiser runs off live `bootstrap-static`, while `fpl_squad_v0.py` ran
> off a fixed August CSV snapshot — prices and ownership drift, so the same maths
> legitimately yields a different squad. Second, the actual GW1 squad was **not**
> the solver's unconstrained output: Haaland, B. Fernandes and three Chelsea assets
> were forced in by hand — a manual override made when the squad was built, not
> tracked in any separate doc. Regressing an unconstrained solve
> against a hand-constrained result would fail by design. Test the formula, and
> test legality. Don't test the squad.
- [ ] Selling price uses the API's `selling_price`, and a test proves a player bought at £6.0 and now worth £6.3 sells for £6.1
- [ ] A hit worth +5 xP is **not** recommended; one worth +7 is
- [ ] `hold` appears in every option set
- [ ] The validator rejects a deliberately corrupted recommendation (wrong price, 4 players from one club, unaffordable) and no message is sent
- [ ] "when should I use my chips" in plain English reaches the chip planner with no slash command
- [ ] A forced inclusion returns a legal squad and quotes the xP cost
- [ ] Every prediction lands in `xp_predictions`

Test the validator by trying to break it, not by confirming it works. It is the
only thing standing between the solver and a wrong recommendation sent at T−15m.