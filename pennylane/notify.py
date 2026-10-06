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
import re
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


DEFAULT_MUTE = {
    # Category prefixes, matched case-insensitively against the item's category path.
    "categories": ["Beauty", "Personal Care/Shaving", "Personal Care/Skin", "Cosmetics", "Skin Care"],
    # Whole-word name patterns, for items whose category is blank or junk ("Shop by Brand").
    "keywords": [r"razors?", r"shav\w*", r"lotion", r"moisturi\w+", r"skin ?care", r"serum", r"cleanser",
                 r"sunscreen", r"spf", r"makeup", r"mascara", r"lipstick", r"lip (?:balm|gloss)", r"eyeliner",
                 r"beauty", r"liquid foundation", r"concealer", r"nail polish", r"hair colou?r",
                 r"cosmetics?", r"facial", r"body wash", r"deodorant"],
}


def muted(name: str, category: str, cfg: dict) -> bool:
    """True when an alert is for a product type Alex asked not to hear about.

    The alert still lands in the app; only the banner and the DM skip it.
    `notify.mute` in config.json replaces DEFAULT_MUTE when present.
    """
    m = (cfg.get("notify") or {}).get("mute") or DEFAULT_MUTE
    cat = (category or "").lower()
    if any(cat.startswith(c.lower()) for c in m.get("categories", [])):
        return True
    words = m.get("keywords", [])
    return bool(words) and re.search(r"\b(?:" + "|".join(words) + r")\b", name or "", re.I) is not None


def digest(alerts: list[dict], cfg: dict, top: int = 10) -> None:
    """One banner and one Discord DM for a whole run, never one per item.

    A run can raise 40+ alerts at once (a new community list lands on many
    stores), so per-item sends flooded the banner history and the DM.
    Pennies sort first, then by MSRP.
    """
    pennies = [a for a in alerts if a["kind"] == "penny_on_shelf"]
    soon = [a for a in alerts if a["kind"] != "penny_on_shelf"]
    alerts = sorted(alerts, key=lambda a: (a["kind"] != "penny_on_shelf", -(a["msrp"] or 0)))
    head = f"{len(pennies)} new on the shelf at a penny, {len(soon)} likely to go soon"
    best = alerts[0]
    banner("Penny Lane", f"{head}. Top: {best['name']} at {best['store']}"
           + (f" (MSRP ${best['msrp']:,.0f})" if best["msrp"] else ""),
           "http://localhost:5033", "\n".join(f"{a['kind']}: {a['name']} at {a['store']}" for a in alerts[:40]))
    lines = [f"**Penny Lane**: {head}"]
    for a in alerts[:top]:
        tag = "PENNY" if a["kind"] == "penny_on_shelf" else "soon"
        line = f"- {tag}: {a['name']} at {a['store']} ({a['retailer']})"
        if a["msrp"]:
            line += f", MSRP ${a['msrp']:,.2f}"
        if a["kind"] == "penny_on_shelf":
            line += f", qty {a['qty'] if a['qty'] is not None else '?'}"
            if a.get("sku"):
                line += f", SKU {a['sku']}"
        lines.append(line)
    if len(alerts) > top:
        lines.append(f"+{len(alerts) - top} more in the app")
    discord("\n".join(lines), cfg)
