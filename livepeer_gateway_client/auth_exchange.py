from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from livepeer_gateway.errors import LivepeerGatewayError
from livepeer_gateway.orchestrator import post_json

from .oidc_auth import OIDCError, client_credentials_token

_LOG = logging.getLogger(__name__)
DEFAULT_SCOPE = "sign:job"


def _signer_access_token(payload: dict[str, Any]) -> str:
    token_obj = payload.get("token")
    if isinstance(token_obj, dict):
        for key in ("accessToken", "access_token"):
            value = token_obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for key in ("accessToken", "access_token"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise LivepeerGatewayError("API key exchange response missing signer access token")


def _signer_url(payload: dict[str, Any]) -> str | None:
    for key in ("signerUrl", "signer_url"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def exchange_api_key_for_signer(
    billing_url: str,
    api_key: str,
    *,
    client_id: str | None = None,
    scope: str | None = DEFAULT_SCOPE,
    timeout: float = 15.0,
) -> tuple[str | None, dict[str, str]]:
    """
    Exchange a PymtHouse API key (``pmth_*``) for a signer JWT via PymtHouse.

    Returns ``(signer_url, {"Authorization": "Bearer <jwt>"})``.
    """
    key = api_key.strip()
    if not key:
        raise LivepeerGatewayError("API key exchange requires a non-empty API key")
    client_id_value = (client_id or "").strip()
    if not client_id_value:
        raise LivepeerGatewayError("API key exchange requires a non-empty client_id")
    url = (
        f"{billing_url.rstrip('/')}/api/v1/apps/"
        f"{quote(client_id_value, safe='')}/auth/api-key/signer-session"
    )
    body: dict[str, Any] = {}
    if scope:
        body["scope"] = scope
    _LOG.info("Exchanging API key for signer JWT at %s", url)
    data = post_json(
        url,
        body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=timeout,
    )
    return _signer_url(data), {"Authorization": f"Bearer {_signer_access_token(data)}"}


def exchange_client_secret_for_signer(
    oidc_base_url: str,
    m2m_client_id: str,
    client_secret: str,
    *,
    scope: str | None = None,
    external_user_id: str | None = None,
    audience: str | None = None,
    timeout: float = 15.0,
) -> tuple[str | None, dict[str, str]]:
    """
    Exchange an M2M client secret (``pmth_cs_*``) for a signer session via OIDC.

    Returns ``(signer_url, {"Authorization": "Bearer <jwt>"})``.
    """
    secret = client_secret.strip()
    if not secret:
        raise LivepeerGatewayError("M2M exchange requires a non-empty client secret")
    client_id_value = m2m_client_id.strip()
    if not client_id_value:
        raise LivepeerGatewayError("M2M exchange requires a non-empty m2m_client_id")
    _LOG.info("Exchanging M2M client credentials for bearer token at %s", oidc_base_url)
    try:
        tokens = client_credentials_token(
            oidc_base_url,
            client_id=client_id_value,
            client_secret=secret,
            scope=scope,
            external_user_id=external_user_id,
            audience=audience,
            timeout=timeout,
        )
    except OIDCError as exc:
        raise LivepeerGatewayError(
            f"M2M client credentials exchange failed: {exc}"
        ) from exc
    access_token = tokens.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise LivepeerGatewayError(
            "M2M client credentials response missing access_token"
        )
    return _signer_url(tokens), {"Authorization": f"Bearer {access_token.strip()}"}
