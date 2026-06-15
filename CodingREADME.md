# Claude Code: Quality & Testing Playbook
### For the Robin Personal Assistant Project

> **Who this is for:** You, working alone in VS Code with Claude Code, Python 3.12, a deployed Telegram bot on Oracle Cloud, and a vibe-coding approach that you want to make more robust without over-engineering it.

---

## The Honest Framing

Claude Code's own team found that unguided sessions succeed ~33% of the time, and the tool's creator abandons 10–20% of sessions. The gap between sessions that produce good code and sessions that burn tokens going nowhere isn't about prompting magic — it's about the discipline you put *around* the tool. This document is that discipline.

The goal is not to make Robin a production-grade enterprise system. It's to catch bugs before they surface at 7am when your morning briefing fails, to keep the codebase navigable as agents multiply, and to avoid the classic vibe-coding trap: everything works until suddenly nothing does and you can't trace why.

---

## 1. The Non-Negotiables (Do These Before Anything Else)

### 1.1 Your CLAUDE.md

This file lives at the root of your repo. Claude reads it at the start of every session. Without it, every session starts blind. With it, Claude Code has persistent context it can't infer from the code alone.

Run `/init` in Claude Code to generate a starter, then shape it to match Robin specifically. Keep it short — it's loaded every session, so bloat here costs you tokens on everything.

**Recommended structure for Robin:**

```markdown
# Robin — Personal Assistant Bot

## Project Overview
Telegram bot with modular agents: gym, meal, calendar, news.
Deployed as a systemd service on Oracle Cloud (Ubuntu 22.04, ARM).
Python 3.12. Virtual env must be named `venv` (not `.venv`).

## Stack
- python-telegram-bot v21 (polling mode)
- OpenRouter API (free tier) — model: claude-sonnet-4-6 or current free model
- SQLite for storage
- Google Calendar API (OAuth)

## Critical Rules
- Never use `.venv` — use `venv` (Python 3.14 compatibility issue)
- Verify nutrition values against USDA FoodData Central, never hardcode macros
- Racing/sports results must use structured APIs, not RSS + LLM (hallucination risk)
- OpenRouter model strings go stale — check if model is still active when adding new ones

## Deployment Workflow
Edit locally → git push → SSH into Oracle → git pull && sudo systemctl restart robin

## Code Style
- Each agent lives in its own module under /agents/
- Handlers in /handlers/, services in /services/
- Functions should do one thing; if it needs a long comment to explain it, split it
- All API calls wrapped in try/except with meaningful error messages

## Testing
- Run tests before any commit: `python -m pytest tests/ -v`
- New agent features need at least one integration test
- Tests live in /tests/ mirroring the module structure

## Common Commands
- Start bot locally: `python bot.py`
- Run tests: `python -m pytest tests/ -v`
- Check logs on Oracle: `sudo journalctl -u robin -f`
- Restart service: `sudo systemctl restart robin`
```

Adjust as the project evolves. The CLAUDE.md is a living document — update it when you add agents, change the stack, or burn yourself on a recurring mistake.

---

## 2. Workflow: The Four Phases

Never let Claude go straight from prompt to code. The pattern that consistently produces better results:

### Phase 1: Explore (Plan Mode)
Use Claude Code's plan mode (`Shift+Tab` to toggle, or prefix prompts with "don't write any code yet"). Let Claude read the relevant files and understand the current state.

```
"Read /agents/meal_agent.py and /services/nutrition.py and understand
how meals are currently looked up and logged. Don't change anything."
```

### Phase 2: Plan
Ask for a written plan before any code is touched.

```
"I want to add a Friday shopping list feature to the meal agent.
What files need to change? What's the data flow? Write a plan."
```

Then **edit the plan yourself** before proceeding. Add inline notes where Claude made wrong assumptions:
- "use SQLite, not a new file"
- "this should trigger on Friday morning, not on demand"

Send the annotated plan back with: `"Address all notes in this plan, don't implement yet."` Iterate until the plan is unambiguous.

### Phase 3: Implement with a Verifiable Check
When you approve the plan, give Claude something it can run to verify its own work.

```
"Implement the shopping list feature from the plan. Write a test for it
in tests/test_meal_agent.py. Run the tests and fix any failures before
finishing."
```

The critical principle: **Claude stops when the work *looks* done. Tests make "looks done" objective.** Without them, you become the verification loop.

### Phase 4: Commit
Small, descriptive commits. Not "stuff" or "updates". One logical change per commit.

```
"Commit this with a descriptive message. Don't bundle the shopping list
feature and the macro fix in one commit — they're separate changes."
```

---

## 3. Testing Strategy for Robin

### The Minimum You Actually Need

Robin is a personal project, not a bank. You don't need 100% coverage. You need enough tests to catch the things that will silently break and ruin your morning. That means:

**Priority 1 — Agent logic** (the thing most likely to regress when you add features)
**Priority 2 — External API calls** (the thing most likely to fail due to changes outside your control)
**Priority 3 — Data parsing** (the thing most likely to produce wrong answers without crashing)

### Test Structure

```
robin/
├── tests/
│   ├── conftest.py          # Shared fixtures (mock Telegram bot, mock API responses)
│   ├── test_gym_agent.py
│   ├── test_meal_agent.py
│   ├── test_calendar_agent.py
│   ├── test_news_agent.py
│   └── test_services/
│       ├── test_nutrition.py
│       └── test_racing.py   # When you replace the RSS feed
```

### What to Test per Agent

**Gym agent**
- Correct routine returned for each day of the PPL split
- Rest day handling
- Response doesn't crash if DB has no recent logs

**Meal agent**
- Macro calculations are correct (pick 3-4 known meals, hardcode expected values)
- Shopping list generates on correct day
- Alcohol is logged by calories (not units) — easy to regress
- No moralising text appears in output (test this explicitly — it will drift)

**Calendar agent**
- Event creation with correct time/timezone (London, BST/GMT depending on season)
- Empty calendar response handled gracefully

**News agent**
- RSS/API failure returns a useful fallback message, not a crash
- Racing results come from structured data, not summarised text

### Writing Tests Claude Code Won't Break

This is the key vulnerability: when a test fails, Claude Code will sometimes fix the test rather than fix the code. To prevent this:

1. **Commit tests before asking Claude to implement against them.** If Claude changes a test, the diff will show it.
2. **Make expected values explicit.** `assert result == 230` is harder to game than `assert result > 0`.
3. **Test structure, not just success.** For the news agent: assert the response *contains* a section header, assert no strings like "I think" or "reportedly" appear (hallucination markers).

### The Minimum Test Setup

Install pytest:
```bash
pip install pytest pytest-mock
```

A minimal `conftest.py`:
```python
import pytest
from unittest.mock import AsyncMock, MagicMock

@pytest.fixture
def mock_update():
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    return update

@pytest.fixture
def mock_context():
    return MagicMock()
```

A minimal example test:
```python
# tests/test_meal_agent.py
import pytest
from agents.meal_agent import calculate_macros

def test_macro_calculation_known_meal():
    # 200g salmon: ~40g protein, ~12g fat, ~0g carbs (verify against USDA)
    result = calculate_macros("salmon", 200)
    assert abs(result["protein"] - 40) < 3  # within 3g tolerance
    assert result["carbs"] < 2

def test_no_moralising_text():
    # Run through the meal response generator and check tone
    response = generate_meal_response(meal="fish and chips", alcohol_units=2)
    forbidden_phrases = ["you should", "be careful", "perhaps consider"]
    for phrase in forbidden_phrases:
        assert phrase.lower() not in response.lower()
```

### Running Tests

Make this a reflex, not an afterthought:

```bash
# Before every commit
python -m pytest tests/ -v

# Quick smoke test on a single agent
python -m pytest tests/test_meal_agent.py -v

# With output on failures (more useful than the default)
python -m pytest tests/ -v --tb=short
```

---

## 4. Code Quality Checks

### Linting

Install and run `ruff` — it's faster than flake8/pylint and Claude Code understands its output:

```bash
pip install ruff
ruff check .
ruff format .
```

Add a `ruff.toml` or `pyproject.toml` section:
```toml
[tool.ruff]
line-length = 100
target-version = "py312"
```

Tell Claude in your CLAUDE.md: `"Run ruff check . and ruff format . on any new files before finishing."`

### Type Hints

You don't need full mypy coverage. But adding type hints to function signatures costs almost nothing and gives Claude Code better context about your intent, which reduces the class of bugs it introduces.

```python
# Instead of:
def get_routine(day):
    ...

# Do:
def get_routine(day: str) -> dict[str, list[str]]:
    ...
```

### What Claude Code Commonly Gets Wrong in This Project

From the patterns in your codebase, watch for these specific failure modes:

| Issue | What Happens | How to Catch It |
|---|---|---|
| Hardcoded nutrition values | Protein estimates are often 20–30% off | Unit test against USDA values |
| Model string goes stale | OpenRouter silently fails or degrades | Log model name in each response during dev |
| `.venv` vs `venv` | Bot won't start on Oracle | CLAUDE.md rule + test your deploy script |
| Timezone errors | Calendar events created in UTC not BST | Integration test with known event times |
| RSS hallucination | Racing results fabricated | Use structured API; test that response matches source data format |
| Moralising meal responses | Agent adds unsolicited commentary | Explicit phrase-matching test |

---

## 5. Context Management (The Most Important Invisible Thing)

Claude's context window fills up and performance degrades as it does. In a long session, Claude starts forgetting earlier instructions and making more mistakes. This is the primary failure mode in Claude Code.

**Rules:**

- **Use `/clear` aggressively.** After finishing one feature, clear context before starting the next. Don't carry a debugging session's context into a new feature build.
- **One task per prompt.** Multi-task prompts produce code where Claude trades off between objectives in ways you can't see.
- **If Claude seems "confused" or is repeating itself** — context is probably degraded. `/clear` and restate the task cleanly.
- **Long file reads are expensive.** Reference specific files with `@filename` rather than asking Claude to "look at the codebase." The CLAUDE.md handles the global context; prompts handle the specific.

---

## 6. Git Discipline

### The Pattern That Prevents Pain

```
main                    ← stable, deployed code only
├── feature/morning-briefing
├── fix/chelsea-rss-feed
└── feature/racing-api
```

Never work directly on `main`. Even solo. The 30 seconds to create a branch has saved hours of untangling bad deploys.

**Commit rules:**
- Commit after every logical unit of work, not at end of day
- One concern per commit — don't bundle a bug fix with a new feature
- Message format: `feat: add Friday shopping list to meal agent` or `fix: handle empty Chelsea RSS feed gracefully`
- Before deploying: `git log --oneline main..HEAD` — read every commit and know what you're pushing

**Before every deploy to Oracle:**
```bash
python -m pytest tests/ -v           # all tests pass
ruff check .                          # no lint errors
git diff main                         # review what's actually changing
git push origin main
# then SSH in:
git pull && sudo systemctl restart robin
sudo journalctl -u robin -f          # watch logs for 30 seconds
```

---

## 7. Prompting Patterns That Actually Work

These are prompts that produce consistently better Claude Code output. Bookmark them.

**For new features:**
```
"I want to build [feature]. Don't write any code yet.
Read [file1] and [file2] to understand the current approach,
then write a plan. I'll review it before you implement."
```

**For debugging:**
```
"The [agent/feature] is failing with [exact error message].
The error appears to come from [file]. Read it, identify the root cause,
and write a failing test that reproduces the issue before fixing it."
```

**For code review:**
```
"Review [file] for bugs, edge cases, and anything that could fail
silently. Flag only things that affect correctness or the stated
requirements — don't refactor for style."
```

**For testing:**
```
"Write pytest tests for [function/module]. Cover: [list 3-4 specific
scenarios including at least one failure case]. Use fixtures from
conftest.py. Make the expected values explicit — no generic assertions."
```

**The challenge prompt (use when output feels mediocre):**
```
"Before you make a PR, grill me on these changes.
What could go wrong? What edge cases haven't we handled?"
```

**The reset prompt (when a session has gone sideways):**
```
"Ignore everything we've tried so far. Now that you know all the
constraints, what's the cleanest solution to the original problem?"
```

---

## 8. Experimentation and Validation

When you add a new agent or integration (e.g., Racing API, morning briefing), don't deploy blind. Use this pattern:

### Pre-Deploy Checklist
- [ ] Feature works locally with `python bot.py`
- [ ] Unit tests pass
- [ ] You've tested the actual Telegram message output (read it; is it what you'd want to receive?)
- [ ] External API call has error handling — what happens if it returns nothing?
- [ ] No credentials or API keys in committed code
- [ ] CLAUDE.md updated if the new feature changes any assumptions

### Manual Smoke Tests (Per Agent)

Keep a `scratch/smoke_test.py` (gitignored) for quick manual validation:

```python
# scratch/smoke_test.py — gitignored
# Run this manually to sanity check agents before deploying

from agents.meal_agent import MealAgent
from agents.gym_agent import GymAgent

# Quick smoke tests — not pytest, just manual sanity
agent = MealAgent()
print(agent.get_todays_meals())  # does it return something sensible?

gym = GymAgent()
print(gym.get_todays_routine())  # is today's split correct?
```

### Evaluating Agent Output Quality

For LLM-powered agents (news, meal planning), output quality is harder to test than correctness. Use a simple rubric:

| Criterion | What to Check |
|---|---|
| **Accuracy** | Does it only state things that are true? (cross-check racing results against Racing API) |
| **Tone** | Does it sound like Robin or like a generic chatbot? |
| **Completeness** | Does it cover everything you'd expect? |
| **Failure behaviour** | What happens when the API is down, returns nothing, or returns garbage? |

Run the agent 3 times with the same input. If you get meaningfully different outputs each time, your prompt needs more constraint.

---

## 9. What Good Looks Like (Reference)

A well-structured Robin session:

1. Open Claude Code, CLAUDE.md loads automatically
2. `/clear` if previous session was on a different feature
3. Describe intent in plan mode: "Read the news agent and explain what sources it currently uses"
4. Claude reads files, explains the state
5. You ask for a plan for the new feature
6. You review and annotate the plan
7. Claude implements with tests
8. You run `python -m pytest tests/ -v` — everything green
9. You run the bot locally and read the Telegram output
10. You commit with a clear message
11. You deploy and watch the logs

A session that's going wrong:
- Claude is touching files you didn't ask about
- Claude is explaining what it's doing rather than doing it
- The context is 50k+ tokens and responses are getting vague
- Tests are passing but Claude changed the expected values to make them pass

When you notice these — stop. `/clear`. Restart with a tighter scope.

---

## 10. Quick Reference

### Commands to Know

```bash
# Testing
python -m pytest tests/ -v
python -m pytest tests/test_meal_agent.py -v --tb=short

# Linting
ruff check .
ruff format .

# Local run
python bot.py

# Deploy
git push origin main
ssh -i "path/to/ssh-key" ubuntu@140.238.77.214
git pull && sudo systemctl restart robin
sudo journalctl -u robin -f

# Check service status
sudo systemctl status robin
```

### Files Claude Code Should Always Know About

| File | Purpose |
|---|---|
| `CLAUDE.md` | Global project context for Claude |
| `CONTEXT.md` | High-level architecture notes |
| `Gym-CONTEXT.md` | Gym agent domain knowledge |
| `Mealplan-CONTEXT.md` | Meal agent domain knowledge |
| `tests/conftest.py` | Shared test fixtures |

### The Cardinal Sins (Don't Do These)

- Hardcode nutrition values — always verify against USDA
- Use RSS + LLM for factual sports data — it hallucinates
- Work directly on `main` without a branch
- Deploy without running tests
- Let a Claude session run for hours on a complex task without `/clear`ing between sub-tasks
- Trust Claude's "it works" without running a test or reading the actual output

---

*Keep this updated. When you burn yourself on something new, add it here.*