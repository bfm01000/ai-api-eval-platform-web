import asyncio

from httpx import ASGITransport, AsyncClient, Response

from api_observatory.app import create_app


async def request(path: str) -> Response:
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
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
