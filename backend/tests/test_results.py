import asyncio
import json
import re
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import SecretStr

from api_observatory.config import ProviderDefinition, ResolvedProvider
from api_observatory.providers.openai_compatible import (
    ChatMessage,
    CompletionParameters,
    OpenAICompatibleAdapter,
    ProviderCallError,
    StandardCompletionRequest,
)
from api_observatory.results import (
    build_cancelled_document,
    build_failure_document,
    build_request_snapshot,
    build_success_document,
    new_request_id,
    new_result_id,
    new_run_id,
)
from api_observatory.storage import _load_result_validator
from api_observatory.timing import RequestTiming

API_KEY = "results-test-secret-key"

STARTED_AT = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)
CREATED_AT = datetime(2026, 9, 20, 11, 59, 59, tzinfo=UTC)

ID_PATTERN = re.compile(r"^(run|req|res)_[0-9A-HJKMNP-TV-Z]{26}$")

STANDARD_BODY = {
    "id": "chatcmpl_results_1",
    "object": "chat.completion",
    "model": "results-model",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "ready"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15},
}

STREAM_CHUNKS = [
    {"id": "chatcmpl_rs", "choices": [{"index": 0, "delta": {"role": "assistant"}}]},
    {"id": "chatcmpl_rs", "choices": [{"index": 0, "delta": {"content": "hi"}}]},
    {
        "id": "chatcmpl_rs",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    },
    {
        "id": "chatcmpl_rs",
        "choices": [],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
    },
]
STREAM_BODY = "".join("data: " + json.dumps(c) + "\n\n" for c in STREAM_CHUNKS) + (
    "data: [DONE]\n\n"
)

UNAUTHORIZED_BODY = {"error": {"code": "invalid_api_key", "message": "Incorrect API key provided."}}


def resolved_provider() -> ResolvedProvider:
    return ResolvedProvider(
        definition=ProviderDefinition(
            provider_id="local_results_test",
            display_name="Local Results Test Provider",
            endpoint="https://api.example.com/v1/chat/completions",
            protocol="openai-chat-completions",
            credential_ref="env:LOCAL_RESULTS_TEST_API_KEY",
            adapter="openai-compatible",
            default_model="results-model",
            timeout_ms=2000,
        ),
        api_key=SecretStr(API_KEY),
    )


def completion_request() -> StandardCompletionRequest:
    return StandardCompletionRequest(
        messages=(ChatMessage(role="user", content="Say ready."),),
        parameters=CompletionParameters(temperature=0, max_output_tokens=16),
    )


def snapshot(*, mode: str, sample_index: int = 1) -> dict[str, object]:
    builder = build_request_snapshot(
        request_id=new_request_id(),
        run_id=new_run_id(),
        sample_index=sample_index,
        provider=resolved_provider().definition,
        model="results-model",
        mode=mode,
        messages=completion_request().messages,
        parameters=completion_request().parameters,
        created_at=CREATED_AT,
        metadata={"test_case_id": "results-unit-v1", "tags": ["unit"]},
    )
    assert isinstance(builder, dict)
    return builder


def validate_document(document: dict[str, object]) -> None:
    validator = _load_result_validator()
    errors = list(validator.iter_errors(document))
    assert errors == []


async def run_standard() -> object:
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, content=json.dumps(STANDARD_BODY).encode("utf-8"))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = OpenAICompatibleAdapter(client)
        return await adapter.complete_standard(resolved_provider(), completion_request())


async def run_streaming() -> object:
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, content=STREAM_BODY.encode("utf-8"))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = OpenAICompatibleAdapter(client)
        return await adapter.complete_streaming(resolved_provider(), completion_request())


async def run_unauthorized() -> ProviderCallError:
    transport = httpx.MockTransport(
        lambda req: httpx.Response(401, content=json.dumps(UNAUTHORIZED_BODY).encode("utf-8"))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = OpenAICompatibleAdapter(client)
        try:
            await adapter.complete_standard(resolved_provider(), completion_request())
        except ProviderCallError as exc:
            return exc
        raise AssertionError("expected ProviderCallError")


def _timestamp_ms(id_string: str) -> int:
    value = 0
    for char in id_string[4:14]:
        value = (value << 5) | "0123456789ABCDEFGHJKMNPQRSTVWXYZ".index(char)
    return value >> 2


def test_ids_match_contract_pattern_and_timestamps_never_decrease() -> None:
    run_ids = [new_run_id() for _ in range(8)]
    request_ids = [new_request_id() for _ in range(3)]
    result_ids = [new_result_id() for _ in range(3)]
    for value in (*run_ids, *request_ids, *result_ids):
        assert ID_PATTERN.match(value), value

    timestamps = [_timestamp_ms(value) for value in run_ids]
    assert timestamps == sorted(timestamps)


def test_request_snapshot_uses_reference_and_rfc3339_zulu_time() -> None:
    document = snapshot(mode="standard")

    assert document["contract_version"] == "performance-request.v1"
    assert document["mode"] == "standard"
    assert document["timeout_ms"] == 2000
    assert document["created_at"] == "2026-09-20T11:59:59Z"
    provider_block = document["provider"]
    assert provider_block["provider_id"] == "local_results_test"
    assert provider_block["endpoint"] == "https://api.example.com/v1/chat/completions"
    assert provider_block["protocol"] == "openai-chat-completions"
    assert provider_block["credential_ref"] == "env:LOCAL_RESULTS_TEST_API_KEY"
    assert provider_block["adapter"] == "openai-compatible"
    assert document["parameters"] == {"temperature": 0, "max_output_tokens": 16}
    assert document["input"] == {"messages": [{"role": "user", "content": "Say ready."}]}
    assert API_KEY not in json.dumps(document)


def test_success_document_standard_result_passes_contract() -> None:
    outcome = asyncio.run(run_standard())
    document = build_success_document(
        result_id=new_result_id(),
        request_snapshot=snapshot(mode="standard"),
        outcome=outcome,
        attempt=1,
        worker_id="worker-results-test",
        worker_version="0.1.0",
    )

    validate_document(document)
    assert document["status"] == "succeeded"
    assert document["error"] is None
    assert document["execution"]["worker_id"] == "worker-results-test"
    assert document["execution"]["worker_version"] == "0.1.0"
    assert document["execution"]["adapter_version"] == "openai-compatible@0.1.0"
    assert document["timing"]["duration_clock"] == "monotonic"
    assert document["timing"]["first_content_ms"] is None
    assert document["timing"]["response_completed_ms"] > 0
    assert document["raw_response"]["http_status"] == 200
    assert document["raw_response"]["credential_redacted"] is False
    assert document["usage"] == {
        "source": "provider",
        "input_tokens": 11,
        "output_tokens": 4,
        "total_tokens": 15,
    }
    assert API_KEY not in json.dumps(document)


def test_success_document_streaming_result_keeps_first_content() -> None:
    outcome = asyncio.run(run_streaming())
    document = build_success_document(
        result_id=new_result_id(),
        request_snapshot=snapshot(mode="streaming"),
        outcome=outcome,
        attempt=1,
        worker_id="worker-results-test",
        worker_version="0.1.0",
    )

    validate_document(document)
    assert document["status"] == "succeeded"
    assert document["timing"]["first_content_ms"] is not None
    assert document["usage"]["source"] == "provider"


def test_failure_document_without_response_uses_null_raw_block() -> None:
    timing = RequestTiming(
        request_started_at=STARTED_AT,
        response_completed_at=STARTED_AT + timedelta(milliseconds=5012),
        response_completed_ms=5012.0,
        first_content_ms=None,
    )
    error = ProviderCallError(
        category="timeout",
        code="TIMEOUT.REQUEST",
        phase="request_send",
        message="Provider request exceeded its timeout.",
        retryable=True,
        timing=timing,
    )
    document = build_failure_document(
        result_id=new_result_id(),
        request_snapshot=snapshot(mode="standard"),
        error=error,
        attempt=1,
        worker_id="worker-results-test",
        worker_version="0.1.0",
    )

    validate_document(document)
    assert document["status"] == "failed"
    assert document["error"]["category"] == "timeout"
    assert document["error"]["phase"] == "request_send"
    assert document["error"]["http_status"] is None
    assert document["raw_response"] == {
        "http_status": None,
        "provider_request_id": None,
        "headers": {},
        "media_type": None,
        "body_text": None,
        "body_sha256": None,
        "bytes_received": 0,
        "credential_redacted": False,
        "truncated": False,
    }
    assert document["usage"] == {
        "source": "unavailable",
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }
    assert API_KEY not in json.dumps(document)


def test_failure_document_with_response_preserves_raw_block() -> None:
    error = asyncio.run(run_unauthorized())
    document = build_failure_document(
        result_id=new_result_id(),
        request_snapshot=snapshot(mode="standard"),
        error=error,
        attempt=2,
        worker_id="worker-results-test",
        worker_version="0.1.0",
    )

    validate_document(document)
    assert document["status"] == "failed"
    assert document["error"]["category"] == "provider"
    assert document["error"]["http_status"] == 401
    assert document["error"]["provider_code"] == "invalid_api_key"
    assert document["error"]["phase"] == "response_body"
    assert document["raw_response"]["http_status"] == 401
    assert document["raw_response"]["bytes_received"] > 0
    assert document["execution"]["attempt"] == 2
    assert API_KEY not in json.dumps(document)


def test_cancelled_document_uses_cancelled_error_category() -> None:
    completed_at = STARTED_AT + timedelta(milliseconds=5)
    timing = RequestTiming(
        request_started_at=STARTED_AT,
        response_completed_at=completed_at,
        response_completed_ms=5.0,
        first_content_ms=None,
    )
    document = build_cancelled_document(
        result_id=new_result_id(),
        request_snapshot=snapshot(mode="streaming"),
        timing=timing,
        attempt=1,
        worker_id="worker-results-test",
        worker_version="0.1.0",
    )

    validate_document(document)
    assert document["status"] == "cancelled"
    assert document["error"]["category"] == "cancelled"
    assert document["error"]["phase"] == "unknown"
    assert document["raw_response"]["http_status"] is None
    assert document["usage"]["source"] == "unavailable"
