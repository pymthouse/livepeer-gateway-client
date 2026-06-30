from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from livepeer_gateway.errors import LivepeerGatewayError
from livepeer_gateway.orchestrator import post_json

from .oidc_auth import OIDCError, client_credentials_token

_LOG = logging.getLogger(__name__)
DEFAULT_SCOPE = "sign:job"


@dataclass(frozen=True, slots=True)
class SignerExchangeResult:
    headers: dict[str, str]
    signer_url: str | None = None
    discovery_url: str | None = None


def _signer_access_token(payload: dict[str, Any]) -> str:
    value = payload.get("access_token")
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise LivepeerGatewayError("API key exchange response missing access_token")


def _optional_url(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _signer_url(payload: dict[str, Any]) -> str | None:
    return _optional_url(payload, "signer_url")


def _discovery_url(payload: dict[str, Any]) -> str | None:
    return _optional_url(payload, "discovery_url")


def _exchange_result(payload: dict[str, Any]) -> SignerExchangeResult:
    return SignerExchangeResult(
        headers={"Authorization": f"Bearer {_signer_access_token(payload)}"},
        signer_url=_signer_url(payload),
        discovery_url=_discovery_url(payload),
    )


def _signer_session_url(billing_url: str, client_id: str) -> str:
    client_id_value = client_id.strip()
    if not client_id_value:
        raise LivepeerGatewayError("signer session exchange requires a non-empty client_id")
    return (
        f"{billing_url.rstrip('/')}/api/v1/apps/"
        f"{quote(client_id_value, safe='')}/auth/signer-session"
    )


def exchange_api_key_for_signer(
    billing_url: str,
    api_key: str,
    *,
    client_id: str | None = None,
    scope: str | None = DEFAULT_SCOPE,
    timeout: float = 15.0,
) -> SignerExchangeResult:
    """
    Exchange a PymtHouse API key (``pmth_*``) for a signer JWT via PymtHouse.

    Returns routing hints plus ``{"Authorization": "Bearer <jwt>"}``.
    """
    key = api_key.strip()
    if not key:
        raise LivepeerGatewayError("API key exchange requires a non-empty API key")
    client_id_value = (client_id or "").strip()
    if not client_id_value:
        raise LivepeerGatewayError("API key exchange requires a non-empty client_id")
    url = _signer_session_url(billing_url, client_id_value)
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
    return _exchange_result(data)


def exchange_oidc_token_for_signer(
    billing_url: str,
    client_id: str,
    oidc_access_token: str,
    *,
    scope: str | None = DEFAULT_SCOPE,
    timeout: float = 15.0,
) -> SignerExchangeResult:
    """
    Exchange an Auth0 end-user access token (device code / OIDC) for a signer session.

    Provisions the OpenMeter customer and returns a minted signer JWT plus routing hints.
    """
    token = oidc_access_token.strip()
    if not token:
        raise LivepeerGatewayError("OIDC exchange requires a non-empty access token")
    client_id_value = client_id.strip()
    if not client_id_value:
        raise LivepeerGatewayError("OIDC exchange requires a non-empty client_id")
    url = _signer_session_url(billing_url, client_id_value)
    body: dict[str, Any] = {}
    if scope:
        body["scope"] = scope
    _LOG.info("Exchanging OIDC access token for signer JWT at %s", url)
    data = post_json(
        url,
        body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=timeout,
    )
    return _exchange_result(data)


def exchange_client_secret_for_signer(
    oidc_base_url: str,
    m2m_client_id: str,
    client_secret: str,
    *,
    scope: str | None = None,
    external_user_id: str | None = None,
    audience: str | None = None,
    timeout: float = 15.0,
) -> SignerExchangeResult:
    """
    Exchange an M2M client secret (``pmth_cs_*``) for a signer session via OIDC.

    Returns routing hints plus ``{"Authorization": "Bearer <jwt>"}``.
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
    return SignerExchangeResult(
        headers={"Authorization": f"Bearer {access_token.strip()}"},
        signer_url=_signer_url(tokens),
        discovery_url=_discovery_url(tokens),
    )
