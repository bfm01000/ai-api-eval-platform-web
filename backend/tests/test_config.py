import traceback
from pathlib import Path

import pytest
from pydantic import SecretStr

from api_observatory.config import (
    AppSettings,
    CredentialResolutionError,
    ProviderConfigError,
    load_provider_catalog,
    resolve_provider,
)

VALID_CONFIG = """
schema_version = "provider-config.v1"

[[providers]]
provider_id = "northstar"
display_name = "Northstar"
endpoint = "https://api.example.com/v1/chat/completions"
protocol = "openai-chat-completions"
credential_ref = "env:NORTHSTAR_API_KEY"
adapter = "openai-compatible"
default_model = "gpt-5.2"
timeout_ms = 30000
"""


def write_config(path: Path, content: str = VALID_CONFIG) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_app_settings_read_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_OBSERVATORY_PROVIDER_CONFIG_PATH", "config/test.toml")
    monkeypatch.setenv("API_OBSERVATORY_LOG_LEVEL", "WARNING")

    settings = AppSettings(_env_file=None)

    assert settings.provider_config_path == Path("config/test.toml")
    assert settings.log_level == "WARNING"


def test_load_and_resolve_provider_without_serializing_secret(tmp_path: Path) -> None:
    catalog = load_provider_catalog(write_config(tmp_path / "providers.toml"))
    definition = catalog.get("northstar")

    resolved = resolve_provider(definition, {"NORTHSTAR_API_KEY": "test-secret-value"})

    assert str(definition.endpoint) == "https://api.example.com/v1/chat/completions"
    assert isinstance(resolved.api_key, SecretStr)
    assert resolved.api_key.get_secret_value() == "test-secret-value"
    assert "test-secret-value" not in repr(resolved)
    assert "test-secret-value" not in resolved.model_dump_json()
    assert "**********" in resolved.model_dump_json()


def test_duplicate_provider_ids_are_rejected(tmp_path: Path) -> None:
    duplicate = (
        VALID_CONFIG + "\n[[providers]]" + VALID_CONFIG.split("[[providers]]", maxsplit=1)[1]
    )

    with pytest.raises(ProviderConfigError, match="Duplicate provider ids: northstar"):
        load_provider_catalog(write_config(tmp_path / "providers.toml", duplicate))


def test_invalid_config_error_does_not_echo_input_value(tmp_path: Path) -> None:
    raw_secret = "sk-value-that-must-not-appear"
    invalid = VALID_CONFIG.replace("env:NORTHSTAR_API_KEY", raw_secret)

    with pytest.raises(ProviderConfigError) as captured:
        load_provider_catalog(write_config(tmp_path / "providers.toml", invalid))

    rendered_traceback = "".join(
        traceback.format_exception(captured.type, captured.value, captured.tb)
    )
    assert raw_secret not in str(captured.value)
    assert raw_secret not in rendered_traceback
    assert "providers.0.credential_ref" in str(captured.value)


def test_missing_credential_names_provider_and_variable_but_not_value(tmp_path: Path) -> None:
    definition = load_provider_catalog(write_config(tmp_path / "providers.toml")).providers[0]

    with pytest.raises(CredentialResolutionError) as captured:
        resolve_provider(definition, {})

    assert "northstar" in str(captured.value)
    assert "NORTHSTAR_API_KEY" in str(captured.value)
