import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from api_observatory.config import ProviderDefinition, ResolvedProvider
from api_observatory.metrics import (
    compute_percentile,
    compute_run_metrics,
    summarize_distribution,
)
from api_observatory.providers.openai_compatible import (
    ChatMessage,
    CompletionParameters,
    ProviderCallError,
    RawProviderResponse,
    StandardCompletionResult,
    StreamingCompletionResult,
    TokenUsage,
)
from api_observatory.results import (
    build_failure_document,
    build_request_snapshot,
    build_success_document,
    new_request_id,
    new_result_id,
)
from api_observatory.storage import ResultStore
from api_observatory.timing import RequestTiming
from api_observatory.worker import RunExecutor, RunPlan


def test_compute_percentile_validation() -> None:
    with pytest.raises(ValueError, match="Cannot compute percentile of empty sequence"):
        compute_percentile([], 50.0)

    assert compute_percentile([42.0], 50.0) == 42.0
    assert compute_percentile([42.0], 99.0) == 42.0

    with pytest.raises(ValueError, match="Percentile must be between 0 and 100"):
        compute_percentile([1.0, 2.0], -1.0)

    with pytest.raises(ValueError, match="Percentile must be between 0 and 100"):
        compute_percentile([1.0, 2.0], 101.0)


def test_compute_percentile_linear_interpolation() -> None:
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    # k = (5 - 1) * (p / 100) = 4 * (p / 100)
    # p=0 -> idx 0 -> 10.0
    # p=50 -> idx 2.0 -> 30.0
    # p=100 -> idx 4.0 -> 50.0
    # p=25 -> idx 1.0 -> 20.0
    # p=75 -> idx 3.0 -> 40.0
    assert compute_percentile(values, 0.0) == 10.0
    assert compute_percentile(values, 25.0) == 20.0
    assert compute_percentile(values, 50.0) == 30.0
    assert compute_percentile(values, 75.0) == 40.0
    assert compute_percentile(values, 100.0) == 50.0

    # Two elements
    assert compute_percentile([100.0, 200.0], 50.0) == 150.0
    assert compute_percentile([100.0, 200.0], 95.0) == 195.0


def test_summarize_distribution_empty_and_single() -> None:
    empty_dist = summarize_distribution([])
    assert empty_dist.count == 0
    assert empty_dist.min is None
    assert empty_dist.max is None
    assert empty_dist.mean is None
    assert empty_dist.p50 is None
    assert empty_dist.p95 is None
    assert empty_dist.p99 is None
    assert empty_dist.stddev is None

    single_dist = summarize_distribution([123.456])
    assert single_dist.count == 1
    assert single_dist.min == 123.456
    assert single_dist.max == 123.456
    assert single_dist.mean == 123.456
    assert single_dist.p50 == 123.456
    assert single_dist.p90 == 123.456
    assert single_dist.p95 == 123.456
    assert single_dist.p99 == 123.456
    assert single_dist.stddev == 0.0


def test_summarize_distribution_multiple() -> None:
    values = [100.0, 200.0, 300.0, 400.0, 500.0]
    dist = summarize_distribution(values)
    assert dist.count == 5
    assert dist.min == 100.0
    assert dist.max == 500.0
    assert dist.mean == 300.0
    assert dist.p50 == 300.0
    assert dist.p90 == 460.0
    assert dist.p95 == 480.0
    assert dist.p99 == 496.0
    assert dist.stddev is not None
    assert round(dist.stddev, 2) == 158.11


def _sample_provider_definition() -> ProviderDefinition:
    return ProviderDefinition(
        provider_id="test_provider",
        display_name="Test Provider",
        endpoint="https://api.example.com/v1/chat/completions",
        protocol="openai-chat-completions",
        credential_ref="env:TEST_API_KEY",
        adapter="openai-compatible",
        default_model="gpt-5.5",
        timeout_ms=5000,
    )


def _make_success_doc(
    *,
    sample_index: int,
    latency_ms: float,
    ttft_ms: float | None = None,
    output_tokens: int | None = 10,
    input_tokens: int | None = 20,
) -> dict:
    from datetime import UTC, datetime

    definition = _sample_provider_definition()
    snapshot = build_request_snapshot(
        request_id=new_request_id(),
        run_id="run_TEST0001",
        sample_index=sample_index,
        provider=definition,
        model="gpt-5.5",
        mode="streaming" if ttft_ms is not None else "standard",
        messages=(ChatMessage(role="user", content="hi"),),
        parameters=CompletionParameters(),
        created_at=datetime.now(UTC),
    )

    timing = RequestTiming(
        request_started_at=datetime.now(UTC),
        response_completed_at=datetime.now(UTC),
        response_completed_ms=latency_ms,
        first_content_ms=ttft_ms,
    )

    usage = (
        TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        )
        if output_tokens is not None and input_tokens is not None
        else None
    )

    raw = RawProviderResponse(
        http_status=200,
        provider_request_id="req_123",
        headers={},
        media_type="application/json",
        body_text='{"ok": true}',
        body_sha256="0" * 64,
        bytes_received=15,
        credential_redacted=False,
        truncated=False,
    )

    outcome = (
        StreamingCompletionResult(
            completion_id="cmpl_123",
            model="gpt-5.5",
            content="ok",
            finish_reason="stop",
            timing=timing,
            raw_response=raw,
            usage=usage,
        )
        if ttft_ms is not None
        else StandardCompletionResult(
            completion_id="cmpl_123",
            model="gpt-5.5",
            content="ok",
            finish_reason="stop",
            timing=timing,
            raw_response=raw,
            usage=usage,
        )
    )

    return build_success_document(
        result_id=new_result_id(),
        request_snapshot=snapshot,
        outcome=outcome,
        attempt=1,
        worker_id="test-worker",
        worker_version="test@0.1",
    )


def _make_failure_doc(*, sample_index: int, category: str, latency_ms: float = 50.0) -> dict:
    from datetime import UTC, datetime

    definition = _sample_provider_definition()
    snapshot = build_request_snapshot(
        request_id=new_request_id(),
        run_id="run_TEST0001",
        sample_index=sample_index,
        provider=definition,
        model="gpt-5.5",
        mode="standard",
        messages=(ChatMessage(role="user", content="hi"),),
        parameters=CompletionParameters(),
        created_at=datetime.now(UTC),
    )
    timing = RequestTiming(
        request_started_at=datetime.now(UTC),
        response_completed_at=datetime.now(UTC),
        response_completed_ms=latency_ms,
    )
    error = ProviderCallError(
        category=category,
        code=f"{category.upper()}.FAILED",
        phase="connect",
        message="Simulated error",
        timing=timing,
        raw_response=None,
        retryable=False,
    )
    return build_failure_document(
        result_id=new_result_id(),
        request_snapshot=snapshot,
        error=error,
        attempt=1,
        worker_id="test-worker",
        worker_version="test@0.1",
    )


def test_compute_run_metrics_empty() -> None:
    metrics = compute_run_metrics([], run_id="run_EMPTY")
    assert metrics.run_id == "run_EMPTY"
    assert metrics.total_samples == 0
    assert metrics.succeeded_samples == 0
    assert metrics.failed_samples == 0
    assert metrics.success_rate == 0.0
    assert metrics.failure_rate == 0.0
    assert metrics.total_latency_ms.count == 0
    assert metrics.ttft_ms is None
    assert metrics.tokens_per_second is None


def test_compute_run_metrics_standard_successes() -> None:
    docs = [
        _make_success_doc(sample_index=1, latency_ms=100.0, input_tokens=10, output_tokens=5),
        _make_success_doc(sample_index=2, latency_ms=200.0, input_tokens=10, output_tokens=5),
        _make_success_doc(sample_index=3, latency_ms=300.0, input_tokens=10, output_tokens=5),
    ]
    metrics = compute_run_metrics(docs, run_id="run_STD")
    assert metrics.total_samples == 3
    assert metrics.succeeded_samples == 3
    assert metrics.failed_samples == 0
    assert metrics.success_rate == 1.0
    assert metrics.failure_rate == 0.0
    assert metrics.total_latency_ms.count == 3
    assert metrics.total_latency_ms.p50 == 200.0
    assert metrics.total_latency_ms.min == 100.0
    assert metrics.total_latency_ms.max == 300.0
    assert metrics.ttft_ms is None
    assert metrics.tokens_per_second is None
    assert metrics.total_input_tokens == 30
    assert metrics.total_output_tokens == 15
    assert metrics.total_tokens == 45


def test_compute_run_metrics_streaming_with_ttft_and_tokens_per_sec() -> None:
    docs = [
        # latency=500ms, ttft=100ms -> generation_duration=400ms (0.4s), output=40 -> 100 tokens/s
        _make_success_doc(
            sample_index=1,
            latency_ms=500.0,
            ttft_ms=100.0,
            input_tokens=10,
            output_tokens=40,
        ),
        # latency=600ms, ttft=200ms -> generation_duration=400ms (0.4s), output=20 -> 50 tokens/s
        _make_success_doc(
            sample_index=2,
            latency_ms=600.0,
            ttft_ms=200.0,
            input_tokens=10,
            output_tokens=20,
        ),
    ]
    metrics = compute_run_metrics(docs, run_id="run_STREAM")
    assert metrics.total_samples == 2
    assert metrics.succeeded_samples == 2
    assert metrics.success_rate == 1.0

    assert metrics.ttft_ms is not None
    assert metrics.ttft_ms.count == 2
    assert metrics.ttft_ms.min == 100.0
    assert metrics.ttft_ms.max == 200.0
    assert metrics.ttft_ms.p50 == 150.0

    assert metrics.tokens_per_second is not None
    assert metrics.tokens_per_second.count == 2
    assert metrics.tokens_per_second.min == 50.0
    assert metrics.tokens_per_second.max == 100.0
    assert metrics.tokens_per_second.p50 == 75.0


def test_compute_run_metrics_mixed_errors() -> None:
    docs = [
        _make_success_doc(sample_index=1, latency_ms=100.0),
        _make_failure_doc(sample_index=2, category="timeout"),
        _make_failure_doc(sample_index=3, category="provider"),
        _make_failure_doc(sample_index=4, category="timeout"),
    ]
    metrics = compute_run_metrics(docs, run_id="run_MIXED")
    assert metrics.total_samples == 4
    assert metrics.succeeded_samples == 1
    assert metrics.failed_samples == 3
    assert metrics.success_rate == 0.25
    assert metrics.failure_rate == 0.75
    assert metrics.error_counts == {"timeout": 2, "provider": 1}
    assert metrics.total_latency_ms.count == 1
    assert metrics.total_latency_ms.p50 == 100.0


def test_storage_get_run_metrics_integration(tmp_path: Path) -> None:
    store = ResultStore.open(tmp_path / "test.sqlite3")
    assert store.get_run_metrics("run_NONEXISTENT") is None

    run_id = "run_STORE_METRICS"
    store.create_run(run_id)
    doc1 = _make_success_doc(sample_index=1, latency_ms=120.0)
    # adjust doc run_id
    doc1["request_snapshot"]["run_id"] = run_id
    doc2 = _make_success_doc(sample_index=2, latency_ms=180.0)
    doc2["request_snapshot"]["run_id"] = run_id

    store.insert_result(doc1)
    store.insert_result(doc2)

    metrics = store.get_run_metrics(run_id)
    assert metrics is not None
    assert metrics.run_id == run_id
    assert metrics.total_samples == 2
    assert metrics.succeeded_samples == 2
    assert metrics.total_latency_ms.p50 == 150.0
    store.close()


def test_run_executor_populates_metrics_in_summary(tmp_path: Path) -> None:
    import asyncio

    resolved = ResolvedProvider(
        definition=_sample_provider_definition(),
        api_key=SecretStr("secret-key"),
    )
    plan = RunPlan(
        provider=resolved.definition,
        resolved=resolved,
        model="gpt-5.5",
        mode="standard",
        messages=(ChatMessage(role="user", content="Hello"),),
        samples=3,
    )

    body = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "gpt-5.5",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, content=json.dumps(body).encode())
        )
    )
    store = ResultStore.open(tmp_path / "test.sqlite3")

    async def go():
        executor = RunExecutor(store=store, client=client)
        return await executor.run(plan)

    summary = asyncio.run(go())
    store.close()

    assert summary.status == "completed"
    assert summary.total_samples == 3
    assert summary.succeeded == 3
    assert summary.metrics is not None
    assert summary.metrics.total_samples == 3
    assert summary.metrics.succeeded_samples == 3
    assert summary.metrics.success_rate == 1.0
    assert summary.metrics.total_latency_ms.count == 3
    assert summary.metrics.total_input_tokens == 15
    assert summary.metrics.total_output_tokens == 6
