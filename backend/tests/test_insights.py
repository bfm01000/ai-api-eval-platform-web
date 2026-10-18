import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from httpx import ASGITransport, AsyncClient, MockTransport
from test_comparison import catalog, provider_response

from api_observatory.app import create_app
from api_observatory.insights import build_insights
from api_observatory.storage import ResultStore


def test_insights_derive_real_local_samples(tmp_path: Path) -> None:
    async def run():
        with ResultStore.open(tmp_path / "insights.sqlite3") as store:
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
                    await client.post(
                        "/api/comparisons",
                        json={"provider_ids": ["provider_1", "provider_2"]},
                    )
                    response = await client.get("/api/insights?window=24h")
                    empty = build_insights(
                        store,
                        catalog().providers,
                        "1h",
                        now=datetime.now(UTC) + timedelta(days=2),
                    )
                    return response, empty

    response, empty = asyncio.run(run())
    assert response.status_code == 200
    document = response.json()
    assert document["sample_count"] == 2
    assert document["metrics"]["success_rate"] == 1
    assert [row["samples"] for row in document["providers"]] == [1, 1]
    assert sum(bucket["samples"] for bucket in document["trend"]) == 2
    assert "local-secret" not in response.text

    assert empty["sample_count"] == 0
    assert empty["metrics"]["total_latency_ms"]["p95"] is None


def test_public_insights_are_not_accessible() -> None:
    async def run():
        app = create_app(public_mode=True)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            return await client.get("/api/insights")

    assert asyncio.run(run()).status_code == 404
