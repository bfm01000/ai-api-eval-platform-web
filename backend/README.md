# Backend

Python 3.12 + FastAPI backend for the AI API evaluation platform. The backend provides health and Provider test endpoints, serves the local dashboard, verifies the shared performance contracts, and implements standard and streaming OpenAI-compatible requests.

## Setup

```bash
uv sync --all-groups
```

## Verify

```bash
uv run pytest
uv run ruff check .
```

## Run locally

Create the ignored local files `config/providers.toml` and `.env`. Provider metadata belongs in TOML; API keys belong only in `.env` and are referenced through `env:VARIABLE_NAME`.

Set `API_OBSERVATORY_PUBLIC_MODE=false` for your private local instance. This enables the configured Provider endpoints and their server-side credentials. For an internet-facing deployment, set `API_OBSERVATORY_PUBLIC_MODE=true`; this disables `/api/providers` and `/api/provider-tests`, and leaves only `/api/self-tests`, where each visitor submits a temporary Endpoint, API Key and model. Do not configure `YLAI_TEST_API_KEY` or `AICOMING_TEST_API_KEY` on the public server.

```bash
uv run uvicorn api_observatory.app:app --reload --port 8000
```

Then open `http://127.0.0.1:8000` to select a configured Provider and run one short test. API documentation is available at `http://127.0.0.1:8000/docs`.

In public mode, open the same address and enter your own OpenAI-compatible Endpoint, API Key and model. The key is used only for that request and is cleared from the browser after completion. Credential values are never returned by the API.
