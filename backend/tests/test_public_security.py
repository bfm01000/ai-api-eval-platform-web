import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest

from api_observatory.app import create_app
from api_observatory.config import AppSettings
from api_observatory.storage import ResultStore

BODY = {
    "endpoint": "https://example.com/v1/chat/completions",
    "api_key": "dummy-review-key",
    "model": "private-model",
}
SUCCESS = {
    "id": "sample",
    "model": "private-model",
    "choices": [{"message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
}


@asynccontextmanager
async def client_for(*, handler=None, store=None, public_mode=True, peer="127.0.0.1", **limits):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            handler or (lambda request: httpx.Response(200, json=SUCCESS))
        )
    ) as upstream:
        app = create_app(
            settings=AppSettings(_env_file=None, public_mode=public_mode, **limits),
            public_mode=public_mode,
            provider_client=upstream,
            store=store,
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=(peer, 1234)),
                base_url="http://localhost",
            ) as client,
        ):
            yield client


@pytest.mark.parametrize("path", ["/api/self-tests", "/api/self-runs"])
@pytest.mark.parametrize(
    "endpoint",
    [
        "https://[::1]/v1",
        "https://[::ffff:127.0.0.1]/v1",
        "https://10.0.0.1/v1",
        "https://169.254.169.254/v1",
        "https://localhost./v1",
        "https://example.local/v1",
        "http://example.com/v1",
        "https://example.com:8443/v1",
        "https://name:password@example.com/v1",
        "https://example.com/v1?key=secret",
        "https://[64:ff9b::7f00:1]/v1",
        "https://[2002:7f00:1::]/v1",
    ],
)
def test_self_tests_reject_unsafe_targets_before_upstream(path, endpoint):
    def forbidden(request):
        pytest.fail("Unsafe endpoint reached the adapter")

    async def run():
        async with client_for(handler=forbidden) as client:
            response = await client.post(path, json={**BODY, "endpoint": endpoint})
            assert response.status_code == 422
            assert "dummy-review-key" not in response.text

    asyncio.run(run())


@pytest.mark.parametrize("path", ["/api/self-tests", "/api/self-runs"])
@pytest.mark.parametrize(
    "changes",
    [
        {"api_key": "SECRET" * 201},
        {"api_key": "SECRET\n"},
        {"api_key": {"SECRET": "input"}},
        {"SECRET_UNKNOWN_FIELD": "SECRET"},
        {"endpoint": "SECRET"},
        {"samples": 100},
    ],
)
def test_validation_does_not_return_input(path, changes):
    async def run():
        async with client_for() as client:
            response = await client.post(path, json={**BODY, **changes})
            assert response.status_code == 422
            assert "SECRET" not in response.text
            assert "dummy-review-key" not in response.text
            assert response.headers["cache-control"] == "no-store"

    asyncio.run(run())


def test_public_history_is_closed_and_self_runs_never_persist(tmp_path):
    async def run():
        with ResultStore.open(tmp_path / "private.sqlite3") as store:
            store.create_run("private-owner-run")
            async with client_for(store=store) as first:
                created = await first.post("/api/self-runs", json={**BODY, "samples": 2})
                assert created.status_code == 200
                assert created.json()["succeeded"] == 2
                assert "dummy-review-key" not in created.text
            async with client_for(store=store) as other:
                for path in (
                    "/api/runs",
                    "/api/runs/private-owner-run",
                    "/api/runs/private-owner-run/results",
                    "/api/providers",
                ):
                    assert (await other.get(path)).status_code == 404
                for path in ("/api/runs", "/api/provider-tests"):
                    response = await other.post(path, json={"provider_id": "test_provider"})
                    assert response.status_code == 404
            assert [r["run_id"] for r in store.list_runs()] == ["private-owner-run"]

    asyncio.run(run())


def test_public_app_does_not_open_persistent_store(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Public startup opened a database")

    monkeypatch.setattr(ResultStore, "open", forbidden)

    async def run():
        async with client_for() as client:
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/api/test-config")).json() == {
                "mode": "public",
                "providers": [],
            }

    asyncio.run(run())


def test_rate_limit_ignores_spoofed_forwarded_header():
    async def run():
        async with client_for(public_requests_per_minute=1) as client:
            assert (await client.post("/api/self-tests", json=BODY)).status_code == 200
            response = await client.post(
                "/api/self-runs", json=BODY, headers={"X-Forwarded-For": "8.8.8.8"}
            )
            assert response.status_code == 429
            assert "retry-after" in response.headers

    asyncio.run(run())


def test_body_limits_origin_and_malformed_json_are_safe():
    async def run():
        async with client_for() as client:
            response = await client.post(
                "/api/self-tests",
                content='{"SECRET":',
                headers={"content-type": "application/json"},
            )
            assert response.status_code == 422
            assert "SECRET" not in response.text
            response = await client.post("/api/self-tests", json={**BODY, "model": "x" * 17000})
            assert response.status_code == 413
            response = await client.post(
                "/api/self-tests", json=BODY, headers={"Origin": "https://attacker.example"}
            )
            assert response.status_code == 403
            response = await client.post("/api/self-tests", content=json.dumps(BODY))
            assert response.status_code == 415

    asyncio.run(run())


def test_concurrency_and_total_timeout_release_slot():
    async def run():
        entered = asyncio.Event()

        async def slow(request):
            entered.set()
            await asyncio.sleep(1)
            return httpx.Response(200, json=SUCCESS)

        async with client_for(
            handler=slow, public_max_concurrency=1, public_run_timeout_seconds=0.05
        ) as client:
            first = asyncio.create_task(client.post("/api/self-tests", json=BODY))
            await entered.wait()
            assert (await client.post("/api/self-runs", json=BODY)).status_code == 429
            assert (await first).status_code == 504
            # A timeout must release capacity, including after cancelling the adapter.
            assert (await client.post("/api/self-tests", json=BODY)).status_code == 504

    asyncio.run(run())


@pytest.mark.parametrize("peer,host", [("192.168.1.2", "localhost"), ("127.0.0.1", "evil.example")])
def test_local_mode_denies_remote_peers_and_rebinding_hosts(peer, host):
    async def run():
        async with client_for(public_mode=False, peer=peer) as client:
            for path in ("/api/test-config", "/api/providers", "/api/runs"):
                response = await client.get(path, headers={"Host": host})
                assert response.status_code == 403

    asyncio.run(run())


def test_daily_and_global_limits_and_expiry(monkeypatch):
    from api_observatory.guard import PublicGuard

    clock = [100.0]
    monkeypatch.setattr("api_observatory.guard.time.monotonic", lambda: clock[0])
    guard = PublicGuard(
        None,
        settings=AppSettings(
            _env_file=None,
            public_requests_per_day=1,
            public_global_requests_per_day=2,
        ),
    )
    assert guard.admit("first")
    clock[0] += 61
    assert not guard.admit("first")
    assert guard.admit("second")
    assert not guard.admit("third")
    clock[0] += 86400
    assert guard.admit("first")


def test_streaming_key_echo_is_not_returned():
    def echo(request):
        payload = {
            "id": "echo",
            "model": BODY["api_key"],
            "choices": [{"delta": {"content": BODY["api_key"]}, "finish_reason": "stop"}],
        }
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n",
        )

    async def run():
        async with client_for(handler=echo) as client:
            response = await client.post("/api/self-tests", json={**BODY, "mode": "streaming"})
            assert response.status_code == 200
            assert BODY["api_key"] not in response.text

    asyncio.run(run())


def test_redirect_is_not_followed_and_does_not_forward_key():
    calls = []

    def redirect(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://127.0.0.1/"})

    async def run():
        async with client_for(handler=redirect) as client:
            response = await client.post("/api/self-tests", json=BODY)
            assert response.status_code == 502
            assert BODY["api_key"] not in response.text
        assert calls == [BODY["endpoint"]]

    asyncio.run(run())


def test_public_private_routes_reject_body_before_validation():
    async def run():
        async with client_for() as client:
            for path in ("/api/runs", "/api/provider-tests"):
                response = await client.post(path, content='{"SECRET":')
                assert response.status_code == 404
                assert "SECRET" not in response.text

    asyncio.run(run())
