"""Loads and exposes all environment variables. Import this everywhere instead of os.getenv directly."""

import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_ALLOWED_USER_ID: int = int(os.environ["TELEGRAM_ALLOWED_USER_ID"])

OPENROUTER_API_KEY: str = os.environ["OPENROUTER_API_KEY"]
OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "openrouter/free")

USDA_API_KEY: str = os.environ["USDA_API_KEY"]

GOOGLE_CREDENTIALS_FILE: str = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
GOOGLE_TOKEN_FILE: str = os.getenv("GOOGLE_TOKEN_FILE", "token.json")

WEBHOOK_URL: str = os.environ["WEBHOOK_URL"]
PORT: int = int(os.getenv("PORT", "8000"))
