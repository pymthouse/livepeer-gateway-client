from __future__ import annotations

from unittest.mock import patch

import pytest
from livepeer_gateway.errors import LivepeerGatewayError

from livepeer_gateway_client.auth_exchange import (
    exchange_api_key_for_signer,
    exchange_client_secret_for_signer,
    exchange_oidc_token_for_signer,
)
from livepeer_gateway_client.errors import format_gateway_error
from livepeer_gateway_client.signer_provider import SignerTokenProvider


def test_exchange_api_key_for_signer_returns_signer_url_and_bearer() -> None:
    payload = {
        "access_token": "jwt",
        "signer_url": "https://signer.example",
        "discovery_url": "https://discovery.example/raw",
    }
    with patch(
        "livepeer_gateway_client.auth_exchange._post_token_exchange",
        return_value=payload,
    ) as post_exchange:
        result = exchange_api_key_for_signer(
            "http://localhost:8095",
            "sk_demo",
            client_id="app_test",
        )

    assert result.signer_url == "https://signer.example"
    assert result.discovery_url == "https://discovery.example/raw"
    assert result.headers == {"Authorization": "Bearer jwt"}
    post_exchange.assert_called_once_with(
        "http://localhost:8095",
        public_client_id="app_test",
        subject_token="sk_demo",
        audience="livepeer-clearinghouse",
        timeout=15.0,
    )


def test_exchange_api_key_for_signer_rejects_empty_api_key() -> None:
    with pytest.raises(LivepeerGatewayError, match="non-empty API key"):
        exchange_api_key_for_signer("http://localhost:8095", "  ", client_id="app_test")


def test_exchange_api_key_for_signer_requires_client_id() -> None:
    with pytest.raises(LivepeerGatewayError, match="client_id"):
        exchange_api_key_for_signer("http://localhost:8095", "sk_demo", client_id="  ")


def test_exchange_api_key_for_signer_requires_access_token() -> None:
    with patch("livepeer_gateway_client.auth_exchange._post_token_exchange", return_value={}):
        with pytest.raises(LivepeerGatewayError, match="missing access_token"):
            exchange_api_key_for_signer(
                "http://localhost:8095",
                "sk_demo",
                client_id="app_test",
            )


def test_signer_token_provider_refresh_remints_headers() -> None:
    payloads = [
        {
            "access_token": "jwt1",
            "signer_url": "https://signer.example",
            "discovery_url": "https://discovery.example/raw",
        },
        {
            "access_token": "jwt2",
            "signer_url": "https://signer.example",
            "discovery_url": "https://discovery.example/raw",
        },
    ]
    with patch(
        "livepeer_gateway_client.auth_exchange._post_token_exchange",
        side_effect=payloads,
    ):
        provider = SignerTokenProvider(
            billing_url="http://localhost:8095",
            api_key="sk_demo",
            client_id="app_test",
        )
        first = provider.refresh()
        assert first == {"Authorization": "Bearer jwt1"}
        assert provider.signer_url == "https://signer.example"
        assert provider.discovery_url == "https://discovery.example/raw"
        assert provider.headers == first

        second = provider.refresh()
        assert second == {"Authorization": "Bearer jwt2"}
        assert provider.headers == second


def test_signer_token_provider_oidc_exchanges_via_builder_api() -> None:
    with (
        patch(
            "livepeer_gateway_client.oidc_auth.ensure_valid_token",
            return_value={"access_token": "device_jwt"},
        ),
        patch(
            "livepeer_gateway_client.auth_exchange._post_token_exchange",
            return_value={
                "access_token": "minted_jwt",
                "signer_url": "http://localhost:8081",
                "discovery_url": "https://discovery.example/raw",
            },
        ) as post_exchange,
    ):
        provider = SignerTokenProvider(
            oidc_base_url="https://pymthouse.us.auth0.com",
            billing_url="http://localhost:8095",
            oidc_client_id="pub-client",
            oidc_audience="livepeer-clearinghouse",
        )
        headers = provider.refresh()

    assert headers == {"Authorization": "Bearer minted_jwt"}
    assert provider.signer_url == "http://localhost:8081"
    assert provider.discovery_url == "https://discovery.example/raw"
    post_exchange.assert_called_once()
    assert post_exchange.call_args.kwargs["subject_token"] == "device_jwt"
    assert post_exchange.call_args.kwargs["public_client_id"] == "pub-client"


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
        result = exchange_client_secret_for_signer(
            "https://staging.pymthouse.com/api/v1/oidc",
            "m2m_test",
            "pmth_cs_test",
            external_user_id="user_123",
            audience="livepeer-remote-signer",
        )

    assert result.signer_url == "https://pymthouse-preview.up.railway.app"
    assert result.headers == {"Authorization": "Bearer m2m_jwt"}
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
