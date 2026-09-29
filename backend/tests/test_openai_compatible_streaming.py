import asyncio
import hashlib
import json
import threading
import time
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
from api_observatory.sse import parse_sse_buffer
from api_observatory.timing import RequestTimer

STREAM_CHUNKS = [
    {
        "id": "chatcmpl_stream_001",
        "object": "chat.completion.chunk",
        "model": "stream-model-2026-09-01",
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    },
    {
        "id": "chatcmpl_stream_001",
        "object": "chat.completion.chunk",
        "model": "stream-model-2026-09-01",
        "choices": [{"index": 0, "delta": {"content": "Hello"}, "finish_reason": None}],
    },
    {
        "id": "chatcmpl_stream_001",
        "object": "chat.completion.chunk",
        "model": "stream-model-2026-09-01",
        "choices": [{"index": 0, "delta": {"content": " world"}, "finish_reason": None}],
    },
    {
        "id": "chatcmpl_stream_001",
        "object": "chat.completion.chunk",
        "model": "stream-model-2026-09-01",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    },
    {
        "id": "chatcmpl_stream_001",
        "object": "chat.completion.chunk",
        "model": "stream-model-2026-09-01",
        "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15},
    },
]

STREAM_BODY = (
    "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in STREAM_CHUNKS) + "data: [DONE]\n\n"
)


class LocalOpenAIStreamHandler(BaseHTTPRequestHandler):
    server: Any
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        self.server.received.append(
            {"path": self.path, "headers": dict(self.headers), "body": json.loads(body)}
        )

        route = getattr(self.server, "routes", {}).get(self.path)
        if route is None:
            self.send_error(404)
            return
        route(self)

    def send_sse_headers(self) -> None:
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("x-request-id", "local-stream-001")
        self.send_header("connection", "close")
        self.end_headers()

    def write_sse(self, text: str) -> None:
        self.wfile.write(text.encode("utf-8"))
        self.wfile.flush()

    def log_message(self, format: str, *args: object) -> None:
        return


def route_stream_success(handler: LocalOpenAIStreamHandler) -> None:
    handler.send_sse_headers()
    handler.write_sse(": keepalive\n\n")
    for chunk in STREAM_CHUNKS:
        if chunk["choices"][0]["delta"].get("content"):
            time.sleep(0.05)
        handler.write_sse("data: " + json.dumps(chunk) + "\n\n")
    handler.write_sse("data: [DONE]\n\n")
    handler.close_connection = True


def route_stream_no_done(handler: LocalOpenAIStreamHandler) -> None:
    handler.send_sse_headers()
    handler.write_sse("data: " + json.dumps(STREAM_CHUNKS[1]) + "\n\n")
    handler.close_connection = True


def route_stream_no_content(handler: LocalOpenAIStreamHandler) -> None:
    handler.send_sse_headers()
    handler.write_sse("data: " + json.dumps(STREAM_CHUNKS[0]) + "\n\n")
    handler.write_sse("data: " + json.dumps(STREAM_CHUNKS[3]) + "\n\n")
    handler.write_sse("data: [DONE]\n\n")
    handler.close_connection = True


def route_stream_invalid_json(handler: LocalOpenAIStreamHandler) -> None:
    handler.send_sse_headers()
    handler.write_sse("data: {broken\n\n")
    handler.write_sse("data: [DONE]\n\n")
    handler.close_connection = True


def route_stream_non_utf8(handler: LocalOpenAIStreamHandler) -> None:
    handler.send_sse_headers()
    handler.wfile.write(b"\xff")
    handler.wfile.flush()
    handler.close_connection = True


def route_stream_hang(handler: LocalOpenAIStreamHandler) -> None:
    handler.send_sse_headers()
    handler.write_sse("data: " + json.dumps(STREAM_CHUNKS[1]) + "\n\n")
    time.sleep(5)
    handler.close_connection = True


def route_stream_unauthorized(handler: LocalOpenAIStreamHandler) -> None:
    body = json.dumps({"error": {"message": "invalid credential", "code": "invalid_api_key"}})
    encoded = body.encode()
    handler.send_response(401)
    handler.send_header("content-type", "application/json")
    handler.send_header("content-length", str(len(encoded)))
    handler.end_headers()
    handler.wfile.write(encoded)


@contextmanager
def local_stream_server() -> Iterator[tuple[str, list[dict[str, object]]]]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalOpenAIStreamHandler)
    server.received = []
    server.routes = {
        "/v1/stream-success": route_stream_success,
        "/v1/stream-no-done": route_stream_no_done,
        "/v1/stream-no-content": route_stream_no_content,
        "/v1/stream-invalid-json": route_stream_invalid_json,
        "/v1/stream-non-utf8": route_stream_non_utf8,
        "/v1/stream-hang": route_stream_hang,
        "/v1/stream-unauthorized": route_stream_unauthorized,
    }
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}", server.received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def resolved_provider(endpoint: str, *, timeout_ms: int = 2000) -> ResolvedProvider:
    return ResolvedProvider(
        definition=ProviderDefinition(
            provider_id="local_stream_test",
            display_name="Local Stream Test Provider",
            endpoint=endpoint,
            protocol="openai-chat-completions",
            credential_ref="env:LOCAL_STREAM_TEST_API_KEY",
            adapter="openai-compatible",
            default_model="stream-model",
            timeout_ms=timeout_ms,
        ),
        api_key=SecretStr("local-stream-secret"),
    )


def streaming_request() -> StandardCompletionRequest:
    return StandardCompletionRequest(
        messages=(ChatMessage(role="user", content="Say hello."),),
        parameters=CompletionParameters(temperature=0, max_output_tokens=32, seed=7),
    )


async def complete_streaming(endpoint: str, *, timeout_ms: int = 2000):
    async with httpx.AsyncClient(trust_env=False) as client:
        adapter = OpenAICompatibleAdapter(client)
        return await adapter.complete_streaming(
            resolved_provider(endpoint, timeout_ms=timeout_ms), streaming_request()
        )


def test_streaming_completion_over_real_local_http() -> None:
    with local_stream_server() as (base_url, received):
        result = asyncio.run(complete_streaming(f"{base_url}/v1/stream-success"))

    assert result.content == "Hello world"
    assert result.completion_id == "chatcmpl_stream_001"
    assert result.model == "stream-model-2026-09-01"
    assert result.finish_reason == "stop"
    assert result.usage is not None
    assert result.usage.model_dump() == {
        "input_tokens": 11,
        "output_tokens": 4,
        "total_tokens": 15,
    }

    assert result.ttft_ms is not None
    assert result.ttft_ms >= 20
    assert result.timing.response_completed_ms >= result.ttft_ms
    generation_ms = result.timing.response_completed_ms - result.ttft_ms
    assert result.tokens_per_second == pytest.approx(
        round(4 / (generation_ms / 1000), 3), abs=0.001
    )
    assert result.timing.first_content_ms == result.ttft_ms
    assert result.timing.generation_duration_ms == pytest.approx(generation_ms, abs=0.001)

    raw = result.raw_response
    assert raw.http_status == 200
    assert raw.provider_request_id == "local-stream-001"
    assert raw.media_type == "text/event-stream"
    assert "data: [DONE]" in raw.body_text
    assert "Hello" in raw.body_text
    assert raw.bytes_received == len(raw.body_text.encode("utf-8"))
    assert raw.body_sha256 == hashlib.sha256(raw.body_text.encode("utf-8")).hexdigest()
    assert raw.credential_redacted is False
    assert raw.truncated is False
    assert "local-stream-secret" not in result.model_dump_json()

    request = received[0]
    received_headers = {key.lower(): value for key, value in request["headers"].items()}
    assert received_headers["authorization"] == "Bearer local-stream-secret"
    assert request["body"]["stream"] is True
    assert request["body"]["stream_options"] == {"include_usage": True}
    assert request["body"]["max_tokens"] == 32


def test_streaming_missing_done_sentinel_is_protocol_error() -> None:
    with local_stream_server() as (base_url, _), pytest.raises(ProviderCallError) as captured:
        asyncio.run(complete_streaming(f"{base_url}/v1/stream-no-done"))

    error = captured.value
    assert error.category == "protocol"
    assert error.code == "PROTOCOL.STREAM_INCOMPLETE"
    assert error.phase == "protocol_decode"
    assert error.http_status == 200
    assert error.retryable is False
    assert error.raw_response is not None
    assert error.raw_response.truncated is True
    assert "Hello" in error.raw_response.body_text
    assert error.timing.response_completed_ms > 0


def test_streaming_without_content_is_protocol_error() -> None:
    with local_stream_server() as (base_url, _), pytest.raises(ProviderCallError) as captured:
        asyncio.run(complete_streaming(f"{base_url}/v1/stream-no-content"))

    error = captured.value
    assert error.category == "protocol"
    assert error.code == "PROTOCOL.NO_CONTENT"
    assert error.raw_response is not None
    assert error.raw_response.truncated is False


def test_streaming_invalid_chunk_is_protocol_error() -> None:
    with local_stream_server() as (base_url, _), pytest.raises(ProviderCallError) as captured:
        asyncio.run(complete_streaming(f"{base_url}/v1/stream-invalid-json"))

    error = captured.value
    assert error.category == "protocol"
    assert error.code == "PROTOCOL.INVALID_STREAM_CHUNK"
    assert error.raw_response is not None
    assert error.raw_response.truncated is True


def test_streaming_non_utf8_is_parse_error() -> None:
    with local_stream_server() as (base_url, _), pytest.raises(ProviderCallError) as captured:
        asyncio.run(complete_streaming(f"{base_url}/v1/stream-non-utf8"))

    error = captured.value
    assert error.category == "parse"
    assert error.code == "PARSE.NON_UTF8_RESPONSE"
    assert error.http_status == 200


def test_streaming_mid_stream_timeout_keeps_partial_raw_response() -> None:
    with local_stream_server() as (base_url, _), pytest.raises(ProviderCallError) as captured:
        asyncio.run(complete_streaming(f"{base_url}/v1/stream-hang", timeout_ms=300))

    error = captured.value
    assert error.category == "timeout"
    assert error.code == "TIMEOUT.REQUEST"
    assert error.phase == "response_body"
    assert error.retryable is True
    assert error.raw_response is not None
    assert error.raw_response.truncated is True
    assert "Hello" in error.raw_response.body_text
    assert error.timing.response_completed_ms >= 250


def test_streaming_unauthorized_is_provider_error() -> None:
    with local_stream_server() as (base_url, _), pytest.raises(ProviderCallError) as captured:
        asyncio.run(complete_streaming(f"{base_url}/v1/stream-unauthorized"))

    error = captured.value
    assert error.category == "provider"
    assert error.code == "PROVIDER.ERROR_RESPONSE"
    assert error.http_status == 401
    assert error.provider_code == "invalid_api_key"
    assert "local-stream-secret" not in str(error)


class FakeClock:
    def __init__(
        self,
        *,
        utc_values: tuple[datetime, datetime],
        monotonic_values: tuple[int, ...],
    ) -> None:
        self._utc_values = iter(utc_values)
        self._monotonic_values = iter(monotonic_values)

    def utc_now(self) -> datetime:
        return next(self._utc_values)

    def monotonic_ns(self) -> int:
        return next(self._monotonic_values)


def test_streaming_ttft_and_tokens_per_second_use_monotonic_clock() -> None:
    started_at = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
    completed_at = started_at + timedelta(milliseconds=300)
    clock = FakeClock(
        utc_values=(started_at, completed_at),
        monotonic_values=(0, 100_000_000, 300_000_000),
    )

    async def run_streaming():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, content=STREAM_BODY.encode("utf-8"))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            adapter = OpenAICompatibleAdapter(client, clock=clock)
            return await adapter.complete_streaming(
                resolved_provider("https://api.example.com/v1/chat/completions"),
                streaming_request(),
            )

    result = asyncio.run(run_streaming())

    assert result.timing.request_started_at == started_at
    assert result.timing.response_completed_at == completed_at
    assert result.timing.first_content_ms == 100.0
    assert result.timing.response_completed_ms == 300.0
    assert result.ttft_ms == 100.0
    assert result.timing.generation_duration_ms == 200.0
    assert result.tokens_per_second == 20.0


def test_streaming_first_content_skips_role_keepalive_and_blank_deltas() -> None:
    started_at = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
    # Only the first-content mark and finish consume the monotonic clock;
    # role-only and whitespace-only deltas record no time of their own.
    clock = FakeClock(
        utc_values=(started_at, started_at + timedelta(milliseconds=400)),
        monotonic_values=(0, 250_000_000, 400_000_000),
    )
    body = (
        ": keepalive\n\n"
        + "data: "
        + json.dumps(STREAM_CHUNKS[0])
        + "\n\n"  # role-only delta: must not count as first content
        + "data: "
        + json.dumps(
            {
                "id": "chatcmpl_stream_001",
                "choices": [{"index": 0, "delta": {"content": "  "}, "finish_reason": None}],
            }
        )
        + "\n\n"  # whitespace-only delta: must not count
        + "data: "
        + json.dumps(STREAM_CHUNKS[1])
        + "\n\n"  # first real content at 250 ms
        + "data: [DONE]\n\n"
    )

    async def run_streaming():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, content=body.encode("utf-8"))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            adapter = OpenAICompatibleAdapter(client, clock=clock)
            return await adapter.complete_streaming(
                resolved_provider("https://api.example.com/v1/chat/completions"),
                streaming_request(),
            )

    result = asyncio.run(run_streaming())

    assert result.content == "  Hello"
    assert result.timing.first_content_ms == 250.0
    assert result.timing.response_completed_ms == 400.0


def test_streaming_usage_only_chunk_does_not_count_as_content() -> None:
    body = (
        "data: "
        + json.dumps(STREAM_CHUNKS[4])
        + "\n\ndata: [DONE]\n\n"  # usage-only chunk, no content deltas
    )

    async def run_streaming():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, content=body.encode("utf-8"))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            adapter = OpenAICompatibleAdapter(client)
            return await adapter.complete_streaming(
                resolved_provider("https://api.example.com/v1/chat/completions"),
                streaming_request(),
            )

    with pytest.raises(ProviderCallError) as captured:
        asyncio.run(run_streaming())
    assert captured.value.code == "PROTOCOL.NO_CONTENT"


def test_request_timer_record_first_content_is_monotonic_and_single_use() -> None:
    started_at = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
    clock = FakeClock(
        utc_values=(started_at, started_at),
        monotonic_values=(1_000_000_000, 1_250_000_000, 1_500_000_000),
    )
    timer = RequestTimer.start(clock)

    assert timer.record_first_content() == 250.0
    with pytest.raises(RuntimeError, match="already been recorded"):
        timer.record_first_content()
    timing = timer.finish()
    assert timing.first_content_ms == 250.0
    assert timing.response_completed_ms == 500.0
    assert timing.generation_duration_ms == 250.0


def test_request_timer_finish_before_first_content_leaves_it_unset() -> None:
    started_at = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
    clock = FakeClock(
        utc_values=(started_at, started_at),
        monotonic_values=(1_000_000_000, 1_200_000_000),
    )
    timer = RequestTimer.start(clock)

    timing = timer.finish()
    assert timing.first_content_ms is None
    assert timing.generation_duration_ms is None


def test_request_timer_rejects_first_content_after_finish() -> None:
    started_at = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
    clock = FakeClock(
        utc_values=(started_at, started_at),
        monotonic_values=(1_000_000_000, 1_100_000_000),
    )
    timer = RequestTimer.start(clock)
    timer.finish()

    with pytest.raises(RuntimeError, match="already been finished"):
        timer.record_first_content()


def test_parse_sse_buffer_handles_event_split_across_byte_chunks() -> None:
    full = "data: " + json.dumps(STREAM_CHUNKS[1]) + "\n\ndata: [DONE]\n\n"
    split_at = full.index("Hello") + 2
    decoder_chunks = [full[:split_at].encode("utf-8"), full[split_at:].encode("utf-8")]

    buffer = ""
    events = []
    for chunk in decoder_chunks:
        buffer += chunk.decode("utf-8")
        parsed = parse_sse_buffer(buffer)
        buffer = parsed.remainder
        events.extend(parsed.events)

    assert buffer == ""
    assert [event.payload for event in events][-1] == "[DONE]"
    assert '"Hello' in events[0].payload
