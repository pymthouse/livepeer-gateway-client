"""OAuth 2.0 / OIDC client for Livepeer gateway authentication (Authlib)."""

from __future__ import annotations

import hashlib
import http.server
import json
import logging
import os
import socket
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from authlib.common.security import generate_token
from authlib.integrations.httpx_client import OAuth2Client
from authlib.oauth2.auth import OAuth2Token

_LOG = logging.getLogger(__name__)


def _ensure_https_for_display(url: str) -> str:
    if not url or not url.startswith("http://"):
        return url
    try:
        parsed = urlparse(url)
        if parsed.hostname in (None, "localhost", "127.0.0.1") or (
            parsed.hostname and parsed.hostname.endswith(".local")
        ):
            return url
        return url.replace("http://", "https://", 1)
    except Exception:
        return url


DEFAULT_CLIENT_ID = "livepeer-sdk"
DEFAULT_SCOPES = "openid profile gateway"
_CALLBACK_PATH = "/callback"
_AUTH_TIMEOUT_S = 300
_DEVICE_POLL_TIMEOUT_S = 600


@dataclass
class OIDCConfig:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    userinfo_endpoint: str
    jwks_uri: str
    device_authorization_endpoint: str | None = None


class OIDCError(Exception):
    """OIDC authentication error."""


def _oauth_verify() -> bool:
    return not bool(os.environ.get("LIVEPEER_ALLOW_INSECURE_TLS"))


def _build_oauth2_client(
    *,
    client_id: str | None = None,
    scopes: str | None = None,
    redirect_uri: str | None = None,
    token: dict[str, Any] | None = None,
    code_challenge_method: str | None = None,
) -> OAuth2Client:
    return OAuth2Client(
        client_id=client_id,
        scope=scopes,
        redirect_uri=redirect_uri,
        token=token,
        token_endpoint_auth_method="none",
        code_challenge_method=code_challenge_method,
        timeout=15.0,
        verify=_oauth_verify(),
        headers={"Accept": "application/json"},
    )


def discover(base_url: str) -> OIDCConfig:
    url = base_url.rstrip("/") + "/.well-known/openid-configuration"
    with _build_oauth2_client() as client:
        resp = client.request("GET", url, withhold_token=True)
        if resp.status_code >= 400:
            raise OIDCError(f"HTTP {resp.status_code} from {url}: {resp.text}")
        data = resp.json()
        return OIDCConfig(
            issuer=data["issuer"],
            authorization_endpoint=data["authorization_endpoint"],
            token_endpoint=data["token_endpoint"],
            userinfo_endpoint=data.get("userinfo_endpoint", ""),
            jwks_uri=data.get("jwks_uri", ""),
            device_authorization_endpoint=data.get("device_authorization_endpoint"),
        )


def probe_oidc(base_url: str) -> bool:
    url = base_url.rstrip("/") + "/.well-known/openid-configuration"
    try:
        with _build_oauth2_client() as client:
            resp = client.request("GET", url, withhold_token=True, timeout=5.0)
            return resp.status_code == 200
    except Exception:
        return False


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    code: str | None = None
    error: str | None = None
    state: str | None = None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != _CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            return

        qs = parse_qs(parsed.query)
        self.__class__.state = qs.get("state", [None])[0]

        if "error" in qs:
            self.__class__.error = qs["error"][0]
            self._respond("Authorization denied. You can close this window.")
            return

        if "code" in qs:
            self.__class__.code = qs["code"][0]
            self._respond("Authorization successful! You can close this window.")
            return

        self.__class__.error = "missing_code"
        self._respond("Missing authorization code. You can close this window.")

    def _respond(self, body: str) -> None:
        html = (
            "<!DOCTYPE html><html><head><title>Livepeer SDK</title></head>"
            f"<body><p>{body}</p></body></html>"
        )
        payload = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: Any) -> None:
        _LOG.debug("OIDC callback server: " + fmt, *args)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def login(
    base_url: str,
    *,
    client_id: str = DEFAULT_CLIENT_ID,
    scopes: str = DEFAULT_SCOPES,
) -> OAuth2Token:
    config = discover(base_url)
    code_verifier = generate_token(48)
    port = _find_free_port()
    redirect_uri = f"http://127.0.0.1:{port}{_CALLBACK_PATH}"

    _CallbackHandler.code = None
    _CallbackHandler.error = None
    _CallbackHandler.state = None

    server = http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server.timeout = _AUTH_TIMEOUT_S

    with _build_oauth2_client(
        client_id=client_id,
        scopes=scopes,
        redirect_uri=redirect_uri,
        code_challenge_method="S256",
    ) as client:
        authorize_url, state = client.create_authorization_url(
            config.authorization_endpoint,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
            resource=config.issuer,
        )

        _LOG.info("Opening browser for OIDC login...")
        print(f"\nOpening browser for login: {authorize_url}\n")
        webbrowser.open(authorize_url)

        result: dict[str, Any] = {}

        def _serve() -> None:
            try:
                server.handle_request()
                result["code"] = _CallbackHandler.code
                result["error"] = _CallbackHandler.error
                result["state"] = _CallbackHandler.state
            except Exception as exc:
                result["error"] = str(exc)
            finally:
                server.server_close()

        thread = threading.Thread(target=_serve, daemon=True)
        thread.start()
        thread.join(timeout=_AUTH_TIMEOUT_S)

        if thread.is_alive():
            server.server_close()
            raise OIDCError("Login timed out — no callback received within 5 minutes")

        if result.get("error"):
            raise OIDCError(f"Authorization failed: {result['error']}")

        code = result.get("code")
        if not code:
            raise OIDCError("No authorization code received")

        received_state = result.get("state")
        authorization_response = f"{redirect_uri}?code={code}&state={received_state}"

        try:
            return client.fetch_token(
                config.token_endpoint,
                authorization_response=authorization_response,
                code_verifier=code_verifier,
                redirect_uri=redirect_uri,
                resource=config.issuer,
                state=state,
            )
        except Exception as exc:
            raise OIDCError(f"Token exchange failed: {exc}") from exc


def device_login(
    base_url: str,
    *,
    client_id: str = DEFAULT_CLIENT_ID,
    scopes: str = DEFAULT_SCOPES,
    on_device_auth: Callable[[str, str, int], None] | None = None,
) -> OAuth2Token:
    config = discover(base_url)

    if not config.device_authorization_endpoint:
        raise OIDCError(
            "Device Authorization Flow not supported by this provider. "
            "The discovery document has no device_authorization_endpoint."
        )

    with _build_oauth2_client(client_id=client_id, scopes=scopes) as client:
        resp = client.request(
            "POST",
            config.device_authorization_endpoint,
            withhold_token=True,
            data={
                "client_id": client_id,
                "scope": scopes,
                "resource": config.issuer,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if resp.status_code >= 400:
            raise OIDCError(
                f"Device authorization request failed (HTTP {resp.status_code}): {resp.text}"
            )

        data = resp.json()
        device_code = data["device_code"]
        user_code = data["user_code"]
        verification_uri = _ensure_https_for_display(data.get("verification_uri", ""))
        verification_uri_complete = _ensure_https_for_display(
            data.get("verification_uri_complete", "")
        )
        expires_in = int(data.get("expires_in", 600))
        interval = int(data.get("interval", 5))

        auth_url = verification_uri_complete or verification_uri
        if on_device_auth:
            try:
                on_device_auth(auth_url, user_code, expires_in)
            except Exception:
                _LOG.warning("on_device_auth callback failed", exc_info=True)
        else:
            print("\n" + "=" * 50)
            print(" DEVICE AUTHORIZATION")
            print("=" * 50)
            if verification_uri_complete:
                print(f"\n Go to: {verification_uri_complete}")
                print(f"\n Or visit: {verification_uri}")
                print(f" and enter code: {user_code}")
            else:
                print(f"\n Go to: {verification_uri}")
                print(f" Enter code: {user_code}")
            print(f"\n Code expires in {expires_in // 60} minutes.")
            print("=" * 50 + "\n")

        deadline = time.time() + min(expires_in, _DEVICE_POLL_TIMEOUT_S)
        poll_interval = interval

        while time.time() < deadline:
            time.sleep(poll_interval)

            resp = client.request(
                "POST",
                config.token_endpoint,
                withhold_token=True,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": client_id,
                    "device_code": device_code,
                    "resource": config.issuer,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            if resp.status_code == 200:
                _LOG.info("Device authorized successfully")
                return OAuth2Token.from_dict(resp.json())

            try:
                err_data = resp.json()
            except Exception:
                raise OIDCError(
                    f"Token poll failed (HTTP {resp.status_code}): {resp.text}"
                ) from None

            error = err_data.get("error", "")

            if error == "authorization_pending":
                continue
            if error == "slow_down":
                poll_interval += 5
                continue
            if error == "access_denied":
                raise OIDCError("User denied the device authorization request")
            if error == "expired_token":
                raise OIDCError("Device code expired before user authorized")

            raise OIDCError(
                f"Device code token exchange failed: "
                f"{err_data.get('error_description', error)}"
            )

        raise OIDCError(
            "Device authorization timed out — user did not authorize in time"
        )


def refresh(
    base_url: str,
    refresh_token: str,
    *,
    client_id: str = DEFAULT_CLIENT_ID,
) -> OAuth2Token:
    config = discover(base_url)
    with _build_oauth2_client(
        client_id=client_id,
        token={"refresh_token": refresh_token},
    ) as client:
        try:
            return client.refresh_token(
                config.token_endpoint,
                refresh_token=refresh_token,
                resource=config.issuer,
            )
        except Exception as exc:
            raise OIDCError(f"Refresh failed: {exc}") from exc


def _cache_dir() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "livepeer-gateway-client" / "tokens"


def _cache_key(
    base_url: str,
    client_id: str = DEFAULT_CLIENT_ID,
    scopes: str = DEFAULT_SCOPES,
) -> str:
    key_material = f"{base_url}|{client_id}|{scopes}"
    return hashlib.sha256(key_material.encode()).hexdigest()[:16]


def load_cached_token(
    base_url: str,
    *,
    client_id: str = DEFAULT_CLIENT_ID,
    scopes: str = DEFAULT_SCOPES,
) -> OAuth2Token | None:
    path = _cache_dir() / f"{_cache_key(base_url, client_id, scopes)}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text("utf-8"))
        return OAuth2Token.from_dict(data)
    except Exception:
        _LOG.debug("Failed to load cached token from %s", path, exc_info=True)
        return None


def save_cached_token(
    base_url: str,
    tokens: OAuth2Token,
    *,
    client_id: str = DEFAULT_CLIENT_ID,
    scopes: str = DEFAULT_SCOPES,
) -> None:
    cache = _cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / f"{_cache_key(base_url, client_id, scopes)}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(tokens)), "utf-8")
    os.chmod(tmp, 0o600)
    tmp.rename(path)


def clear_cached_token(
    base_url: str,
    *,
    client_id: str = DEFAULT_CLIENT_ID,
    scopes: str = DEFAULT_SCOPES,
) -> None:
    path = _cache_dir() / f"{_cache_key(base_url, client_id, scopes)}.json"
    path.unlink(missing_ok=True)


def clear_all_cached_tokens() -> int:
    cache = _cache_dir()
    if not cache.exists():
        return 0
    count = 0
    for path in cache.glob("*.json"):
        path.unlink(missing_ok=True)
        count += 1
    return count


def ensure_valid_token(
    base_url: str,
    *,
    client_id: str = DEFAULT_CLIENT_ID,
    scopes: str = DEFAULT_SCOPES,
    headless: bool = True,
    on_device_auth: Callable[[str, str, int], None] | None = None,
) -> OAuth2Token:
    if headless and os.environ.get("LIVEPEER_AUTH_BROWSER", "").lower() in (
        "1",
        "true",
        "yes",
    ):
        headless = False

    cached = load_cached_token(base_url, client_id=client_id, scopes=scopes)

    if cached and not cached.is_expired():
        _LOG.debug("Using cached OIDC token for %s", base_url)
        return cached

    if cached and cached.get("refresh_token"):
        _LOG.info("Access token expired, refreshing...")
        try:
            tokens = refresh(base_url, cached["refresh_token"], client_id=client_id)
            save_cached_token(base_url, tokens, client_id=client_id, scopes=scopes)
            return tokens
        except Exception:
            _LOG.warning("Token refresh failed, falling back to login", exc_info=True)

    if headless:
        _LOG.info("Starting OIDC device authorization flow for %s", base_url)
        tokens = device_login(
            base_url,
            client_id=client_id,
            scopes=scopes,
            on_device_auth=on_device_auth,
        )
    else:
        _LOG.info("Starting OIDC browser login for %s", base_url)
        tokens = login(base_url, client_id=client_id, scopes=scopes)
    save_cached_token(base_url, tokens, client_id=client_id, scopes=scopes)
    return tokens
