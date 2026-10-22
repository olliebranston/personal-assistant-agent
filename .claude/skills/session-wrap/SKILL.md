---
name: session-wrap
description: Use at the end of a meaningful work session in this repo (a debug, a non-obvious decision, a rework, or a new feature), or when Ollie says "wrap up" - captures transferable lessons in docs/LEARNINGS.md and keeps CLAUDE.md accurate. Skip for trivial edits.
metadata:
  origin: adapted from ECC growth-log (MIT), tailored for Ollie
---

# Session Wrap

The point is that Ollie and Claude don't re-learn the same lesson twice. A lesson is only useful if it changes what happens next time.

## When it's worth it

Only if the session involved debugging, rework, a rollback, or a non-obvious decision. Typo fixes and routine content updates: skip.

## Procedure

1. **Extract 0-3 lessons.** For each: what happened, root cause (ask "why" until you hit the mechanism), and the rule for next time.
2. **De-duplicate.** Search `docs/LEARNINGS.md` for the same root cause. Same cause, new symptom → add the symptom to the existing entry instead of creating a new one.
3. **Append** new entries to `docs/LEARNINGS.md` (create it with a `# Learnings` heading if missing) using:

```markdown
## <the pattern, not the event>
- Context: <1 sentence>
- Root cause: <the mechanism>
- Next time: when <signal>, do <action>.
- Seen: YYYY-MM-DD (<commit or file>)
```

4. **Check CLAUDE.md is still true.** If the session changed a command, file location, architecture rule, or added a hard-won rule that every future session must know, propose the exact edit to CLAUDE.md. Keep CLAUDE.md short - rules there, history in LEARNINGS.md.
5. **Show Ollie the proposed additions** as a diff and apply on approval. Never commit without being asked.

## Quality bar

- Title names the pattern ("Server isn't in UK time - always use ZoneInfo") not the event ("fixed timezone bug")
- There is a "Next time" line with a concrete trigger
- 3-6 lines per entry. Longer = narrating; shorter = not understood yet
