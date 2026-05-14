"""Async wrapper around the OpenAI SDK pointed at OpenRouter. All LLM calls go through here."""

from openai import AsyncOpenAI

import config

# Single client instance reused for every call — handles connection pooling internally.
# HTTP-Referer and X-Title are recommended by OpenRouter for free-model rate limit tracking.
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
) -> str:
    """Send a chat completion to OpenRouter and return the response text.

    Args:
        messages: The current turn's messages as OpenAI-format dicts.
        system:   Optional system prompt prepended to the conversation.
        history:  Prior conversation turns for context (from services.memory).
                  Inserted between the system prompt and the current message.

    Returns:
        The model's reply as a plain string.

    Raises:
        openai.APIError (and subclasses) on network or API failures.
    """
    full_messages: list[dict] = []
    if system:
        full_messages.append({"role": "system", "content": system})
    if history:
        full_messages.extend(history)
    full_messages.extend(messages)

    response = await _client.chat.completions.create(
        model=config.OPENROUTER_MODEL,
        messages=full_messages,
    )
    return response.choices[0].message.content
