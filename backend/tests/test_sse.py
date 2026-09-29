import pytest

from api_observatory.sse import (
    SseParseError,
    StreamState,
    decode_event_payload,
    parse_sse_buffer,
)


def test_parse_single_event_with_multiple_data_lines() -> None:
    result = parse_sse_buffer("data: line-one\ndata: line-two\n\n")

    assert result.remainder == ""
    assert len(result.events) == 1
    assert result.events[0].data == ("line-one", "line-two")
    assert result.events[0].payload == "line-one\nline-two"


def test_parse_ignores_comments_and_non_data_fields() -> None:
    result = parse_sse_buffer(": keepalive\n\nevent: message\ndata: value\n\n")

    assert len(result.events) == 1
    assert result.events[0].payload == "value"


def test_parse_returns_incomplete_tail_as_remainder() -> None:
    result = parse_sse_buffer("data: complete\n\ndata: partial")

    assert len(result.events) == 1
    assert result.events[0].payload == "complete"
    assert result.remainder == "data: partial"


def test_parse_splits_events_across_chunks() -> None:
    first = parse_sse_buffer("data: hea")
    second = parse_sse_buffer(first.remainder + "der-one\n\nda")
    third = parse_sse_buffer(second.remainder + "ta: two\n\n")

    assert [event.payload for event in first.events] == []
    assert [event.payload for event in second.events] == ["header-one"]
    assert [event.payload for event in third.events] == ["two"]
    assert third.remainder == ""


def test_parse_normalizes_crlf_line_endings() -> None:
    result = parse_sse_buffer("data: one\r\n\r\ndata: two\r\rdata: three\n\n")

    assert [event.payload for event in result.events] == ["one", "two", "three"]


def test_parse_skips_events_without_data_lines() -> None:
    result = parse_sse_buffer(": ping\n\nretry: 3000\n\ndata: real\n\n")

    assert [event.payload for event in result.events] == ["real"]


def test_done_sentinel_is_case_sensitive_and_trimmed() -> None:
    assert decode_event_payload("[DONE]").kind == "done"
    assert decode_event_payload(" [DONE] \n").kind == "done"
    with pytest.raises(SseParseError):
        decode_event_payload("[done]")  # not a valid chunk payload either


def test_decode_chunk_extracts_content_finish_and_usage() -> None:
    decoded = decode_event_payload(
        '{"id":"chatcmpl_1","model":"m","choices":[{"index":0,'
        '"delta":{"role":"assistant","content":"Hi"},"finish_reason":null}],'
        '"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}'
    )

    assert decoded.kind == "chunk"
    assert decoded.chunk is not None
    assert decoded.chunk.content_deltas == ("Hi",)
    assert decoded.chunk.usage == (3, 2, 5)
    assert decoded.chunk.completion_id == "chatcmpl_1"
    assert decoded.chunk.model == "m"


def test_decode_chunk_ignores_role_only_and_empty_deltas() -> None:
    decoded = decode_event_payload(
        '{"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}'
    )
    assert decoded.kind == "chunk"
    assert decoded.chunk is not None
    assert decoded.chunk.content_deltas == ()
    assert decoded.chunk.completion_id is None


def test_decode_rejects_invalid_json() -> None:
    with pytest.raises(SseParseError):
        decode_event_payload("{not-json")


def test_decode_rejects_non_object_json() -> None:
    with pytest.raises(SseParseError):
        decode_event_payload("[1, 2]")


def test_stream_state_tracks_first_non_empty_content() -> None:
    state = StreamState()
    state.apply(
        decode_event_payload('{"choices":[{"delta":{"role":"assistant"}}]}').chunk  # type: ignore[union-attr]
    )
    assert state.first_content_seen is False

    state.apply(
        decode_event_payload('{"choices":[{"delta":{"content":"   "}}]}').chunk  # type: ignore[union-attr]
    )
    assert state.first_content_seen is False
    assert state.content == "   "

    state.apply(
        decode_event_payload('{"choices":[{"delta":{"content":"ready"}}]}').chunk  # type: ignore[union-attr]
    )
    assert state.first_content_seen is True
    assert state.content == "   ready"


def test_stream_state_accumulates_finish_reason_usage_and_identity() -> None:
    state = StreamState()
    for payload in (
        '{"id":"chatcmpl_9","model":"m","choices":[{"delta":{"content":"a"}}]}',
        '{"id":"chatcmpl_9","model":"m","choices":[{"delta":{"content":"b"},"finish_reason":"stop"}]}',
        '{"choices":[],"usage":{"prompt_tokens":4,"completion_tokens":2,"total_tokens":6}}',
    ):
        decoded = decode_event_payload(payload)
        assert decoded.kind == "chunk"
        state.apply(decoded.chunk)  # type: ignore[arg-type]

    assert state.content == "ab"
    assert state.finish_reason == "stop"
    assert state.usage == (4, 2, 6)
    assert state.completion_id == "chatcmpl_9"
    assert state.model == "m"
    assert state.chunks_seen == 3
