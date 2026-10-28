"""Spotify sign-in with the Authorization Code + PKCE flow (no client secret needed)."""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import subprocess
import threading
import time
import urllib.parse

import httpx

from .config import AUTH_FILE, SCOPES

TOKEN_URL = "https://accounts.spotify.com/api/token"
AUTHORIZE_URL = "https://accounts.spotify.com/authorize"


class AuthError(Exception):
    pass


def _save(data: dict) -> None:
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = AUTH_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.chmod(tmp, 0o600)
    tmp.replace(AUTH_FILE)


def load() -> dict | None:
    try:
        return json.loads(AUTH_FILE.read_text())
    except (OSError, ValueError):
        return None


def login(cfg: dict) -> dict:
    """Open the browser, wait for Spotify's redirect, and store the tokens."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    redirect_uri = f"http://127.0.0.1:{cfg['redirect_port']}{cfg['redirect_path']}"
    url = AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "client_id": cfg["client_id"],
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "scope": SCOPES,
        "state": state,
    })

    result: dict = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != cfg["redirect_path"]:
                self.send_response(404)
                self.end_headers()
                return
            q = urllib.parse.parse_qs(parsed.query)
            if q.get("state", [""])[0] != state:
                result["error"] = "state mismatch"
            elif "error" in q:
                result["error"] = q["error"][0]
            else:
                result["code"] = q.get("code", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Signed in. You can close this tab and go back to spin." if "code" in result else f"Sign-in failed: {result.get('error')}"
            self.wfile.write(f"<html><body style='font-family:monospace;padding:2em'>{msg}</body></html>".encode())
            done.set()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", cfg["redirect_port"]), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print("Opening Spotify sign-in in your browser. If it doesn't open, visit:\n\n  " + url + "\n")
    try:
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass
    try:
        if not done.wait(timeout=300):
            raise AuthError("timed out waiting for sign-in")
    finally:
        server.shutdown()
    if "code" not in result:
        raise AuthError(result.get("error", "sign-in failed"))

    r = httpx.post(TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": result["code"],
        "redirect_uri": redirect_uri,
        "client_id": cfg["client_id"],
        "code_verifier": verifier,
    }, timeout=15)
    if r.status_code != 200:
        raise AuthError(f"token exchange failed: HTTP {r.status_code} {r.text[:200]}")
    tok = r.json()
    data = {
        "access_token": tok["access_token"],
        "refresh_token": tok["refresh_token"],
        "expires_at": time.time() + tok.get("expires_in", 3600) - 60,
        "scope": tok.get("scope", ""),
    }
    _save(data)
    return data


async def refresh(cfg: dict, data: dict, client: httpx.AsyncClient) -> dict:
    r = await client.post(TOKEN_URL, data={
        "grant_type": "refresh_token",
        "refresh_token": data["refresh_token"],
        "client_id": cfg["client_id"],
    })
    if r.status_code != 200:
        raise AuthError(f"sign-in expired (HTTP {r.status_code}), run `spin login`")
    tok = r.json()
    data = {
        **data,
        "access_token": tok["access_token"],
        # Spotify may rotate the refresh token; keep the old one if it doesn't.
        "refresh_token": tok.get("refresh_token", data["refresh_token"]),
        "expires_at": time.time() + tok.get("expires_in", 3600) - 60,
    }
    _save(data)
    return data
