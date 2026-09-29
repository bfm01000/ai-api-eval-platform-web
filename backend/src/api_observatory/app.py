import ipaddress
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import httpx
from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr, field_validator

from api_observatory import __version__
from api_observatory.config import (
    AppSettings,
    CredentialResolutionError,
    ProviderCatalog,
    ProviderConfigError,
    ProviderDefinition,
    ResolvedProvider,
    load_provider_catalog,
    resolve_provider,
)
from api_observatory.providers.openai_compatible import (
    ChatMessage,
    CompletionParameters,
    OpenAICompatibleAdapter,
    ProviderCallError,
    StandardCompletionRequest,
    TokenUsage,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = BACKEND_ROOT.parent


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok"]
    service: str
    version: str
    contract_versions: tuple[str, ...]


class ProviderOption(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_id: str
    display_name: str
    endpoint: str
    default_model: str


class ProviderTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str = Field(min_length=1, max_length=64)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    mode: Literal["standard", "streaming"] = "standard"


class ProviderTestResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_id: str
    display_name: str
    model: str
    mode: Literal["standard", "streaming"]
    content: str
    total_latency_ms: float
    ttft_ms: float | None
    tokens_per_second: float | None
    usage: TokenUsage | None


class TestConfigResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: Literal["local", "public"]
    providers: tuple[ProviderOption, ...]


class SelfTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    endpoint: AnyHttpUrl
    api_key: SecretStr = Field(min_length=1, max_length=1000, repr=False)
    model: str = Field(min_length=1, max_length=200)
    mode: Literal["standard", "streaming"] = "standard"

    @field_validator("api_key")
    @classmethod
    def api_key_must_not_be_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("api_key must not be blank")
        return value

    @field_validator("endpoint")
    @classmethod
    def endpoint_must_be_public_https(cls, endpoint: AnyHttpUrl) -> AnyHttpUrl:
        if endpoint.scheme != "https":
            raise ValueError("endpoint must use HTTPS")

        host = endpoint.host.lower().rstrip(".")
        blocked_host_suffixes = (".localhost", ".local", ".internal", ".home.arpa")
        if host in {"localhost", "metadata.google.internal"} or host.endswith(
            blocked_host_suffixes
        ):
            raise ValueError("endpoint must not target a local or internal hostname")

        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return endpoint
        if not address.is_global:
            raise ValueError("endpoint must not target a local or reserved IP address")
        return endpoint


def create_app(
    *,
    settings: AppSettings | None = None,
    catalog: ProviderCatalog | None = None,
    environment: Mapping[str, str] | None = None,
    provider_client: httpx.AsyncClient | None = None,
    frontend_root: Path = FRONTEND_ROOT,
    public_mode: bool | None = None,
) -> FastAPI:
    application = FastAPI(
        title="AI API Observatory",
        summary="Reproducible AI API service quality evaluation.",
        version=__version__,
    )

    active_settings = settings or AppSettings(_env_file=BACKEND_ROOT / ".env")
    is_public_mode = active_settings.public_mode if public_mode is None else public_mode

    def require_local_mode() -> None:
        if is_public_mode:
            raise HTTPException(
                status_code=404,
                detail="Configured Provider tests are available only in local mode.",
            )

    def configured_catalog() -> ProviderCatalog:
        if catalog is not None:
            return catalog
        config_path = active_settings.provider_config_path
        if not config_path.is_absolute():
            config_path = BACKEND_ROOT / config_path
        try:
            return load_provider_catalog(config_path)
        except ProviderConfigError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None

    def configured_environment() -> Mapping[str, str]:
        if environment is not None:
            return environment
        local_values = {
            key: value
            for key, value in dotenv_values(BACKEND_ROOT / ".env").items()
            if value is not None
        }
        return {**local_values, **os.environ}

    @application.get("/health", tags=["system"], response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            service="api-observatory-backend",
            version=__version__,
            contract_versions=("performance-request.v1", "performance-result.v1"),
        )

    @application.get("/api/test-config", tags=["providers"], response_model=TestConfigResponse)
    async def test_config() -> TestConfigResponse:
        if is_public_mode:
            return TestConfigResponse(mode="public", providers=())
        return TestConfigResponse(
            mode="local",
            providers=tuple(
                ProviderOption(
                    provider_id=provider.provider_id,
                    display_name=provider.display_name,
                    endpoint=str(provider.endpoint),
                    default_model=provider.default_model,
                )
                for provider in configured_catalog().providers
            ),
        )

    @application.get("/api/providers", tags=["providers"], response_model=list[ProviderOption])
    async def list_providers() -> list[ProviderOption]:
        require_local_mode()
        return [
            ProviderOption(
                provider_id=provider.provider_id,
                display_name=provider.display_name,
                endpoint=str(provider.endpoint),
                default_model=provider.default_model,
            )
            for provider in configured_catalog().providers
        ]

    @application.post(
        "/api/provider-tests",
        tags=["providers"],
        response_model=ProviderTestResponse,
    )
    async def test_provider(request: ProviderTestRequest) -> ProviderTestResponse:
        require_local_mode()
        active_catalog = configured_catalog()
        try:
            definition = active_catalog.get(request.provider_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Provider not found.") from None
        try:
            provider = resolve_provider(definition, configured_environment())
        except CredentialResolutionError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None

        return await execute_provider_test(
            provider,
            provider_id=definition.provider_id,
            display_name=definition.display_name,
            model=request.model or definition.default_model,
            mode=request.mode,
        )

    @application.post(
        "/api/self-tests",
        tags=["providers"],
        response_model=ProviderTestResponse,
    )
    async def test_self_provider(request: SelfTestRequest) -> ProviderTestResponse:
        try:
            definition = ProviderDefinition(
                provider_id="self_supplied",
                display_name="Your API Key",
                endpoint=request.endpoint,
                protocol="openai-chat-completions",
                credential_ref="env:USER_SUBMITTED_API_KEY",
                adapter="openai-compatible",
                default_model=request.model,
                timeout_ms=60000,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        provider = ResolvedProvider(definition=definition, api_key=request.api_key)
        return await execute_provider_test(
            provider,
            provider_id="self_supplied",
            display_name="Your API Key",
            model=request.model,
            mode=request.mode,
        )

    async def execute_provider_test(
        provider: ResolvedProvider,
        *,
        provider_id: str,
        display_name: str,
        model: str,
        mode: Literal["standard", "streaming"],
    ) -> ProviderTestResponse:
        owns_client = provider_client is None
        client = provider_client or httpx.AsyncClient(trust_env=False)
        adapter = OpenAICompatibleAdapter(client)
        completion_request = StandardCompletionRequest(
            model=model,
            messages=(ChatMessage(role="user", content="Reply with only OK."),),
            parameters=CompletionParameters(temperature=0, max_output_tokens=16),
        )
        try:
            if mode == "streaming":
                result = await adapter.complete_streaming(provider, completion_request)
                tokens_per_second = result.tokens_per_second
            else:
                result = await adapter.complete_standard(provider, completion_request)
                tokens_per_second = None
        except ProviderCallError as exc:
            status_code = 504 if exc.category == "timeout" else 502
            raise HTTPException(
                status_code=status_code,
                detail={
                    "code": exc.code,
                    "category": exc.category,
                    "message": exc.message,
                    "http_status": exc.http_status,
                    "total_latency_ms": exc.timing.total_latency_ms,
                },
            ) from None
        finally:
            if owns_client:
                await client.aclose()

        return ProviderTestResponse(
            provider_id=provider_id,
            display_name=display_name,
            model=result.model or completion_request.model or model,
            mode=mode,
            content=result.content,
            total_latency_ms=result.timing.total_latency_ms,
            ttft_ms=result.timing.first_content_ms,
            tokens_per_second=tokens_per_second,
            usage=result.usage,
        )

    @application.get("/", include_in_schema=False, response_class=FileResponse)
    async def frontend() -> FileResponse:
        return FileResponse(frontend_root / "index.html")

    @application.get("/styles.css", include_in_schema=False, response_class=FileResponse)
    async def frontend_styles() -> FileResponse:
        return FileResponse(frontend_root / "styles.css", media_type="text/css")

    @application.get("/app.js", include_in_schema=False, response_class=FileResponse)
    async def frontend_script() -> FileResponse:
        return FileResponse(frontend_root / "app.js", media_type="text/javascript")

    return application


app = create_app()
