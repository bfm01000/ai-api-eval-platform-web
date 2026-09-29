import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import SecretStr

from api_observatory.config import ProviderDefinition, ResolvedProvider
from api_observatory.providers.openai_compatible import (
    ChatMessage,
    CompletionParameters,
    OpenAICompatibleAdapter,
    StandardCompletionRequest,
)

STANDARD_BODY_WITHOUT_USAGE = {
    "id": "chatcmpl_no_usage",
    "object": "chat.completion",
    "model": "test-model",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "ready"},
            "finish_reason": "stop",
        }
    ],
}


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


def provider() -> ResolvedProvider:
    return ResolvedProvider(
        definition=ProviderDefinition(
            provider_id="local_usage_test",
            display_name="Local Usage Test Provider",
            endpoint="https://api.example.com/v1/chat/completions",
            protocol="openai-chat-completions",
            credential_ref="env:LOCAL_USAGE_TEST_API_KEY",
            adapter="openai-compatible",
            default_model="usage-model",
            timeout_ms=2000,
        ),
        api_key=SecretStr("usage-secret"),
    )


def request() -> StandardCompletionRequest:
    return StandardCompletionRequest(
        messages=(ChatMessage(role="user", content="Count to two."),),
        parameters=CompletionParameters(temperature=0, max_output_tokens=16),
    )


def stream_body(*, with_usage: bool, output_tokens: int = 4) -> str:
    chunks = [
        {"id": "chatcmpl_u1", "choices": [{"index": 0, "delta": {"content": "ab"}}]},
        {"id": "chatcmpl_u1", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    if with_usage:
        chunks.append(
            {
                "id": "chatcmpl_u1",
                "choices": [],
                "usage": {
                    "prompt_tokens": 9,
                    "completion_tokens": output_tokens,
                    "total_tokens": 9 + output_tokens,
                },
            }
        )
    return "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"


async def run_standard(body: dict[str, object]):
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, content=json.dumps(body).encode("utf-8"))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = OpenAICompatibleAdapter(client)
        return await adapter.complete_standard(provider(), request())


async def run_streaming(body_text: str, clock: FakeClock | None = None):
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, content=body_text.encode("utf-8"))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        adapter = OpenAICompatibleAdapter(client, clock=clock)
        return await adapter.complete_streaming(provider(), request())


def test_standard_completion_without_usage_keeps_it_unavailable() -> None:
    result = asyncio.run(run_standard(STANDARD_BODY_WITHOUT_USAGE))

    assert result.usage is None
    assert result.content == "ready"
    assert result.finish_reason == "stop"


def test_streaming_without_usage_chunk_keeps_it_unavailable() -> None:
    result = asyncio.run(run_streaming(stream_body(with_usage=False)))

    assert result.content == "ab"
    assert result.usage is None
    assert result.ttft_ms is not None
    assert result.tokens_per_second is None


def test_tokens_per_second_is_null_when_generation_duration_is_zero() -> None:
    started_at = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
    clock = FakeClock(
        utc_values=(started_at, started_at + timedelta(milliseconds=120)),
        monotonic_values=(0, 120_000_000, 120_000_000),
    )

    result = asyncio.run(run_streaming(stream_body(with_usage=True), clock=clock))

    assert result.timing.first_content_ms == 120.0
    assert result.timing.response_completed_ms == 120.0
    assert result.timing.generation_duration_ms == 0.0
    assert result.tokens_per_second is None


def test_tokens_per_second_with_zero_output_tokens_is_zero() -> None:
    started_at = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
    clock = FakeClock(
        utc_values=(started_at, started_at + timedelta(milliseconds=200)),
        monotonic_values=(0, 100_000_000, 300_000_000),
    )

    result = asyncio.run(run_streaming(stream_body(with_usage=True, output_tokens=0), clock))

    assert result.usage is not None
    assert result.usage.output_tokens == 0
    assert result.timing.generation_duration_ms == 200.0
    assert result.tokens_per_second == 0.0


def test_tokens_per_second_uses_generation_window_not_total_latency() -> None:
    started_at = datetime(2026, 9, 6, 8, 0, tzinfo=UTC)
    clock = FakeClock(
        utc_values=(started_at, started_at + timedelta(milliseconds=500)),
        monotonic_values=(0, 400_000_000, 500_000_000),
    )

    result = asyncio.run(run_streaming(stream_body(with_usage=True), clock=clock))

    # Generation window is 100 ms even though total latency is 500 ms.
    assert result.timing.total_latency_ms == 500.0
    assert result.timing.generation_duration_ms == 100.0
    assert result.tokens_per_second == 40.0
