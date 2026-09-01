# FPL module — status and handover

**Last updated:** 1 Sept 2026, after GW2. Read this first in any new session.

Companion docs: `FPL-CONTEXT.md` (strategy doctrine, config, league),
`FPL-PHASE1-BRIEF.md`, `FPL-PHASE2-BRIEF.md`, `GW3-ACTIONS.md`.

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

1. **The Telegram module is not running.** No messages received, and FPL questions
   get generic LLM answers rather than tool calls. Lead hypothesis: the Phase 1/2
   code was built on the Windows machine and never deployed to the Oracle box, so
   the live bot has neither the jobs nor the tools. Diagnostic commands in
   `GW3-ACTIONS.md` §5. **Unresolved as of this writing.**
2. **The squad has never been changed** — same XI, bench order and captain since
   GW1. This is the actual failure mode the whole system was built to prevent, and
   it is happening because of problem 1.
3. `cost_change_start` sign convention still unverified in anger — 17 players have
   now moved price, so `verify_squad_value()` will catch it if wrong. Check logs
   once the deployment is fixed.

## Next session

- Did the deployment fix work? Did T−24h fire?
- GW4 review and transfer call.
- Phase 3 (mini-league EO) still not started — and note it would have caught the
  captaincy error, since it compares against rivals' actual picks.

## Standing reminders

- First-set chips expire at the **GW19 deadline, 13:30 GMT, 2 Jan 2027**. No
  blanks or doubles before then. Wildcard GW5–9, Bench Boost straight after.
- **GW33 (Sat 24 Apr 2027) collides with the FA Cup semi-finals** — second-set
  Free Hit target; doubles follow across GW34–37.
- The default recommendation is often "roll the transfer." That remains true.
- Captaincy is the single biggest lever. Revisit it every week, not once a season.