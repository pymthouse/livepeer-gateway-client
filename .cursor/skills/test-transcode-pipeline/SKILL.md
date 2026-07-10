---
name: test-transcode-pipeline
description: Verify the transcode/ffmpeg Live Runner pipeline end-to-end from the livepeer-gateway repo — probe the discovery endpoint, confirm the pinned SDK exposes the live-runner functions, and run batch + live transcodes with a signer. Use when testing, debugging, or validating examples/transcode.py or a hosted transcode/ffmpeg runner.
---

# Test the transcode/ffmpeg pipeline

How to confirm a hosted `transcode/ffmpeg` Live Runner works from this repo. All
paths are relative to the repo root (`livepeer-gateway/`); use the repo venv at
`.venv/bin/python`.

## Inputs

- `DISCOVERY` — the gateway discovery URL, e.g. `http://154.61.61.108:8787/discovery`
- `SIGNER` — a remote signer URL (the hosted runner is **paid**), e.g. `https://signer.eliteencoder.net`
- Sample clip: `examples/clip.mp4`

## Workflow

```
- [ ] 1. Probe the server + discovery
- [ ] 2. Confirm the pinned SDK exposes the live-runner API
- [ ] 3. Run a batch transcode
- [ ] 4. Run a live (trickle) transcode
- [ ] 5. Lint
```

### 1. Probe the server + discovery

The runner sits behind a go-livepeer gateway. `/` returns `404 page not found`
(normal); the real signals are `/healthz` (200) and `/discovery` (JSON listing
runners). Confirm an entry with `"app": "transcode/ffmpeg"` and note its
`price_info` — a non-zero `price_per_unit` means a signer is required.

```bash
curl -s http://154.61.61.108:8787/healthz -o /dev/null -w "%{http_code}\n"
curl -s http://154.61.61.108:8787/discovery | python3 -m json.tool
```

### 2. Confirm the pinned SDK exposes the live-runner API

`examples/transcode.py` needs these from the `livepeer-gateway` pin in
`pyproject.toml` / `uv.lock`. Verify before bumping the pin:

```bash
.venv/bin/python -c "from livepeer_gateway.selection import reserve_session; \
from livepeer_gateway.live_runner import call_runner, stop_runner_session; \
from livepeer_gateway.http import post_json; \
from livepeer_gateway.media_output import MediaOutput; \
from livepeer_gateway.media_publish import MediaPublish; print('ok')"
```

If this prints `ok`, no version bump is needed. Per-track encoder options
(livepeer-python-gateway#35) are **runner-side only** and do not affect this client.

### 3. Run a batch transcode

Expect a line like `360p: h264_nvenc 0x360 <bytes> bytes -> <prefix>-360p.mp4`
and a non-empty output file.

```bash
.venv/bin/python examples/transcode.py examples/clip.mp4 \
  --heights 360 --discovery http://154.61.61.108:8787/discovery \
  --signer https://signer.eliteencoder.net --output-prefix /tmp/gw-tc
ls -la /tmp/gw-tc-360p.mp4
```

### 4. Run a live (trickle) transcode

Expect `publish complete; draining N rendition(s)...` then `wrote <prefix>-360p.ts`
and a non-empty `.ts`.

```bash
.venv/bin/python examples/transcode.py examples/clip.mp4 --live \
  --heights 360 --discovery http://154.61.61.108:8787/discovery \
  --signer https://signer.eliteencoder.net --output-prefix /tmp/gw-tclive
ls -la /tmp/gw-tclive-360p.ts
```

### 5. Lint

```bash
.venv/bin/ruff check examples/transcode.py
```

## Troubleshooting

- `Live runner paid call requires signer_url` — the runner is paid; pass `--signer`
  (or `--billing-url` + `--client-id` + `--api-key` for a PymtHouse exchange).
- `All runners failed` / empty discovery — no `transcode/ffmpeg` runner is
  registered at that gateway; re-check step 1.
- Write outputs to `/tmp/...` (or `examples/out-*`, which is gitignored) so tests
  never commit generated media.
