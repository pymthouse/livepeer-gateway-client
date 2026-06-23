# livepeer-gateway-client

Python client that complements the core [`livepeer-gateway`](https://github.com/livepeer/livepeer-python-gateway)
transport library with higher-level integration: provider login, OIDC/API-key authentication,
remote signer usage, and automatic payment refresh for long-running streams.

Repository: [github.com/pymthouse/livepeer-gateway-client](https://github.com/pymthouse/livepeer-gateway-client)

## Layout

```
livepeer_gateway_client/   # this package (import name matches PyPI name)
examples/                  # runnable scripts
tests/
```

Upstream transport stays in the **`livepeer-gateway`** dependency (`livepeer_gateway` import).
This repo is **`livepeer-gateway-client`** (`livepeer_gateway_client` import).

## Install

```bash
uv sync --extra dev --extra examples
```

Open this repo as the VS Code/Cursor workspace root so `.vscode/settings.json` resolves
`${workspaceFolder}/.venv` for the debugger and terminal.

## Auth modes

### Raw API key (webhook-validated signer)

Pass the long-lived key directly; your signer's identity webhook validates it on each
payment call (no JWT exchange, no client-side refresh):

```python
from livepeer_gateway_client import LivepeerClient
from livepeer_gateway.lv2v import StartJobRequest

client = LivepeerClient(
    model_id="streamdiffusion-sdxl",
    signer_url="http://localhost:8081",
    signer_headers={"Authorization": "Bearer sk_demo_local_key"},
    discovery_url="https://discovery.example.com/v1/discovery/raw",
)
await client.connect(StartJobRequest(model_id="streamdiffusion-sdxl"))
```

### API key exchange (dashboard BFF → short-lived JWT)

Exchange a `pmth_*` key for a `sign:job` JWT; the client re-mints automatically when it
expires mid-stream:

```python
from livepeer_gateway_client import LivepeerClient, SignerTokenProvider
from livepeer_gateway.lv2v import StartJobRequest

provider = SignerTokenProvider(
    billing_url="https://dashboard.example.com",
    api_key="pmth_...",
    client_id="app_...",
)

client = LivepeerClient(
    model_id="streamdiffusion-sdxl",
    signer_provider=provider,
    discovery_url="https://discovery.example.com/v1/discovery/raw",
)
await client.connect(StartJobRequest(model_id="streamdiffusion-sdxl"))
```

### OIDC (interactive)

```python
provider = SignerTokenProvider(oidc_base_url="https://pymthouse.example.com")
provider.refresh()

client = LivepeerClient(
    model_id="streamdiffusion-sdxl",
    signer_provider=provider,
    discovery_url="https://discovery.example.com/v1/discovery/raw",
)
await client.connect(StartJobRequest(model_id="streamdiffusion-sdxl"))
```

## Example

```bash
uv sync --extra examples

# Webhook-validated signer (clearinghouse stack)
uv run examples/write_frames.py \
  --signer http://localhost:8081 \
  --api-key sk_demo_local_key \
  --discovery "https://discovery.example.com/v1/discovery/raw" \
  --model streamdiffusion-sdxl

# Dashboard BFF exchange
uv run examples/write_frames.py \
  --discovery "https://discovery.example.com/v1/discovery/raw" \
  --billing-url http://localhost:3000 \
  --client-id app_xxx \
  --api-key pmth_xxx \
  --model streamdiffusion-sdxl
```

VS Code launch configs for both modes are in `.vscode/launch.json`.

## Development

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run ruff format .
```
