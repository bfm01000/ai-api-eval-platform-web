import asyncio
import json
from decimal import Decimal
from pathlib import Path

from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response

from api_observatory.app import create_app
from api_observatory.benchmark import (
    BENCHMARK_SHA256,
    BENCHMARK_VERSION,
    CASES,
    estimated_max_cny,
    score_answer,
)
from api_observatory.config import ProviderCatalog, ProviderDefinition
from api_observatory.storage import ResultStore


def catalog() -> ProviderCatalog:
    return ProviderCatalog(
        schema_version="provider-config.v1",
        providers=(
            ProviderDefinition(
                provider_id="one",
                display_name="One",
                endpoint="https://one.example.com/v1/chat/completions",
                protocol="openai-chat-completions",
                credential_ref="env:TEST_KEY",
                adapter="openai-compatible",
                default_model="test-model",
                timeout_ms=5000,
            ),
        ),
    )


def upstream(request: Request) -> Response:
    prompt = json.loads(request.content)["messages"][0]["content"]
    answer = next(case.expected for case in CASES if case.prompt == prompt)
    return Response(
        200,
        json={
            "id": "benchmark-test",
            "model": "test-model",
            "choices": [{"message": {"role": "assistant", "content": answer}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    )


def test_scoring_and_conservative_estimate() -> None:
    assert score_answer(CASES[0], " 391 ")
    assert not score_answer(CASES[0], "390")
    assert score_answer(CASES[4], '{ "ok": true }')
    assert not score_answer(CASES[4], '{"ok": false}')
    estimate = estimated_max_cny(
        samples=2, input_cny_per_million=Decimal("1"), output_cny_per_million=Decimal("2")
    )
    assert estimate > 0
    assert estimate < Decimal("0.01")


def test_benchmark_workflow_and_reference_import(tmp_path: Path) -> None:
    async def run():
        with ResultStore.open(tmp_path / "benchmark.sqlite3") as store:
            async with AsyncClient(transport=MockTransport(upstream)) as provider_client:
                app = create_app(
                    catalog=catalog(),
                    environment={"TEST_KEY": "private-test-secret"},
                    provider_client=provider_client,
                    store=store,
                    public_mode=False,
                )
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://localhost"
                ) as client:
                    payload = {
                        "provider_id": "one",
                        "samples": 1,
                        "input_cny_per_million": "1",
                        "output_cny_per_million": "2",
                        "max_budget_cny": "1",
                    }
                    catalog_response = await client.get("/api/benchmark")
                    estimate = await client.post("/api/benchmark/estimate", json=payload)
                    denied = await client.post(
                        "/api/benchmark/runs", json={**payload, "max_budget_cny": "0.00001"}
                    )
                    reference = await client.post(
                        "/api/benchmark/reference",
                        json={
                            "benchmark_version": BENCHMARK_VERSION,
                            "benchmark_sha256": BENCHMARK_SHA256,
                            "reference_model": "user-described-reference",
                            "source": "manual import, unverified",
                            "answers": [
                                {"case_id": case.case_id, "answer": case.expected} for case in CASES
                            ],
                        },
                    )
                    wrong_version = await client.post(
                        "/api/benchmark/reference",
                        json={
                            "benchmark_version": BENCHMARK_VERSION,
                            "benchmark_sha256": "0" * 64,
                            "reference_model": "wrong",
                            "source": "test",
                            "answers": [{"case_id": CASES[0].case_id, "answer": "391"}],
                        },
                    )
                    result = await client.post("/api/benchmark/runs", json=payload)
                    reports = await client.get("/api/benchmark/reports")
                    by_id = await client.get(f"/api/benchmark/reports/{result.json()['batch_id']}")
                    return (
                        catalog_response,
                        estimate,
                        denied,
                        reference,
                        wrong_version,
                        result,
                        reports,
                        by_id,
                        store.list_runs(),
                    )

    catalog_response, estimate, denied, reference, wrong_version, result, reports, by_id, runs = (
        asyncio.run(run())
    )
    assert catalog_response.status_code == 200
    assert len(catalog_response.json()["cases"]) == len(CASES)
    assert estimate.json()["within_budget"]
    assert denied.status_code == 422
    assert reference.json()["identity_verified"] is False
    assert wrong_version.status_code == 422
    assert result.status_code == 200
    assert result.json()["capability_score"] == 1
    assert result.json()["reference"]["agreement"] == 1
    assert result.json()["reference"]["identity_verified"] is False
    assert len(result.json()["rows"]) == len(CASES)
    assert len(runs) == len(CASES)
    assert len(reports.json()) == 1
    assert by_id.json()["batch_id"] == result.json()["batch_id"]
    assert "private-test-secret" not in result.text


def test_cumulative_estimated_budget(tmp_path: Path) -> None:
    async def run():
        with ResultStore.open(tmp_path / "budget.sqlite3") as store:
            async with AsyncClient(transport=MockTransport(upstream)) as provider_client:
                app = create_app(
                    catalog=catalog(),
                    environment={"TEST_KEY": "private-test-secret"},
                    provider_client=provider_client,
                    store=store,
                    public_mode=False,
                )
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://localhost"
                ) as client:
                    payload = {
                        "provider_id": "one",
                        "samples": 1,
                        "input_cny_per_million": "50000",
                        "output_cny_per_million": "50000",
                        "max_budget_cny": "100",
                    }
                    first = await client.post("/api/benchmark/runs", json=payload)
                    second = await client.post("/api/benchmark/runs", json=payload)
                    return first, second, store.list_runs()

    first, second, runs = asyncio.run(run())
    assert first.status_code == 200
    assert second.status_code == 422
    assert len(runs) == len(CASES)


def test_public_cannot_access_benchmark(tmp_path: Path) -> None:
    async def run():
        with ResultStore.open(tmp_path / "local.sqlite3") as store:
            app = create_app(store=store, public_mode=True)
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://localhost"
            ) as client:
                return [
                    await client.get("/api/benchmark"),
                    await client.get("/api/benchmark/reports"),
                    await client.post("/api/benchmark/reference", json={}),
                    await client.post("/api/benchmark/runs", json={}),
                ]

    assert all(response.status_code == 404 for response in asyncio.run(run()))
