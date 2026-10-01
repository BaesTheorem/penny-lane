"""User configuration: home zip, radius, which stores to watch per retailer.

Lives in `config.json` at the repo root (gitignored, it holds a home zip).
`config.example.json` is the committed template. Values can be overridden
with PENNYLANE_CONFIG (path) for tests.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("PENNYLANE_CONFIG") or ROOT / "config.json")
DATA_DIR = Path(os.environ.get("PENNYLANE_DATA") or ROOT / "data")
DB_PATH = DATA_DIR / "pennylane.db"
LOG_DIR = Path.home() / "Library" / "Logs" / "exobrain"
PORT = int(os.environ.get("PENNYLANE_PORT") or 5033)

DEFAULTS = {
    "zip": "",
    "radius_miles": 25,
    "retailers": {
        "homedepot": {"enabled": True, "stores": []},
        "walmart": {"enabled": True, "stores": []},
        "lowes": {"enabled": True, "stores": []},
        "dollargeneral": {"enabled": True, "stores": []},
    },
    "sources": {"enabled": True},
    "notify": {"enabled": True, "min_score": 70},
    "sweep": {"homedepot_pages": 400, "request_gap_s": 0.6},
}


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    if CONFIG_PATH.exists():
        user = json.loads(CONFIG_PATH.read_text())
        merge(cfg, user)
    return cfg


def save(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")


def merge(base: dict, over: dict) -> None:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            merge(base[k], v)
        else:
            base[k] = v


def stores_for(cfg: dict, retailer: str) -> list[str]:
    r = cfg["retailers"].get(retailer) or {}
    return [str(s) for s in r.get("stores", [])] if r.get("enabled", True) else []
