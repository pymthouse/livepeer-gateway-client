from __future__ import annotations

from livepeer_gateway.errors import LivepeerGatewayError

from .auth_exchange import DEFAULT_SCOPE, exchange_api_key_for_signer


class SignerTokenProvider:
    """Re-exchangeable source of signer auth headers.

    Supports OIDC (via :mod:`oidc_auth`) or a non-interactive API-key exchange
    against the Dashboard BFF. The signer session JWT is short-lived
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
        oidc_client_id: str = "livepeer-sdk",
        oidc_scopes: str = "openid profile gateway",
        oidc_headless: bool = True,
        scope: str | None = DEFAULT_SCOPE,
        timeout: float = 15.0,
    ) -> None:
        self._oidc_base_url = oidc_base_url
        self._billing_url = billing_url
        self._api_key = api_key
        self._client_id = client_id
        self._oidc_client_id = oidc_client_id
        self._oidc_scopes = oidc_scopes
        self._oidc_headless = oidc_headless
        self._scope = scope
        self._timeout = timeout
        self.signer_url: str | None = None
        self.headers: dict[str, str] = {}

    def refresh(self) -> dict[str, str]:
        """Re-mint signer auth headers."""
        if self._billing_url and self._api_key:
            self.signer_url, self.headers = exchange_api_key_for_signer(
                self._billing_url,
                self._api_key,
                client_id=self._client_id,
                scope=self._scope,
                timeout=self._timeout,
            )
            return self.headers

        if self._oidc_base_url:
            from .oidc_auth import ensure_valid_token

            tokens = ensure_valid_token(
                self._oidc_base_url,
                client_id=self._oidc_client_id,
                scopes=self._oidc_scopes,
                headless=self._oidc_headless,
            )
            access_token = tokens.get("access_token")
            if not isinstance(access_token, str) or not access_token.strip():
                raise LivepeerGatewayError("OIDC token response missing access_token")
            self.headers = {"Authorization": f"Bearer {access_token.strip()}"}
            return self.headers

        raise LivepeerGatewayError(
            "SignerTokenProvider requires billing_url+api_key or oidc_base_url"
        )
