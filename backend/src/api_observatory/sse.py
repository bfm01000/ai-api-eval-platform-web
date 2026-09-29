"""Server-Sent Events parsing for OpenAI-compatible streaming responses.

The parser is incremental: feed decoded text chunks and receive every SSE
event whose terminating blank line has been observed, plus the raw text tail
that is still incomplete. Raw text is preserved separately for storage; this
module only derives protocol-level events.
"""

import json
from dataclasses import dataclass
from typing import Literal


class SseParseError(ValueError):
    """Raised when SSE data payloads cannot be decoded as stream chunks."""


@dataclass(frozen=True)
class SseEvent:
    data: tuple[str, ...]

    @property
    def payload(self) -> str:
        return "\n".join(self.data)


@dataclass(frozen=True)
class SseParseResult:
    events: tuple[SseEvent, ...]
    remainder: str


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_sse_buffer(text: str) -> SseParseResult:
    """Split SSE text into complete events and the unterminated raw tail.

    Comment lines and non-``data`` field lines are ignored per the SSE
    specification; OpenAI-compatible providers only send ``data:`` lines and
    comments. A block ends at its terminating blank line.
    """
    normalized = _normalize_newlines(text)
    events: list[SseEvent] = []
    start = 0
    while True:
        separator = normalized.find("\n\n", start)
        if separator == -1:
            break
        block = normalized[start:separator]
        start = separator + 2
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            if field != "data":
                continue
            if value.startswith(" "):
                value = value[1:]
            data_lines.append(value)
        if data_lines:
            events.append(SseEvent(data=tuple(data_lines)))
    return SseParseResult(events=tuple(events), remainder=normalized[start:])


@dataclass(frozen=True)
class StreamChunk:
    """Decoded OpenAI-compatible streaming chunk."""

    content_deltas: tuple[str, ...]
    finish_reason: str | None
    usage: tuple[int, int, int] | None  # (prompt, completion, total)
    completion_id: str | None = None
    model: str | None = None


class StreamState:
    """Accumulates decoded chunks and tracks first valid content."""

    def __init__(self) -> None:
        self.content_parts: list[str] = []
        self.finish_reason: str | None = None
        self.usage: tuple[int, int, int] | None = None
        self.completion_id: str | None = None
        self.model: str | None = None
        self.first_content_seen = False
        self.chunks_seen = 0

    def apply(self, chunk: StreamChunk) -> None:
        self.chunks_seen += 1
        for delta in chunk.content_deltas:
            self.content_parts.append(delta)
            if delta.strip() and not self.first_content_seen:
                self.first_content_seen = True
        if chunk.finish_reason is not None:
            self.finish_reason = chunk.finish_reason
        if chunk.usage is not None:
            self.usage = chunk.usage
        if self.completion_id is None and chunk.completion_id:
            self.completion_id = chunk.completion_id
        if self.model is None and chunk.model:
            self.model = chunk.model

    @property
    def content(self) -> str:
        return "".join(self.content_parts)


@dataclass(frozen=True)
class DecodedEvent:
    kind: Literal["chunk", "done"]
    chunk: StreamChunk | None = None


def decode_event_payload(payload: str) -> DecodedEvent:
    """Decode one SSE event payload into a stream chunk or the [DONE] sentinel."""
    if payload.strip() == "[DONE]":
        return DecodedEvent(kind="done")
    try:
        document = json.loads(payload)
    except json.JSONDecodeError:
        raise SseParseError("SSE data is not valid JSON.") from None
    if not isinstance(document, dict):
        raise SseParseError("SSE data must decode to a JSON object.")

    completion_id = document.get("id")
    model = document.get("model")

    deltas: list[str] = []
    finish_reason: str | None = None
    choices = document.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str):
                    deltas.append(content)
            reason = choice.get("finish_reason")
            if isinstance(reason, str) and reason:
                finish_reason = reason

    usage: tuple[int, int, int] | None = None
    usage_document = document.get("usage")
    if isinstance(usage_document, dict):
        prompt = usage_document.get("prompt_tokens")
        completion = usage_document.get("completion_tokens")
        total = usage_document.get("total_tokens")
        if all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in (prompt, completion, total)
        ):
            usage = (prompt, completion, total)

    return DecodedEvent(
        kind="chunk",
        chunk=StreamChunk(
            content_deltas=tuple(deltas),
            finish_reason=finish_reason,
            usage=usage,
            completion_id=completion_id if isinstance(completion_id, str) else None,
            model=model if isinstance(model, str) else None,
        ),
    )
