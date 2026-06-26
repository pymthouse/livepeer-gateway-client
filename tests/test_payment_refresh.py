from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest
from livepeer_gateway.errors import LivepeerGatewayError, PaymentError

from livepeer_gateway_client.client import LivepeerClient
from livepeer_gateway_client.errors import is_signer_auth_error
from livepeer_gateway_client.signer_provider import SignerTokenProvider

EXPIRED_BODY = (
    'unexpected JWT "exp" (expiration time) claim value, '
    "expiration is past current timestamp"
)


def test_is_signer_auth_error_detects_expired_jwt():
    assert is_signer_auth_error(Exception(EXPIRED_BODY))
    cause = HTTPError("https://signer.test", 401, "unauthorized", {}, None)
    wrapped = Exception("boom")
    wrapped.__cause__ = cause
    assert is_signer_auth_error(wrapped)
    assert not is_signer_auth_error(Exception("HTTP 500 internal error"))


def _client_with_session(*, send_payment) -> LivepeerClient:
    session = MagicMock()
    session.send_payment = send_payment

    job = MagicMock()
    job.payment_session = session
    job.signer_url = "https://signer.test"

    client = LivepeerClient(model_id="test-model")
    client._job = job
    client._connected = True
    client._shutdown_started = False
    return client


def test_payment_loop_refreshes_expired_token_then_succeeds():
    calls = {"send": 0}
    client = _client_with_session(send_payment=lambda: None)

    def _send_payment():
        calls["send"] += 1
        if calls["send"] == 1:
            raise PaymentError(EXPIRED_BODY)
        client._shutdown_started = True

    client._job.payment_session.send_payment = _send_payment
    provider = MagicMock(spec=SignerTokenProvider)
    provider.refresh.return_value = {"Authorization": "Bearer jwt_fresh"}
    client._signer_provider = provider

    asyncio.run(client._payment_loop())

    assert provider.refresh.call_count == 1
    assert client._job.payment_session._signer_headers == {
        "Authorization": "Bearer jwt_fresh"
    }
    assert calls["send"] == 2


def test_payment_loop_without_provider_stops_on_expiry():
    calls = {"send": 0}

    def _send_payment():
        calls["send"] += 1
        raise PaymentError(EXPIRED_BODY)

    client = _client_with_session(send_payment=_send_payment)
    client._signer_provider = None

    async def _stop_after_sleep(*_args):
        client._shutdown_started = True

    with patch.object(asyncio, "sleep", side_effect=_stop_after_sleep):
        asyncio.run(client._payment_loop())

    assert calls["send"] == 1


def test_resolve_signer_auth_requires_signer_url_from_provider() -> None:
    provider = MagicMock(spec=SignerTokenProvider)
    provider.refresh.return_value = {"Authorization": "Bearer jwt"}
    provider.signer_url = None

    client = LivepeerClient(model_id="test-model", signer_provider=provider)

    with pytest.raises(LivepeerGatewayError, match="Signer URL required"):
        client._resolve_signer_auth()
