import asyncio
import os
from collections.abc import Mapping
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import httpx
from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from api_observatory import __version__
from api_observatory.benchmark import (
    BENCHMARK_SHA256,
    BENCHMARK_VERSION,
    CASE_BY_ID,
    CASES,
    build_report,
    estimated_max_cny,
)
from api_observatory.comparison import ComparisonReport, compare_providers
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
from api_observatory.egress import PublicHTTPTransport, validate_public_endpoint
from api_observatory.guard import PublicGuard
from api_observatory.insights import InsightWindow, build_insights
from api_observatory.providers.openai_compatible import (
    ChatMessage,
    CompletionParameters,
    OpenAICompatibleAdapter,
    ProviderCallError,
    StandardCompletionRequest,
    TokenUsage,
)
from api_observatory.redaction import redact_text
from api_observatory.results import new_run_id
from api_observatory.storage import DEFAULT_DB_PATH, ResultStore
from api_observatory.worker import RunExecutor, RunPlan, RunSummary

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


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str = Field(min_length=1, max_length=64)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    mode: Literal["standard", "streaming"] = "standard"
    samples: int = Field(default=3, ge=1, le=100)


class TestConfigResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: Literal["local", "public"]
    providers: tuple[ProviderOption, ...]


class SelfTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    endpoint: AnyHttpUrl
    api_key: SecretStr = Field(min_length=4, max_length=1000, repr=False)
    model: str = Field(min_length=1, max_length=200)
    mode: Literal["standard", "streaming"] = "standard"

    @field_validator("api_key")
    @classmethod
    def api_key_must_not_be_blank(cls, value: SecretStr) -> SecretStr:
        if any(not 33 <= ord(char) <= 126 for char in value.get_secret_value()):
            raise ValueError("api_key must contain visible ASCII characters only")
        return value

    @field_validator("endpoint")
    @classmethod
    def endpoint_must_be_public_https(cls, endpoint: AnyHttpUrl) -> AnyHttpUrl:
        return validate_public_endpoint(endpoint)


class SelfRunCreateRequest(SelfTestRequest):
    samples: int = Field(default=1, ge=1, le=3)


class LocalComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_ids: tuple[str, ...] = Field(min_length=2, max_length=5)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    mode: Literal["standard", "streaming"] = "standard"
    samples: int = Field(default=1, ge=1, le=3)

    @model_validator(mode="after")
    def unique_providers(self):
        if len(set(self.provider_ids)) != len(self.provider_ids):
            raise ValueError("provider_ids must be unique")
        return self


class SelfComparisonTarget(SelfTestRequest):
    display_name: str = Field(min_length=1, max_length=60)


class SelfComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    targets: tuple[SelfComparisonTarget, ...] = Field(min_length=2, max_length=3)
    mode: Literal["standard", "streaming"] = "standard"
    samples: int = Field(default=1, ge=1, le=1)


class BenchmarkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str = Field(min_length=1, max_length=64)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    samples: int = Field(default=1, ge=1, le=3)
    input_cny_per_million: Decimal = Field(gt=0, le=100000)
    output_cny_per_million: Decimal = Field(gt=0, le=100000)
    max_budget_cny: Decimal = Field(gt=0, le=100)


class ReferenceAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    answer: str = Field(min_length=1, max_length=10000)


class ReferenceImport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    benchmark_version: Literal["capability-core.v1"]
    benchmark_sha256: str
    reference_model: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=500)
    answers: tuple[ReferenceAnswer, ...] = Field(min_length=1, max_length=len(CASES))

    @model_validator(mode="after")
    def validate_dataset(self):
        ids = [answer.case_id for answer in self.answers]
        if self.benchmark_sha256 != BENCHMARK_SHA256 or len(ids) != len(set(ids)):
            raise ValueError("Reference dataset version or case IDs are invalid")
        if set(ids) - CASE_BY_ID.keys():
            raise ValueError("Unknown benchmark case ID")
        return self


def create_app(
    *,
    settings: AppSettings | None = None,
    catalog: ProviderCatalog | None = None,
    environment: Mapping[str, str] | None = None,
    provider_client: httpx.AsyncClient | None = None,
    store: ResultStore | None = None,
    frontend_root: Path = FRONTEND_ROOT,
    public_mode: bool | None = None,
) -> FastAPI:
    active_settings = settings or AppSettings(_env_file=BACKEND_ROOT / ".env")
    is_public_mode = active_settings.public_mode if public_mode is None else public_mode
    active_store = store
    benchmark_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            if store is None and active_store is not None:
                active_store.close()

    application = FastAPI(
        title="AI API Observatory",
        summary="Reproducible AI API service quality evaluation.",
        version=__version__,
        lifespan=lifespan,
    )
    application.add_middleware(
        PublicGuard, settings=active_settings.model_copy(update={"public_mode": is_public_mode})
    )

    @application.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, _error: RequestValidationError):
        # Pydantic's errors include raw input, even for SecretStr and unknown fields.
        return JSONResponse(
            status_code=422,
            content={"detail": "参数无效，请检查 HTTPS 地址、Key、模型和样本数（1–3）。"},
        )

    def local_store() -> ResultStore:
        nonlocal active_store
        require_local_mode()
        if active_store is None:
            active_store = ResultStore.open(DEFAULT_DB_PATH)
        return active_store

    @asynccontextmanager
    async def self_client():
        if provider_client is not None:
            yield provider_client
        else:
            async with httpx.AsyncClient(
                transport=PublicHTTPTransport(), trust_env=False, follow_redirects=False
            ) as client:
                yield client

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
        except ValueError:
            raise HTTPException(status_code=422, detail="测试参数无效。") from None
        provider = ResolvedProvider(definition=definition, api_key=request.api_key)
        return await execute_provider_test(
            provider,
            provider_id="self_supplied",
            display_name="Your API Key",
            model=request.model,
            mode=request.mode,
        )

    @application.post(
        "/api/runs",
        tags=["runs"],
        response_model=RunSummary,
    )
    async def create_run(request: RunCreateRequest) -> RunSummary:
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

        plan = RunPlan(
            provider=definition,
            resolved=provider,
            model=request.model or definition.default_model,
            mode=request.mode,
            messages=(ChatMessage(role="user", content="Reply with only OK."),),
            parameters=CompletionParameters(temperature=0, max_output_tokens=16),
            samples=request.samples,
        )
        executor = RunExecutor(store=local_store(), client=provider_client)
        return await executor.run(plan)

    @application.post(
        "/api/self-runs",
        tags=["runs"],
        response_model=RunSummary,
    )
    async def create_self_run(request: SelfRunCreateRequest) -> RunSummary:
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
        except ValueError:
            raise HTTPException(status_code=422, detail="测试参数无效。") from None
        provider = ResolvedProvider(definition=definition, api_key=request.api_key)
        plan = RunPlan(
            provider=definition,
            resolved=provider,
            model=request.model,
            mode=request.mode,
            messages=(ChatMessage(role="user", content="Reply with only OK."),),
            parameters=CompletionParameters(temperature=0, max_output_tokens=16),
            samples=request.samples,
        )
        # Self-supplied runs never share the local database or survive the request.
        with ResultStore.open(Path(":memory:")) as temporary_store:
            async with self_client() as client:
                executor = RunExecutor(store=temporary_store, client=client)
                return await executor.run(plan)

    @application.post("/api/comparisons", tags=["runs"], response_model=ComparisonReport)
    async def create_local_comparison(request: LocalComparisonRequest) -> ComparisonReport:
        require_local_mode()
        active_catalog = configured_catalog()
        targets = []
        for provider_id in request.provider_ids:
            try:
                definition = active_catalog.get(provider_id)
            except KeyError:
                raise HTTPException(status_code=404, detail="Provider not found.") from None
            try:
                resolved = resolve_provider(definition, configured_environment())
            except CredentialResolutionError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from None
            targets.append(
                (definition.display_name, resolved, request.model or definition.default_model)
            )
        if provider_client is None:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
                return await compare_providers(
                    tuple(targets),
                    mode=request.mode,
                    samples=request.samples,
                    store=local_store(),
                    client=client,
                )
        return await compare_providers(
            tuple(targets),
            mode=request.mode,
            samples=request.samples,
            store=local_store(),
            client=provider_client,
        )

    @application.post("/api/self-comparisons", tags=["runs"], response_model=ComparisonReport)
    async def create_self_comparison(request: SelfComparisonRequest) -> ComparisonReport:
        targets = []
        for index, target in enumerate(request.targets, start=1):
            try:
                definition = ProviderDefinition(
                    provider_id=f"self_{index}",
                    display_name=target.display_name,
                    endpoint=target.endpoint,
                    protocol="openai-chat-completions",
                    credential_ref="env:USER_SUBMITTED_API_KEY",
                    adapter="openai-compatible",
                    default_model=target.model,
                    timeout_ms=20000,
                )
            except ValueError:
                raise HTTPException(status_code=422, detail="测试参数无效。") from None
            targets.append(
                (
                    target.display_name,
                    ResolvedProvider(definition=definition, api_key=target.api_key),
                    target.model,
                )
            )
        with ResultStore.open(Path(":memory:")) as temporary_store:
            async with self_client() as client:
                return await compare_providers(
                    tuple(targets),
                    mode=request.mode,
                    samples=request.samples,
                    store=temporary_store,
                    client=client,
                )

    @application.get("/api/runs", tags=["runs"])
    async def get_runs() -> list[dict[str, Any]]:
        return local_store().list_runs()

    @application.get("/api/insights", tags=["runs"])
    async def get_insights(window: InsightWindow = "24h") -> dict[str, Any]:
        require_local_mode()
        return build_insights(local_store(), configured_catalog().providers, window)

    @application.get("/api/benchmark", tags=["benchmark"])
    async def benchmark_catalog() -> dict[str, Any]:
        require_local_mode()
        return {
            "version": BENCHMARK_VERSION,
            "sha256": BENCHMARK_SHA256,
            "cases": [case.__dict__ for case in CASES],
            "reference": local_store().get_reference_set(),
        }

    @application.post("/api/benchmark/reference", tags=["benchmark"])
    async def import_reference(request: ReferenceImport) -> dict[str, Any]:
        require_local_mode()
        local_store().save_reference_set(request.model_dump())
        return {"imported": len(request.answers), "identity_verified": False}

    @application.post("/api/benchmark/estimate", tags=["benchmark"])
    async def benchmark_estimate(request: BenchmarkRequest) -> dict[str, Any]:
        require_local_mode()
        try:
            configured_catalog().get(request.provider_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Provider not found.") from None
        estimate = estimated_max_cny(
            samples=request.samples,
            input_cny_per_million=request.input_cny_per_million,
            output_cny_per_million=request.output_cny_per_million,
        )
        return {
            "requests": len(CASES) * request.samples,
            "estimated_max_cny": str(estimate),
            "within_budget": estimate <= request.max_budget_cny,
            "pricing_verified": False,
        }

    @application.post("/api/benchmark/runs", tags=["benchmark"])
    async def run_benchmark(request: BenchmarkRequest) -> dict[str, Any]:
        require_local_mode()
        try:
            definition = configured_catalog().get(request.provider_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Provider not found.") from None
        try:
            provider = resolve_provider(definition, configured_environment())
        except CredentialResolutionError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        estimate = estimated_max_cny(
            samples=request.samples,
            input_cny_per_million=request.input_cny_per_million,
            output_cny_per_million=request.output_cny_per_million,
        )
        if estimate > request.max_budget_cny:
            raise HTTPException(status_code=422, detail="预估费用超过本轮预算。")
        async with benchmark_lock:
            active = local_store()
            reservations = {
                run["metadata"]["benchmark_batch_id"]: Decimal(run["metadata"]["estimated_max_cny"])
                for run in active.list_runs()
                if "benchmark_batch_id" in run.get("metadata", {})
            }
            if sum(reservations.values(), Decimal(0)) + estimate > Decimal(100):
                raise HTTPException(status_code=422, detail="本机累计评测预估已达 100 元上限。")
            batch_id = new_run_id()
            if provider_client is None:
                client = httpx.AsyncClient(trust_env=False, follow_redirects=False)
            else:
                client = provider_client
            try:
                executor = RunExecutor(store=active, client=client)
                for case in CASES:
                    plan = RunPlan(
                        provider=definition,
                        resolved=provider,
                        model=request.model or definition.default_model,
                        mode="standard",
                        messages=(ChatMessage(role="user", content=case.prompt),),
                        parameters=CompletionParameters(temperature=0, max_output_tokens=64),
                        samples=request.samples,
                        metadata={
                            "benchmark_batch_id": batch_id,
                            "benchmark_case_id": case.case_id,
                            "test_case_id": case.case_id,
                            "benchmark_version": BENCHMARK_VERSION,
                            "benchmark_sha256": BENCHMARK_SHA256,
                            "provider_id": definition.provider_id,
                            "model": request.model or definition.default_model,
                            "samples": request.samples,
                            "estimated_max_cny": str(estimate),
                        },
                    )
                    await executor.run(plan)
            finally:
                if provider_client is None:
                    await client.aclose()
            return build_report(active, batch_id) or {}

    @application.get("/api/benchmark/reports", tags=["benchmark"])
    async def benchmark_reports() -> list[dict[str, Any]]:
        require_local_mode()
        active = local_store()
        ids = list(
            dict.fromkeys(
                run["metadata"]["benchmark_batch_id"]
                for run in active.list_runs()
                if "benchmark_batch_id" in run.get("metadata", {})
            )
        )
        return [report for batch_id in ids if (report := build_report(active, batch_id))]

    @application.get("/api/benchmark/reports/{batch_id}", tags=["benchmark"])
    async def benchmark_report(batch_id: str) -> dict[str, Any]:
        require_local_mode()
        report = build_report(local_store(), batch_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Benchmark report not found.")
        return report

    @application.get("/api/runs/{run_id}", tags=["runs"])
    async def get_run_by_id(run_id: str) -> dict[str, Any]:
        doc = local_store().get_run(run_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        metrics = local_store().get_run_metrics(run_id)
        return {
            "run": doc,
            "metrics": metrics.model_dump() if metrics else None,
        }

    @application.get("/api/runs/{run_id}/results", tags=["runs"])
    async def get_run_results(run_id: str) -> list[dict[str, Any]]:
        return local_store().list_results(run_id)

    async def execute_provider_test(
        provider: ResolvedProvider,
        *,
        provider_id: str,
        display_name: str,
        model: str,
        mode: Literal["standard", "streaming"],
    ) -> ProviderTestResponse:
        owns_client = provider_client is None
        client = provider_client or httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            transport=PublicHTTPTransport() if provider_id == "self_supplied" else None,
        )
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
            model=redact_text(
                result.model or completion_request.model or model, (provider.api_key,)
            ),
            mode=mode,
            content=redact_text(result.content, (provider.api_key,)),
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
