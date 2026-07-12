#!/usr/bin/env python3
"""Client for the ``transcode/ffmpeg`` Live Runner — batch (VOD) or live (trickle).

Sends go-livepeer's native profile ladder (``{"profiles": [...]}``) to whichever
surface you pick. Discovery accepts either:

  - an orchestrator ``GET /discovery`` URL (single-hop runner list), or
  - discovery-service ``/v1/discovery/raw`` (two-hop: raw orch addresses, then
    each orch's ``/discovery``)

  batch (default): reserve -> call_runner POST /transcode -> write each rendition
  live (--live)  : reserve -> POST /transcode/live -> publish frames -> write .ts

    # batch via discovery-service (two-hop)
    uv run examples/transcode.py clip.mp4 --heights 720,360 \\
        --discovery https://discovery-service-production-8955.up.railway.app/v1/discovery/raw \\
        --signer https://signer.eliteencoder.net

    # batch against a single orch /discovery
    uv run examples/transcode.py clip.mp4 --heights 720,360 \\
        --discovery http://154.61.61.108:8787/discovery \\
        --signer https://signer.eliteencoder.net

    # batch at scale: object-storage URLs, bytes never touch the control plane
    uv run examples/transcode.py --input-url https://.../src.mp4 \\
        --profiles '[{"name":"720p","height":720}]' \\
        --output-urls '{"720p":"https://.../720p.mp4?sig=..."}' \\
        --discovery http://154.61.61.108:8787/discovery --signer https://signer.example.com

    # live: real-time trickle ladder
    uv run examples/transcode.py clip.mp4 --live --heights 720,360 \\
        --discovery http://154.61.61.108:8787/discovery --signer https://signer.example.com

Profiles are native JsonProfile objects (name/width/height/bitrate/fps/gop/
profile/encoder/quality). The pipeline is paid, so pass a signer: either a raw
remote signer URL (``--signer``) or a PymtHouse credential exchanged into a
short-lived signer session (``--billing-url`` + ``--client-id`` + ``--api-key``).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import time
from contextlib import AsyncExitStack, suppress
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import av
from livepeer_gateway.discovery import discover_orchestrators
from livepeer_gateway.errors import LivepeerGatewayError
from livepeer_gateway.http import post_json
from livepeer_gateway.live_runner import call_runner, stop_runner_session
from livepeer_gateway.media_output import MediaOutput
from livepeer_gateway.media_publish import MediaPublish
from livepeer_gateway.selection import reserve_session

from livepeer_gateway_client import SignerTokenProvider, format_gateway_error

DEFAULT_DISCOVERY = (
    "https://discovery-service-production-8955.up.railway.app/v1/discovery/raw"
)
APP_ID = "transcode/ffmpeg"

log = logging.getLogger("transcode-client")


def _is_discovery_service_raw(url: str) -> bool:
    return "/v1/discovery/raw" in urlparse(url).path


def _raw_discovery_url(url: str, app: str) -> str:
    parsed = urlparse(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if not any(key == "caps" for key, _ in query):
        query.append(("caps", app))
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


async def _reserve(args: argparse.Namespace, signer_url, signer_headers):
    if _is_discovery_service_raw(args.discovery):
        raw_url = _raw_discovery_url(args.discovery, APP_ID)
        orchs = discover_orchestrators(discovery_url=raw_url)
        log.info("discovery-service returned %d orchestrator(s)", len(orchs))
        return await reserve_session(
            orchestrators=orchs,
            app=APP_ID,
            signer_url=signer_url,
            signer_headers=signer_headers,
        )
    return await reserve_session(
        discovery_url=args.discovery,
        app=APP_ID,
        signer_url=signer_url,
        signer_headers=signer_headers,
    )

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Transcode a video through the transcode/ffmpeg Live Runner."
    )
    p.add_argument(
        "input",
        nargs="?",
        help="Input video file (batch: base64; live: source to publish).",
    )
    p.add_argument(
        "--input-url",
        default="",
        help="Batch: source URL the runner fetches (object storage).",
    )
    p.add_argument(
        "--output-urls",
        default="",
        help='Batch: JSON {"<name>": "<presigned PUT url>"} to upload renditions.',
    )
    p.add_argument(
        "--discovery",
        default=DEFAULT_DISCOVERY,
        help=(
            "Discovery URL: discovery-service /v1/discovery/raw (two-hop) or "
            f"an orch GET /discovery (single-hop). Default: {DEFAULT_DISCOVERY}"
        ),
    )
    p.add_argument(
        "--heights",
        default="720,360",
        help="Comma-separated rendition heights (shorthand).",
    )
    p.add_argument(
        "--profiles",
        default="",
        help="Full native profile ladder as JSON. Overrides --heights.",
    )
    p.add_argument(
        "--output-prefix",
        default="out",
        help="Batch/live: writes <prefix>-<name>.<ext>.",
    )
    p.add_argument(
        "--live",
        action="store_true",
        help="Use the live trickle surface instead of batch.",
    )
    p.add_argument(
        "--signer",
        default=None,
        help="Remote signer URL (on-chain/paid path). Omit for a free pipeline.",
    )
    p.add_argument(
        "--billing-url",
        default=None,
        help="PymtHouse base URL for pmth_* API-key signer-session exchange.",
    )
    p.add_argument(
        "--client-id",
        default=None,
        help=(
            "PymtHouse public client id (app_*). Optional when --api-key is a "
            "composite app_<24hex>_<secret> key."
        ),
    )
    p.add_argument(
        "--m2m-client-id",
        default=None,
        help="PymtHouse confidential client id (m2m_*) for pmth_cs_* client_credentials exchange.",
    )
    p.add_argument(
        "--external-user-id",
        default=None,
        help="End-user id required by pmth_cs_* mint (sent as external_user_id).",
    )
    p.add_argument(
        "--m2m-audience",
        default=None,
        help="Optional audience for pmth_cs_* mint (e.g. livepeer-remote-signer).",
    )
    p.add_argument(
        "--api-key",
        default=None,
        help="PymtHouse credential (pmth_* user key or pmth_cs_* M2M secret).",
    )
    p.add_argument(
        "--oidc-url",
        default=None,
        help="OIDC issuer URL (Auth0 or PymtHouse). Device code flow when headless (default).",
    )
    p.add_argument(
        "--oidc-client-id",
        default=None,
        help="Public/native OIDC client id for device/browser login.",
    )
    p.add_argument(
        "--oidc-audience",
        default=None,
        help="OIDC API audience for device login.",
    )
    p.add_argument(
        "--oidc-scopes",
        default="openid sign:job offline_access",
        help="OIDC scopes for device/browser login (default: openid sign:job offline_access).",
    )
    p.add_argument(
        "--oidc-browser",
        action="store_true",
        help="Use browser redirect login instead of RFC 8628 device code.",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging.",
    )
    return p.parse_args()


def _resolve_signer(
    args: argparse.Namespace,
) -> tuple[str | None, dict[str, str] | None]:
    if args.api_key and args.billing_url:
        provider = SignerTokenProvider(
            billing_url=args.billing_url,
            api_key=args.api_key,
            client_id=args.client_id,
            m2m_client_id=args.m2m_client_id,
            external_user_id=args.external_user_id,
            m2m_audience=args.m2m_audience,
        )
        headers = provider.refresh()
        return provider.signer_url or args.signer, headers
    if args.oidc_url:
        provider = SignerTokenProvider(
            oidc_base_url=args.oidc_url,
            billing_url=args.billing_url,
            client_id=args.oidc_client_id,
            oidc_client_id=args.oidc_client_id or "livepeer-sdk",
            oidc_scopes=args.oidc_scopes,
            oidc_audience=args.oidc_audience,
            oidc_headless=not args.oidc_browser,
        )
        headers = provider.refresh()
        return provider.signer_url or args.signer, headers
    if args.api_key and args.signer:
        return args.signer.strip(), {"Authorization": f"Bearer {args.api_key.strip()}"}
    return (args.signer.strip() if args.signer else None), None


def _profiles(args: argparse.Namespace) -> list[dict]:
    if args.profiles.strip():
        return json.loads(args.profiles)
    return [
        {"name": f"{h.strip()}p", "height": int(h)}
        for h in args.heights.split(",")
        if h.strip()
    ]


async def _run_batch(session, args, profiles, signer_url, signer_headers) -> None:
    payload: dict = {"profiles": profiles}
    if args.input_url:
        payload["input_url"] = args.input_url
    else:
        payload["video_b64"] = base64.b64encode(Path(args.input).read_bytes()).decode()
    if args.output_urls.strip():
        payload["output_urls"] = json.loads(args.output_urls)

    result = await call_runner(
        runner_url=session.app_url.rstrip("/") + "/transcode",
        runner=session.runner,
        payload=payload,
        signer_url=signer_url,
        signer_headers=signer_headers,
        timeout=3600.0,
    )
    data = result.data
    if "renditions" not in data:
        raise SystemExit(f"ERROR: {data.get('error', data)}")
    for r in data["renditions"]:
        line = f"  {r['name']}: {r['encoder']} {r['width']}x{r['height']} {r['bytes']} bytes"
        if "output_b64" in r:
            out = f"{args.output_prefix}-{r['name']}.{r['ext']}"
            Path(out).write_bytes(base64.b64decode(r["output_b64"]))
            line += f" -> {out}"
        else:
            line += f" -> {r['output_url']}"
        print(line)


async def _publish(source: str, url: str) -> None:
    container = av.open(source)
    try:
        publisher = MediaPublish(url)
        prev_t = prev_wall = None
        try:
            for frame in container.decode(video=0):
                t = (
                    float(frame.pts * frame.time_base)
                    if frame.pts is not None and frame.time_base
                    else None
                )
                if prev_t is not None and prev_wall is not None and t is not None:
                    sleep_s = max(0.0, (t - prev_t) - (time.monotonic() - prev_wall))
                    if sleep_s:
                        await asyncio.sleep(sleep_s)
                if t is not None:
                    prev_t, prev_wall = t, time.monotonic()
                await publisher.write_frame(frame)
        finally:
            await publisher.close()
    finally:
        container.close()


async def _run_live(session, args, profiles) -> None:
    resp = await post_json(
        f"{session.app_url.rstrip('/')}/transcode/live", {"profiles": profiles}
    )
    outputs: dict[str, str] = resp["outputs"]
    files = {}
    try:
        async with AsyncExitStack() as stack:
            for name, url in outputs.items():
                fh = open(f"{args.output_prefix}-{name}.ts", "wb")
                files[name] = fh
                await stack.enter_async_context(MediaOutput(url, on_bytes=fh.write))
            await _publish(args.input, resp["in"])
            log.info("publish complete; draining %d rendition(s)...", len(outputs))
    finally:
        for fh in files.values():
            fh.close()
    for name in outputs:
        print(f"  wrote {args.output_prefix}-{name}.ts")


async def main() -> None:
    args = _parse_args()
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s %(message)s")

    if not args.input and not args.input_url:
        raise SystemExit("give an input file (batch/live) or --input-url (batch)")
    if args.live and not args.input:
        raise SystemExit("--live needs a source file to publish")

    signer_url, signer_headers = _resolve_signer(args)
    profiles = _profiles(args)

    session = None
    try:
        session = await _reserve(args, signer_url, signer_headers)
        log.info("session_id=%s app=%s", session.session_id, APP_ID)
        if args.live:
            await _run_live(session, args, profiles)
        else:
            await _run_batch(session, args, profiles, signer_url, signer_headers)
    except LivepeerGatewayError as exc:
        raise SystemExit(f"ERROR: {format_gateway_error(exc)}") from exc
    finally:
        if session is not None:
            with suppress(Exception):
                await stop_runner_session(session)


if __name__ == "__main__":
    asyncio.run(main())
