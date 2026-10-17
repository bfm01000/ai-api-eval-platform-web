# Backend

Python 3.12 + FastAPI backend for the AI API evaluation platform. The backend provides health and Provider test endpoints, serves the local dashboard, verifies the shared performance contracts, and implements standard and streaming OpenAI-compatible requests.

## Setup

```bash
uv sync --all-groups
```

## Verify

```bash
API_OBSERVATORY_PUBLIC_MODE=true uv run pytest -W error
uv run ruff check .
uv run ruff format --check .
```

## Run locally

Create the ignored local files `config/providers.toml` and `.env`. Provider metadata belongs in TOML; API keys belong only in `.env` and are referenced through `env:VARIABLE_NAME`.

Set `API_OBSERVATORY_PUBLIC_MODE=false` only for your private local instance. Configured Provider routes require a loopback peer and localhost Host. Public deployments must use `API_OBSERVATORY_PUBLIC_MODE=true`; configured Provider and history routes return 404. Visitors submit their own full HTTPS endpoint, key, model and 1–3 samples through `/api/self-tests` or `/api/self-runs`. Public runs use per-request memory, never the local database. Do not configure server Provider keys on the public instance.

```bash
uv run uvicorn api_observatory.app:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

Then open `http://127.0.0.1:8000` to select a configured Provider and run one short test. API documentation is available at `http://127.0.0.1:8000/docs`.

Keys are cleared from the browser on submission. Public targets require real public DNS and HTTPS port 443. Fake-IP DNS and transparent proxies resolving to private addresses are intentionally rejected.

For production, use the repository Dockerfile and Compose configuration with one worker and one replica. See [deployment and release checks](../docs/DEPLOYMENT.md) for limits, trusted proxy configuration, HTTPS, smoke checks and rollback.
