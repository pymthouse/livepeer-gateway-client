import argparse
import logging
import sys

from livepeer_gateway.orch_info import OrchestratorRpcError, get_orch_info

from livepeer_gateway_client import SignerTokenProvider


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fetch and display OrchestratorInfo via gRPC."
    )
    p.add_argument(
        "--orchestrator-url",
        required=True,
        help="Orchestrator gRPC URL (e.g. https://host:8936).",
    )
    p.add_argument(
        "--signer",
        default=None,
        help="Remote signer URL. If omitted, runs without auth (address/sig will be empty).",
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
        "--no-tofu",
        action="store_true",
        help="Disable TOFU cert pinning (use system CA trust store).",
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging.",
    )
    return p.parse_args()


def _fmt_hex(b: bytes) -> str:
    return "0x" + b.hex() if b else "(empty)"


def _fmt_big_int(b: bytes) -> int:
    return int.from_bytes(b, "big") if b else 0


def _print_section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


def _print_kv(key: str, value: object, indent: int = 0) -> None:
    pad = "  " * indent
    print(f"{pad}{key:<32} {value}")


def main() -> None:
    args = _parse_args()
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)s %(name)s: %(message)s")

    signer_url = args.signer
    signer_headers: dict[str, str] | None = None

    if args.api_key and args.billing_url:
        provider = SignerTokenProvider(
            billing_url=args.billing_url,
            api_key=args.api_key,
            client_id=args.client_id,
        )
        signer_headers = provider.refresh()
        if provider.signer_url:
            signer_url = provider.signer_url

    try:
        info = get_orch_info(
            args.orchestrator_url,
            signer_url=signer_url,
            signer_headers=signer_headers,
            use_tofu=not args.no_tofu,
        )
    except OrchestratorRpcError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\nOrchestratorInfo  ←  {args.orchestrator_url}")

    # ── Identity ────────────────────────────────────────────────────────────
    _print_section("Identity")
    _print_kv("transcoder", info.transcoder or "(none)")
    _print_kv("address (ETH)", _fmt_hex(info.address))

    # ── Price ────────────────────────────────────────────────────────────────
    if info.HasField("price_info"):
        _print_section("Price (default)")
        pi = info.price_info
        _print_kv("pricePerUnit", pi.pricePerUnit)
        _print_kv("pixelsPerUnit", pi.pixelsPerUnit)
        if pi.pricePerUnit and pi.pixelsPerUnit:
            price_per_mpx = pi.pricePerUnit / pi.pixelsPerUnit * 1_000_000
            _print_kv("≈ wei / M-pixel", f"{price_per_mpx:.4f}")
        if pi.capability:
            _print_kv("capability", pi.capability)
        if pi.constraint:
            _print_kv("constraint", pi.constraint)

    if info.capabilities_prices:
        _print_section("Per-capability prices")
        for cp in info.capabilities_prices:
            label = f"cap={cp.capability}"
            if cp.constraint:
                label += f"  model={cp.constraint}"
            ppu_str = (
                f"{cp.pricePerUnit}/{cp.pixelsPerUnit} wei/px"
                if cp.pixelsPerUnit
                else f"{cp.pricePerUnit} wei"
            )
            print(f"  {label:<48} {ppu_str}")

    # ── Ticket params ────────────────────────────────────────────────────────
    if info.HasField("ticket_params"):
        _print_section("Ticket params")
        tp = info.ticket_params
        _print_kv("recipient", _fmt_hex(tp.recipient))
        fv = _fmt_big_int(tp.face_value)
        _print_kv("face_value (wei)", fv)
        wp = _fmt_big_int(tp.win_prob)
        max_uint256 = (1 << 256) - 1
        win_pct = (wp / max_uint256 * 100) if max_uint256 else 0
        _print_kv("win_prob", f"{_fmt_hex(tp.win_prob)}  ({win_pct:.6f}%)")
        _print_kv("expiration_block", _fmt_hex(tp.expiration_block))

    # ── Capabilities ─────────────────────────────────────────────────────────
    if info.HasField("capabilities"):
        caps = info.capabilities
        _print_section("Capabilities")
        _print_kv("version", caps.version or "(none)")
        _print_kv("bitstring words", len(caps.bitstring))
        if caps.capacities:
            _print_kv("capacities (cap → slots)", "")
            for k, v in sorted(caps.capacities.items()):
                print(f"    cap {k:<8} {v} slot(s)")
        if caps.HasField("constraints"):
            constraints = caps.constraints
            if constraints.minVersion:
                _print_kv("minVersion", constraints.minVersion)
            if constraints.PerCapability:
                print(f"  {'Per-capability model constraints':}")
                for cap_id, cap_constraints in sorted(constraints.PerCapability.items()):
                    for model_key, mc in cap_constraints.models.items():
                        warm_str = "warm" if mc.warm else "cold"
                        cap_str = f"cap={cap_in_use}/{mc.capacity}" if (cap_in_use := mc.capacityInUse) else f"cap={mc.capacity}"
                        runner = f"  runner={mc.runnerVersion}" if mc.runnerVersion else ""
                        print(f"    cap={cap_id:<6} {model_key:<48} {warm_str:<5} {cap_str}{runner}")

    # ── Hardware ─────────────────────────────────────────────────────────────
    if info.hardware:
        _print_section("Hardware")
        for hw in info.hardware:
            label = hw.pipeline or "(pipeline unknown)"
            if hw.model_id:
                label += f"  model={hw.model_id}"
            print(f"  {label}")
            for gpu_key, gpu in hw.gpu_info.items():
                free_gb = gpu.memory_free / 1024**3
                total_gb = gpu.memory_total / 1024**3
                print(
                    f"    GPU {gpu_key}: {gpu.name or '?'}  "
                    f"compute={gpu.major}.{gpu.minor}  "
                    f"mem={free_gb:.1f}/{total_gb:.1f} GB free/total"
                )

    # ── Nodes ─────────────────────────────────────────────────────────────────
    if info.nodes:
        _print_section("Nodes")
        for n in info.nodes:
            print(f"  {n}")

    print()


if __name__ == "__main__":
    main()
