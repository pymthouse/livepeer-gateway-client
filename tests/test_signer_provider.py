from __future__ import annotations

from unittest.mock import patch

import pytest
from livepeer_gateway.errors import LivepeerGatewayError

from livepeer_gateway_client.auth_exchange import (
    exchange_api_key_for_signer,
    exchange_client_secret_for_signer,
)
from livepeer_gateway_client.errors import format_gateway_error
from livepeer_gateway_client.signer_provider import SignerTokenProvider


def test_exchange_api_key_for_signer_returns_signer_url_and_bearer() -> None:
    payload = {
        "token": {"accessToken": "jwt"},
        "signerUrl": "https://signer.example",
    }
    with patch(
        "livepeer_gateway_client.auth_exchange.post_json",
        return_value=payload,
    ) as post_json:
        signer_url, headers = exchange_api_key_for_signer(
            "https://staging.pymthouse.com",
            "pmth_test",
            client_id="app_test",
        )

    assert signer_url == "https://signer.example"
    assert headers == {"Authorization": "Bearer jwt"}
    post_json.assert_called_once_with(
        "https://staging.pymthouse.com/api/v1/apps/app_test/auth/api-key/signer-session",
        {"scope": "sign:job"},
        headers={
            "Authorization": "Bearer pmth_test",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=15.0,
    )


def test_exchange_api_key_for_signer_rejects_empty_api_key() -> None:
    with pytest.raises(LivepeerGatewayError, match="non-empty API key"):
        exchange_api_key_for_signer("https://staging.pymthouse.com", "  ")


def test_exchange_api_key_for_signer_requires_client_id() -> None:
    with pytest.raises(LivepeerGatewayError, match="non-empty client_id"):
        exchange_api_key_for_signer("https://staging.pymthouse.com", "pmth_test")


def test_exchange_api_key_for_signer_requires_access_token() -> None:
    with patch("livepeer_gateway_client.auth_exchange.post_json", return_value={}):
        with pytest.raises(LivepeerGatewayError, match="missing signer access token"):
            exchange_api_key_for_signer(
                "https://staging.pymthouse.com",
                "pmth_test",
                client_id="app_test",
            )


def test_signer_token_provider_refresh_remints_headers() -> None:
    payloads = [
        {"token": {"accessToken": "jwt1"}, "signerUrl": "https://signer.example"},
        {"token": {"accessToken": "jwt2"}, "signerUrl": "https://signer.example"},
    ]
    with patch(
        "livepeer_gateway_client.auth_exchange.post_json",
        side_effect=payloads,
    ):
        provider = SignerTokenProvider(
            billing_url="https://staging.pymthouse.com",
            api_key="pmth_test",
            client_id="app_test",
        )
        first = provider.refresh()
        assert first == {"Authorization": "Bearer jwt1"}
        assert provider.signer_url == "https://signer.example"
        assert provider.headers == first

        second = provider.refresh()
        assert second == {"Authorization": "Bearer jwt2"}
        assert provider.headers == second


def test_exchange_client_secret_for_signer_returns_signer_url_and_bearer() -> None:
    payload = {
        "access_token": "m2m_jwt",
        "token_type": "Bearer",
        "expires_in": 3600,
        "signer_url": "https://pymthouse-preview.up.railway.app",
    }
    with patch(
        "livepeer_gateway_client.auth_exchange.client_credentials_token",
        return_value=payload,
    ) as client_credentials_token:
        signer_url, headers = exchange_client_secret_for_signer(
            "https://staging.pymthouse.com/api/v1/oidc",
            "m2m_test",
            "pmth_cs_test",
            external_user_id="user_123",
            audience="livepeer-remote-signer",
        )

    assert signer_url == "https://pymthouse-preview.up.railway.app"
    assert headers == {"Authorization": "Bearer m2m_jwt"}
    client_credentials_token.assert_called_once_with(
        "https://staging.pymthouse.com/api/v1/oidc",
        client_id="m2m_test",
        client_secret="pmth_cs_test",
        scope=None,
        external_user_id="user_123",
        audience="livepeer-remote-signer",
        timeout=15.0,
    )


def test_exchange_client_secret_for_signer_requires_m2m_client_id() -> None:
    with pytest.raises(LivepeerGatewayError, match="non-empty m2m_client_id"):
        exchange_client_secret_for_signer(
            "https://staging.pymthouse.com/api/v1/oidc",
            "  ",
            "pmth_cs_test",
        )


def test_signer_token_provider_pmth_cs_uses_client_credentials_path() -> None:
    with patch(
        "livepeer_gateway_client.auth_exchange.client_credentials_token",
        return_value={
            "access_token": "jwt_m2m",
            "signer_url": "https://pymthouse-preview.up.railway.app",
        },
    ) as client_credentials_token:
        provider = SignerTokenProvider(
            billing_url="https://staging.pymthouse.com",
            api_key="pmth_cs_test",
            m2m_client_id="m2m_test",
            external_user_id="user_123",
            m2m_audience="livepeer-remote-signer",
        )
        headers = provider.refresh()

    assert headers == {"Authorization": "Bearer jwt_m2m"}
    assert provider.signer_url == "https://pymthouse-preview.up.railway.app"
    client_credentials_token.assert_called_once_with(
        "https://staging.pymthouse.com/api/v1/oidc",
        client_id="m2m_test",
        client_secret="pmth_cs_test",
        scope=None,
        external_user_id="user_123",
        audience="livepeer-remote-signer",
        timeout=15.0,
    )


def test_signer_token_provider_pmth_cs_requires_m2m_client_id() -> None:
    provider = SignerTokenProvider(
        billing_url="https://staging.pymthouse.com",
        api_key="pmth_cs_test",
    )
    with pytest.raises(LivepeerGatewayError, match="requires m2m_client_id"):
        provider.refresh()


def test_signer_token_provider_pmth_cs_requires_external_user_id() -> None:
    provider = SignerTokenProvider(
        billing_url="https://staging.pymthouse.com",
        api_key="pmth_cs_test",
        m2m_client_id="m2m_test",
    )
    with pytest.raises(LivepeerGatewayError, match="requires external_user_id"):
        provider.refresh()


def test_format_gateway_error_includes_orchestrator_rejections() -> None:
    from livepeer_gateway.errors import (
        NoOrchestratorAvailableError,
        OrchestratorRejection,
    )

    exc = NoOrchestratorAvailableError(
        "All orchestrators failed (2 tried)",
        rejections=[
            OrchestratorRejection(
                url="https://orch1.example", reason="model not found"
            ),
            OrchestratorRejection(url="https://orch2.example", reason="timeout"),
        ],
    )
    formatted = format_gateway_error(exc)
    assert "All orchestrators failed" in formatted
    assert "orch1.example: model not found" in formatted
    assert "orch2.example: timeout" in formatted
