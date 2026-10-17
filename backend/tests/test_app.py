import asyncio
import json

from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response

from api_observatory.app import create_app
from api_observatory.config import ProviderCatalog, ProviderDefinition


async def request(path: str) -> Response:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://localhost") as client:
        return await client.get(path)


def test_health_reports_service_and_contract_versions() -> None:
    response = asyncio.run(request("/health"))

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "api-observatory-backend",
        "version": "0.1.0",
        "contract_versions": ["performance-request.v1", "performance-result.v1"],
    }


def test_openapi_exposes_health_endpoint() -> None:
    document = asyncio.run(request("/openapi.json")).json()

    assert document["info"]["title"] == "AI API Observatory"
    assert document["info"]["version"] == "0.1.0"
    assert "/health" in document["paths"]
    assert "/api/providers" in document["paths"]
    assert "/api/provider-tests" in document["paths"]


def provider_catalog() -> ProviderCatalog:
    return ProviderCatalog(
        schema_version="provider-config.v1",
        providers=(
            ProviderDefinition(
                provider_id="test_provider",
                display_name="Test Provider",
                endpoint="https://api.example.com/v1/chat/completions",
                protocol="openai-chat-completions",
                credential_ref="env:TEST_PROVIDER_API_KEY",
                adapter="openai-compatible",
                default_model="test-model",
                timeout_ms=5000,
            ),
        ),
    )


def test_provider_list_exposes_metadata_without_credentials() -> None:
    async def run() -> Response:
        app = create_app(
            catalog=provider_catalog(),
            environment={"TEST_PROVIDER_API_KEY": "must-not-be-returned"},
            public_mode=False,
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost") as client:
            return await client.get("/api/providers")

    response = asyncio.run(run())

    assert response.status_code == 200
    assert response.json() == [
        {
            "provider_id": "test_provider",
            "display_name": "Test Provider",
            "endpoint": "https://api.example.com/v1/chat/completions",
            "default_model": "test-model",
        }
    ]
    assert "must-not-be-returned" not in response.text
    assert "credential_ref" not in response.text


def test_provider_test_runs_real_adapter_without_returning_credential() -> None:
    received: dict[str, object] = {}

    def provider_handler(request: Request) -> Response:
        received["authorization"] = request.headers["authorization"]
        received["body"] = json.loads(request.content)
        return Response(
            200,
            json={
                "id": "chatcmpl-api-test",
                "model": "test-model",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 1,
                    "total_tokens": 9,
                },
            },
        )

    async def run() -> Response:
        async with AsyncClient(transport=MockTransport(provider_handler)) as upstream:
            app = create_app(
                catalog=provider_catalog(),
                environment={"TEST_PROVIDER_API_KEY": "test-provider-secret"},
                provider_client=upstream,
                public_mode=False,
            )
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://localhost") as client:
                return await client.post(
                    "/api/provider-tests",
                    json={
                        "provider_id": "test_provider",
                        "model": "test-model",
                        "mode": "standard",
                    },
                )

    response = asyncio.run(run())

    assert response.status_code == 200
    assert received["authorization"] == "Bearer test-provider-secret"
    assert received["body"] == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "Reply with only OK."}],
        "stream": False,
        "temperature": 0.0,
        "max_tokens": 16,
    }
    document = response.json()
    assert document["provider_id"] == "test_provider"
    assert document["content"] == "OK"
    assert document["total_latency_ms"] >= 0
    assert document["ttft_ms"] is None
    assert document["usage"] == {
        "input_tokens": 8,
        "output_tokens": 1,
        "total_tokens": 9,
    }
    assert "test-provider-secret" not in response.text


def test_public_mode_hides_configured_providers() -> None:
    async def run() -> tuple[Response, Response]:
        app = create_app(
            catalog=provider_catalog(),
            environment={"TEST_PROVIDER_API_KEY": "server-secret"},
            public_mode=True,
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost") as client:
            return await client.get("/api/test-config"), await client.get("/api/providers")

    config_response, providers_response = asyncio.run(run())

    assert config_response.status_code == 200
    assert config_response.json() == {"mode": "public", "providers": []}
    assert providers_response.status_code == 404
    assert "server-secret" not in config_response.text
    assert "server-secret" not in providers_response.text


def test_public_mode_tests_user_supplied_key_without_persisting_or_returning_it() -> None:
    received: dict[str, object] = {}

    def provider_handler(request: Request) -> Response:
        received["authorization"] = request.headers["authorization"]
        return Response(
            200,
            json={
                "id": "chatcmpl-self-test",
                "model": "user-model",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    async def run() -> Response:
        async with AsyncClient(transport=MockTransport(provider_handler)) as upstream:
            app = create_app(public_mode=True, provider_client=upstream)
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://localhost") as client:
                return await client.post(
                    "/api/self-tests",
                    json={
                        "endpoint": "https://user.example.com/v1/chat/completions",
                        "api_key": "user-submitted-secret",
                        "model": "user-model",
                        "mode": "standard",
                    },
                )

    response = asyncio.run(run())

    assert response.status_code == 200
    assert received["authorization"] == "Bearer user-submitted-secret"
    assert response.json()["provider_id"] == "self_supplied"
    assert response.json()["content"] == "OK"
    assert "user-submitted-secret" not in response.text


def test_public_self_test_rejects_http_endpoint() -> None:
    async def run() -> Response:
        app = create_app(public_mode=True)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost") as client:
            return await client.post(
                "/api/self-tests",
                json={
                    "endpoint": "http://user.example.com/v1/chat/completions",
                    "api_key": "user-submitted-secret",
                    "model": "user-model",
                },
            )

    response = asyncio.run(run())

    assert response.status_code == 422
    assert "user-submitted-secret" not in response.text


def test_public_self_test_rejects_local_or_reserved_https_endpoints() -> None:
    async def run(endpoint: str) -> Response:
        app = create_app(public_mode=True)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost") as client:
            return await client.post(
                "/api/self-tests",
                json={
                    "endpoint": endpoint,
                    "api_key": "user-submitted-secret",
                    "model": "user-model",
                },
            )

    for endpoint in ("https://localhost/v1/chat/completions", "https://127.0.0.1/v1"):
        response = asyncio.run(run(endpoint))
        assert response.status_code == 422
        assert "user-submitted-secret" not in response.text


def test_frontend_is_served_by_backend() -> None:
    response = asyncio.run(request("/"))

    assert response.status_code == 200
    assert "API 观测台" in response.text
    assert "运行真实测试" in response.text


def test_runs_api_lifecycle(tmp_path) -> None:
    from pathlib import Path

    from api_observatory.storage import ResultStore

    db_path = Path(tmp_path) / "runs_test.sqlite3"
    store = ResultStore.open(db_path)

    def provider_handler(request: Request) -> Response:
        return Response(
            200,
            json={
                "id": "chatcmpl-run-test",
                "model": "test-model",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 1,
                    "total_tokens": 6,
                },
            },
        )

    async def run() -> None:
        async with AsyncClient(transport=MockTransport(provider_handler)) as upstream:
            app = create_app(
                catalog=provider_catalog(),
                environment={"TEST_PROVIDER_API_KEY": "test-secret"},
                provider_client=upstream,
                store=store,
                public_mode=False,
            )
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://localhost") as client:
                # 1. Create Run with 2 samples
                create_resp = await client.post(
                    "/api/runs",
                    json={
                        "provider_id": "test_provider",
                        "model": "test-model",
                        "mode": "standard",
                        "samples": 2,
                    },
                )
                assert create_resp.status_code == 200
                data = create_resp.json()
                run_id = data["run_id"]
                assert data["status"] == "completed"
                assert data["total_samples"] == 2
                assert data["succeeded"] == 2
                assert data["metrics"] is not None
                assert data["metrics"]["success_rate"] == 1.0

                # 2. List runs
                list_resp = await client.get("/api/runs")
                assert list_resp.status_code == 200
                runs = list_resp.json()
                assert any(r["run_id"] == run_id for r in runs)

                # 3. Get run by id
                get_resp = await client.get(f"/api/runs/{run_id}")
                assert get_resp.status_code == 200
                detail = get_resp.json()
                assert detail["run"]["run_id"] == run_id
                assert detail["metrics"] is not None
                assert detail["metrics"]["succeeded_samples"] == 2

                # 4. Get run results
                results_resp = await client.get(f"/api/runs/{run_id}/results")
                assert results_resp.status_code == 200
                results = results_resp.json()
                assert len(results) == 2
                assert results[0]["status"] == "succeeded"

    asyncio.run(run())
    store.close()
