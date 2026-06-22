"""Async wrapper around the OpenAI SDK pointed at OpenRouter. All LLM calls go through here."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import httpx
import openai
from openai import AsyncOpenAI

import config

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(
    api_key=config.OPENROUTER_API_KEY,
    base_url=config.OPENROUTER_BASE_URL,
    default_headers={
        "HTTP-Referer": "https://github.com/personal-assistant-agent",
        "X-Title": "Personal Assistant Bot",
    },
)

ToolExecutor = Callable[[str, dict], Awaitable[dict]]


def _is_rate_limit(exc: openai.APIError) -> bool:
    status = getattr(exc, "status_code", None)
    return status == 429 or "429" in str(exc) or "rate" in str(exc).lower()


async def _call_api(messages: list[dict], tools: list[dict] | None, max_attempts: int):
    """Make one chat completion call, retrying on openai.APIError with backoff.

    A 429 (rate limit) is never retried — a 1-2s backoff can't outrun a
    per-minute quota, so retrying just burns extra calls for nothing. It
    fails immediately on the first 429 instead.

    Returns the response message object (has .content and .tool_calls).
    Raises the last openai.APIError after max_attempts failures (or
    immediately on a 429).
    """
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            kwargs: dict = {"model": config.OPENROUTER_MODEL, "messages": messages}
            if tools:
                kwargs["tools"] = tools
            response = await _client.chat.completions.create(**kwargs)
            if not response.choices:
                raise openai.APIError(
                    f"OpenRouter returned empty choices (model: {config.OPENROUTER_MODEL})",
                    request=httpx.Request("POST", config.OPENROUTER_BASE_URL),
                    body=None,
                )
            return response.choices[0].message
        except openai.APIError as exc:
            last_exc = exc
            if _is_rate_limit(exc):
                logger.warning("OpenRouter rate limited (429) — not retrying")
                break
            if attempt < max_attempts - 1:
                wait = 2 ** attempt  # 1 s, then 2 s
                logger.warning(
                    "OpenRouter attempt %d/%d failed (%s), retrying in %ds",
                    attempt + 1, max_attempts, type(exc).__name__, wait,
                )
                await asyncio.sleep(wait)

    raise last_exc  # type: ignore[misc]


async def complete(
    messages: list[dict],
    system: str = "",
    history: list[dict] | None = None,
    tools: list[dict] | None = None,
    tool_executor: ToolExecutor | None = None,
    max_attempts: int = 3,
    max_tool_iterations: int = 5,
) -> str:
    """Send a chat completion to OpenRouter and return the response text.

    Args:
        messages:            The current turn's messages as OpenAI-format dicts.
        system:              Optional system prompt prepended to the conversation.
        history:             Prior conversation turns (from services.memory) for context.
        tools:               Optional OpenAI-format tool schemas. If None, behaviour is
                              identical to a plain chat completion (no tool-call loop).
        tool_executor:       Required if `tools` is set. Async callable
                              (tool_name, args) -> dict, invoked for each tool call the
                              model makes. Must not raise — any exception is caught and
                              converted to {"error": str(exc)} so one broken tool can't
                              crash the turn.
        max_attempts:        Retry attempts per API call on failure with exponential backoff.
        max_tool_iterations: Safety cap on the tool-call loop (only used when `tools` is set).

    Returns:
        The model's final reply as a plain string.

    Raises:
        openai.APIError after max_attempts failures on any single API call — handlers
        catch this and return a friendly error message to Ollie rather than crashing.
    """
    full_messages: list[dict] = []
    if system:
        full_messages.append({"role": "system", "content": system})
    if history:
        full_messages.extend(history)
    full_messages.extend(messages)

    if not tools:
        message = await _call_api(full_messages, None, max_attempts)
        return message.content

    message = None
    for _ in range(max_tool_iterations):
        message = await _call_api(full_messages, tools, max_attempts)

        if not message.tool_calls:
            return message.content

        full_messages.append({
            "role": "assistant",
            "content": message.content,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in message.tool_calls
            ],
        })

        for call in message.tool_calls:
            logger.debug("Tool call: %s(%s)", call.function.name, call.function.arguments)
            try:
                args = json.loads(call.function.arguments)
                result = await tool_executor(call.function.name, args)
            except Exception as exc:
                logger.warning("Tool '%s' failed: %s", call.function.name, exc)
                result = {"error": str(exc)}

            full_messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result),
            })

    logger.warning("Tool loop exceeded %d iterations", max_tool_iterations)
    return message.content or "Sorry, I got stuck working on that — try rephrasing."
