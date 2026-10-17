"""Sequential repeat executor for performance samples.

Runs the same request configuration against one provider multiple times,
storing each result under a single run. Samples execute one at a time so
that network contention cannot skew latency measurements.

Each sample is an independent attempt with its own request_id and
sample_index. Per-sample provider/transport errors are recorded as failed
result documents; the run only transitions to ``failed`` when the executor
itself cannot continue (for example the store rejects a document).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from api_observatory.config import ProviderDefinition, ResolvedProvider
from api_observatory.metrics import RunMetrics, compute_run_metrics
from api_observatory.providers.openai_compatible import (
    ChatMessage,
    CompletionParameters,
    OpenAICompatibleAdapter,
    ProviderCallError,
    StandardCompletionRequest,
)
from api_observatory.results import (
    build_failure_document,
    build_request_snapshot,
    build_success_document,
    new_request_id,
    new_result_id,
    new_run_id,
)
from api_observatory.storage import ResultStore
from api_observatory.timing import Clock, SystemClock

logger = logging.getLogger(__name__)

WORKER_ID = "sequential-worker"
WORKER_VERSION = "sequential-worker@0.1.0"


class RunPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: ProviderDefinition
    resolved: ResolvedProvider
    model: str
    mode: Literal["standard", "streaming"]
    messages: tuple[ChatMessage, ...] = Field(min_length=1)
    parameters: CompletionParameters = Field(default_factory=CompletionParameters)
    samples: int = Field(default=1, ge=1, le=10_000)
    metadata: dict[str, object] | None = None


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    total_samples: int
    succeeded: int
    failed: int
    cancelled: int
    metrics: RunMetrics | None = None


class _SampleRunner:
    def __init__(
        self,
        *,
        adapter: OpenAICompatibleAdapter,
        plan: RunPlan,
        run_id: str,
    ) -> None:
        self._adapter = adapter
        self._plan = plan
        self._run_id = run_id

    def _build_snapshot(self, sample_index: int, created_at: datetime) -> dict:
        return build_request_snapshot(
            request_id=new_request_id(),
            run_id=self._run_id,
            sample_index=sample_index,
            provider=self._plan.provider,
            model=self._plan.model,
            mode=self._plan.mode,
            messages=self._plan.messages,
            parameters=self._plan.parameters,
            created_at=created_at,
            metadata=self._plan.metadata,
        )

    async def execute(self, sample_index: int) -> dict:
        created_at = datetime.now(UTC)
        snapshot = self._build_snapshot(sample_index, created_at)
        result_id = new_result_id()
        request = StandardCompletionRequest(
            messages=self._plan.messages,
            parameters=self._plan.parameters,
            model=self._plan.model,
        )

        try:
            if self._plan.mode == "standard":
                outcome = await self._adapter.complete_standard(self._plan.resolved, request)
            else:
                outcome = await self._adapter.complete_streaming(self._plan.resolved, request)
        except ProviderCallError as error:
            logger.warning("Sample %d failed: %s (%s)", sample_index, error.code, error.category)
            return build_failure_document(
                result_id=result_id,
                request_snapshot=snapshot,
                error=error,
                attempt=1,
                worker_id=WORKER_ID,
                worker_version=WORKER_VERSION,
            )

        return build_success_document(
            result_id=result_id,
            request_snapshot=snapshot,
            outcome=outcome,
            attempt=1,
            worker_id=WORKER_ID,
            worker_version=WORKER_VERSION,
        )


class RunExecutor:
    """Execute a RunPlan sequentially and persist each result."""

    def __init__(
        self,
        *,
        store: ResultStore,
        client: httpx.AsyncClient | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or SystemClock()
        self._client = client

    async def run(self, plan: RunPlan, *, run_id: str | None = None) -> RunSummary:
        actual_run_id = run_id or new_run_id()
        self._store.create_run(actual_run_id, metadata=plan.metadata)
        self._store.set_run_status(
            actual_run_id,
            "running",
            started_at=self._clock.utc_now().isoformat().replace("+00:00", "Z"),
        )

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(trust_env=False)
        adapter = OpenAICompatibleAdapter(client, clock=self._clock)
        runner = _SampleRunner(adapter=adapter, plan=plan, run_id=actual_run_id)

        succeeded = 0
        failed = 0
        run_status: Literal["completed", "failed"] = "completed"

        try:
            for index in range(1, plan.samples + 1):
                document = await runner.execute(index)
                try:
                    self._store.insert_result(document)
                except Exception:
                    logger.exception("Store rejected result document for sample %d", index)
                    run_status = "failed"
                    break
                status = document["status"]
                if status == "succeeded":
                    succeeded += 1
                elif status == "failed":
                    failed += 1
        except Exception:
            run_status = "failed"
            logger.exception("Run %s aborted by an unexpected error", actual_run_id)
        finally:
            if owns_client:
                await client.aclose()

        completed_at = self._clock.utc_now().isoformat().replace("+00:00", "Z")
        self._store.set_run_status(actual_run_id, run_status, completed_at=completed_at)

        results = self._store.list_results(actual_run_id)
        metrics = compute_run_metrics(results, run_id=actual_run_id) if results else None

        return RunSummary(
            run_id=actual_run_id,
            status=run_status,
            total_samples=plan.samples,
            succeeded=succeeded,
            failed=failed,
            cancelled=0,
            metrics=metrics,
        )
