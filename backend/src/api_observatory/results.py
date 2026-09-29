"""Build performance-result.v1 contract documents from adapter outcomes.

The functions in this module assemble immutable raw result documents that
satisfy ``contracts/performance-result.v1.schema.json`` plus the domain rules
in ``docs/PERFORMANCE_CONTRACT.md``. Persistence-time schema validation lives
in ``storage.py``; this module only performs faithful field mapping.

Design decisions:
- Usage is recorded as ``source=provider`` when the adapter captured provider
  usage, otherwise all three token fields are ``null`` with
  ``source=unavailable``. Failed or cancelled samples always report
  ``unavailable``: partial usage parsed before an error is not trustworthy.
- Transport-level failures that never produced an HTTP response are stored
  with an all-null raw response block (``bytes_received=0``), mirroring the
  timeout fixture in ``contracts/examples/``.
- httpx does not expose DNS/connect/TLS segment timings, so the timing block
  honestly leaves those fields ``null`` as the contract permits.
"""

import secrets
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from api_observatory.config import ProviderDefinition
from api_observatory.providers.openai_compatible import (
    ADAPTER_VERSION,
    ChatMessage,
    CompletionParameters,
    ProviderCallError,
    RawProviderResponse,
    StandardCompletionResult,
    StreamingCompletionResult,
    TokenUsage,
)
from api_observatory.timing import RequestTiming

REQUEST_CONTRACT_VERSION = "performance-request.v1"
RESULT_CONTRACT_VERSION = "performance-result.v1"

_CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_NO_RESPONSE_RAW: dict[str, Any] = {
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

_UNAVAILABLE_USAGE: dict[str, Any] = {
    "source": "unavailable",
    "input_tokens": None,
    "output_tokens": None,
    "total_tokens": None,
}


def _encode_crockford_base32(value: int, length: int) -> str:
    chars: list[str] = []
    for _ in range(length):
        chars.append(_CROCKFORD_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def _new_sortable_id(prefix: str) -> str:
    """Return ``prefix`` + 26 Crockford base32 chars encoding time + randomness.

    The first 48 bits hold the UTC millisecond timestamp and the trailing 80
    bits are cryptographically random, so IDs are roughly time-ordered by
    string sort while staying unguessable.
    """

    timestamp_ms = time.time_ns() // 1_000_000
    if timestamp_ms >= 1 << 48:
        raise RuntimeError("timestamp no longer fits the 48-bit id field")
    body = (timestamp_ms << 80) | secrets.randbits(80)
    return f"{prefix}{_encode_crockford_base32(body, 26)}"


def new_run_id() -> str:
    return _new_sortable_id("run_")


def new_request_id() -> str:
    return _new_sortable_id("req_")


def new_result_id() -> str:
    return _new_sortable_id("res_")


def _format_rfc3339(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def build_request_snapshot(
    *,
    request_id: str,
    run_id: str,
    sample_index: int,
    provider: ProviderDefinition,
    model: str,
    mode: Literal["standard", "streaming"],
    messages: tuple[ChatMessage, ...],
    parameters: CompletionParameters,
    created_at: datetime,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "contract_version": REQUEST_CONTRACT_VERSION,
        "request_id": request_id,
        "run_id": run_id,
        "sample_index": sample_index,
        "provider": {
            "provider_id": provider.provider_id,
            "endpoint": str(provider.endpoint),
            "protocol": provider.protocol,
            "credential_ref": provider.credential_ref,
            "adapter": provider.adapter,
        },
        "model": model,
        "mode": mode,
        "timeout_ms": provider.timeout_ms,
        "input": {"messages": [message.model_dump() for message in messages]},
        "parameters": parameters.model_dump(exclude_none=True),
        "created_at": _format_rfc3339(created_at),
    }
    if metadata:
        snapshot["metadata"] = dict(metadata)
    return snapshot


def _execution_block(
    *,
    timing: RequestTiming,
    attempt: int,
    worker_id: str,
    worker_version: str,
) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "worker_id": worker_id,
        "worker_version": worker_version,
        "adapter_version": ADAPTER_VERSION,
        "started_at": _format_rfc3339(timing.request_started_at),
        "completed_at": _format_rfc3339(timing.response_completed_at),
    }


def _timing_block(timing: RequestTiming) -> dict[str, Any]:
    return {
        "duration_clock": "monotonic",
        "dns_completed_ms": None,
        "connect_completed_ms": None,
        "tls_completed_ms": None,
        "response_headers_ms": None,
        "first_byte_ms": None,
        "first_content_ms": timing.first_content_ms,
        "response_completed_ms": timing.response_completed_ms,
    }


def _raw_block(raw: RawProviderResponse) -> dict[str, Any]:
    return {
        "http_status": raw.http_status,
        "provider_request_id": raw.provider_request_id,
        "headers": dict(raw.headers),
        "media_type": raw.media_type,
        "body_text": raw.body_text,
        "body_sha256": raw.body_sha256,
        "bytes_received": raw.bytes_received,
        "credential_redacted": raw.credential_redacted,
        "truncated": raw.truncated,
    }


def _usage_block(usage: TokenUsage | None) -> dict[str, Any]:
    if usage is None:
        return dict(_UNAVAILABLE_USAGE)
    return {
        "source": "provider",
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
    }


def _error_block(error: ProviderCallError) -> dict[str, Any]:
    return {
        "category": error.category,
        "code": error.code,
        "phase": error.phase,
        "message": error.message,
        "retryable": error.retryable,
        "http_status": error.http_status,
        "provider_code": error.provider_code,
    }


def _base_result(
    *,
    result_id: str,
    status: Literal["succeeded", "failed", "cancelled"],
    request_snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "contract_version": RESULT_CONTRACT_VERSION,
        "result_id": result_id,
        "status": status,
        "request_snapshot": dict(request_snapshot),
    }


def build_success_document(
    *,
    result_id: str,
    request_snapshot: Mapping[str, Any],
    outcome: StandardCompletionResult | StreamingCompletionResult,
    attempt: int,
    worker_id: str,
    worker_version: str,
) -> dict[str, Any]:
    document = _base_result(
        result_id=result_id, status="succeeded", request_snapshot=request_snapshot
    )
    document.update(
        execution=_execution_block(
            timing=outcome.timing,
            attempt=attempt,
            worker_id=worker_id,
            worker_version=worker_version,
        ),
        timing=_timing_block(outcome.timing),
        raw_response=_raw_block(outcome.raw_response),
        usage=_usage_block(outcome.usage),
        error=None,
    )
    return document


def build_failure_document(
    *,
    result_id: str,
    request_snapshot: Mapping[str, Any],
    error: ProviderCallError,
    attempt: int,
    worker_id: str,
    worker_version: str,
) -> dict[str, Any]:
    document = _base_result(result_id=result_id, status="failed", request_snapshot=request_snapshot)
    document.update(
        execution=_execution_block(
            timing=error.timing,
            attempt=attempt,
            worker_id=worker_id,
            worker_version=worker_version,
        ),
        timing=_timing_block(error.timing),
        raw_response=(
            _raw_block(error.raw_response)
            if error.raw_response is not None
            else dict(_NO_RESPONSE_RAW)
        ),
        usage=dict(_UNAVAILABLE_USAGE),
        error=_error_block(error),
    )
    return document


def build_cancelled_document(
    *,
    result_id: str,
    request_snapshot: Mapping[str, Any],
    timing: RequestTiming,
    attempt: int,
    worker_id: str,
    worker_version: str,
    message: str = "Sample execution was cancelled before completion.",
) -> dict[str, Any]:
    document = _base_result(
        result_id=result_id, status="cancelled", request_snapshot=request_snapshot
    )
    document.update(
        execution=_execution_block(
            timing=timing,
            attempt=attempt,
            worker_id=worker_id,
            worker_version=worker_version,
        ),
        timing=_timing_block(timing),
        raw_response=dict(_NO_RESPONSE_RAW),
        usage=dict(_UNAVAILABLE_USAGE),
        error={
            "category": "cancelled",
            "code": "CANCELLED.RUN",
            "phase": "unknown",
            "message": message,
            "retryable": True,
            "http_status": None,
            "provider_code": None,
        },
    )
    return document
