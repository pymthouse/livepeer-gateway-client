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

### API key exchange (PymtHouse signer-session → short-lived JWT)

Exchange a `pmth_*` key for a `sign:job` JWT; the client re-mints automatically when it
expires mid-stream:

```python
from livepeer_gateway_client import LivepeerClient, SignerTokenProvider
from livepeer_gateway.lv2v import StartJobRequest

provider = SignerTokenProvider(
    billing_url="https://staging.pymthouse.com",
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

`SignerTokenProvider` also supports `pmth_cs_*` M2M secrets with
`m2m_client_id="m2m_..."` and `external_user_id="..."` (OIDC client
credentials exchange for user-scoped minting). The token response includes
`signer_url` and optional `discovery_url` in the same `SignerSession` envelope as the API-key path.
You can also pass `m2m_audience` when required by the issuer.

### OIDC (interactive)

Browser or device-code login against an OIDC issuer (e.g. Auth0). Pass `oidc_audience`
when the issuer expects `audience` on the token request (Auth0) instead of `resource`
(PymtHouse):

```python
provider = SignerTokenProvider(
    oidc_base_url="https://pymthouse.us.auth0.com",
    oidc_client_id="xEJfZBtEP0JLJtlXm9UnJrDrA9bwepLx",
    oidc_audience="livepeer-clearinghouse",
    oidc_scopes="openid sign:job offline_access",
)
provider.refresh()

client = LivepeerClient(
    model_id="streamdiffusion-sdxl",
    signer_url="http://localhost:8081",
    signer_provider=provider,
    discovery_url="https://discovery.example.com/v1/discovery/raw",
)
await client.connect(StartJobRequest(model_id="streamdiffusion-sdxl"))
```

For RFC 8628 device login without embedding in `write_frames`, use `examples/device_login.py`.

## Example

```bash
uv sync --extra examples

# Webhook-validated signer (clearinghouse stack — bypasses Builder API exchange)
uv run examples/write_frames.py \
  --signer http://localhost:8081 \
  --api-key sk_demo_local_key \
  --discovery "https://discovery.example.com/v1/discovery/raw" \
  --model streamdiffusion-sdxl

# Clearinghouse Builder API signer-session exchange (sk_* API key; discovery_url from response)
uv run examples/write_frames.py \
  --billing-url http://localhost:8095 \
  --client-id xEJfZBtEP0JLJtlXm9UnJrDrA9bwepLx \
  --api-key sk_demo_local_key \
  --model streamdiffusion-sdxl
uv run examples/write_frames.py \
  --discovery "https://discovery.example.com/v1/discovery/raw" \
  --billing-url https://staging.pymthouse.com \
  --client-id app_xxx \
  --api-key pmth_xxx \
  --model streamdiffusion-sdxl

# PymtHouse M2M mint (pmth_cs_* client secret)
uv run examples/write_frames.py \
  --discovery "https://discovery.example.com/v1/discovery/raw" \
  --billing-url https://staging.pymthouse.com \
  --client-id app_xxx \
  --m2m-client-id m2m_xxx \
  --external-user-id user_123 \
  --api-key pmth_cs_xxx \
  --model streamdiffusion-sdxl

# Auth0 device code (clearinghouse — provisions OpenMeter via Builder API exchange)
uv run examples/device_login.py \
  --issuer https://pymthouse.us.auth0.com \
  --client-id xEJfZBtEP0JLJtlXm9UnJrDrA9bwepLx \
  --audience livepeer-clearinghouse \
  --billing-url http://localhost:8095 \
  --discovery "https://discovery.example.com/v1/discovery/raw" \
  --run-frames \
  --model streamdiffusion-sdxl

# Or pass OIDC + billing-url directly to write_frames
uv run examples/write_frames.py \
  --oidc-url https://pymthouse.us.auth0.com \
  --oidc-client-id xEJfZBtEP0JLJtlXm9UnJrDrA9bwepLx \
  --oidc-audience livepeer-clearinghouse \
  --billing-url http://localhost:8095 \
  --discovery "https://discovery.example.com/v1/discovery/raw" \
  --model streamdiffusion-sdxl
```

VS Code launch configs for API key, Builder API exchange, and device code are in `.vscode/launch.json`.

## Development

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run ruff format .
```
