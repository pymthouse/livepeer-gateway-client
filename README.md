# livepeer-gateway-client

Python client for Livepeer inference with **OIDC authentication** (Authlib) and
**automatic signer JWT refresh** for long-running streams.

Depends on upstream [`livepeer-gateway`](https://github.com/livepeer/livepeer-python-gateway)
(the transport library) via a git-pinned uv source.

## Install

```bash
uv sync --extra dev --extra examples
```

Open this repo as the VS Code/Cursor workspace root so `.vscode/settings.json` resolves
`${workspaceFolder}/.venv` for the debugger and terminal.

## Auth modes

### OIDC (interactive)

```python
from livepeer_gateway_client import SignerTokenProvider, LivepeerClient
from livepeer_gateway.lv2v import StartJobRequest

provider = SignerTokenProvider(oidc_base_url="https://pymthouse.example.com")
headers = provider.refresh()

client = LivepeerClient(
    model_id="streamdiffusion-sdxl",
    signer_provider=provider,
    discovery_url="https://discovery.example.com/v1/discovery/raw",
)
await client.connect(StartJobRequest(model_id="streamdiffusion-sdxl"))
```

### API key (non-interactive fast path)

```python
provider = SignerTokenProvider(
    billing_url="https://dashboard.example.com",
    api_key="pmth_...",
    client_id="app_...",
)
headers = provider.refresh()
```

The provider's `refresh()` is called automatically when the short-lived
`sign:job` signer JWT expires during the payment loop.

## Example

```bash
uv sync --extra examples
uv run examples/write_frames.py \
  --discovery "https://discovery.example.com/v1/discovery/raw" \
  --billing-url http://localhost:3000 \
  --client-id app_xxx \
  --api-key pmth_xxx \
  --model streamdiffusion-sdxl
```

## Development

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run ruff format .
```
