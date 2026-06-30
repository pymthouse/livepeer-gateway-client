from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from livepeer_gateway.errors import LivepeerGatewayError

from .oidc_auth import OIDCError, client_credentials_token

_LOG = logging.getLogger(__name__)
DEFAULT_SCOPE = "sign:job"
DEFAULT_TOKEN_EXCHANGE_AUDIENCE = "livepeer-clearinghouse"
TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
SUBJECT_ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"
ISSUED_ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"


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


def _token_endpoint_url(billing_url: str, public_client_id: str) -> str:
    client_id = public_client_id.strip()
    if not client_id:
        raise LivepeerGatewayError("token exchange requires a public client_id")
    return f"{billing_url.rstrip('/')}/api/v1/apps/{client_id}/oidc/token"


def _post_token_exchange(
    billing_url: str,
    *,
    public_client_id: str,
    subject_token: str,
    audience: str | None = DEFAULT_TOKEN_EXCHANGE_AUDIENCE,
    timeout: float = 15.0,
) -> dict[str, Any]:
    url = _token_endpoint_url(billing_url, public_client_id)
    form = {
        "grant_type": TOKEN_EXCHANGE_GRANT,
        "subject_token": subject_token,
        "subject_token_type": SUBJECT_ACCESS_TOKEN_TYPE,
        "requested_token_type": ISSUED_ACCESS_TOKEN_TYPE,
        "audience": (audience or DEFAULT_TOKEN_EXCHANGE_AUDIENCE).strip(),
    }
    _LOG.info("Exchanging subject token for signer JWT at %s", url)
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            url,
            content=urlencode(form),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
    if response.status_code >= 400:
        raise LivepeerGatewayError(
            f"HTTP {response.status_code} from endpoint (url={url}); body={response.text!r}"
        )
    data = response.json()
    if not isinstance(data, dict):
        raise LivepeerGatewayError("token exchange response must be a JSON object")
    return data


def exchange_api_key_for_signer(
    billing_url: str,
    api_key: str,
    *,
    client_id: str,
    audience: str | None = DEFAULT_TOKEN_EXCHANGE_AUDIENCE,
    scope: str | None = DEFAULT_SCOPE,
    timeout: float = 15.0,
) -> SignerExchangeResult:
    """
    Exchange an end-user API key (``sk_*``) for a signer JWT via RFC 8693 token exchange.
    """
    _ = scope
    key = api_key.strip()
    if not key:
        raise LivepeerGatewayError("API key exchange requires a non-empty API key")
    public_client_id = client_id.strip()
    if not public_client_id:
        raise LivepeerGatewayError("API key exchange requires client_id (public app client)")
    data = _post_token_exchange(
        billing_url,
        public_client_id=public_client_id,
        subject_token=key,
        audience=audience,
        timeout=timeout,
    )
    return _exchange_result(data)


def exchange_oidc_token_for_signer(
    billing_url: str,
    client_id: str,
    oidc_access_token: str,
    *,
    audience: str | None = DEFAULT_TOKEN_EXCHANGE_AUDIENCE,
    scope: str | None = DEFAULT_SCOPE,
    timeout: float = 15.0,
) -> SignerExchangeResult:
    """
    Exchange an Auth0 end-user access token (device code / OIDC) for a signer session.
    """
    _ = scope
    public_client_id = client_id.strip()
    if not public_client_id:
        raise LivepeerGatewayError("OIDC exchange requires client_id (public app client)")
    token = oidc_access_token.strip()
    if not token:
        raise LivepeerGatewayError("OIDC exchange requires a non-empty access token")
    data = _post_token_exchange(
        billing_url,
        public_client_id=public_client_id,
        subject_token=token,
        audience=audience,
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
