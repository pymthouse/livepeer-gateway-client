from __future__ import annotations

from livepeer_gateway.errors import LivepeerGatewayError

from .auth_exchange import (
    DEFAULT_SCOPE,
    SignerExchangeResult,
    exchange_api_key_for_signer,
    exchange_client_secret_for_signer,
    exchange_oidc_token_for_signer,
)


class SignerTokenProvider:
    """Re-exchangeable source of signer auth headers.

    Supports OIDC (via :mod:`oidc_auth`) or a non-interactive API-key exchange
    against the PymtHouse issuer. The signer session JWT is short-lived
    (``sign:job`` scope, typically minutes). Long-running streams must re-mint
    it when the signer rejects an expired token.
    """

    def __init__(
        self,
        *,
        oidc_base_url: str | None = None,
        billing_url: str | None = None,
        api_key: str | None = None,
        client_id: str | None = None,
        m2m_client_id: str | None = None,
        external_user_id: str | None = None,
        m2m_audience: str | None = None,
        oidc_client_id: str = "livepeer-sdk",
        oidc_scopes: str = "openid profile gateway",
        oidc_audience: str | None = None,
        oidc_headless: bool = True,
        scope: str | None = DEFAULT_SCOPE,
        timeout: float = 15.0,
    ) -> None:
        self._oidc_base_url = oidc_base_url
        self._billing_url = billing_url
        self._api_key = api_key
        self._client_id = client_id
        self._m2m_client_id = m2m_client_id
        self._external_user_id = external_user_id
        self._m2m_audience = m2m_audience
        self._oidc_client_id = oidc_client_id
        self._oidc_scopes = oidc_scopes
        self._oidc_audience = oidc_audience
        self._oidc_headless = oidc_headless
        self._scope = scope
        self._timeout = timeout
        self.signer_url: str | None = None
        self.discovery_url: str | None = None
        self.headers: dict[str, str] = {}

    def _apply_routing(self, result: SignerExchangeResult) -> None:
        if result.signer_url:
            self.signer_url = result.signer_url
        if result.discovery_url:
            self.discovery_url = result.discovery_url

    def refresh(self) -> dict[str, str]:
        """Re-mint signer auth headers."""
        if self._billing_url and self._api_key:
            api_key = self._api_key.strip()
            if api_key.startswith("pmth_cs_"):
                if not self._m2m_client_id:
                    raise LivepeerGatewayError(
                        "pmth_cs_* requires m2m_client_id for client_credentials exchange"
                    )
                if not self._external_user_id:
                    raise LivepeerGatewayError(
                        "pmth_cs_* requires external_user_id for user-scoped token mint"
                    )
                oidc_base_url = (self._oidc_base_url or "").strip()
                if not oidc_base_url:
                    oidc_base_url = f"{self._billing_url.rstrip('/')}/api/v1/oidc"
                result = exchange_client_secret_for_signer(
                    oidc_base_url,
                    self._m2m_client_id,
                    api_key,
                    timeout=self._timeout,
                    external_user_id=self._external_user_id,
                    audience=self._m2m_audience,
                )
                self._apply_routing(result)
                self.headers = result.headers
                return self.headers

            if not self._client_id:
                raise LivepeerGatewayError("API key exchange requires client_id (public app client)")
            result = exchange_api_key_for_signer(
                self._billing_url,
                api_key,
                client_id=self._client_id,
                scope=self._scope,
                timeout=self._timeout,
            )
            self._apply_routing(result)
            self.headers = result.headers
            return self.headers

        if self._oidc_base_url:
            from .oidc_auth import ensure_valid_token

            oidc_client_id = self._oidc_client_id
            tokens = ensure_valid_token(
                self._oidc_base_url,
                client_id=oidc_client_id,
                scopes=self._oidc_scopes,
                audience=self._oidc_audience,
                headless=self._oidc_headless,
            )
            access_token = tokens.get("access_token")
            if not isinstance(access_token, str) or not access_token.strip():
                raise LivepeerGatewayError("OIDC token response missing access_token")

            public_client_id = self._client_id or oidc_client_id
            if self._billing_url and public_client_id:
                result = exchange_oidc_token_for_signer(
                    self._billing_url,
                    public_client_id,
                    access_token,
                    scope=self._scope,
                    timeout=self._timeout,
                )
                self._apply_routing(result)
                self.headers = result.headers
                return self.headers

            self.headers = {"Authorization": f"Bearer {access_token.strip()}"}
            return self.headers

        raise LivepeerGatewayError(
            "SignerTokenProvider requires billing_url+api_key (plus client_id for pmth_* or m2m_client_id+external_user_id for pmth_cs_*) or oidc_base_url"
        )
