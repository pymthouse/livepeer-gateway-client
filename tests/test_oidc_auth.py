from __future__ import annotations

from unittest.mock import MagicMock, patch

from authlib.oauth2.auth import OAuth2Token

from livepeer_gateway_client.oidc_auth import (
    _cache_key,
    ensure_valid_token,
    load_cached_token,
    save_cached_token,
)


def test_cache_key_is_stable():
    assert _cache_key("https://issuer.test", "client", "openid") == _cache_key(
        "https://issuer.test",
        "client",
        "openid",
    )


def test_ensure_valid_token_uses_cached_token(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "livepeer_gateway_client.oidc_auth._cache_dir",
        lambda: tmp_path,
    )
    tokens = OAuth2Token(
        {
            "access_token": "cached",
            "token_type": "Bearer",
            "expires_in": 3600,
        }
    )
    save_cached_token("https://issuer.test", tokens)

    loaded = load_cached_token("https://issuer.test")
    assert loaded is not None
    assert loaded["access_token"] == "cached"

    with patch("livepeer_gateway_client.oidc_auth.device_login") as device_login:
        result = ensure_valid_token("https://issuer.test", headless=True)
        device_login.assert_not_called()
    assert result["access_token"] == "cached"


def test_probe_oidc_returns_true_on_200():
    response = MagicMock()
    response.status_code = 200

    with patch(
        "livepeer_gateway_client.oidc_auth._build_oauth2_client"
    ) as build_client:
        client = MagicMock()
        client.__enter__.return_value = client
        client.request.return_value = response
        build_client.return_value = client

        from livepeer_gateway_client.oidc_auth import probe_oidc

        assert probe_oidc("https://issuer.test") is True
