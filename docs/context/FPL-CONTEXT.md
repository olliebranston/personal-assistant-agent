# FPL-CONTEXT.md — Robin's Fantasy Premier League module

**Status:** Live — Phases 1-3 implemented and in production (see
`FPL-STATUS.md`/`PHASE3-BRIEF.md` for current build state). Written 20 Aug
2026; the doctrine/rules below remain the design reference — read "build
order" language as historical, not a future plan.
**Season:** 2026/27. **GW1 deadline: Fri 21 Aug 2026, 18:30 BST (17:30 UTC).**

---

## 0. The uncomfortable diagnosis

Read your own description of why you lose: *build a team, do alright, lose
interest, don't stay on top of transfers, don't use chips properly, wheels come
off.*

Note what is **not** in that list: "my player selection model was insufficiently
sophisticated." You don't lose to better analysis. You lose to **disengagement**.

That matters enormously for what we build, because the two things point in
opposite directions:

- The fun engineering problem is an expected-points model and a MILP optimiser.
- The problem that actually costs you the forfeit is **adherence** — never
  missing a deadline, never fielding a player who isn't playing, always taking a
  reasonable captain, never leaving a free transfer to rot for six weeks.

A crude agent that guarantees adherence beats a brilliant agent you stop reading
in October. So the build order below deliberately front-loads the boring stuff.
Phase 1 has no model in it at all and is, honestly, where most of your points
come from.

Second uncomfortable point: **your homegrown xP model will not beat FPL Review or
Fantasy Football Hub's models.** Those are years of work by people with better
data. Don't build this expecting alpha from the projections. The genuine edges
available to us are three, and none of them are the model:

1. **Adherence** (above).
2. **Mini-league-relative effective ownership** — optimising against the ~10
   specific people in your league, not against 6.5 million. No commercial tool
   does this, and the FPL API exposes exactly the data needed. This is the real
   custom edge.
3. **Chip planning discipline** — a plan written in August and revisited weekly,
   rather than panic-playing a Wildcard in GW30 because your team fell apart.

---

## 1. The 2026/27 rules, confirmed

### Structure (unchanged)
- £100.0m budget, 15 players: 2 GK / 5 DEF / 5 MID / 3 FWD.
- Max 3 players per club. Starting XI + bench, captain doubled.
- 6.5m registered managers as of 20 Aug.

### Transfers
- 1 free transfer per GW, **rolling up to a maximum of 5**. Extra transfers cost −4.
- **No bonus December transfers this season** — AFCON has moved to June/July 2027,
  so the usual extra-transfer sweetener isn't happening.

### Chips — two sets of four
| Chip | Set 1 | Set 2 |
|---|---|---|
| Wildcard | GW1–19 | GW20–38 |
| Free Hit | GW2–19 (not GW1) | GW20–38 |
| Triple Captain | GW1–19 | GW20–38 |
| Bench Boost | GW1–19 | GW20–38 |

- **Set 1 expires at the GW19 deadline: 13:30 GMT, Sat 2 Jan 2027.** Unused = lost.
- One chip per gameweek. If you Free Hit in GW19 you cannot Free Hit in GW20.

### Scoring changes for 2026/27
- **Defensive Contribution (DefCon) points stay.** Defenders need **10** defensive
  actions (tackles, interceptions, clearances, blocks, recoveries per FPL's
  definition) in a match for **+2**; midfielders and forwards need **12** for **+2**.
- **BPS rebalanced** to reduce double-counting with DefCon:
  - No BPS penalty for being tackled (helps dribblers).
  - Clearances/blocks/interceptions now 1 BPS per **three** (was per two).
  - Goalkeeper saves restructured: in-box save 3 BPS, other saves 2 BPS, big-chance
    save 1 BPS, penalty save 8 BPS. Net effect: **keepers and attacking players gain
    bonus-point share; ball-winning defenders lose some.**
- **Live bonus and live ranks.** Projected bonus appears after 20 minutes of each
  match; mini-league tables update in real time.
- **Lockdown moved to 09:00 the day after the GW's final match** (was ~1 hour after
  final whistle), to allow post-match Opta review. Practical consequence: **final
  scores are not final until Monday/Tuesday morning.** Robin must not post a
  "final" GW review before lockdown.
- **Official price-change predictor** on the FPL site, updated daily at 00:00 UK
  time, showing "likely" / "very likely" risers and fallers. Prices now change at
  **midnight UK time** (previously ~01:30/02:30). This is new and useful — it means
  we no longer need a third-party predictor site.

### Price mechanics
- Moves in £0.1m steps, max £0.3m per gameweek, driven by net transfers in/out.
- Wildcard transfers don't count toward the thresholds.
- **50% sell-on fee:** if a player rises, you keep half the rise (rounded down).
  If he falls, you eat the whole fall. Team value is a scoreboard, not a strategy —
  chasing value at the cost of points is one of the classic traps.

### Season context that actually matters
- **A World Cup finished on 19 July 2026.** Deep-run internationals had ~4 weeks off.
  Expect elevated early-season injuries, late fitness, and rotation among players
  from finalist nations. This is a real, underpriced GW1–6 risk factor and Robin
  should weight minutes-certainty higher than usual in the opening month.
- Summer transfer window closes **1 Sept 2026, 23:00 BST** — new signings arriving
  in the last week won't be in the game until the following price update.
- Merged international break **21 Sept – 6 Oct**.

---

## 2. Strategy doctrine (what Robin should actually believe)

This is the section that becomes the agent's system prompt. It's deliberately
opinionated so the agent gives you a recommendation rather than a shrug.

### 2.1 Objective function
Your objective is **not** overall rank. It is: *finish above ~N specific people,
and definitely not last.* Two consequences:

- Effective ownership should be computed **within your mini-league**, not globally.
  If nine of ten rivals own Haaland, captaining Haaland gains you nothing on them.
  If three of ten own him, it does.
- Risk appetite is **path-dependent**. Leading in April → converge on rivals' teams
  (mirror them, and variance can't hurt you). Chasing in April → diverge hard.
  Early season → play the highest-EV team and ignore ownership almost entirely.

### 2.2 The template shield comes first (Ollie's correction, and it's right)
Differentials are a *tiebreaker*, not a strategy. The default posture is to **own
what the field owns at the top of the squad**, because the risk is asymmetric:

- You own the 75%-owned striker and he blanks → everyone else blanked too. You
  lose nothing.
- You *don't* own him and he hauls → you lose ground to three quarters of the
  field in one afternoon, and in a ten-man mini-league that is most of your rivals
  at once.

The downside of missing a template player is unbounded; the upside of avoiding him
is capped at whatever you spent the money on instead. So:

**Rule: differentiate at the bottom of the squad, never at the top.**
- Premiums (£9m+) and the captain: match the field unless you have a strong,
  specific reason.
- £4.5–6.5m squad slots: differentiate freely. If your £5m midfielder blanks,
  nobody gains meaningful ground, because theirs probably blanked too.

This is implemented as the `--min-template` constraint in `fpl_squad_v0.py`:
require N starters owned above a threshold, then optimise freely within that.
Robin should report **XI ownership sum** on every recommendation as a
template-exposure gauge — under ~200% means you've drifted too far from the field.

### 2.3 Effective ownership maths
`EO = ownership% + (ownership% × captaincy rate among owners)`

A 55%-owned midfielder captained by 65% of his owners has EO ≈ 91% — nearly the
whole field takes his score at full weight. Rules of thumb:
- EO > 75%: almost no upside from a haul; you're just avoiding a loss.
- EO < 35%: meaningful upside if he performs.
- If your best-xP captain beats the alternatives by **2+ points**, captain him
  regardless of EO. If the gap is **under 1.5**, use EO as the tiebreaker.

### 2.4 Transfers and hits
- Default is **do nothing and roll the transfer**. Robin must be willing to
  recommend "no transfer this week" and should do so often.
- Take a −4 only when projected gain over the planning horizon (next 4–5 GWs)
  exceeds **~6 points**, not 4. The 2-point buffer covers model error, and model
  error is large.
- Never take a hit for a player you were going to buy next week anyway unless
  price movement or a fixture genuinely forces it.
- Reactive transfers on injury news are usually fine. Reactive transfers on one
  bad performance are usually a mistake.

### 2.5 Squad structure
- Consensus among optimisation work is that **midfield-heavy setups (3-5-2 /
  3-4-3) tend to dominate**, and that consistent starters at well-organised clubs
  beat superstar-hoarding under a hard budget cap.
- **Bench policy is a real decision, not an afterthought.** Two options:
  - *Playing bench* (£4.5m+ nailed starters): protects you from blanks, costs
    ~£2m of XI quality, makes Bench Boost viable.
  - *Non-playing bench* (£4.0m fodder): maximises XI quality, but one injury and
    you're autosubbing a defender who hasn't played since August.
  Given your failure mode is *disengagement*, a playing bench is the right call
  for you. It is the structural equivalent of insurance against not checking in.
- Budget shape: 2–3 premiums (£9m+) max. Beyond three, the squad gets brittle.

### 2.6 DefCon
DefCon has changed defender valuation permanently. A £5.0m centre-back averaging
10+ defensive actions per 90 has a floor of ~4 points before any clean sheet.
That floor is worth more to you than a £6.5m attacking full-back's ceiling,
because floors are what protect a disengaged manager. Target the **£4.5–5.5m
bracket** for two of your defenders. Note the BPS change slightly reduces the
bonus upside of clearance-heavy defenders, so DefCon and bonus no longer stack as
well as last season — don't double-count.

### 2.7 Chip doctrine — this is where you've been losing
The single biggest error casual managers make is **hoarding first-half chips for
double gameweeks that never come.** Double and blank gameweeks are almost entirely
a *second-half* phenomenon (FA Cup rounds and European rescheduling, roughly
GW25–37). Before GW19 there is usually nothing to save for, and on 2 January your
unused chips evaporate.

Therefore:

| Chip | First set (by GW19) | Second set (GW20+) |
|---|---|---|
| **Wildcard** | Play it when your squad is broken, most likely **GW5–9** — after the September international break, when you have real minutes data, post-window signings are priced in, and the World Cup fitness picture has resolved. Don't play it in GW2–3 out of panic. | Hold for the DGW/BGW run-up, typically **GW26–30**, to build a double-loaded squad. |
| **Free Hit** | Use on the first genuine **blank** or on a standout single-week fixture swing. If nothing appears by ~GW17, spend it rather than lose it. | Save for the biggest blank gameweek. |
| **Triple Captain** | A premium at home to a promoted side, in a week they play once. Don't wait for perfect. | Ideally a premium in a double gameweek. |
| **Bench Boost** | Immediately after your first Wildcard, when all 15 are fresh and fixtures align. | A double gameweek where all 15 play twice. |

Robin's job: **hold a live chip plan, review it every week, and escalate loudly
from GW14 if first-half chips are still unused.**

### 2.8 Captaincy
Captaincy is the highest-variance single decision each week. Framework:
1. Shortlist 2–4 candidates by xP.
2. Compute mini-league EO for each.
3. Check fixture via opponent xGA split by home/away, not the generic FDR colour.
4. Weight by minutes probability (rotation risk is a captaincy killer).
5. Set vice-captain **independently** — not the second-best captain, but the best
   option whose fixture is on a *different day*, so you're covered if your captain
   is benched at 14:00 Saturday.

---

## 3. Data layer

### 3.1 The FPL API (unofficial, public, no auth for reads)
Base: `https://fantasy.premierleague.com/api/`

| Endpoint | What it gives you |
|---|---|
| `bootstrap-static/` | The big one: all players, prices, ownership, form, teams, team strengths, all 38 gameweek deadlines, chip definitions. ~3MB. |
| `fixtures/` and `fixtures/?event={gw}` | Full fixture list with FDR, kickoff times, finished flags. |
| `element-summary/{player_id}/` | Per-player match-by-match history + upcoming fixtures. |
| `event/{gw}/live/` | Live points and full stat lines for every player. |
| `entry/{team_id}/` | Your team's metadata and leagues joined. |
| `entry/{team_id}/history/` | Your GW-by-GW points, rank, transfers, chips used, team value, bank. |
| `entry/{team_id}/event/{gw}/picks/` | **A team's actual squad, captain, chip for that GW — for any public team.** |
| `entry/{team_id}/transfers/` | Full transfer history. |
| `leagues-classic/{league_id}/standings/` | Mini-league table (paginated 50/page). |

**Critical design consequence:** `entry/{id}/event/{gw}/picks/` becomes readable
once the deadline passes. So Robin **never has to trust you to report what you
did.** After every deadline it reads your actual team from the API and reconciles.
That single decision removes the biggest fragility in your original spec ("I can
say, okay I've made these changes"). You can tell it, but it doesn't need you to.

The same endpoint applied to your rivals' team IDs is the mini-league EO engine.

**Important gotchas:**
- Player prices in the API are in tenths: `now_cost: 155` = £15.5m.
- The API already includes `expected_goals`, `expected_assists`, `expected_goals_conceded`
  and defensive stats per player. **You do not need to scrape Understat or FBref
  for a v1 model.** This is a bigger deal than it sounds — it removes the most
  fragile dependency most FPL projects have.
- `chance_of_playing_next_round` and `news` carry the injury flags.
- **Not** covered by any published terms as an open API. Treat it as a courtesy:
  cache aggressively, one `bootstrap-static` pull per hour maximum, identify
  yourself with a sane User-Agent, back off on 429s. People have been rate-limited
  for hammering it.
- It changes between seasons without notice. Schema assertions on startup, not
  silent `KeyError`s at 18:00 on a Friday.

### 3.2 Everything else
| Source | Use | Honest verdict |
|---|---|---|
| Official price-change predictor (new for 26/27) | Nightly rise/fall warnings | **Use this.** Replaces LiveFPL/FPL Statistics. |
| FPL Review "Free Planner" | Third-party xP projections | Worth evaluating; check their terms before automating any download. |
| Fantasy Football Scout / Hub / Fix | Editorial, team news, scout picks | **Mostly paywalled. Do not scrape.** See §3.2b. |
| BBC Sport, club sites, press conference roundups | Team news T-48h to T-2h | Free, RSS-friendly, legitimate. This is your team-news feed. |
| Understat / FBref | Deeper xG | Only if the API's own xG proves insufficient. Adds fragility. |

### 3.2b "Where does the LLM get its recommendations from?" — the honest answer

You asked whether Robin can scrape Fantasy Football Fix, fantasy.premierleague.com
and the BBC. Taking those in turn:

- **fantasy.premierleague.com — yes, and no scraping needed.** It has a full JSON
  API (§3.1). Scraping the HTML would be strictly worse than reading the API.
  The Scout's free editorial pages are also fetchable.
- **BBC Sport — yes.** Free, public, RSS feeds available. Legitimate team-news feed.
- **Fantasy Football Fix — no, and I'd push back on wanting to.** It's a
  subscription product; its terms forbid automated extraction, and a scraper
  against a Cloudflare-fronted paid site is a dependency that breaks without
  warning and puts your account at risk. If you value their projections, pay for
  them and read them yourself — Robin can take your read of them as an input.

But there's a better answer hiding in the question. **Robin shouldn't be pulling
"recommendations" from anywhere.** A recommendation scraped from a tipster is that
tipster's opinion, laundered through an LLM that can't tell whether it's any good.
What Robin should pull is *evidence*, and then compute its own recommendation:

| Signal | Where it actually lives |
|---|---|
| Consensus / what everyone is doing | `selected_by_percent` in `bootstrap-static` — the raw crowd position, better than any article about it |
| Live crowd movement this week | `transfers_in_event` / `transfers_out_event` — who the field is buying, updated continuously |
| Elite-manager consensus | Top-10k team IDs via `leagues-classic/314/standings/` (the Overall league), then their `picks` — a real, computable "what are good managers doing" number |
| Form, xG, xA, xGC, defensive actions | All present in the API. No Understat scrape needed |
| Injuries and availability | `news`, `status`, `chance_of_playing_next_round` |
| Price movement | Official predictor page |
| Team news / press conferences | BBC Sport RSS, club sites |
| Editorial nuance | r/FantasyPL (free API, weekly consensus threads) |

That set is strictly more informative than scraping a tipster, is fully
legitimate, and — importantly — is *quantitative*, so the optimiser can use it
directly instead of an LLM paraphrasing someone's blog. The "what are the good
managers doing" query in particular is a genuinely strong signal that most casual
managers never see.

### 3.3 What we can't do
**Robin cannot make your transfers for you.** The FPL login is behind
Cloudflare/captcha; automating it is fragile, breaks constantly, and sits in a
grey area of their terms. Every serious open-source project (AIrsenal included)
either warns heavily about this or doesn't do it.

This is fine. You said you want agency over your own team. Human-in-the-loop is
both the safe design and the one you asked for.

---

## 4. Architecture

```
                    ┌──────────────────────────┐
                    │  scheduler (systemd      │
                    │  timers, as per Robin)   │
                    └───────────┬──────────────┘
                                │
   ┌────────────────────────────┼────────────────────────────┐
   │                            │                            │
┌──▼──────────┐        ┌────────▼────────┐         ┌─────────▼────────┐
│ ingest      │        │ analyse         │         │ notify           │
│ ─────────── │        │ ─────────────── │         │ ──────────────── │
│ fpl_client  │───────▶│ xp_model        │────────▶│ telegram msgs    │
│ news_feed   │        │ optimiser(MILP) │         │ escalation ladder│
│ price_watch │        │ eo_engine       │         │                  │
└──┬──────────┘        │ chip_planner    │         └──────────────────┘
   │                   └────────┬────────┘
   │                            │
┌──▼────────────────────────────▼─────────────────────────────────────┐
│ state: SQLite                                                        │
│  players / fixtures / my_squad / rival_squads / recommendations /    │
│  decisions_taken / chip_plan / model_predictions_vs_actual           │
└──────────────────────────────────────────────────────────────────────┘
```

Key point: **the LLM sits at the edges, not the centre.** It writes the message
and handles your conversational replies. It does **not** pick the team.

### 4.1 The hallucination firewall (non-negotiable)
The #1 failure mode of every LLM FPL bot on GitHub is inventing players, prices,
or illegal squads. The mitigation:

1. Code produces a **validated candidate set** from the API.
2. The optimiser (MILP) produces **legal squads only** — budget, 3-per-club,
   formation, and transfer constraints are hard constraints in the solver.
3. The LLM is handed the solved options and writes prose **about them only**.
4. A final validator re-checks every recommendation against live API data before
   the message sends: does this player exist, is he that price, can you afford it,
   does it break 3-per-club, is he flagged?
5. If validation fails, the message doesn't go out — Robin sends an error instead
   of a plausible lie.

You will be tempted to skip step 4. Don't. A confidently wrong recommendation at
T-45min is worse than no recommendation.

### 4.2 The xP model — build order
- **v0 (in use since GW1, still current — `services/fpl_xp.py:MODEL_VERSION`):**
  no model. Rank candidates on FPL's own `form`, `points_per_game`,
  `expected_goal_involvements_per_90`, fixture FDR, and minutes-certainty.
  Crude, and about 80% as good as anything fancier.
- **v1 (referred to as "Phase 4" in `PHASE3-BRIEF.md`'s build-order — not
  started, deliberately deprioritised):** Poisson / Dixon-Coles team-strength
  model fitted on results → per-fixture
  scoreline distribution → clean-sheet probability and goals-conceded distribution.
  Combine with per-90 xGI to get `xP = P(plays) × (appearance + xG·pos_mult +
  xA·3 + P(CS)·cs_mult + P(DefCon)·2 + E[bonus])`. **This is a genuine weekend or
  two of work**, and it's the piece with real transferable skill.
- **v2:** calibration. Log every prediction, compare to actuals, measure whether
  you're actually beating "just use points_per_game." **Be prepared for the answer
  to be no.** Track it honestly; that's the difference between an engineering
  project and a horoscope.

### 4.3 The optimiser
MILP via PuLP or `highspy`. This is well-trodden ground — sertalpbilal's
FPL-Optimization-Tools and the Turing Institute's AIrsenal are both good reference
implementations to read (not to copy wholesale; you want to understand it).

Decision variables: buy/sell/start/bench/captain per player per gameweek over a
4–6 GW horizon. Objective: maximise Σ discounted xP − 4×(extra transfers).

**Your preferences enter as constraints.** This is the elegant bit:
- "I want at least 2 Chelsea players" → `Σ(chelsea players) ≥ 2`.
- "I'm keeping Palmer whatever you say" → force `x_palmer = 1`.
- "No hits this week" → cap extra transfers at 0.

And crucially, Robin can then **price your preferences**: re-solve unconstrained,
diff the objective, and tell you *"your Chelsea requirement costs 1.4 xP this
week."* You then decide. That's agency with information, which is what you
actually asked for — not an agent that either bosses you or capitulates.

### 4.4 The weekly cadence

| When | What Robin does |
|---|---|
| Nightly 23:15 | Check official price predictor. Alert only if it affects a player you own or one on your shortlist, and only if action is warranted before midnight. |
| Deadline −48h | Pull fixtures, injuries, form. Refresh xP. Quiet — no message. |
| **Deadline −24h** | **The main event.** Full briefing: 3 ranked options (Hold / Balanced / Aggressive), captain recommendation with mini-league EO, chip status, and a one-line "here's what I'd do." |
| Deadline −3h | Press-conference sweep. Message *only if something changed* — new injury, a recommended player flagged, a confirmed rotation. Silence is a feature. |
| **Deadline −45min** | Hard nudge. "Deadline 45 min. Current plan: X. Have you done it?" Repeats at −15min if unacknowledged. |
| Deadline +5min | Read `entry/{id}/event/{gw}/picks/`. Reconcile actual vs recommended. Log the delta silently. |
| During matches | Optional live score + mini-league position (the new live-rank feature makes this cheap). Off by default — this is entertainment, not decision-support. |
| Lockdown +1h (Mon/Tue 10:00) | GW review: final points, mini-league table, what the delta between your choices and the recommendation cost or gained. **Include when Robin was wrong.** |
| Weekly Wed | Chip-plan review. Escalating urgency on unused first-half chips from GW14. |

Note the escalation ladder is the part that actually solves your problem. Build
it first, even if it's just hardcoded reminders with no analysis in them.

### 4.5 Handling "I did something different"
Because Robin reads your real squad post-deadline, this is automatic. But the
conversational path matters too:
- You reply "I took Semenyo not Mbeumo" → Robin adds a **standing constraint**
  (Semenyo owned) and re-plans from your actual position.
- You reply "I want Palmer in next week" → stored as a **forced inclusion** for
  the next solve, with the xP cost quoted back to you.
- Preferences persist in SQLite with an expiry, so a one-off whim in October
  doesn't silently distort your squad in March.

---

## 5. Build phases

| Phase | Scope | Effort | Value |
|---|---|---|---|
| **0. Squad build** | Build the GW1 team with Claude, manually. No code. | Tonight | Critical, expires tomorrow |
| **1. Adherence layer** | `fpl_client.py` + SQLite + Telegram `/fpl` command + the full reminder ladder (−24h, −3h, −45min, −15min) + Monday review. **No model, no optimiser.** | ~1 weekend | **Highest. This is the fix for your actual failure mode.** |
| **2. Recommendation engine** | v0 heuristic xP + MILP optimiser + 3-option briefings + preference constraints. | ~2 weekends | High |
| **3. Mini-league EO engine** | Pull rivals' squads each GW, compute league-relative EO, mode-switch (chase/protect) based on your position and GWs remaining. | ~1 weekend | **The genuine edge. Nobody else has this.** |
| **4. Proper xP model** | Dixon-Coles + calibration tracking. | ~2 weekends | Moderate, and the most fun |
| **5. Chip planner** | Multi-period solve with chip decision variables; DGW/BGW detection from fixture list. | ~1 weekend | Moderate–high (targets a stated failure) |

Phases 3 and 5 matter more than 4. Resist the urge to do 4 first because it's the
interesting one.

### Skills that transfer to your other projects
- **MILP with user preferences as constraints, and pricing those preferences** —
  directly applicable to land-management optimisation (which parcels, subject to
  constraints, at what opportunity cost).
- **Reconciling intent against reality by reading external state** rather than
  trusting user self-report — applies to anything with a system of record.
- **Prediction logging and calibration measurement** — the discipline that stops
  any forecasting feature (energy pricing, yields) from becoming vibes.
- **Escalation ladders on scheduled jobs** — reusable across all of Robin.

---

## 6. Traps, stated plainly

1. **Don't scrape paywalled sites.** Scout/Hub/Fix. It's a ToS breach and a
   dependency that dies to one Cloudflare rule.
2. **Don't automate the transfers themselves.** Fragile, grey-area, and you don't
   want it.
3. **Don't let the LLM pick players.** Firewall in §4.1.
4. **Don't chase team value.** The 50% sell-on fee means value gains are half-real,
   and points beat value every time.
5. **Don't hoard first-half chips.** They expire 2 Jan. §2.7.
6. **Don't over-transfer.** The agent's default recommendation should frequently
   be "roll it."
7. **Don't trust a single-season backtest.** 38 gameweeks is a tiny sample.
   If your model "beats the field" on one season of backtest, that's noise.
8. **Don't build Phase 4 before Phase 1.** You will want to. It is how this
   project fails.

---

## 7. Confirmed configuration

```
FPL_TEAM_ID=6748844        # "Reece lightning" — Ollie Branston
FPL_LEAGUE_ID=1342398      # "FPL Rugby league", created 19 Aug 2026
```

Both verified live against the API on 20 Aug 2026.

### The league: 7 managers

| Entry | Team | Manager |
|---|---|---|
| 6748844 | Reece lightning | **Ollie Branston** |
| 2954812 | Beto Than Haaland | Jack Lea-Jones |
| 5330114 | Could be Wirtz | Angus Smith |
| 2846954 | Eze Come Eze Go | George Langran |
| 1180800 | kouyate kid | Arun Mehta |
| 1896251 | Red Djed Redemption | Angus Robinson |
| 670639 | Red Djed redemption | Archie Powell (league admin) |

**Two teams share a name bar capitalisation.** Key everything on `entry` id, never
on `entry_name` or `player_name`. This is a live bug waiting to happen in the
league table renderer and the EO engine.

### What a 7-manager league changes

This is small, and it changes the strategy meaningfully:

1. **Variance dominates.** With six rivals, one captain haul can flip the table in
   an afternoon. Season-long expected-points edges are real but slow; in a league
   this size, luck has a much larger share of the outcome than in a 200-person
   league. Robin should say so rather than implying the model is destiny.
2. **Global ownership is the wrong denominator.** What matters is what *these six*
   own. If five of six own Haaland, captaining him is neutral within the league
   regardless of his 75% global ownership. The §2.3 EO maths should run on the
   six, with global ownership only as a prior before their picks are visible.
3. **The EO engine is trivially cheap here.** Six `entry/{id}/event/{gw}/picks/`
   calls per gameweek. Nothing to optimise, nothing to rate-limit around. Phase 3
   is a much smaller job than it would be in a big league.
4. **Two distinct objectives, and they diverge.** "Don't come last" means beating
   one person and argues for convergence. "Win the money" means beating six and
   argues for divergence when behind. Robin needs to know which mode it's in —
   drive it off current league position and gameweeks remaining, per §2.1.
5. **Rival picks are invisible until each deadline passes.** The EO engine can't
   run before GW1 lockdown. Until then, global ownership is the only proxy.

### Still open

- Entry fee, prize split, and the forfeit — affects how hard to push at the end.
- Is it the same group every year? (If so, past seasons via
  `entry/{id}/history/` reveal each rival's habits: do they take hits, do they
  chip early, do they go dormant?)
- Notification tolerance: is a T−3h "nothing changed" silence acceptable, or do
  you want a heartbeat either way?

---

## Sources
- [FPL 2026/27 changes — Premier League](https://www.premierleague.com/en/news/4679873/all-you-need-to-know-about-changes-to-fpl-for-202627)
- [FPL chips 2026/27 — Premier League](https://www.premierleague.com/en/news/4679879)
- [How and when to use your chips — Premier League](https://www.premierleague.com/en/news/4362085)
- [Defensive contributions explained — Premier League](https://www.premierleague.com/en/news/4361991/whats-new-in-202526-fantasy-defensive-contributions)
- [Best DefCon earners — Premier League](https://www.premierleague.com/en/news/4681840/whos-best-at-earning-defensive-contribution-points-in-fantasy)
- [5 rule changes & new features — Fantasy Football Scout](https://www.fantasyfootballscout.co.uk/2026/07/20/fpl-2026-27-5-rule-changes-new-features-announced)
- [How FPL price changes work — Fantasy Football Scout](https://www.fantasyfootballscout.co.uk/2026/07/20/how-do-fpl-price-changes-work)
- [Best defenders/midfielders for DefCon — Fantasy Football Scout](https://www.fantasyfootballscout.co.uk/2026/08/19/best-fpl-defenders-midfielders-for-defcon-the-ultimate-guide)
- [Template vs differentials: the EO math — FPL Oracle](https://fploracle.team/blog/template-vs-differential-fpl)
- [Rank protection vs climbing — FPL Oracle](https://fploracle.team/blog/rank-protection-vs-rank-climbing-fpl)
- [Captaincy decision framework — FPL Oracle](https://fploracle.team/blog/fpl-captaincy-strategy)
- [GW1 guide 2026/27 — Fantasy Football Fix](https://www.fantasyfootballfix.com/blog-index/fpl-gameweek-1-guide/)
- [Key dates 2026/27 — Fantasy Football Fix](https://www.fantasyfootballfix.com/blog-index/fpl-2026-27-key-dates/)
- [Fixture difficulty GW1–6 — RotoWire](https://www.rotowire.com/soccer/article/fpl-fixture-difficulty-2026-27-best-and-worst-opening-gameweeks-fantasy-premier-league-125046)
- [FPL API cheatsheet](https://glama.ai/mcp/servers/@owen-lacey/fpl-mcp/blob/e9171d6bb4bc00c522d02752962554112a340d30/docs/fpl-api-cheatsheet.md)
- [FPL API endpoints guide — Medium](https://medium.com/@frenzelts/fantasy-premier-league-api-endpoints-a-detailed-guide-acbd5598eb19)
- [FPL-Optimization-Tools — sertalpbilal](https://github.com/sertalpbilal/FPL-Optimization-Tools)
- [AIrsenal — Alan Turing Institute](https://github.com/alan-turing-institute/AIrsenal)
- [fpl-agent — ajaydhungel7](https://github.com/ajaydhungel7/fpl-agent)
- [A data-driven framework for team selection in FPL — arXiv 2505.02170](https://arxiv.org/html/2505.02170)
- [2026/27 strategy playbook — FPL Horizon](https://fplhorizon.app/fpl_strategy_manual_updated.html)

