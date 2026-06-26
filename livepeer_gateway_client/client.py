from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Sequence
from typing import Any

from livepeer_gateway.errors import LivepeerGatewayError, SkipPaymentCycle
from livepeer_gateway.lv2v import LiveVideoToVideo, StartJobRequest, start_lv2v
from livepeer_gateway.scope import start_scope

from .errors import is_signer_auth_error
from .signer_provider import SignerTokenProvider

_LOG = logging.getLogger(__name__)

PAYMENT_SEND_INTERVAL_S = 10.0
MAX_AUTH_REFRESH_RETRIES = 3
SHUTDOWN_TIMEOUT_S = 5.0


class LivepeerClient:
    """Livepeer client with signer JWT refresh for long-running streams.

    Wraps upstream ``start_lv2v`` / ``start_scope`` and owns a payment loop that
    re-mints the signer bearer when it expires mid-stream.
    """

    def __init__(
        self,
        *,
        model_id: str,
        orchestrator_url: Sequence[str] | str | None = None,
        discovery_url: str | None = None,
        token: str | None = None,
        signer_url: str | None = None,
        signer_headers: dict[str, str] | None = None,
        signer_provider: SignerTokenProvider | None = None,
        job_kind: str = "lv2v",
        use_tofu: bool = True,
        timeout: float = 300.0,
    ) -> None:
        self._model_id = model_id
        self._orchestrator_url = orchestrator_url
        self._discovery_url = discovery_url
        self._token = token
        self._signer_url = signer_url
        self._signer_headers = signer_headers
        self._signer_provider = signer_provider
        self._job_kind = job_kind
        self._use_tofu = use_tofu
        self._timeout = timeout

        self._job: LiveVideoToVideo | None = None
        self._payment_task: asyncio.Task | None = None
        self._connected = False
        self._shutdown_started = False

    @property
    def is_connected(self) -> bool:
        return self._connected and self._job is not None

    @property
    def job(self) -> LiveVideoToVideo | None:
        return self._job

    def _resolve_signer_auth(self) -> tuple[str | None, dict[str, str] | None]:
        signer_url = self._signer_url
        signer_headers = self._signer_headers

        if self._signer_provider is not None:
            signer_headers = self._signer_provider.refresh()
            if self._signer_provider.signer_url:
                signer_url = self._signer_provider.signer_url
            elif signer_url is None:
                signer_url = os.environ.get("LIVEPEER_SIGNER")
            if signer_url is None:
                raise LivepeerGatewayError(
                    "Signer URL required: token exchange must return signer_url, "
                    "or pass signer_url to LivepeerClient / set LIVEPEER_SIGNER"
                )

        return signer_url, signer_headers

    async def connect(
        self,
        req: StartJobRequest | None = None,
        *,
        initial_parameters: dict[str, Any] | None = None,
    ) -> LiveVideoToVideo:
        if self.is_connected:
            await self.disconnect()

        self._shutdown_started = False
        request = req or StartJobRequest(model_id=self._model_id)
        if initial_parameters and request.params is None:
            request = StartJobRequest(
                request_id=request.request_id,
                model_id=request.model_id or self._model_id,
                params=initial_parameters,
                stream_id=request.stream_id,
            )

        signer_url, signer_headers = self._resolve_signer_auth()
        _LOG.info(
            "Connecting with signer_url=%s discovery_url=%s",
            signer_url,
            self._discovery_url,
        )

        start_fn = start_scope if self._job_kind == "scope" else start_lv2v
        orch_url = self._orchestrator_url

        self._job = await asyncio.to_thread(
            start_fn,
            orch_url,
            request,
            start_payments=False,
            token=self._token,
            signer_url=signer_url,
            signer_headers=signer_headers,
            discovery_url=self._discovery_url,
            discovery_headers=signer_headers,
            use_tofu=self._use_tofu,
            timeout=self._timeout,
        )

        self._connected = True

        if self._job.payment_session is not None and self._job.signer_url:
            self._payment_task = asyncio.create_task(self._payment_loop())

        _LOG.info(
            "Connected to Livepeer job %s",
            self._job.manifest_id or "unknown",
        )
        return self._job

    async def disconnect(self) -> None:
        await self._shutdown()

    async def _shutdown(self) -> None:
        if self._shutdown_started:
            return
        self._shutdown_started = True
        self._connected = False

        payment_task = self._payment_task
        self._payment_task = None
        if payment_task is not None and payment_task is not asyncio.current_task():
            payment_task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.shield(payment_task),
                    timeout=SHUTDOWN_TIMEOUT_S,
                )
            except (asyncio.CancelledError, TimeoutError):
                pass

        job = self._job
        self._job = None
        if job is not None:
            await job.close()

        _LOG.info("Disconnected")

    async def _payment_loop(self) -> None:
        job = self._job
        if job is None:
            return
        session = job.payment_session
        if session is None:
            return

        _LOG.info(
            "Livepeer payment loop started (interval=%.1fs)",
            PAYMENT_SEND_INTERVAL_S,
        )

        try:
            while not self._shutdown_started and self._job is job and self._connected:
                auth_attempts = 0
                while True:
                    try:
                        await asyncio.to_thread(session.send_payment)
                        break
                    except SkipPaymentCycle as exc:
                        _LOG.debug("Livepeer payment loop skipped cycle: %s", exc)
                        break
                    except LivepeerGatewayError as exc:
                        if (
                            self._signer_provider is not None
                            and is_signer_auth_error(exc)
                            and auth_attempts < MAX_AUTH_REFRESH_RETRIES
                        ):
                            _LOG.info("Signer token expired; re-minting signer auth")
                            new_headers = self._signer_provider.refresh()
                            if new_headers:
                                session._signer_headers = dict(new_headers)
                            auth_attempts += 1
                            continue
                        _LOG.warning("Livepeer periodic payment failed: %s", exc)
                        break
                    except Exception as exc:
                        _LOG.warning("Livepeer periodic payment failed: %s", exc)
                        break

                if (
                    self._shutdown_started
                    or not self._connected
                    or self._job is not job
                ):
                    break
                await asyncio.sleep(PAYMENT_SEND_INTERVAL_S)
        except asyncio.CancelledError:
            pass
        finally:
            _LOG.debug("Livepeer payment loop stopped")
