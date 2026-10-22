---
name: robin-add-tool
description: Use when adding a new capability, integration or API to Robin (e.g. Garmin, racing results, a new data source, a new Telegram command) - matches the existing tools/*.py + registry pattern with offline mocked tests, instead of inventing a new architecture.
metadata:
  origin: adapted from ECC api-connector-builder (MIT), tailored to Robin's tool-calling design
---

# Add a Tool to Robin

Robin has one architecture: **a single LLM call with tool-calling; domain logic in deterministic `tools/*.py`; external I/O in `services/*.py`; storage in `storage/models.py`.** New capabilities slot into it. Don't add agents, routers or classify-then-route layers (that pattern was deliberately removed).

Start with `plan-before-code`. The plan must name every file below that changes.

## Anatomy (copy the closest existing domain, e.g. `tools/running.py`)

1. **`services/<name>.py`** - only if there's external I/O (HTTP API, RSS, OAuth). Async (`httpx`), returns plain dicts, handles its own errors and timeouts, reads secrets from `config`/`.env`. No LLM calls here.
2. **`storage/models.py`** - only if persisting. Add a `*_DDL` constant, dataclass, insert/get functions. Any migration: each `ALTER TABLE` in its own try/except.
3. **`tools/<domain>.py`** - module docstring citing the `TOOL_CALLING_DESIGN.md` section. Each tool is `async def name(conn, **kwargs) -> dict`, JSON-serialisable, returns `{"error": "..."}` on failure and never raises to the model. Dates via `config.TZ`. Export `TOOL_SCHEMAS` (OpenAI function schema list).
4. **`tools/registry.py`** - import the module, add `*<domain>.TOOL_SCHEMAS` to `schemas`, add `functools.partial(<domain>.<fn>, conn)` entries to `dispatch`. Schema names must match dispatch keys exactly.
5. **`main.py` `_ROBIN_SYSTEM`** - only if the model needs static domain knowledge or a behaviour rule. Keep it short. Facts the tool returns don't belong in the prompt.
6. **`docs/context/TOOL_CALLING_DESIGN.md`** - add the new section the docstrings cite.
7. **`config.py` / `.env.example`** - any new env var, with a placeholder value. Never put real keys in `.env.example`.

## Tool schema quality (this is what the model actually reads)

- Descriptions say **when** to call the tool and when NOT to (distinguish it from near-neighbour tools)
- Parameters: tight types, enums where possible, units in the name (`distance_km`)
- Fewer, well-described tools beat many overlapping ones. Multi-tool reliability is Robin's known weak spot

## Tests (`tests/test_tools_<domain>.py`, and `tests/test_services_<name>.py`)

- In-memory SQLite built from the DDL constants (see `_make_conn()` in `tests/test_tools_running.py`)
- `@pytest.mark.asyncio` for async tools
- Monkeypatch every HTTP call. **Zero real network, zero OpenRouter calls.**
- Cover: happy path, missing/optional params, the `{"error": ...}` path, and a timezone-sensitive date if dates are involved
- Add a registry test asserting the new names are in both `schemas` and `dispatch`

## Done means

`verify-before-done` passes, **plus** a manual smoke test list for Ollie: 2-3 natural Telegram messages that should trigger the new tool, and one that should NOT, with the expected behaviour.
