"""Loads and exposes all environment variables. Import this everywhere instead of os.getenv directly."""

import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

# Single source of truth for the bot's operating timezone — every tool/job
# used to independently define its own `_TZ = ZoneInfo("Europe/London")`.
# TZ_NAME (a plain string) exists because the Google Calendar API's
# timeZone field wants a string, not a tzinfo object.
TZ_NAME: str = "Europe/London"
TZ: ZoneInfo = ZoneInfo(TZ_NAME)

TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_ALLOWED_USER_ID: int = int(os.environ["TELEGRAM_ALLOWED_USER_ID"])

OPENROUTER_API_KEY: str = os.environ["OPENROUTER_API_KEY"]
OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "openrouter/free")

USDA_API_KEY: str = os.environ["USDA_API_KEY"]

GOOGLE_CREDENTIALS_FILE: str = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
GOOGLE_TOKEN_FILE: str = os.getenv("GOOGLE_TOKEN_FILE", "token.json")

RACING_API_USERNAME: str = os.getenv("RACING_API_USERNAME", "")
RACING_API_PASSWORD: str = os.getenv("RACING_API_PASSWORD", "")

FPL_ENABLED: bool = os.getenv("FPL_ENABLED", "false").lower() == "true"
FPL_TEAM_ID: int = int(os.getenv("FPL_TEAM_ID", "0"))
FPL_LEAGUE_ID: int = int(os.getenv("FPL_LEAGUE_ID", "0"))
