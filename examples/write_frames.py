import argparse
import asyncio
import logging
from fractions import Fraction

import av
from livepeer_gateway.errors import LivepeerGatewayError
from livepeer_gateway.lv2v import StartJobRequest
from livepeer_gateway.media_publish import MediaPublishConfig, VideoOutputConfig

from livepeer_gateway_client import (
    LivepeerClient,
    SignerTokenProvider,
    format_gateway_error,
)

DEFAULT_MODEL_ID = "noop"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Start an LV2V job and publish raw frames via publish_url."
    )
    p.add_argument(
        "orchestrator",
        nargs="?",
        default=None,
        help="Orchestrator (host:port). If omitted, discovery is used.",
    )
    p.add_argument(
        "--signer",
        default=None,
        help="Remote signer URL (no path). If omitted, runs in offchain mode.",
    )
    p.add_argument(
        "--discovery",
        default=None,
        help="Discovery service URL. Used when no orchestrator is specified.",
    )
    p.add_argument(
        "--billing-url",
        default=None,
        help="PymtHouse base URL for API-key signer-session exchange (e.g. https://staging.pymthouse.com).",
    )
    p.add_argument(
        "--client-id",
        default=None,
        help="PymtHouse public client id (app_*) for pmth_* API-key signer-session exchange.",
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
        help="PymtHouse credential (pmth_* user API key or pmth_cs_* M2M secret).",
    )
    p.add_argument(
        "--oidc-url",
        default=None,
        help="PymtHouse OIDC issuer base URL for interactive auth.",
    )
    p.add_argument(
        "--token",
        default=None,
        help="Base64-encoded gateway token (signer, discovery, headers).",
    )
    p.add_argument(
        "--model",
        default=DEFAULT_MODEL_ID,
        help=f"Pipeline model to start via /live-video-to-video. Default: {DEFAULT_MODEL_ID}",
    )
    p.add_argument("--width", type=int, default=320, help="Frame width (default: 320).")
    p.add_argument(
        "--height", type=int, default=180, help="Frame height (default: 180)."
    )
    p.add_argument(
        "--fps", type=float, default=30.0, help="Frames per second (default: 30)."
    )
    p.add_argument(
        "--count", type=int, default=90, help="Number of frames to send (default: 90)."
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging (equivalent to LOG_LEVEL=DEBUG).",
    )
    return p.parse_args()


def _solid_rgb_frame(
    width: int, height: int, rgb: tuple[int, int, int]
) -> av.VideoFrame:
    frame = av.VideoFrame(width, height, "rgb24")
    r, g, b = rgb
    frame.planes[0].update(bytes([r, g, b]) * (width * height))
    return frame


async def main() -> None:
    args = _parse_args()
    frame_interval = 1.0 / max(1e-6, args.fps)
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)s %(name)s: %(message)s")

    client = None
    try:
        signer_provider = None
        signer_url = args.signer
        signer_headers = None

        if args.api_key and args.billing_url:
            signer_provider = SignerTokenProvider(
                billing_url=args.billing_url,
                api_key=args.api_key,
                client_id=args.client_id,
                m2m_client_id=args.m2m_client_id,
                external_user_id=args.external_user_id,
                m2m_audience=args.m2m_audience,
            )
        elif args.oidc_url:
            signer_provider = SignerTokenProvider(oidc_base_url=args.oidc_url)
        elif args.api_key and args.signer:
            signer_headers = {"Authorization": f"Bearer {args.api_key.strip()}"}

        client = LivepeerClient(
            model_id=args.model,
            orchestrator_url=args.orchestrator,
            discovery_url=args.discovery,
            token=args.token,
            signer_url=signer_url,
            signer_headers=signer_headers,
            signer_provider=signer_provider,
        )
        job = await client.connect(StartJobRequest(model_id=args.model))

        print("=== LiveVideoToVideo ===")
        print("publish_url:", job.publish_url)
        print()

        media = job.start_media(
            MediaPublishConfig(
                tracks=[VideoOutputConfig(fps=args.fps)],
            )
        )

        time_base = Fraction(1, int(round(args.fps)))
        for i in range(max(0, args.count)):
            color = (i * 5) % 255
            frame = _solid_rgb_frame(args.width, args.height, (color, 0, 255 - color))
            frame.pts = i
            frame.time_base = time_base
            await media.write_frame(frame)
            await asyncio.sleep(frame_interval)
    except LivepeerGatewayError as e:
        print(f"ERROR: {format_gateway_error(e)}")
    finally:
        if client is not None:
            await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
