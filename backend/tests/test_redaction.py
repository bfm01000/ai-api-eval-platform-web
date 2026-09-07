import logging
import sys

from pydantic import SecretStr

from api_observatory.redaction import REDACTED, RedactingFormatter, redact_text, redact_value


def test_redact_value_masks_sensitive_keys_and_known_secret() -> None:
    secret = SecretStr("provider-secret-value")
    payload = {
        "headers": {
            "Authorization": "Bearer provider-secret-value",
            "x_api_key": "provider-secret-value",
            "content-type": "application/json",
        },
        "error": "upstream echoed provider-secret-value",
        "nested": [{"access_token": "another-secret"}],
    }

    redacted = redact_value(payload, [secret])

    assert redacted["headers"]["Authorization"] == REDACTED
    assert redacted["headers"]["x_api_key"] == REDACTED
    assert redacted["headers"]["content-type"] == "application/json"
    assert redacted["error"] == f"upstream echoed {REDACTED}"
    assert redacted["nested"][0]["access_token"] == REDACTED


def test_redact_text_masks_assignments_and_bearer_tokens() -> None:
    message = "api_key=abc123 authorization: Bearer token.123 cookie=safe-to-ignore"

    redacted = redact_text(message)

    assert "abc123" not in redacted
    assert "token.123" not in redacted
    assert "cookie=safe-to-ignore" in redacted


def test_logging_formatter_redacts_message_arguments_and_exception() -> None:
    secret = "provider-secret-value"
    formatter = RedactingFormatter("%(levelname)s %(message)s", secrets=[secret])
    try:
        raise RuntimeError(f"upstream echoed {secret}")
    except RuntimeError:
        exc_info = sys.exc_info()
    record = logging.LogRecord(
        name="test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="request failed with key %s",
        args=(secret,),
        exc_info=exc_info,
    )

    output = formatter.format(record)

    assert secret not in output
    assert f"ERROR request failed with key {REDACTED}" in output
    assert f"RuntimeError: upstream echoed {REDACTED}" in output
