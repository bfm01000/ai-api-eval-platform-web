import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import SecretStr

REDACTED = "[REDACTED]"
SENSITIVE_KEYS = {
    "authorization",
    "proxy-authorization",
    "api-key",
    "x-api-key",
    "cookie",
    "set-cookie",
    "access-token",
}
SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(authorization|proxy-authorization|api[-_]?key|x-api-key|access[-_]?token)"
    r"(\s*[:=]\s*)(?:bearer\s+)?([^\s,;&]+)"
)
BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")


def _normalized_key(key: object) -> str:
    return str(key).strip().lower().replace("_", "-")


def _secret_values(secrets: Iterable[str | SecretStr]) -> tuple[str, ...]:
    values = []
    for secret in secrets:
        value = secret.get_secret_value() if isinstance(secret, SecretStr) else secret
        if len(value) >= 4:
            values.append(value)
    return tuple(sorted(set(values), key=len, reverse=True))


def redact_text(text: str, secrets: Iterable[str | SecretStr] = ()) -> str:
    redacted = text
    for secret in _secret_values(secrets):
        redacted = redacted.replace(secret, REDACTED)
    redacted = SENSITIVE_ASSIGNMENT.sub(r"\1\2[REDACTED]", redacted)
    return BEARER_TOKEN.sub(f"Bearer {REDACTED}", redacted)


def redact_value(value: Any, secrets: Iterable[str | SecretStr] = ()) -> Any:
    secret_values = _secret_values(secrets)
    if isinstance(value, Mapping):
        return {
            key: REDACTED
            if _normalized_key(key) in SENSITIVE_KEYS
            else redact_value(child, secret_values)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [redact_value(child, secret_values) for child in value]
    if isinstance(value, tuple):
        return tuple(redact_value(child, secret_values) for child in value)
    if isinstance(value, str):
        return redact_text(value, secret_values)
    if isinstance(value, SecretStr):
        return REDACTED
    return value


def redact_headers(
    headers: Mapping[str, str], secrets: Iterable[str | SecretStr] = ()
) -> dict[str, str]:
    secret_values = _secret_values(secrets)
    return {
        key: REDACTED
        if _normalized_key(key) in SENSITIVE_KEYS
        else redact_text(value, secret_values)
        for key, value in headers.items()
    }


class RedactingFormatter(logging.Formatter):
    def __init__(self, fmt: str, *, secrets: Iterable[str | SecretStr] = ()) -> None:
        super().__init__(fmt)
        self._secrets = _secret_values(secrets)

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record), self._secrets)
