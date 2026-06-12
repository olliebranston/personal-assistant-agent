"""Async wrapper around the OpenAI SDK pointed at OpenRouter. All LLM calls go through here."""

from __future__ import annotations

import asyncio
import logging

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


async def complete(
    messages: list[dict],
    system: str = "",
    history: list[dict] | None = None,
    max_attempts: int = 3,
) -> str:
    """Send a chat completion to OpenRouter and return the response text.

    Args:
        messages:     The current turn's messages as OpenAI-format dicts.
        system:       Optional system prompt prepended to the conversation.
        history:      Prior conversation turns (from services.memory) for context.
        max_attempts: Retry attempts on API failure with exponential backoff.

    Returns:
        The model's reply as a plain string.

    Raises:
        openai.APIError after max_attempts failures — handlers catch this and
        return a friendly error message to Ollie rather than crashing.
    """
    full_messages: list[dict] = []
    if system:
        full_messages.append({"role": "system", "content": system})
    if history:
        full_messages.extend(history)
    full_messages.extend(messages)

    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            response = await _client.chat.completions.create(
                model=config.OPENROUTER_MODEL,
                messages=full_messages,
            )
            if not response.choices:
                raise openai.APIError(
                    f"OpenRouter returned empty choices (model: {config.OPENROUTER_MODEL})",
                    response=response,
                    body=None,
                )
            return response.choices[0].message.content
        except openai.APIError as exc:
            last_exc = exc
            if attempt < max_attempts - 1:
                wait = 2 ** attempt  # 1 s, then 2 s
                logger.warning(
                    "OpenRouter attempt %d/%d failed (%s), retrying in %ds",
                    attempt + 1, max_attempts, type(exc).__name__, wait,
                )
                await asyncio.sleep(wait)

    raise last_exc  # type: ignore[misc]
