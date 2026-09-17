"""Spotify OAuth login (PKCE) for the Web API.

Not a tool (it is not decorated with @tool): it is an internal helper used
by tools/music.py. Logging in is only needed once -- it opens the browser,
the user authorizes, and the refresh token is stored in
~/.config/tero/spotify_token.json (outside the repo, never in git).
Later calls ask for a fresh access token with that refresh token without
showing the browser again.

PKCE instead of classic Authorization Code: this is a desktop app, there
is no safe place to keep a client secret, and Spotify requires PKCE for
public apps like this one (no secret needed at all).
"""

import base64
import hashlib
import http.server
import json
import secrets
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

import httpx

_AUTH_URL = "https://accounts.spotify.com/authorize"
_TOKEN_URL = "https://accounts.spotify.com/api/token"
_REDIRECT_URI = "http://127.0.0.1:8942/callback"
_SCOPE = "user-modify-playback-state user-read-playback-state user-library-read"
_TOKEN_PATH = Path.home() / ".config" / "tero" / "spotify_token.json"


def _code_verifier() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    code: str | None = None

    def do_GET(self) -> None:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _CallbackHandler.code = params.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("<p>Listo, ya podés cerrar esta pestaña.</p>".encode())

    def log_message(self, *args) -> None:
        pass  # keep http logs out of the daemon's stdout


def _wait_for_callback() -> str:
    server = http.server.HTTPServer(("127.0.0.1", 8942), _CallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout=120)
    server.server_close()
    if _CallbackHandler.code is None:
        raise RuntimeError("No llegó el código de autorización (¿se canceló el login o pasaron 2 min?)")
    return _CallbackHandler.code


def _login(client_id: str) -> dict:
    verifier = _code_verifier()
    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": _REDIRECT_URI,
        "code_challenge_method": "S256",
        "code_challenge": _code_challenge(verifier),
        "scope": _SCOPE,
        # Without this, if the user had already authorized the app before,
        # Spotify does not show the permission screen again and the login
        # keeps the old scope even though a new one is requested here.
        "show_dialog": "true",
    }
    webbrowser.open(f"{_AUTH_URL}?{urllib.parse.urlencode(params)}")
    code = _wait_for_callback()
    response = httpx.post(
        _TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()


def _refresh(client_id: str, refresh_token: str) -> dict:
    response = httpx.post(
        _TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        },
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()


def _save(tokens: dict) -> None:
    _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "refresh_token": tokens["refresh_token"],
        "access_token": tokens["access_token"],
        "expires_at": time.time() + tokens["expires_in"] - 60,
    }
    _TOKEN_PATH.write_text(json.dumps(data))
    _TOKEN_PATH.chmod(0o600)


def _client_id() -> str:
    import tomllib

    config_path = Path(__file__).parent.parent / "config.toml"
    with open(config_path, "rb") as f:
        config = tomllib.load(f)
    return config["spotify"]["client_id"]


def get_token() -> str:
    """A valid access token, logging in or refreshing if needed."""
    client_id = _client_id()
    tokens = None
    if _TOKEN_PATH.exists():
        stored = json.loads(_TOKEN_PATH.read_text())
        # .get instead of [] so a token file written by an older version
        # of this module (it stored the expiry under a different key) just
        # counts as expired and gets refreshed, instead of raising and
        # forcing a new browser login.
        if time.time() < stored.get("expires_at", 0):
            return stored["access_token"]
        refresh_token = stored.get("refresh_token")
        if refresh_token:
            tokens = _refresh(client_id, refresh_token)
            tokens.setdefault("refresh_token", refresh_token)  # not always echoed back
    if tokens is None:
        tokens = _login(client_id)
    _save(tokens)
    return tokens["access_token"]


if __name__ == "__main__":
    get_token()
    print(f"Login OK, token guardado en {_TOKEN_PATH}")
