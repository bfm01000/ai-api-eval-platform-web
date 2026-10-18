import asyncio
import json
from pathlib import Path

from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response

from api_observatory.app import create_app
from api_observatory.config import ProviderCatalog, ProviderDefinition
from api_observatory.storage import ResultStore


def provider_response(request: Request) -> Response:
    return Response(
        200,
        json={
            "id": "comparison-test",
            "model": json.loads(request.content)["model"],
            "choices": [
                {"message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8},
        },
    )


def catalog() -> ProviderCatalog:
    return ProviderCatalog(
        schema_version="provider-config.v1",
        providers=tuple(
            ProviderDefinition(
                provider_id=f"provider_{index}",
                display_name=f"Provider {index}",
                endpoint=f"https://api{index}.example.com/v1/chat/completions",
                protocol="openai-chat-completions",
                credential_ref=f"env:TEST_KEY_{index}",
                adapter="openai-compatible",
                default_model="test-model",
                timeout_ms=5000,
            )
            for index in (1, 2)
        ),
    )


def test_local_comparison_persists_separate_runs(tmp_path: Path) -> None:
    async def run():
        with ResultStore.open(tmp_path / "comparison.sqlite3") as store:
            async with AsyncClient(transport=MockTransport(provider_response)) as upstream:
                app = create_app(
                    catalog=catalog(),
                    environment={"TEST_KEY_1": "local-secret-1", "TEST_KEY_2": "local-secret-2"},
                    provider_client=upstream,
                    store=store,
                    public_mode=False,
                )
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://localhost"
                ) as client:
                    response = await client.post(
                        "/api/comparisons",
                        json={"provider_ids": ["provider_1", "provider_2"], "samples": 2},
                    )
                    invalid = await client.post(
                        "/api/comparisons", json={"provider_ids": ["provider_1", "provider_1"]}
                    )
                    return response, invalid, store.list_runs()

    response, invalid, saved_runs = asyncio.run(run())
    assert response.status_code == 200
    assert [entry["summary"]["succeeded"] for entry in response.json()["entries"]] == [2, 2]
    assert len(saved_runs) == 2
    assert invalid.status_code == 422
    assert "local-secret" not in response.text


def test_public_comparison_is_ephemeral_and_hides_keys(tmp_path: Path) -> None:
    seen = []

    def upstream_handler(request: Request) -> Response:
        seen.append(request.headers["authorization"])
        return provider_response(request)

    async def run():
        with ResultStore.open(tmp_path / "local.sqlite3") as local_store:
            async with AsyncClient(transport=MockTransport(upstream_handler)) as upstream:
                app = create_app(store=local_store, provider_client=upstream, public_mode=True)
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://localhost"
                ) as client:
                    payload = {
                        "targets": [
                            {
                                "display_name": "First first-user-secret",
                                "endpoint": "https://first.example.com/v1/chat/completions",
                                "api_key": "first-user-secret",
                                "model": "test-model first-user-secret",
                            },
                            {
                                "display_name": "Second",
                                "endpoint": "https://second.example.com/v1/chat/completions",
                                "api_key": "second-user-secret",
                                "model": "test-model",
                            },
                        ]
                    }
                    response = await client.post("/api/self-comparisons", json=payload)
                    local_response = await client.post(
                        "/api/comparisons", json={"provider_ids": ["provider_1", "provider_2"]}
                    )
                    return response, local_response, local_store.list_runs()

    response, local_response, saved_runs = asyncio.run(run())
    assert response.status_code == 200
    assert [entry["summary"]["succeeded"] for entry in response.json()["entries"]] == [1, 1]
    assert seen == ["Bearer first-user-secret", "Bearer second-user-secret"]
    assert not saved_runs
    assert local_response.status_code == 404
    assert "first-user-secret" not in response.text
    assert "second-user-secret" not in response.text
