#!/usr/bin/env python3
"""Fetch and display live-runner discovery entries from a gateway /discovery route.

Use this to sanity-check the same discovery URL that examples like transcode.py
pass to ``--discovery`` before running a paid pipeline.

    uv run examples/get_discovery_info.py \\
        --discovery http://154.61.61.108:8787/discovery

    uv run examples/get_discovery_info.py \\
        --discovery http://154.61.61.108:8787/discovery \\
        --app transcode/ffmpeg

    uv run examples/get_discovery_info.py \\
        --discovery http://154.61.61.108:8787/discovery \\
        --billing-url https://staging.pymthouse.com \\
        --client-id app_51803fb3e53dce667cf0e4df \\
        --api-key pmth_...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

from livepeer_gateway.discovery import discover_runners, orchestrator_discovery_urls
from livepeer_gateway.errors import LivepeerGatewayError

from livepeer_gateway_client import SignerTokenProvider, format_gateway_error

DEFAULT_DISCOVERY = "http://154.61.61.108:8787/discovery"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fetch and display live-runner discovery from a gateway /discovery route.",
    )
    p.add_argument(
        "--discovery",
        default=DEFAULT_DISCOVERY,
        help=f"Gateway discovery URL (default: {DEFAULT_DISCOVERY}).",
    )
    p.add_argument(
        "--orchestrator",
        default=None,
        help="Orchestrator base URL; derives <orchestrator>/discovery (overrides --discovery).",
    )
    p.add_argument(
        "--app",
        default=None,
        help="Filter by app id, e.g. transcode/ffmpeg (repeatable via comma list).",
    )
    p.add_argument(
        "--gpu",
        default=None,
        help="Filter by GPU name (repeatable via comma list).",
    )
    p.add_argument(
        "--signer",
        default=None,
        help="Remote signer URL. Discovery headers fall back to signer headers.",
    )
    p.add_argument(
        "--billing-url",
        default=None,
        help="PymtHouse base URL for pmth_* API-key signer-session exchange.",
    )
    p.add_argument(
        "--client-id",
        default=None,
        help="PymtHouse public client id (app_*) for pmth_* key exchange.",
    )
    p.add_argument(
        "--api-key",
        default=None,
        help="PymtHouse credential (pmth_* user key or pmth_cs_* M2M secret).",
    )
    p.add_argument(
        "--healthz",
        action="store_true",
        help="Also probe <gateway>/healthz before discovery.",
    )
    p.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format (default: text).",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging.",
    )
    return p.parse_args()


def _split_filters(value: str | None) -> list[str] | None:
    if value is None:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


def _resolve_discovery_url(args: argparse.Namespace) -> str:
    if args.orchestrator:
        urls = orchestrator_discovery_urls(args.orchestrator)
        if not urls:
            raise SystemExit(f"ERROR: invalid orchestrator URL: {args.orchestrator!r}")
        return urls[0]
    return args.discovery.strip()


def _resolve_auth(
    args: argparse.Namespace,
) -> tuple[str | None, dict[str, str] | None, dict[str, str] | None]:
    signer_url = args.signer.strip() if args.signer else None
    signer_headers: dict[str, str] | None = None
    discovery_headers: dict[str, str] | None = None

    if args.api_key and args.billing_url:
        provider = SignerTokenProvider(
            billing_url=args.billing_url,
            api_key=args.api_key,
            client_id=args.client_id,
        )
        signer_headers = provider.refresh()
        discovery_headers = signer_headers
        if provider.signer_url:
            signer_url = provider.signer_url

    return signer_url, signer_headers, discovery_headers


def _gateway_base_url(discovery_url: str) -> str:
    parsed = urlparse(discovery_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/discovery"):
        path = path[: -len("/discovery")] or ""
    return urlunparse((parsed.scheme, parsed.netloc, path.rstrip("/") + "/", "", "", ""))


def _probe_healthz(discovery_url: str) -> tuple[int, str]:
    base = _gateway_base_url(discovery_url).rstrip("/")
    health_url = f"{base}/healthz"
    request = Request(health_url, method="GET")
    try:
        with urlopen(request, timeout=5.0) as response:
            return response.status, health_url
    except HTTPError as exc:
        return exc.code, health_url
    except URLError as exc:
        raise LivepeerGatewayError(f"healthz probe failed: {exc.reason} (url={health_url})") from exc


def _runner_summary(runner: dict[str, Any]) -> dict[str, Any]:
    gpu = runner.get("gpu") if isinstance(runner.get("gpu"), dict) else {}
    price = runner.get("price_info") if isinstance(runner.get("price_info"), dict) else {}
    return {
        "url": runner.get("url"),
        "app": runner.get("app"),
        "mode": runner.get("mode"),
        "version": runner.get("version"),
        "capacity": runner.get("capacity"),
        "capacity_used": runner.get("capacity_used"),
        "capacity_available": runner.get("capacity_available"),
        "gpu": {
            "id": gpu.get("id"),
            "name": gpu.get("name"),
            "vram_mb": gpu.get("vram_mb"),
        }
        if gpu
        else None,
        "price_info": {
            "price_per_unit": price.get("price_per_unit"),
            "pixels_per_unit": price.get("pixels_per_unit"),
            "unit": price.get("unit"),
            "paid": bool(price.get("price_per_unit")),
        }
        if price
        else None,
    }


def _entries_summary(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for entry in entries:
        runners = entry.get("runners")
        if not isinstance(runners, list):
            continue
        summary.append(
            {
                "address": entry.get("address"),
                "runners": [_runner_summary(runner) for runner in runners if isinstance(runner, dict)],
            }
        )
    return summary


def _print_section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


def _print_kv(key: str, value: object, indent: int = 0) -> None:
    pad = "  " * indent
    print(f"{pad}{key:<28} {value}")


def _print_text(discovery_url: str, entries: list[dict[str, Any]], *, healthz: tuple[int, str] | None) -> None:
    print(f"\nLive runner discovery  ←  {discovery_url}")
    if healthz is not None:
        status, health_url = healthz
        print(f"healthz                 ←  {health_url} ({status})")

    if not entries:
        print("\n(no matching runners)")
        return

    runner_count = sum(
        len(entry.get("runners", []))
        for entry in entries
        if isinstance(entry.get("runners"), list)
    )
    _print_section(f"Orchestrators ({len(entries)})  ·  Runners ({runner_count})")

    for entry in entries:
        address = entry.get("address") or "(unknown orchestrator)"
        runners = entry.get("runners")
        if not isinstance(runners, list):
            continue
        print(f"\n  {address}")
        for runner in runners:
            if not isinstance(runner, dict):
                continue
            app = runner.get("app") or "(unknown app)"
            url = runner.get("url") or "(no url)"
            mode = runner.get("mode") or "persistent"
            version = runner.get("version") or "-"
            print(f"\n    {app}")
            _print_kv("url", url, indent=2)
            _print_kv("mode", mode, indent=2)
            _print_kv("version", version, indent=2)
            _print_kv(
                "capacity",
                f"{runner.get('capacity_available', '?')}/{runner.get('capacity', '?')} available "
                f"({runner.get('capacity_used', '?')} in use)",
                indent=2,
            )
            gpu = runner.get("gpu")
            if isinstance(gpu, dict):
                name = gpu.get("name") or "?"
                vram_mb = gpu.get("vram_mb")
                gpu_id = gpu.get("id") or ""
                vram = f"{int(vram_mb)} MB" if isinstance(vram_mb, (int, float)) else "?"
                line = f"{name}  vram={vram}"
                if gpu_id:
                    line += f"  id={gpu_id}"
                _print_kv("gpu", line, indent=2)
            price = runner.get("price_info")
            if isinstance(price, dict):
                ppu = price.get("price_per_unit")
                pxu = price.get("pixels_per_unit") or 1
                unit = price.get("unit") or "?"
                paid = bool(ppu)
                _print_kv("paid", "yes" if paid else "no (free)", indent=2)
                if paid:
                    _print_kv("price", f"{ppu}/{pxu} {unit}", indent=2)
    print()


async def _run(args: argparse.Namespace) -> int:
    discovery_url = _resolve_discovery_url(args)
    signer_url, signer_headers, discovery_headers = _resolve_auth(args)

    healthz: tuple[int, str] | None = None
    if args.healthz:
        healthz = _probe_healthz(discovery_url)

    entries = await discover_runners(
        discovery_url=discovery_url,
        discovery_headers=discovery_headers,
        signer_url=signer_url,
        signer_headers=signer_headers,
        app=_split_filters(args.app),
        gpu=_split_filters(args.gpu),
    )

    if args.format == "json":
        payload = {
            "discovery_url": discovery_url,
            "healthz": {"url": healthz[1], "status": healthz[0]} if healthz else None,
            "entries": _entries_summary(entries),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_text(discovery_url, entries, healthz=healthz)
    return 0


def main() -> None:
    args = _parse_args()
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)s %(name)s: %(message)s")

    try:
        raise SystemExit(asyncio.run(_run(args)))
    except LivepeerGatewayError as exc:
        print(f"ERROR: {format_gateway_error(exc)}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
