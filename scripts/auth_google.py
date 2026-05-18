"""One-time Google OAuth setup. Run this before starting the bot for the first time.

Usage:
    python scripts/auth_google.py

Opens a browser window to authenticate with Google. On success, writes token.json
to the project root (or wherever GOOGLE_TOKEN_FILE points). After that, the bot
handles token refresh automatically — you should not need to run this again.

Requires credentials.json (OAuth client secret) to exist at GOOGLE_CREDENTIALS_FILE.
Download it from Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client.
"""

import sys
from pathlib import Path

# Allow running from the project root or from the scripts/ subdirectory.
sys.path.insert(0, str(Path(__file__).parent.parent))

from google_auth_oauthlib.flow import InstalledAppFlow

import config

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def main() -> None:
    creds_path = Path(config.GOOGLE_CREDENTIALS_FILE)
    token_path = Path(config.GOOGLE_TOKEN_FILE)

    if not creds_path.exists():
        print(f"ERROR: credentials file not found at '{creds_path}'.")
        print("Download it from Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client IDs.")
        sys.exit(1)

    print(f"Opening browser for Google authentication...")
    print(f"Credentials: {creds_path}")
    print(f"Token will be saved to: {token_path}\n")

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
    creds = flow.run_local_server(port=0)

    token_path.write_text(creds.to_json())
    print(f"\nAuthentication complete. Token written to '{token_path}'.")
    print("You can now start the bot with: python main.py")


if __name__ == "__main__":
    main()
