#!/usr/bin/env bash
# Robin health check — run on the VM from the repo: `bash scripts/robin_check.sh`
#
# Read-only. Prints the deployed commit, service state, startup lines, any
# errors since the current process started, and the FPL history state.
# Everything goes through the redaction filter (bot token, OpenRouter key,
# IPs, FPL entry ids) — the journal has leaked the bot token before.
#
# Exists because long multi-line pastes into Ollie's SSH terminal lose
# characters (Oct 2026); one short command can't be mangled.

set -u
cd "$(dirname "$0")/.." || exit 1

REDACT='s/[0-9]{8,10}:[A-Za-z0-9_-]{30,}/[REDACTED_TOKEN]/g'
REDACT="$REDACT"'; s/sk-or-[A-Za-z0-9-]+/[REDACTED_KEY]/g'
REDACT="$REDACT"'; s/([0-9]{1,3}\.){3}[0-9]{1,3}/[REDACTED_IP]/g'
REDACT="$REDACT"'; s#/entry/[0-9]+/#/entry/[ID]/#g'

{
  echo "== commit"
  git log -1 --format='%h %ad %s' --date=short

  echo "== service"
  systemctl is-active robin
  started=$(systemctl show robin -p ActiveEnterTimestamp --value)
  echo "running since: ${started:-unknown}"

  echo "== startup lines (expect Deployed commit + 2 'registered' lines = 9 jobs)"
  sudo journalctl -u robin --no-pager | grep -E 'Deployed commit|registered|FPL disabled' | tail -3

  echo "== errors since this process started (expect none)"
  if [ -n "${started:-}" ]; then
    sudo journalctl -u robin --no-pager --since "$started" | grep -iE 'traceback|error|conflict' | tail -15
  fi

  echo "== FPL history (gw, points, overall rank) and gameweeks marked final"
  python3 - <<'EOF'
import sqlite3
c = sqlite3.connect("file:assistant.db?mode=ro", uri=True)
print(c.execute("SELECT gw, points, overall_rank FROM my_history ORDER BY gw").fetchall())
print("final:", [r[0] for r in c.execute(
    "SELECT gw FROM notifications_sent WHERE kind = 'history_final' ORDER BY gw")])
EOF
} 2>&1 | sed -E "$REDACT"
