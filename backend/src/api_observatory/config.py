import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderConfigError(ValueError):
    """Raised when provider metadata cannot be loaded safely."""


class CredentialResolutionError(ValueError):
    """Raised when a provider credential reference cannot be resolved."""


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="API_OBSERVATORY_",
        extra="ignore",
        frozen=True,
    )

    provider_config_path: Path = Path("config/providers.toml")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"


class ProviderDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$")
    display_name: str = Field(min_length=1, max_length=120)
    endpoint: AnyHttpUrl
    protocol: Literal["openai-chat-completions"]
    credential_ref: str = Field(pattern=r"^env:[A-Za-z_][A-Za-z0-9_]*$")
    adapter: Literal["openai-compatible"]
    default_model: str = Field(min_length=1, max_length=200)
    timeout_ms: int = Field(default=30000, ge=100, le=600000)

    @field_validator("endpoint")
    @classmethod
    def endpoint_must_not_contain_credentials(cls, endpoint: AnyHttpUrl) -> AnyHttpUrl:
        if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise ValueError(
                "endpoint must not contain credentials, query parameters, or fragments"
            )
        local_hosts = {"localhost", "127.0.0.1", "[::1]"}
        if endpoint.scheme != "https" and endpoint.host not in local_hosts:
            raise ValueError("non-local provider endpoints must use HTTPS")
        return endpoint


class ProviderCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["provider-config.v1"]
    providers: tuple[ProviderDefinition, ...] = Field(min_length=1)

    def get(self, provider_id: str) -> ProviderDefinition:
        for provider in self.providers:
            if provider.provider_id == provider_id:
                return provider
        raise KeyError(provider_id)


class ResolvedProvider(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    definition: ProviderDefinition
    api_key: SecretStr = Field(repr=False)


def _validation_locations(error: ValidationError) -> str:
    locations = {
        ".".join(str(part) for part in issue["loc"])
        for issue in error.errors(include_input=False, include_url=False)
    }
    return ", ".join(sorted(locations))


def load_provider_catalog(path: Path) -> ProviderCatalog:
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except FileNotFoundError:
        raise ProviderConfigError(f"Provider config file not found: {path}") from None
    except (OSError, tomllib.TOMLDecodeError):
        raise ProviderConfigError(f"Provider config file cannot be read: {path}") from None

    try:
        catalog = ProviderCatalog.model_validate(document)
    except ValidationError as exc:
        locations = _validation_locations(exc)
        raise ProviderConfigError(f"Invalid provider config fields: {locations}") from None

    provider_ids = [provider.provider_id for provider in catalog.providers]
    duplicates = sorted(
        {provider_id for provider_id in provider_ids if provider_ids.count(provider_id) > 1}
    )
    if duplicates:
        raise ProviderConfigError(f"Duplicate provider ids: {', '.join(duplicates)}")
    return catalog


def resolve_provider(
    definition: ProviderDefinition,
    environment: Mapping[str, str] | None = None,
) -> ResolvedProvider:
    source = os.environ if environment is None else environment
    reference_type, variable_name = definition.credential_ref.split(":", maxsplit=1)
    if reference_type != "env":
        raise CredentialResolutionError(
            f"Unsupported credential reference type for provider {definition.provider_id}"
        )

    api_key = source.get(variable_name)
    if not api_key:
        raise CredentialResolutionError(
            f"Credential environment variable is missing for provider {definition.provider_id}: "
            f"{variable_name}"
        )
    return ResolvedProvider(definition=definition, api_key=SecretStr(api_key))
