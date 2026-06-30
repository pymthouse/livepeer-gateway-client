#!/usr/bin/env python3
"""OpenAI-compatible local gateway for live-runner LLM apps (vLLM, etc.).

Mirrors the example-apps ``gateway.py`` flow using ``livepeer_gateway.live_runner``
and optional PymtHouse signing via :class:`SignerTokenProvider`.

Usage (offchain)::

    uv run examples/llm_gateway.py --discovery http://localhost:8935/discovery

Usage (PymtHouse signer-session exchange)::

    uv run examples/llm_gateway.py \\
      --discovery http://localhost:8935/discovery \\
      --billing-url http://localhost:3000 \\
      --client-id app_... \\
      --api-key pmth_...

Point any OpenAI client at ``http://127.0.0.1:18080/v1`` (default; set ``GATEWAY_PORT`` or ``--port``).
"""

from __future__ import annotations

import argparse
import logging
import os
from contextlib import suppress

from aiohttp import web
from livepeer_gateway.live_runner import call_runner, stop_runner_session
from livepeer_gateway.selection import reserve_session

from livepeer_gateway_client.signer_provider import SignerTokenProvider

DEFAULT_APP_ID = "vllm/qwen2.5-0.5b-instruct"
DEFAULT_GATEWAY_PORT = int(os.environ.get("GATEWAY_PORT", "18080"))

log = logging.getLogger("llm-gateway")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OpenAI-compatible gateway in front of a live-runner LLM app.",
    )
    parser.add_argument("--discovery", default="http://localhost:8935/discovery")
    parser.add_argument("--app", default=DEFAULT_APP_ID, help="Live-runner app id.")
    parser.add_argument(
        "--billing-url",
        default="",
        help="PymtHouse base URL for API-key signer-session exchange.",
    )
    parser.add_argument("--api-key", default="", help="PymtHouse pmth_* API key.")
    parser.add_argument(
        "--client-id", default="", help="Public app client id (app_...)."
    )
    parser.add_argument(
        "--m2m-client-id",
        default="",
        help="Confidential client id (m2m_...) for pmth_cs_* exchange.",
    )
    parser.add_argument(
        "--external-user-id",
        default="",
        help="End-user id for pmth_cs_* mint (external_user_id).",
    )
    parser.add_argument(
        "--signer",
        default="",
        help="Remote signer base URL (optional when using billing-url exchange).",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_GATEWAY_PORT)
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    args = _parse_args()

    provider: SignerTokenProvider | None = None
    signer_url = args.signer.strip() or None
    signer_headers: dict[str, str] | None = None

    if args.billing_url.strip() and args.api_key.strip():
        provider = SignerTokenProvider(
            billing_url=args.billing_url.strip(),
            api_key=args.api_key.strip(),
            client_id=args.client_id.strip() or None,
            m2m_client_id=args.m2m_client_id.strip() or None,
            external_user_id=args.external_user_id.strip() or None,
        )
        provider.refresh()
        signer_url = provider.signer_url or signer_url
        signer_headers = dict(provider.headers)
        if signer_url is None:
            raise SystemExit(
                "Token exchange did not return signer_url; "
                "pass --signer or set LIVEPEER_SIGNER"
            )
        log.info("signer via pymthouse exchange (signer=%s)", signer_url)

    async def _forward(request: web.Request) -> web.StreamResponse:
        payload = await request.json()
        runner_path = request.path
        session = await reserve_session(
            discovery_url=args.discovery,
            app=args.app,
            signer_url=signer_url,
            signer_headers=signer_headers,
        )

        try:
            runner_url = session.app_url.rstrip("/") + runner_path
            if payload.get("stream"):
                async with await call_runner(
                    runner_url=runner_url,
                    payload=payload,
                    signer_url=signer_url,
                    signer_headers=signer_headers,
                    stream=True,
                ) as stream:
                    resp = web.StreamResponse(
                        status=stream.status,
                        headers={
                            "Content-Type": stream.content_type or "text/event-stream"
                        },
                    )
                    await resp.prepare(request)
                    async for chunk in stream.aiter_bytes():
                        await resp.write(chunk)
                    await resp.write_eof()
                    return resp

            result = await call_runner(
                runner_url=runner_url,
                payload=payload,
                signer_url=signer_url,
                signer_headers=signer_headers,
            )
            return web.json_response(result.data)
        finally:
            if provider is not None:
                with suppress(Exception):
                    provider.refresh()
            with suppress(Exception):
                await stop_runner_session(session)

    app = web.Application()
    app.router.add_post("/v1/{tail:.*}", _forward)
    log.info(
        "gateway on http://%s:%d/v1 -> %s (app=%s signer=%s)",
        args.host,
        args.port,
        args.discovery,
        args.app,
        signer_url or "none",
    )
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
