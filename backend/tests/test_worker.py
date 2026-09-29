import asyncio
import json
from pathlib import Path

import httpx
from pydantic import SecretStr

from api_observatory.config import ProviderDefinition, ResolvedProvider
from api_observatory.providers.openai_compatible import (
    ChatMessage,
    CompletionParameters,
)
from api_observatory.storage import ResultStore
from api_observatory.worker import RunExecutor, RunPlan, RunSummary

STANDARD_BODY = {
    "id": "chatcmpl-worker1",
    "object": "chat.completion",
    "model": "test-model",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
}

ERROR_BODY = {
    "error": {
        "message": "invalid key",
        "type": "authentication_error",
        "code": "invalid_api_key",
    }
}


def _provider() -> ResolvedProvider:
    return ResolvedProvider(
        definition=ProviderDefinition(
            provider_id="worker_test",
            display_name="Worker Test Provider",
            endpoint="https://api.example.com/v1/chat/completions",
            protocol="openai-chat-completions",
            credential_ref="env:WORKER_TEST_API_KEY",
            adapter="openai-compatible",
            default_model="test-model",
            timeout_ms=5000,
        ),
        api_key=SecretStr("worker-secret-key"),
    )


def _plan(resolved: ResolvedProvider, *, samples: int = 1, mode: str = "standard") -> RunPlan:
    return RunPlan(
        provider=resolved.definition,
        resolved=resolved,
        model="test-model",
        mode=mode,
        messages=(ChatMessage(role="user", content="Hello"),),
        parameters=CompletionParameters(temperature=0, max_output_tokens=8),
        samples=samples,
    )


def _stream_body() -> str:
    chunks = [
        {"id": "chatcmpl-s1", "choices": [{"index": 0, "delta": {"content": "hi"}}]},
        {"id": "chatcmpl-s1", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {
            "id": "chatcmpl-s1",
            "choices": [],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
        },
    ]
    return "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"


def _make_client(handler):
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


def _run(coro):
    return asyncio.run(coro)


def test_single_standard_success(tmp_path: Path) -> None:
    store = ResultStore.open(tmp_path / "test.sqlite3")
    resolved = _provider()
    client = _make_client(
        lambda req: httpx.Response(200, content=json.dumps(STANDARD_BODY).encode())
    )

    async def go():
        executor = RunExecutor(store=store, client=client)
        return await executor.run(_plan(resolved))

    summary = _run(go())
    store.close()

    assert isinstance(summary, RunSummary)
    assert summary.status == "completed"
    assert summary.total_samples == 1
    assert summary.succeeded == 1
    assert summary.failed == 0

    store2 = ResultStore.open(tmp_path / "test.sqlite3")
    run_doc = store2.get_run(summary.run_id)
    assert run_doc is not None
    assert run_doc["status"] == "completed"
    assert run_doc["started_at"] is not None
    assert run_doc["completed_at"] is not None

    results = store2.list_results(summary.run_id)
    assert len(results) == 1
    doc = results[0]
    assert doc["status"] == "succeeded"
    assert doc["request_snapshot"]["sample_index"] == 1
    assert doc["request_snapshot"]["run_id"] == summary.run_id
    assert doc["request_snapshot"]["request_id"].startswith("req_")
    assert doc["result_id"].startswith("res_")
    assert doc["execution"]["worker_id"] == "sequential-worker"
    assert doc["usage"]["source"] == "provider"
    assert doc["usage"]["output_tokens"] == 1
    store2.close()


def test_multiple_samples_all_succeed(tmp_path: Path) -> None:
    store = ResultStore.open(tmp_path / "test.sqlite3")
    resolved = _provider()
    client = _make_client(
        lambda req: httpx.Response(200, content=json.dumps(STANDARD_BODY).encode())
    )

    async def go():
        executor = RunExecutor(store=store, client=client)
        return await executor.run(_plan(resolved, samples=3))

    summary = _run(go())
    store.close()

    assert summary.total_samples == 3
    assert summary.succeeded == 3
    assert summary.failed == 0

    store2 = ResultStore.open(tmp_path / "test.sqlite3")
    results = store2.list_results(summary.run_id)
    assert len(results) == 3

    indices = [r["request_snapshot"]["sample_index"] for r in results]
    assert indices == [1, 2, 3]

    request_ids = {r["request_snapshot"]["request_id"] for r in results}
    assert len(request_ids) == 3

    result_ids = {r["result_id"] for r in results}
    assert len(result_ids) == 3
    store2.close()


def test_mixed_success_and_failure(tmp_path: Path) -> None:
    call_count = 0

    def handler(req):
        nonlocal call_count
        call_count += 1
        if call_count % 2 == 0:
            return httpx.Response(401, content=json.dumps(ERROR_BODY).encode())
        return httpx.Response(200, content=json.dumps(STANDARD_BODY).encode())

    store = ResultStore.open(tmp_path / "test.sqlite3")
    resolved = _provider()
    client = _make_client(handler)

    async def go():
        executor = RunExecutor(store=store, client=client)
        return await executor.run(_plan(resolved, samples=4))

    summary = _run(go())
    store.close()

    assert summary.total_samples == 4
    assert summary.succeeded == 2
    assert summary.failed == 2
    assert summary.status == "completed"

    store2 = ResultStore.open(tmp_path / "test.sqlite3")
    results = store2.list_results(summary.run_id)
    statuses = [r["status"] for r in results]
    assert statuses == ["succeeded", "failed", "succeeded", "failed"]

    failed_doc = results[1]
    assert failed_doc["error"] is not None
    assert failed_doc["error"]["category"] == "provider"
    assert failed_doc["error"]["http_status"] == 401
    assert failed_doc["usage"]["source"] == "unavailable"
    assert failed_doc["raw_response"]["http_status"] == 401
    store2.close()


def test_streaming_samples(tmp_path: Path) -> None:
    store = ResultStore.open(tmp_path / "test.sqlite3")
    resolved = _provider()
    body = _stream_body()
    client = _make_client(lambda req: httpx.Response(200, content=body.encode()))

    async def go():
        executor = RunExecutor(store=store, client=client)
        return await executor.run(_plan(resolved, samples=2, mode="streaming"))

    summary = _run(go())
    store.close()

    assert summary.status == "completed"
    assert summary.succeeded == 2

    store2 = ResultStore.open(tmp_path / "test.sqlite3")
    results = store2.list_results(summary.run_id)
    for doc in results:
        assert doc["status"] == "succeeded"
        assert doc["request_snapshot"]["mode"] == "streaming"
        assert doc["timing"]["first_content_ms"] is not None
        assert doc["usage"]["source"] == "provider"
    store2.close()


def test_run_id_is_persisted_when_provided(tmp_path: Path) -> None:
    store = ResultStore.open(tmp_path / "test.sqlite3")
    resolved = _provider()
    client = _make_client(
        lambda req: httpx.Response(200, content=json.dumps(STANDARD_BODY).encode())
    )

    custom_run_id = "run_CUSTOM00TEST000000000001"

    async def go():
        executor = RunExecutor(store=store, client=client)
        return await executor.run(_plan(resolved), run_id=custom_run_id)

    summary = _run(go())
    store.close()

    assert summary.run_id == custom_run_id

    store2 = ResultStore.open(tmp_path / "test.sqlite3")
    run_doc = store2.get_run(custom_run_id)
    assert run_doc is not None
    assert run_doc["status"] == "completed"
    store2.close()


def test_store_rejection_marks_run_failed(tmp_path: Path) -> None:
    store = ResultStore.open(tmp_path / "test.sqlite3")
    resolved = _provider()
    client = _make_client(
        lambda req: httpx.Response(200, content=json.dumps(STANDARD_BODY).encode())
    )

    def broken_insert(document):
        raise RuntimeError("simulated write failure")

    store.insert_result = broken_insert

    async def go():
        executor = RunExecutor(store=store, client=client)
        return await executor.run(_plan(resolved, samples=2))

    summary = _run(go())
    store.close()

    assert summary.status == "failed"
    assert summary.succeeded == 0

    store2 = ResultStore.open(tmp_path / "test.sqlite3")
    run_doc = store2.get_run(summary.run_id)
    assert run_doc["status"] == "failed"
    assert len(store2.list_results(summary.run_id)) == 0
    store2.close()
