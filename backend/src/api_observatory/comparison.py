"""Comparable sequential runs over a shared prompt and sampling plan."""

from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict

from api_observatory.config import ResolvedProvider
from api_observatory.providers.openai_compatible import ChatMessage, CompletionParameters
from api_observatory.redaction import redact_text
from api_observatory.storage import ResultStore
from api_observatory.worker import RunExecutor, RunPlan, RunSummary


class ComparisonEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider_id: str
    display_name: str
    model: str
    summary: RunSummary


class ComparisonReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    mode: Literal["standard", "streaming"]
    samples_per_provider: int
    entries: tuple[ComparisonEntry, ...]


async def compare_providers(
    providers: tuple[tuple[str, ResolvedProvider, str], ...],
    *,
    mode: Literal["standard", "streaming"],
    samples: int,
    store: ResultStore,
    client: httpx.AsyncClient,
) -> ComparisonReport:
    entries = []
    secrets = tuple(resolved.api_key for _, resolved, _ in providers)
    for display_name, resolved, model in providers:
        definition = resolved.definition
        plan = RunPlan(
            provider=definition,
            resolved=resolved,
            model=model,
            mode=mode,
            messages=(ChatMessage(role="user", content="Reply with only OK."),),
            parameters=CompletionParameters(temperature=0, max_output_tokens=16),
            samples=samples,
        )
        summary = await RunExecutor(store=store, client=client).run(plan)
        entries.append(
            ComparisonEntry(
                provider_id=definition.provider_id,
                display_name=redact_text(display_name, secrets),
                model=redact_text(model, secrets),
                summary=summary,
            )
        )
    return ComparisonReport(mode=mode, samples_per_provider=samples, entries=tuple(entries))
