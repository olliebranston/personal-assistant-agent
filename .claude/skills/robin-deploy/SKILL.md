---
name: robin-deploy
description: Use when Ollie wants to deploy Robin to the Oracle VM, says "push it live", "deploy", or "restart Robin", or asks whether the live bot is healthy - runs the verify, push, pull, restart and log-check sequence and confirms the service is actually healthy.
metadata:
  origin: new - encodes the deploy workflow in README.md
---

# Deploy Robin

Robin runs as the `robin` systemd service on an Oracle Cloud Always Free ARM VM (Ubuntu 22.04). SSH details are in README.md. **Never print, paste or log the SSH key path contents, `.env`, or the Telegram bot token.** The token has appeared in logs before, so redact it when quoting journal output.

## 1. Pre-flight (local)

- `verify-before-done` must be READY. No deploying with failing tests.
- `git status` clean, on `main`, and `git log origin/main..HEAD --oneline` shows exactly what's going live. Read it back to Ollie in one line each.
- If the change adds an env var or a dependency: flag that the VM needs the `.env` updated / `pip install -r requirements.txt` in its venv **before** restart.

## 2. Push

`git push origin main`. Only after Ollie confirms.

## 3. Deploy on the VM

Over SSH (command from README.md):

```
cd ~/personal-assistant-agent && git pull --ff-only   # adjust if the clone lives elsewhere - check with `ls ~` once and fix this line
# only if requirements changed:
venv/bin/pip install -r requirements.txt
sudo systemctl restart robin
```

If `git pull --ff-only` fails, **stop**. The VM has local changes or has diverged. Show Ollie `git status` from the VM. Don't force anything.

## 4. Health check (don't skip)

Preferred: `bash scripts/robin_check.sh` on the VM — commit, active state, startup lines,
errors since the current process started, and FPL history, all redacted. Give Ollie VM
commands **one short line at a time**: his terminal drops characters from long multi-line
pastes (Oct 2026).

Manual equivalent:

```
sudo systemctl is-active robin                     # expect: active
sudo systemctl status robin --no-pager -n 0        # uptime should reset to seconds ago
sudo journalctl -u robin --since "2 min ago" --no-pager | tail -40
```

Look for: tracebacks, `Conflict: terminated by other getUpdates request` (another instance polling, e.g. one still running locally), auth errors (Google token expiry, Racing API 401), and scheduler jobs registering.

## 5. Report

```
DEPLOYED <short sha> - <n> commits
Service: active (since HH:MM)
Logs: clean / <issues>
Manual check: send Robin "<one message relevant to the change>" and confirm the reply.
```

## Rollback

If the bot is broken after deploy: on the VM `git log --oneline -5`, then `git checkout <previous sha>` and `sudo systemctl restart robin`, re-check, then debug locally with `systematic-debugging`. Afterwards tell Ollie the VM is on a detached HEAD so the next deploy needs `git checkout main && git pull`.

## Automating this later

The natural next step is a GitHub Actions workflow that runs tests on push and deploys over SSH on success (SSH key stored as a repo secret). Propose it only if Ollie asks. It changes where the key lives.
