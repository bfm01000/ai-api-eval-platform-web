import asyncio
import hashlib
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from api_observatory.config import ProviderDefinition, ResolvedProvider
from api_observatory.providers.openai_compatible import (
    ChatMessage,
    CompletionParameters,
    OpenAICompatibleAdapter,
    ProviderCallError,
    StandardCompletionRequest,
)
from api_observatory.redaction import REDACTED
from api_observatory.timing import RequestTimer

SUCCESS_BODY = {
    "id": "chatcmpl_local_001",
    "object": "chat.completion",
    "model": "test-model-2026-09-01",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "ready"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 12, "completion_tokens": 1, "total_tokens": 13},
    "debug_auth": "local-provider-secret",
}


class LocalOpenAIHandler(BaseHTTPRequestHandler):
    server: Any

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        self.server.received.append(
            {"path": self.path, "headers": dict(self.headers), "body": json.loads(body)}
        )

        if self.path == "/v1/unauthorized":
            self._send_json(
                401,
                {"error": {"message": "invalid credential", "code": "invalid_api_key"}},
            )
            return
        if self.path == "/v1/invalid":
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b"not-json")
            return
        if self.path == "/v1/non-utf8":
            self.send_response(200)
            self.send_header("content-type", "application/octet-stream")
            self.send_header("content-length", "1")
            self.end_headers()
            self.wfile.write(b"\xff")
            return
        self._send_json(200, SUCCESS_BODY, include_sensitive_header=True)

    def _send_json(
        self, status: int, body: dict[str, object], *, include_sensitive_header: bool = False
    ) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        self.send_header("x-request-id", "local-request-001")
        if include_sensitive_header:
            self.send_header("set-cookie", "session=must-not-be-stored")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def local_provider_server() -> Iterator[tuple[str, list[dict[str, object]]]]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalOpenAIHandler)
    server.received = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}", server.received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def resolved_provider(endpoint: str) -> ResolvedProvider:
    return ResolvedProvider(
        definition=ProviderDefinition(
            provider_id="local_test",
            display_name="Local Test Provider",
            endpoint=endpoint,
            protocol="openai-chat-completions",
            credential_ref="env:LOCAL_TEST_API_KEY",
            adapter="openai-compatible",
            default_model="test-model",
            timeout_ms=2000,
        ),
        api_key=SecretStr("local-provider-secret"),
    )


def completion_request() -> StandardCompletionRequest:
    return StandardCompletionRequest(
        messages=(ChatMessage(role="user", content="Return ready."),),
        parameters=CompletionParameters(temperature=0, max_output_tokens=32, seed=42),
    )


class FakeClock:
    def __init__(
        self,
        *,
        utc_values: tuple[datetime, datetime],
        monotonic_values: tuple[int, int],
    ) -> None:
        self._utc_values = iter(utc_values)
        self._monotonic_values = iter(monotonic_values)

    def utc_now(self) -> datetime:
        return next(self._utc_values)

    def monotonic_ns(self) -> int:
        return next(self._monotonic_values)


async def complete(endpoint: str):
    async with httpx.AsyncClient(trust_env=False) as client:
        adapter = OpenAICompatibleAdapter(client)
        return await adapter.complete_standard(resolved_provider(endpoint), completion_request())


def test_standard_completion_over_real_local_http() -> None:
    with local_provider_server() as (base_url, received):
        result = asyncio.run(complete(f"{base_url}/v1/chat/completions"))

    assert result.content == "ready"
    assert result.completion_id == "chatcmpl_local_001"
    assert result.model == "test-model-2026-09-01"
    assert result.finish_reason == "stop"
    assert result.usage is not None
    assert result.usage.model_dump() == {
        "input_tokens": 12,
        "output_tokens": 1,
        "total_tokens": 13,
    }
    assert result.raw_response.provider_request_id == "local-request-001"
    assert result.raw_response.headers["set-cookie"] == REDACTED
    assert result.raw_response.credential_redacted is True
    assert result.raw_response.bytes_received > len(result.raw_response.body_text.encode())
    assert (
        result.raw_response.body_sha256
        == hashlib.sha256(result.raw_response.body_text.encode()).hexdigest()
    )
    assert "local-provider-secret" not in repr(result)
    assert "local-provider-secret" not in result.model_dump_json()
    assert "local-provider-secret" not in result.raw_response.body_text
    assert result.timing.duration_clock == "monotonic"
    assert result.timing.response_completed_ms > 0
    assert result.timing.total_latency_ms == result.timing.response_completed_ms
    assert result.timing.request_started_at.utcoffset() == timedelta(0)
    assert result.timing.response_completed_at.utcoffset() == timedelta(0)
    assert result.timing.response_completed_at >= result.timing.request_started_at

    request = received[0]
    assert request["path"] == "/v1/chat/completions"
    received_headers = {key.lower(): value for key, value in request["headers"].items()}
    assert received_headers["authorization"] == "Bearer local-provider-secret"
    assert request["body"] == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "Return ready."}],
        "stream": False,
        "temperature": 0.0,
        "max_tokens": 32,
        "seed": 42,
    }


def test_provider_error_is_typed_and_preserves_raw_response() -> None:
    with local_provider_server() as (base_url, _), pytest.raises(ProviderCallError) as captured:
        asyncio.run(complete(f"{base_url}/v1/unauthorized"))

    error = captured.value
    assert error.category == "provider"
    assert error.code == "PROVIDER.ERROR_RESPONSE"
    assert error.http_status == 401
    assert error.provider_code == "invalid_api_key"
    assert error.retryable is False
    assert error.raw_response is not None
    assert "invalid credential" in error.raw_response.body_text
    assert error.timing.response_completed_ms > 0
    assert error.timing.response_completed_at >= error.timing.request_started_at
    assert "local-provider-secret" not in str(error)
    assert "local-provider-secret" not in repr(error)


def test_invalid_success_response_is_protocol_error_with_raw_body() -> None:
    with local_provider_server() as (base_url, _), pytest.raises(ProviderCallError) as captured:
        asyncio.run(complete(f"{base_url}/v1/invalid"))

    error = captured.value
    assert error.category == "protocol"
    assert error.code == "PROTOCOL.INVALID_COMPLETION"
    assert error.phase == "protocol_decode"
    assert error.http_status == 200
    assert error.raw_response is not None
    assert error.raw_response.body_text == "not-json"
    assert error.timing.response_completed_ms > 0


def test_non_utf8_response_retains_completed_timing() -> None:
    with local_provider_server() as (base_url, _), pytest.raises(ProviderCallError) as captured:
        asyncio.run(complete(f"{base_url}/v1/non-utf8"))

    error = captured.value
    assert error.category == "parse"
    assert error.code == "PARSE.NON_UTF8_RESPONSE"
    assert error.http_status == 200
    assert error.timing.response_completed_ms > 0


def test_total_latency_uses_monotonic_clock_not_wall_clock() -> None:
    started_at = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
    completed_at = started_at + timedelta(seconds=10)
    clock = FakeClock(
        utc_values=(started_at, completed_at),
        monotonic_values=(4_000_000_000, 4_250_250_000),
    )

    async def run_completion():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=SUCCESS_BODY))
        async with httpx.AsyncClient(transport=transport) as client:
            adapter = OpenAICompatibleAdapter(client, clock=clock)
            return await adapter.complete_standard(
                resolved_provider("https://api.example.com/v1/chat/completions"),
                completion_request(),
            )

    result = asyncio.run(run_completion())

    assert result.timing.request_started_at == started_at
    assert result.timing.response_completed_at == completed_at
    assert result.timing.response_completed_ms == 250.25
    assert result.timing.total_latency_ms == 250.25


@pytest.mark.parametrize(
    ("exception_type", "expected_category", "expected_code"),
    [
        (httpx.ConnectError, "connect", "CONNECT.REQUEST_FAILED"),
        (httpx.ReadTimeout, "timeout", "TIMEOUT.REQUEST"),
    ],
)
def test_transport_error_retains_elapsed_timing(
    exception_type: type[httpx.RequestError],
    expected_category: str,
    expected_code: str,
) -> None:
    started_at = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
    clock = FakeClock(
        utc_values=(started_at, started_at + timedelta(milliseconds=125)),
        monotonic_values=(8_000_000_000, 8_125_500_000),
    )

    def fail(request: httpx.Request) -> httpx.Response:
        raise exception_type("forced transport failure", request=request)

    async def run_completion() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            adapter = OpenAICompatibleAdapter(client, clock=clock)
            await adapter.complete_standard(
                resolved_provider("https://api.example.com/v1/chat/completions"),
                completion_request(),
            )

    with pytest.raises(ProviderCallError) as captured:
        asyncio.run(run_completion())

    error = captured.value
    assert error.category == expected_category
    assert error.code == expected_code
    assert error.timing.response_completed_ms == 125.5
    assert error.timing.response_completed_at == started_at + timedelta(milliseconds=125)


def test_request_timer_rejects_negative_monotonic_elapsed_time() -> None:
    now = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
    clock = FakeClock(
        utc_values=(now, now),
        monotonic_values=(2_000_000, 1_999_999),
    )
    timer = RequestTimer.start(clock)

    with pytest.raises(ValueError, match="moved backwards"):
        timer.finish()


def test_non_local_http_endpoint_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-local provider endpoints must use HTTPS"):
        resolved_provider("http://api.example.com/v1/chat/completions")


def test_endpoint_with_query_credential_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not contain credentials"):
        resolved_provider("https://api.example.com/v1/chat/completions?api_key=unsafe")
