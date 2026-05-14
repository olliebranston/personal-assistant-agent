"""Logging filter that redacts API keys from all log output."""

import logging
import re


class ApiKeyScrubber(logging.Filter):
    """Remove api_key= values from log records before they're emitted.

    Catches keys in query strings (api_key=abc123) regardless of surrounding
    context. Applied to the root logger so it covers all libraries including
    httpx, openai, and python-telegram-bot.
    """

    _pattern = re.compile(r"(api[_-]key=)[^\s&\"']+", re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        # Interpolate args into msg first so the pattern can match the full string.
        if record.args:
            try:
                record.msg = record.getMessage()
                record.args = None
            except Exception:
                pass
        record.msg = self._pattern.sub(r"\1[REDACTED]", str(record.msg))
        return True


def install() -> None:
    """Attach the scrubber to every handler on the root logger."""
    scrubber = ApiKeyScrubber()
    for handler in logging.root.handlers:
        handler.addFilter(scrubber)
