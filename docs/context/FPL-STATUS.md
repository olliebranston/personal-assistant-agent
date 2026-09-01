# FPL module — status and handover

**Last updated:** 1 Sept 2026, after GW2. Read this first in any new session.

Companion docs: `FPL-CONTEXT.md` (strategy doctrine, config, league), and
`docs/history/PHASE1-BRIEF.md`/`PHASE2-BRIEF.md`/`PHASE3-BRIEF.md`.
(`GW3-ACTIONS.md`, referenced by an earlier version of this doc, was never
created — the diagnostic commands it would have held are in
`PHASE3-BRIEF.md`'s Step 0.)

---

## Season position

Team 6748844 "Reece lightning". League 1342398 "FPL Rugby league", 8 managers.

| GW | Points | Bench | Notes |
|---|---|---|---|
| 1 | 53 | 8 | GW average 50 |
| 2 | 77 | 14 | GW average 79 |
| **Total** | **130** | **22 wasted** | Overall rank ~4.4m |

**8th of 8.** Leader Angus Robinson on 198. But Ollie is within a point of the
combined gameweek average (129) — the league is running hot, he hasn't collapsed.

**Diagnosis of the gap:** roughly 40 of the 68 points are captaincy and bench
selection, not squad quality. GW2: he owned both B. Fernandes (23 pts, hat-trick)
and Haaland (13), captained Haaland; the two rivals above him captained Fernandes.
A 20-point swing on the armband alone. Mukiele scored 9 from his bench.

**No chips used by anyone** — including the league leader, whose 127 was a
captained hat-trick, not a Bench Boost. Ollie has all 8 chips and 2 free transfers.

## Known problems

1. **The Telegram module may not be running on the Oracle deployment.** This
   was the diagnosis as of the start of this session (1 Sept 2026) — no
   messages received, FPL questions answered with generic LLM waffle instead
   of tool calls, suspected cause: the code was built locally and never
   deployed. This is a live-server question this repo alone can't answer —
   run `PHASE3-BRIEF.md`'s Step 0 diagnostic commands on the Oracle box to
   check, don't assume either way from the code.
2. **(as of the same starting point) The squad had never been changed** —
   same XI, bench order and captain since GW1, the actual failure mode the
   whole system was built to prevent, downstream of problem 1 if it's real.
3. `cost_change_start` sign convention still unverified in anger — 17 players
   had moved price by GW2, so `verify_squad_value()` will catch it if wrong.
   Check logs once the deployment question above is settled.

## Next session

- Did the deployment fix work? Did T−24h fire? (Still needs verifying against
  the live server — not resolvable from the repo.)
- GW4 review and transfer call.
- **Phase 3 (mini-league EO) is now shipped** (commits `92746f4` "Step 1/2 —
  surface lineup/bench and upgrade captaincy", `eccc66e` "Step 3 — retime
  main briefing to Thursday evening", `7e0ba7e` "Step 4 — the mini-league
  engine"; `services/fpl_league.py` has `league_eo`/`my_differentials`/
  `league_template_holes`) — it would have caught the GW2 captaincy error,
  since it compares against rivals' actual picks. Confirm it's actually
  wired into a live briefing once problem 1 above is resolved.

## Standing reminders

- First-set chips expire at the **GW19 deadline, 13:30 GMT, 2 Jan 2027**. No
  blanks or doubles before then. Wildcard GW5–9, Bench Boost straight after.
- **GW33 (Sat 24 Apr 2027) collides with the FA Cup semi-finals** — second-set
  Free Hit target; doubles follow across GW34–37.
- The default recommendation is often "roll the transfer." That remains true.
- Captaincy is the single biggest lever. Revisit it every week, not once a season.