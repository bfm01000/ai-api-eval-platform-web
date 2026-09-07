# Backend

Python 3.12 + FastAPI backend for the AI API evaluation platform. The backend provides a health endpoint, verifies the shared performance contracts, and implements a non-streaming OpenAI-compatible adapter. The adapter has local HTTP integration coverage but has not been verified against an external Provider yet.

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

```bash
uv run uvicorn api_observatory.app:app --reload --port 8000
```

Then open `http://127.0.0.1:8000/docs` or request `GET /health`.
