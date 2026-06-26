from __future__ import annotations

from urllib.error import HTTPError

from livepeer_gateway.errors import (
    LivepeerGatewayError,
    NoOrchestratorAvailableError,
    OrchestratorRejection,
    PaymentError,
    SignerRefreshRequired,
    SkipPaymentCycle,
)


class SignerAuthExpired(LivepeerGatewayError):
    """Raised when the remote signer rejects an expired/invalid bearer token.

    Distinct from ``SignerRefreshRequired`` (orchestrator-info refresh): this
    signals the signer session JWT itself must be re-minted before retrying.
    """


def is_signer_auth_error(exc: BaseException) -> bool:
    """Heuristic: did the signer reject the request due to an expired/invalid token?

    The signer (or PymtHouse in front of it) returns 401/403 for a bad bearer,
    and a 502 carrying a JWT validation message (e.g. ``"exp" claim ...
    expiration is past current timestamp``) when the session token has expired.
    """
    cause = getattr(exc, "__cause__", None) or exc
    if isinstance(cause, HTTPError) and cause.code in (401, 403):
        return True
    text = str(exc).lower()
    if "expired" in text or "expiration is past" in text:
        return True
    return '"exp"' in text and "claim" in text


def format_gateway_error(exc: LivepeerGatewayError) -> str:
    """Format gateway errors with orchestrator rejection details when available."""
    if isinstance(exc, NoOrchestratorAvailableError) and exc.rejections:
        lines = [str(exc)]
        for index, rejection in enumerate(exc.rejections[:10], start=1):
            lines.append(f"  {index}. {rejection.url}: {rejection.reason}")
        remaining = len(exc.rejections) - 10
        if remaining > 0:
            lines.append(f"  ... and {remaining} more")
        return "\n".join(lines)
    return str(exc)


__all__ = [
    "LivepeerGatewayError",
    "NoOrchestratorAvailableError",
    "OrchestratorRejection",
    "PaymentError",
    "SignerAuthExpired",
    "SignerRefreshRequired",
    "SkipPaymentCycle",
    "format_gateway_error",
    "is_signer_auth_error",
]
