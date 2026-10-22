---
name: plan-before-code
description: Use before changing code for any feature, refactor, integration or multi-file fix in this repo - produces a short agreed plan (files, approach, verification) and waits for Ollie's sign-off before any edit. Skip for one-line fixes, typo edits, or when Ollie says "just do it".
metadata:
  origin: adapted from obra/superpowers writing-plans + brainstorming (MIT), tailored for Ollie
---

# Plan Before Code

Ollie wants a plan agreed before code is touched, the simplest version first, and an explanation of what changes do and why. This skill turns that into a fixed procedure.

## The rule

**No Edit/Write to project files until Ollie has approved the plan in this conversation.** Reading, searching and running read-only commands are fine.

Exceptions: single-line fixes, typo/copy edits, or when Ollie explicitly says to go ahead without a plan.

## Procedure

1. **Restate the goal in one sentence.** If the request is ambiguous (dictation errors are common - words may be misheard), ask one clarifying question instead of guessing.
2. **Investigate first.** Read the files involved, the relevant CLAUDE.md sections and `docs/LEARNINGS.md` if it exists. Find the existing pattern this change should follow. Never invent a second architecture when one exists.
3. **Write the plan** in chat, using this shape and nothing longer than it needs to be:

```
GOAL: <one sentence>
SIMPLEST VERSION: <what the minimum useful change is>
FILES: <path - what changes and why> (one line each)
APPROACH: <2-4 sentences, incl. the existing pattern being followed>
NOT DOING: <tempting extras deliberately left out>
RISKS / TRADEOFFS: <shortcut vs proper approach, if there is one worth flagging>
VERIFICATION: <exact commands/checks that will prove it works>
```

4. **Stop and wait for approval.** Do not start editing in the same message.
5. **Execute one step at a time.** Ollie works sequentially. After each meaningful change, say in 1-2 lines what changed and why.
6. **If the plan turns out wrong mid-build** (unexpected structure, failing assumption), stop, say what you found, and propose the revised plan. Don't silently drift.
7. **Finish with `verify-before-done`.**

## Red flags - stop and re-plan

- The change is touching files not listed in FILES
- You're adding a dependency, a framework, or a new abstraction layer
- You're "improving" adjacent code nobody asked about
- The plan has more than ~6 files for what Ollie described as a small change

## Scope check

If the request really contains several independent pieces, say so and propose doing them one at a time, in an order, each with its own verification.
