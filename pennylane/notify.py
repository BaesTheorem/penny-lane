"""Outbound alerts: a macOS banner through mist-notify and a Discord DM.

Discord uses the MIST bot token (`DISCORD_BOT_TOKEN` in the bot's env file)
and the DM channel id (`DISCORD_NOTIFY_CHAT_ID` in the harness .env), the
same lane every other watcher here uses. Both paths are configurable in
config.json under `notify.discord`. Best effort: a failed send is logged,
never raised, because the scan must finish.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import urllib.request
from pathlib import Path

log = logging.getLogger("pennylane.notify")
HARNESS = Path.home() / "Documents" / "Exobrain harness"
DISCORD_API = "https://discord.com/api/v10"


def _env_value(path: Path, key: str) -> str | None:
    try:
        for line in path.read_text().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def discord(message: str, cfg: dict) -> bool:
    d = (cfg.get("notify") or {}).get("discord") or {}
    if not d.get("enabled", True):
        return False
    token = os.environ.get("DISCORD_BOT_TOKEN") or _env_value(Path(os.path.expanduser(
        d.get("token_file") or "~/.claude/channels/discord/.env")), "DISCORD_BOT_TOKEN")
    channel = os.environ.get("DISCORD_NOTIFY_CHAT_ID") or _env_value(Path(os.path.expanduser(
        d.get("env_file") or str(HARNESS / ".env"))), "DISCORD_NOTIFY_CHAT_ID")
    if not token or not channel:
        log.warning("discord skip: missing token or channel id")
        return False
    req = urllib.request.Request(
        f"{DISCORD_API}/channels/{channel}/messages", method="POST",
        data=json.dumps({"content": message[:1900]}).encode(),
        headers={"Authorization": f"Bot {token}", "Content-Type": "application/json",
                 "User-Agent": "DiscordBot (https://exobrain.local, 1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("discord send failed: %s", e)
        return False


def banner(title: str, message: str, url: str = "", context: str = "") -> None:
    cmd = [str(HARNESS / "mist-voice" / "bin" / "mist-notify"), message, title, "Glass"]
    if url:
        cmd.append(url)
    if context:
        cmd += ["--context", context]
    cmd += ["--group", "penny-lane"]
    try:
        subprocess.run(cmd, timeout=15, check=False, capture_output=True)
    except Exception as e:  # noqa: BLE001
        log.warning("banner failed: %s", e)
