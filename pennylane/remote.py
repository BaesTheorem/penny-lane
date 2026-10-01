"""Off-LAN access for the iPhone: a cloudflared quick tunnel in front of a
token-guarded origin, with the current public URL published to Workers KV
so the phone finds the Mac from any store without re-pairing.

Same shape as the MIST Console's remote module, smaller: one token (in
data/remote.json, 0600), one cookie, one tunnel. Loopback requests with no
proxy header are trusted (the Mac app, the CLI); everything else needs the
token. Publishing reuses the Console's share Worker + KV namespace via its
`share` module when present; without it the tunnel still runs and the
phone is paired by QR each time the URL changes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time

from flask import abort, jsonify, redirect, request

from . import config

log = logging.getLogger("pennylane.remote")
CONFIG_PATH = config.DATA_DIR / "remote.json"
COOKIE = "penny_remote"
PUBLIC = {"/remote/ping", "/remote/login", "/health"}
_PROXY_HEADERS = ("X-Forwarded-For", "Cf-Connecting-Ip", "Cf-Ray", "X-Real-Ip", "Forwarded")
_TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
CONSOLE = os.path.expanduser("~/Documents/mist-console")
_cfg: dict = {}


class _Tunnel:
    proc: subprocess.Popen | None = None
    url: str | None = None
    error: str | None = None
    since: float | None = None
    published_url: str | None = None
    published_at: float = 0.0


_tunnel = _Tunnel()


def _load() -> dict:
    global _cfg
    if not _cfg:
        try:
            _cfg = json.loads(CONFIG_PATH.read_text())
        except Exception:  # noqa: BLE001
            _cfg = {}
        _cfg.setdefault("enabled", True)
        _cfg.setdefault("tunnel", True)
        if not _cfg.get("token"):
            _cfg["token"] = secrets.token_urlsafe(32)
        if not _cfg.get("discovery_id"):
            _cfg["discovery_id"] = secrets.token_urlsafe(16)
        _save()
    return _cfg


def _save() -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(_cfg, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONFIG_PATH)


def token() -> str:
    return _load()["token"]


def cookie_value() -> str:
    return hmac.new(token().encode(), b"penny-lane", hashlib.sha256).hexdigest()


def is_local(req) -> bool:
    if any(h in req.headers for h in _PROXY_HEADERS):
        return False
    return req.remote_addr in ("127.0.0.1", "::1")


def authorized(req) -> bool:
    if is_local(req):
        return True
    if not _load().get("enabled"):
        return False
    auth = req.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and hmac.compare_digest(auth[7:], token()):
        return True
    return hmac.compare_digest(req.cookies.get(COOKIE, ""), cookie_value())


# ---- tunnel ------------------------------------------------------------------

def _cloudflared() -> str | None:
    return shutil.which("cloudflared") or next((p for p in ("/opt/homebrew/bin/cloudflared", "/usr/local/bin/cloudflared")
                                                if os.path.exists(p)), None)


def _start_tunnel() -> None:
    binary = _cloudflared()
    if not binary:
        _tunnel.error = "cloudflared not installed"
        return
    origin = f"http://127.0.0.1:{config.PORT}"
    proc = subprocess.Popen([binary, "tunnel", "--url", origin, "--no-autoupdate"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    _tunnel.proc, _tunnel.url, _tunnel.error, _tunnel.since = proc, None, None, time.time()
    threading.Thread(target=_read, args=(proc,), daemon=True).start()


def _read(proc) -> None:
    logf = config.LOG_DIR / "penny-lane-tunnel.log"
    try:
        with open(logf, "a") as out:
            for line in proc.stderr:
                out.write(line)
                m = _TUNNEL_URL.search(line)
                if m and m.group(0) != _tunnel.url:
                    _tunnel.url = m.group(0)
                    threading.Thread(target=publish, daemon=True).start()
    except Exception:  # noqa: BLE001
        pass
    proc.wait()
    if proc is _tunnel.proc:
        _tunnel.url = None


def _probe(url: str) -> bool:
    try:
        from urllib.request import Request, urlopen
        with urlopen(Request(url + "/remote/ping"), timeout=10) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def _supervise() -> None:
    misses = 0
    while True:
        try:
            if _load().get("tunnel"):
                p = _tunnel.proc
                if p is None or p.poll() is not None:
                    _start_tunnel()
                    misses = 0
                elif _tunnel.url:
                    if _probe(_tunnel.url):
                        misses = 0
                    else:
                        misses += 1
                        if misses >= 3:
                            log.warning("tunnel dead (%s), restarting", _tunnel.url)
                            p.terminate()
                            misses = 0
                if _tunnel.url and (_tunnel.published_url != _tunnel.url or time.time() - _tunnel.published_at > 3600):
                    publish()
        except Exception as e:  # noqa: BLE001
            log.warning("supervisor: %s", e)
        time.sleep(60)


# ---- discovery via the Console's share Worker KV -----------------------------

def _share():
    if CONSOLE not in sys.path:
        sys.path.insert(0, CONSOLE)
    import share  # type: ignore  # noqa: PLC0415 - optional private module
    return share


def discovery_key() -> str:
    return "penny-" + _load()["discovery_id"]


def discovery_url() -> str | None:
    try:
        base = (_share()._load_cloud_config() or {}).get("base_url")  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return None
    return f"{base}/s/{discovery_key()}" if base else None


def publish() -> dict:
    urls = [_tunnel.url] if _tunnel.url else []
    try:
        share = _share()
        account, tok = share._creds()  # noqa: SLF001
        base_url, kv_id = share._ensure_cloud()  # noqa: SLF001
        doc = json.dumps({"v": 1, "urls": urls, "updated": int(time.time())})
        share._req("PUT", f"/accounts/{account}/storage/kv/namespaces/{kv_id}/values/{discovery_key()}",  # noqa: SLF001
                   tok, doc.encode(), ctype="text/plain", raw=True)
        _tunnel.published_url, _tunnel.published_at = _tunnel.url, time.time()
        return {"ok": True, "url": f"{base_url}/s/{discovery_key()}"}
    except Exception as e:  # noqa: BLE001
        log.warning("discovery publish failed: %s", e)
        return {"ok": False, "why": str(e)}


# ---- flask wiring ------------------------------------------------------------

def init(app) -> None:
    _load()

    @app.before_request
    def remote_guard():
        if request.path in PUBLIC or request.path.startswith("/static/"):
            return None
        if not authorized(request):
            abort(403)
        return None

    @app.get("/remote/ping")
    def remote_ping():
        return jsonify(app="penny-lane", ok=True)

    @app.route("/remote/login", methods=["GET", "POST"])
    def remote_login():
        cand = request.form.get("token") or request.args.get("token") or ""
        if not hmac.compare_digest(cand, token()):
            abort(403)
        resp = redirect("/")
        resp.set_cookie(COOKIE, cookie_value(), max_age=365 * 86400, httponly=True, samesite="Lax",
                        secure=request.is_secure)
        return resp

    @app.get("/remote/status")
    def remote_status():
        if not is_local(request):
            abort(403)
        return jsonify(enabled=_load().get("enabled"),
                       tunnel={"url": _tunnel.url, "error": _tunnel.error, "since": _tunnel.since,
                               "running": _tunnel.proc is not None and _tunnel.proc.poll() is None},
                       discovery=discovery_url(), pairing=pairing())

    @app.post("/remote/publish")
    def remote_publish():
        if not is_local(request):
            abort(403)
        return jsonify(publish())

    threading.Thread(target=_supervise, daemon=True, name="penny-tunnel").start()


def pairing() -> dict:
    """What the QR carries: the token, the discovery URL (stable) and the
    current tunnel URL (may change)."""
    return {"token": token(), "discovery": discovery_url(), "url": _tunnel.url}
