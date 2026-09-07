#!/usr/bin/env python3
"""Verify v1 performance contract fixtures without third-party dependencies."""

import copy
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"
EXAMPLES = CONTRACTS / "examples"

FORBIDDEN_KEYS = {
    "authorization",
    "proxy-authorization",
    "api-key",
    "api_key",
    "x-api-key",
    "cookie",
    "set-cookie",
    "access-token",
    "access_token",
}
REQUEST_KEYS = {
    "contract_version", "request_id", "run_id", "sample_index", "provider",
    "model", "mode", "timeout_ms", "input", "parameters", "metadata", "created_at",
}
PROVIDER_KEYS = {"provider_id", "endpoint", "protocol", "credential_ref", "adapter"}
RESULT_KEYS = {
    "contract_version", "result_id", "status", "request_snapshot", "execution",
    "timing", "raw_response", "usage", "error",
}
TIMING_ORDER = (
    "dns_completed_ms",
    "connect_completed_ms",
    "tls_completed_ms",
    "response_headers_ms",
    "first_byte_ms",
    "first_content_ms",
    "response_completed_ms",
)
ERROR_CATEGORIES = {
    "dns", "connect", "tls", "timeout", "http", "provider",
    "protocol", "parse", "internal", "cancelled",
}
ERROR_PHASES = {
    "dns", "connect", "tls", "request_send", "response_headers",
    "response_body", "protocol_decode", "internal", "unknown",
}


class ContractError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ContractError(message)


def require_exact_keys(value, allowed, required, path):
    require(isinstance(value, dict), "%s must be an object" % path)
    missing = required - set(value)
    extra = set(value) - allowed
    require(not missing, "%s missing keys: %s" % (path, sorted(missing)))
    require(not extra, "%s has unsupported keys: %s" % (path, sorted(extra)))


def check_forbidden_keys(value, path="$"):
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.lower().replace("_", "-")
            require(
                key.lower() not in FORBIDDEN_KEYS and normalized not in FORBIDDEN_KEYS,
                "%s.%s is a forbidden credential field" % (path, key),
            )
            check_forbidden_keys(child, "%s.%s" % (path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            check_forbidden_keys(child, "%s[%d]" % (path, index))


def parse_utc(value, path):
    require(isinstance(value, str), "%s must be an RFC 3339 string" % path)
    require(value.endswith("Z") or value.endswith("+00:00"), "%s must use UTC" % path)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ContractError("%s is not a valid RFC 3339 timestamp" % path) from exc
    require(parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed), "%s must use UTC" % path)
    return parsed


def validate_request(value, path="$"):
    require_exact_keys(value, REQUEST_KEYS, REQUEST_KEYS - {"metadata"}, path)
    check_forbidden_keys(value, path)
    require(value["contract_version"] == "performance-request.v1", "%s.contract_version is unsupported" % path)
    require(re.fullmatch(r"req_[A-Za-z0-9_-]{8,64}", value["request_id"]) is not None, "%s.request_id is invalid" % path)
    require(re.fullmatch(r"run_[A-Za-z0-9_-]{8,64}", value["run_id"]) is not None, "%s.run_id is invalid" % path)
    require(isinstance(value["sample_index"], int) and value["sample_index"] >= 1, "%s.sample_index must be >= 1" % path)

    provider = value["provider"]
    require_exact_keys(provider, PROVIDER_KEYS, PROVIDER_KEYS, path + ".provider")
    require(re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", provider["provider_id"]) is not None, "%s.provider.provider_id is invalid" % path)
    endpoint = urlparse(provider["endpoint"])
    require(endpoint.scheme in {"http", "https"} and bool(endpoint.netloc), "%s.provider.endpoint must be an HTTP(S) URI" % path)
    require(provider["protocol"] == "openai-chat-completions", "%s.provider.protocol is unsupported" % path)
    require(
        isinstance(provider["credential_ref"], str)
        and re.fullmatch(r"(env|keychain|vault|config):[A-Za-z0-9_.:/-]{1,120}", provider["credential_ref"]) is not None,
        "%s.provider.credential_ref is invalid" % path,
    )
    require(isinstance(provider["adapter"], str) and bool(provider["adapter"]), "%s.provider.adapter is required" % path)

    require(isinstance(value["model"], str) and bool(value["model"]), "%s.model is required" % path)
    require(value["mode"] in {"standard", "streaming"}, "%s.mode is invalid" % path)
    require(isinstance(value["timeout_ms"], int) and 100 <= value["timeout_ms"] <= 600000, "%s.timeout_ms is out of range" % path)

    input_value = value["input"]
    require_exact_keys(input_value, {"messages"}, {"messages"}, path + ".input")
    messages = input_value["messages"]
    require(isinstance(messages, list) and 1 <= len(messages) <= 100, "%s.input.messages must contain 1..100 items" % path)
    for index, message in enumerate(messages):
        message_path = "%s.input.messages[%d]" % (path, index)
        require_exact_keys(message, {"role", "content"}, {"role", "content"}, message_path)
        require(message["role"] in {"system", "developer", "user", "assistant"}, "%s.role is invalid" % message_path)
        require(isinstance(message["content"], str), "%s.content must be a string" % message_path)

    parameters = value["parameters"]
    parameter_keys = {"temperature", "top_p", "max_output_tokens", "seed", "stop"}
    require_exact_keys(parameters, parameter_keys, set(), path + ".parameters")
    if "temperature" in parameters:
        require(isinstance(parameters["temperature"], (int, float)) and 0 <= parameters["temperature"] <= 2, "%s.parameters.temperature is out of range" % path)
    if "top_p" in parameters:
        require(isinstance(parameters["top_p"], (int, float)) and 0 < parameters["top_p"] <= 1, "%s.parameters.top_p is out of range" % path)
    if "max_output_tokens" in parameters:
        require(isinstance(parameters["max_output_tokens"], int) and 1 <= parameters["max_output_tokens"] <= 1000000, "%s.parameters.max_output_tokens is invalid" % path)
    if "seed" in parameters:
        require(isinstance(parameters["seed"], int) and not isinstance(parameters["seed"], bool), "%s.parameters.seed must be an integer" % path)
    if "stop" in parameters:
        stop = parameters["stop"]
        valid_stop = isinstance(stop, str) and bool(stop)
        valid_stop_list = isinstance(stop, list) and 1 <= len(stop) <= 16 and all(isinstance(item, str) and bool(item) for item in stop)
        require(valid_stop or valid_stop_list, "%s.parameters.stop is invalid" % path)

    if "metadata" in value:
        metadata = value["metadata"]
        require_exact_keys(metadata, {"test_case_id", "tags"}, set(), path + ".metadata")
        if "tags" in metadata:
            tags = metadata["tags"]
            require(isinstance(tags, list) and len(tags) <= 32, "%s.metadata.tags must be an array with at most 32 items" % path)
            require(all(isinstance(tag, str) and 1 <= len(tag) <= 64 for tag in tags), "%s.metadata.tags contains an invalid tag" % path)
            require(len(tags) == len(set(tags)), "%s.metadata.tags must be unique" % path)
    parse_utc(value["created_at"], path + ".created_at")


def validate_result(value, path="$"):
    require_exact_keys(value, RESULT_KEYS, RESULT_KEYS, path)
    check_forbidden_keys(value, path)
    require(value["contract_version"] == "performance-result.v1", "%s.contract_version is unsupported" % path)
    require(re.fullmatch(r"res_[A-Za-z0-9_-]{8,64}", value["result_id"]) is not None, "%s.result_id is invalid" % path)
    require(value["status"] in {"succeeded", "failed", "cancelled"}, "%s.status is invalid" % path)
    validate_request(value["request_snapshot"], path + ".request_snapshot")

    execution = value["execution"]
    execution_keys = {"attempt", "worker_id", "worker_version", "adapter_version", "started_at", "completed_at"}
    require_exact_keys(execution, execution_keys, execution_keys, path + ".execution")
    require(isinstance(execution["attempt"], int) and execution["attempt"] >= 1, "%s.execution.attempt must be >= 1" % path)
    for key in ("worker_id", "worker_version", "adapter_version"):
        require(isinstance(execution[key], str) and bool(execution[key]), "%s.execution.%s is required" % (path, key))
    started_at = parse_utc(execution["started_at"], path + ".execution.started_at")
    completed_at = parse_utc(execution["completed_at"], path + ".execution.completed_at")
    require(completed_at >= started_at, "%s.execution.completed_at precedes started_at" % path)

    timing = value["timing"]
    timing_keys = {"duration_clock"} | set(TIMING_ORDER)
    require_exact_keys(timing, timing_keys, timing_keys, path + ".timing")
    require(timing["duration_clock"] == "monotonic", "%s.timing.duration_clock must be monotonic" % path)
    previous = None
    for key in TIMING_ORDER:
        point = timing[key]
        require(point is None or (isinstance(point, (int, float)) and not isinstance(point, bool) and point >= 0), "%s.timing.%s must be null or non-negative" % (path, key))
        if point is not None:
            require(previous is None or point >= previous, "%s.timing.%s is earlier than a preceding observed point" % (path, key))
            previous = point
    require(timing["response_completed_ms"] is not None, "%s.timing.response_completed_ms is required" % path)

    mode = value["request_snapshot"]["mode"]
    if value["status"] == "succeeded" and mode == "streaming":
        require(timing["first_content_ms"] is not None, "%s successful streaming result requires first_content_ms" % path)
    if mode == "standard":
        require(timing["first_content_ms"] is None, "%s standard result must not define first_content_ms" % path)

    response = value["raw_response"]
    response_keys = {
        "http_status",
        "provider_request_id",
        "headers",
        "media_type",
        "body_text",
        "body_sha256",
        "bytes_received",
        "credential_redacted",
        "truncated",
    }
    require_exact_keys(response, response_keys, response_keys, path + ".raw_response")
    require(isinstance(response["headers"], dict), "%s.raw_response.headers must be an object" % path)
    require(all(isinstance(key, str) and isinstance(header, str) for key, header in response["headers"].items()), "%s.raw_response.headers must contain string values" % path)
    require(isinstance(response["bytes_received"], int) and response["bytes_received"] >= 0, "%s.raw_response.bytes_received is invalid" % path)
    require(isinstance(response["credential_redacted"], bool), "%s.raw_response.credential_redacted must be boolean" % path)
    require(isinstance(response["truncated"], bool), "%s.raw_response.truncated must be boolean" % path)
    if response["body_text"] is None:
        require(response["body_sha256"] is None, "%s body_sha256 must be null when body_text is null" % path)
        require(response["bytes_received"] == 0, "%s bytes_received must be 0 when body_text is null" % path)
        require(not response["credential_redacted"], "%s null body cannot be credential-redacted" % path)
    else:
        require(isinstance(response["body_text"], str), "%s.raw_response.body_text must be a string or null" % path)
        require(isinstance(response["body_sha256"], str) and re.fullmatch(r"[a-f0-9]{64}", response["body_sha256"]) is not None, "%s raw body requires a SHA-256" % path)
        encoded_body = response["body_text"].encode("utf-8")
        actual_hash = hashlib.sha256(encoded_body).hexdigest()
        require(response["body_sha256"] == actual_hash, "%s.raw_response.body_sha256 does not match body_text" % path)
        if not response["credential_redacted"]:
            if response["truncated"]:
                require(response["bytes_received"] >= len(encoded_body), "%s truncated body cannot exceed bytes_received" % path)
            else:
                require(response["bytes_received"] == len(encoded_body), "%s bytes_received does not match complete body_text" % path)

    usage = value["usage"]
    usage_keys = {"source", "input_tokens", "output_tokens", "total_tokens"}
    require_exact_keys(usage, usage_keys, usage_keys, path + ".usage")
    require(usage["source"] in {"provider", "reconciled", "unavailable"}, "%s.usage.source is invalid" % path)
    token_values = [usage["input_tokens"], usage["output_tokens"], usage["total_tokens"]]
    for token in token_values:
        require(token is None or (isinstance(token, int) and not isinstance(token, bool) and token >= 0), "%s usage values must be null or non-negative integers" % path)
    if usage["source"] == "unavailable":
        require(all(token is None for token in token_values), "%s unavailable usage must contain only null counts" % path)
    if all(token is not None for token in token_values):
        require(usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"], "%s total_tokens must equal input_tokens + output_tokens" % path)

    error = value["error"]
    if value["status"] == "succeeded":
        require(error is None, "%s successful result must have error=null" % path)
        require(isinstance(response["http_status"], int) and 200 <= response["http_status"] <= 299, "%s successful result requires a 2xx status" % path)
    else:
        error_keys = {"category", "code", "phase", "message", "retryable", "http_status", "provider_code"}
        require_exact_keys(error, error_keys, error_keys, path + ".error")
        require(error["category"] in ERROR_CATEGORIES, "%s.error.category is invalid" % path)
        require(error["phase"] in ERROR_PHASES, "%s.error.phase is invalid" % path)
        require(re.fullmatch(r"[A-Z][A-Z0-9_]*(\.[A-Z0-9_]+)*", error["code"]) is not None, "%s.error.code is invalid" % path)
        require(isinstance(error["message"], str) and bool(error["message"]), "%s.error.message is required" % path)
        require(isinstance(error["retryable"], bool), "%s.error.retryable must be boolean" % path)
        if value["status"] == "cancelled":
            require(error["category"] == "cancelled", "%s cancelled result requires cancelled error category" % path)


def load_json(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main():
    schema_paths = sorted(CONTRACTS.glob("*.schema.json"))
    valid_paths = sorted(EXAMPLES.glob("*.valid.json"))
    invalid_paths = sorted((EXAMPLES / "invalid").glob("*.invalid.json"))
    require(len(schema_paths) == 2, "expected exactly two v1 schema files")
    require(valid_paths, "no valid fixtures found")
    require(invalid_paths, "no invalid fixtures found")

    for path in schema_paths:
        schema = load_json(path)
        require(schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema", "%s uses an unexpected JSON Schema draft" % path.name)

    accepted = 0
    for path in valid_paths:
        value = load_json(path)
        if value.get("contract_version") == "performance-request.v1":
            validate_request(value)
        else:
            validate_result(value)
        accepted += 1
        print("PASS valid   %s" % path.relative_to(ROOT))

    rejected = 0
    for path in invalid_paths:
        value = load_json(path)
        try:
            if value.get("contract_version") == "performance-request.v1":
                validate_request(value)
            else:
                validate_result(value)
        except ContractError as exc:
            rejected += 1
            print("PASS invalid %s -> %s" % (path.relative_to(ROOT), exc))
        else:
            raise ContractError("invalid fixture was accepted: %s" % path.relative_to(ROOT))

    streaming_success = load_json(EXAMPLES / "result.streaming-success.valid.json")
    standard_success = load_json(EXAMPLES / "result.standard-success.valid.json")
    streaming_without_ttft = copy.deepcopy(streaming_success)
    streaming_without_ttft["timing"]["first_content_ms"] = None
    standard_with_ttft = copy.deepcopy(standard_success)
    standard_with_ttft["timing"]["first_content_ms"] = 400
    inconsistent_usage = copy.deepcopy(streaming_success)
    inconsistent_usage["usage"]["total_tokens"] = 999
    mutation_cases = [
        ("streaming success without TTFT", streaming_without_ttft),
        ("standard success with TTFT", standard_with_ttft),
        ("inconsistent token total", inconsistent_usage),
    ]
    mutation_rejected = 0
    for name, value in mutation_cases:
        try:
            validate_result(value)
        except ContractError as exc:
            mutation_rejected += 1
            print("PASS mutation %s -> %s" % (name, exc))
        else:
            raise ContractError("invalid mutation was accepted: %s" % name)

    print(
        "\nContract verification passed: %d valid accepted, %d invalid fixtures rejected, "
        "%d invalid mutations rejected, %d schemas parsed."
        % (accepted, rejected, mutation_rejected, len(schema_paths))
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ContractError, json.JSONDecodeError, OSError) as exc:
        print("FAIL: %s" % exc, file=sys.stderr)
        sys.exit(1)
