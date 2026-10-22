---
name: verify-before-done
description: Use before saying any work is done, fixed, passing or ready to commit/push in this repo - runs the repo's real checks fresh and reports evidence, not assumptions. Also use when Ollie asks "is it working?" or before any git commit.
metadata:
  origin: merges obra/superpowers verification-before-completion (MIT) with ECC verification-loop (MIT), tailored for Ollie
---

# Verify Before Done

**Iron law: no completion claim without fresh evidence from this message.** "Should work", "looks right", "tests passed earlier" are not evidence. Ollie values accuracy over reassurance - an honest "2 checks fail" beats a confident "done".

## The gate

Before claiming any status:
1. **Identify** which command proves the claim.
2. **Run** it in full, now.
3. **Read** the output and exit code; count failures.
4. **Report** the actual state with the evidence, including anything skipped.

## This repo's checks

Run from the repo root. Use `.venv/` — it has the full dependency set (decided Oct 2026; `venv/` is missing pytest/ruff/PuLP and isn't maintained).

```bash
.venv/Scripts/python.exe -m pytest tests/ -q       # expect: N passed, 0 failed/errors
.venv/Scripts/python.exe -m ruff check .           # expect: All checks passed! (plain `ruff check .` if ruff is not in the venv)
.venv/Scripts/python.exe -c "import main"          # expect: no traceback
# Timezone rule: any new hit must pass tz= / config.TZ
grep -rnE "datetime\.now\(\)|date\.today\(\)" --include=*.py tools services bot storage main.py
# Offline-test rule: every hit must be mocked/monkeypatched, never a real call
grep -rnE "openrouter|OpenAI\(" tests/
```

Rules that make a change NOT READY even if tests pass:
- Any automated test that could hit OpenRouter for real (CLAUDE.md critical rule)
- Bare `datetime.now()` / `date.today()` in new code
- Hardcoded nutrition values that bypass `services/nutrition.lookup_macros`

If the change affects LLM behaviour (system prompt, tool schemas/descriptions, briefing composition), automated tests **cannot** prove it works. Say so, and give Ollie 2-3 specific Telegram messages to send as a manual smoke test (`python main.py` locally) with what a good reply looks like.

## Diff review (always)

```
git status
git diff --stat
git diff
```
Check every changed file for: unintended edits, debug prints left behind, secrets or tokens (`.env`, API keys, bot tokens), files outside the agreed plan.

## Report format

```
VERIFICATION
Checks:   <command> -> PASS/FAIL (detail)
          ...
Diff:     <n> files changed - all within plan? yes/no
Not verified: <anything you could not check, and why>
Status:   READY / NOT READY
```

## Red flags

- Using "should", "probably", "seems to" about the result
- Saying "Done!" before running checks
- Trusting a subagent's success report without checking the diff
- Treating lint-pass as proof that behaviour works
