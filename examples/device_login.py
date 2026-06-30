#!/usr/bin/env python3
"""RFC 8628 device code login against Auth0 (Clearinghouse Demo App).

Runs the device authorization flow, prints the access token, and optionally
starts a short write_frames job using the minted bearer with the local signer.

Prerequisites:
  - ./auth0-provisioner/provision/bootstrap.sh (Demo App public client + API)
  - clearinghouse stack: identity-webhook + remote-signer (+ openmeter-collector)

Usage::

    uv run examples/device_login.py \\
      --issuer https://pymthouse.us.auth0.com \\
      --client-id xEJfZBtEP0JLJtlXm9UnJrDrA9bwepLx \\
      --audience livepeer-clearinghouse

    # After login, run a 3-frame smoke test:
    uv run examples/device_login.py ... --run-frames --signer http://localhost:8081
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

from livepeer_gateway_client.oidc_auth import (
    OIDCError,
    clear_all_cached_tokens,
    clear_cached_token,
    ensure_valid_token,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Auth0 device code login (Clearinghouse).")
    p.add_argument(
        "--issuer",
        default=None,
        help="Auth0 issuer URL (e.g. https://tenant.us.auth0.com).",
    )
    p.add_argument(
        "--client-id",
        default=None,
        help="Demo App public client id (DEMO_APP_AUTH0_PUBLIC_CLIENT_ID).",
    )
    p.add_argument(
        "--audience",
        default="livepeer-clearinghouse",
        help="API audience (default: livepeer-clearinghouse).",
    )
    p.add_argument(
        "--scopes",
        default="openid sign:job offline_access",
        help="Space-separated scopes (default: openid sign:job offline_access).",
    )
    p.add_argument(
        "--clear-cache",
        action="store_true",
        help="Delete cached tokens for this issuer/client/scopes before login.",
    )
    p.add_argument(
        "--logout",
        action="store_true",
        help="Clear cached device/OIDC session for this issuer/client and exit.",
    )
    p.add_argument(
        "--logout-all",
        action="store_true",
        help="Clear all cached OIDC tokens (every issuer/client) and exit.",
    )
    p.add_argument(
        "--run-frames",
        action="store_true",
        help="After login, invoke write_frames with the access token.",
    )
    p.add_argument(
        "--signer",
        default=None,
        help="Remote signer URL override when --run-frames is set (default: from exchange).",
    )
    p.add_argument(
        "--billing-url",
        default="http://localhost:8095",
        help="Clearinghouse Builder API for RFC 8693 token exchange (OpenMeter provision).",
    )
    p.add_argument(
        "--discovery",
        default=None,
        help="Discovery URL for --run-frames (required for device code; overrides exchange).",
    )
    p.add_argument("--model", default="streamdiffusion-sdxl")
    p.add_argument("--count", type=int, default=3)
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.logout_all:
        cleared = clear_all_cached_tokens()
        print(f"Logged out — cleared {cleared} cached token file(s).")
        return 0

    if args.logout:
        if not args.issuer or not args.client_id:
            print("--logout requires --issuer and --client-id", file=sys.stderr)
            return 1
        issuer = args.issuer.rstrip("/")
        clear_cached_token(
            issuer,
            client_id=args.client_id,
            scopes=args.scopes,
            audience=args.audience,
        )
        print("Logged out — next run will prompt for device login.")
        print(f"  issuer: {issuer}")
        print(f"  client: {args.client_id}")
        return 0

    if not args.issuer or not args.client_id:
        print("--issuer and --client-id are required", file=sys.stderr)
        return 1

    issuer = args.issuer.rstrip("/")
    if args.clear_cache:
        clear_cached_token(
            issuer,
            client_id=args.client_id,
            scopes=args.scopes,
            audience=args.audience,
        )

    try:
        tokens = ensure_valid_token(
            issuer,
            client_id=args.client_id,
            scopes=args.scopes,
            audience=args.audience,
            headless=True,
        )
    except OIDCError as exc:
        print(f"device login failed: {exc}", file=sys.stderr)
        return 1

    access_token = tokens.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        print("no access_token in response", file=sys.stderr)
        return 1

    print("\nDevice login succeeded.")
    print(f"  issuer:   {issuer}")
    print(f"  client:   {args.client_id}")
    print(f"  audience: {args.audience}")
    print(f"  scopes:   {tokens.get('scope', args.scopes)}")
    print(f"  token:    {access_token[:48]}...")

    if not args.run_frames:
        print("\nUse the token as Bearer auth against the remote signer, or re-run with --run-frames.")
        return 0

    write_frames = Path(__file__).resolve().parent / "write_frames.py"
    cmd = [
        sys.executable,
        str(write_frames),
        "--oidc-url",
        issuer,
        "--oidc-client-id",
        args.client_id,
        "--oidc-audience",
        args.audience,
        "--oidc-scopes",
        args.scopes,
        "--model",
        args.model,
        "--count",
        str(args.count),
    ]
    if args.billing_url:
        cmd.extend(["--billing-url", args.billing_url])
    if args.signer:
        cmd.extend(["--signer", args.signer])
    if args.discovery:
        cmd.extend(["--discovery", args.discovery])

    print("\nStarting write_frames with cached device token...")
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
