---
name: systematic-debugging
description: Use when hitting any bug, failing test, failing GitHub Action, crash, or unexpected behaviour in this repo - before proposing any fix. Enforces root-cause investigation, a single tested hypothesis, and a regression test or check.
metadata:
  origin: adapted from obra/superpowers systematic-debugging (MIT), tailored for Ollie
---

# Systematic Debugging

**Iron law: no fix without a root cause.** A fix that makes the symptom disappear without explaining why is a guess, and guesses cost Ollie more sessions later.

## Phase 1 - Investigate (no edits yet)

1. **Read the full error.** Whole stack trace / Actions log, exact file and line. Don't skim.
2. **Reproduce it.** Find the smallest command that triggers it. If you can't reproduce, gather more evidence - don't guess.
3. **Check what changed.** `git log --oneline -15`, `git diff`, recent dependency or data changes. Most bugs live in the last few commits.
4. **Check known patterns.** Read `docs/LEARNINGS.md` (if present) and the known-bug list below. Same root cause, new symptom, is common.
5. **Multi-component systems:** find which boundary fails before theorising (e.g. API -> fetch script -> JSON in data/ -> JS render). Log/inspect the data at each boundary once, then narrow.

## Phase 2 - Compare

Find a working example of the same thing in this codebase and list every difference between working and broken. Don't dismiss small differences.

## Phase 3 - One hypothesis

State it plainly: "I think X is the root cause because Y." Test it with the smallest possible change or experiment. One variable at a time. If it's wrong, form a new hypothesis - don't stack fixes.

## Phase 4 - Fix

1. Where practical, write a failing test or check that captures the bug first.
2. Fix at the source, not where the symptom shows.
3. Run the test/check again and the wider suite (see `verify-before-done`).

## Three-strikes rule

If three fix attempts have failed, **stop**. Tell Ollie what's been tried, what was learned, and that the problem is probably architectural or a wrong assumption. Discuss before attempt four.

## Red flags - you're guessing

- "Let me just try..." / "This should fix it"
- Changing several things at once
- Proposing a fix before reproducing
- Adding retries/try-except to make an error go away

## Report format

```
SYMPTOM: ...
ROOT CAUSE: ... (evidence: ...)
FIX: ... (file:line)
PROOF: <command + result>
PATTERN: <one line for docs/LEARNINGS.md via session-wrap, if non-obvious>
```

## Known bugs and gotchas in this repo (check these first)

- **Wrong day / off-by-one dates**: server isn't in UK time. Always `ZoneInfo("Europe/London")` / `config.TZ` (commit `fcd1d4b`).
- **Rate-limit failures multiplying**: Robin's retry logic stacking on the OpenAI SDK's own retries. Consolidate to one retry layer.
- **Model "forgets" to call tools / malformed multi-tool JSON**: model choice. DeepSeek V3 managed ~53% on multi-tool turns. Evaluate any model change on multi-tool reliability before switching.
- **Google Calendar auth dies after ~7 days**: OAuth app left in Testing mode. Must be Published.
- **Nutrition values wildly off for oats/tuna/potatoes/coconut milk**: USDA mismatch. Use the fallback table / user calibration table.
- **Migration crashes on second run**: wrap each `ALTER TABLE` in its own try/except.
- **asyncio errors from `run_polling()`**: Python 3.14. Use 3.12.
- **Weird import or package errors locally**: check you're using `.venv/` (the maintained local env since Oct 2026), not `venv/`.
- **Gym exercises sent as separate messages split into separate sessions**: known open issue. Check the today-session lookup in `tools/gym.py`.
