from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest
from livepeer_gateway.errors import LivepeerGatewayError

from livepeer_gateway_client.errors import is_signer_auth_error
from livepeer_gateway_client.signer_provider import SignerTokenProvider


def _stub_orch_info():
    info = MagicMock()
    info.transcoder = "https://orch.test:8935"
    info.SerializeToString = lambda: b"stub-orch-info-protobuf"
    return info


class _MockResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


EXPIRED_BODY = (
    b'unexpected JWT "exp" (expiration time) claim value, '
    b"expiration is past current timestamp"
)


def test_is_signer_auth_error_detects_expired_jwt():
    assert is_signer_auth_error(Exception(EXPIRED_BODY.decode()))
    cause = HTTPError("https://signer.test", 401, "unauthorized", {}, None)
    wrapped = Exception("boom")
    wrapped.__cause__ = cause
    assert is_signer_auth_error(wrapped)
    assert not is_signer_auth_error(Exception("HTTP 500 internal error"))


def test_payment_loop_refreshes_expired_token_then_succeeds():
    from livepeer_gateway.remote_signer import PaymentSession

    calls = {"signer": 0}
    seen_auth: list[str] = []

    def _fake_urlopen(req, *args, **kwargs):
        url = req.full_url
        hdrs = {k.lower(): v for k, v in req.header_items()}
        seen_auth.append(hdrs.get("authorization", ""))
        calls["signer"] += 1
        if calls["signer"] == 1:
            err = HTTPError(url, 502, "bad gateway", {}, None)
            err.read = lambda: EXPIRED_BODY
            raise err
        return _MockResponse(
            json.dumps(
                {"payment": "PAY", "segCreds": "SEG", "state": {"k": "v"}}
            ).encode()
        )

    provider = SignerTokenProvider(
        billing_url="https://dashboard.example.com",
        api_key="pmth_test",
    )

    session = PaymentSession(
        "https://signer.test",
        _stub_orch_info(),
        signer_headers={"Authorization": "Bearer jwt_expired"},
        type="lv2v",
    )

    with patch("livepeer_gateway.orchestrator.urlopen", side_effect=_fake_urlopen):
        with patch.object(
            provider, "refresh", return_value={"Authorization": "Bearer jwt_fresh"}
        ):
            try:
                session.send_payment()
            except LivepeerGatewayError:
                if is_signer_auth_error(Exception(EXPIRED_BODY.decode())):
                    new_headers = provider.refresh()
                    session._signer_headers = dict(new_headers)
                    result = session.get_payment()
                    assert result.payment == "PAY"
                    assert provider.refresh.call_count == 1
                    assert seen_auth[1] == "Bearer jwt_fresh"
                    return
            pytest.fail("expected auth error on first payment attempt")


def test_payment_loop_without_provider_raises_on_expiry():
    from livepeer_gateway.remote_signer import PaymentSession

    def _fake_urlopen(req, *args, **kwargs):
        err = HTTPError(req.full_url, 502, "bad gateway", {}, None)
        err.read = lambda: EXPIRED_BODY
        raise err

    session = PaymentSession(
        "https://signer.test",
        _stub_orch_info(),
        signer_headers={"Authorization": "Bearer jwt_expired"},
        type="lv2v",
    )

    with patch("livepeer_gateway.orchestrator.urlopen", side_effect=_fake_urlopen):
        with pytest.raises(LivepeerGatewayError):
            session.get_payment()
