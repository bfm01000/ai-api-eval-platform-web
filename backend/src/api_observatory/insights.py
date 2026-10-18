"""Local historical summaries derived from stored raw samples."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from api_observatory.config import ProviderDefinition
from api_observatory.metrics import compute_run_metrics
from api_observatory.storage import ResultStore

InsightWindow = Literal["1h", "24h", "7d"]
WINDOWS = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}


def _completed_at(document: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(document["execution"]["completed_at"].replace("Z", "+00:00"))


def build_insights(
    store: ResultStore,
    providers: Sequence[ProviderDefinition],
    window: InsightWindow,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    end = now or datetime.now(UTC)
    start = end - WINDOWS[window]
    documents = [
        document
        for document in store.list_results_since(start.isoformat().replace("+00:00", "Z"))
        if _completed_at(document) <= end
    ]
    overall = compute_run_metrics(documents)
    provider_rows = []
    for provider in providers:
        matching = [
            document
            for document in documents
            if document["request_snapshot"]["provider"]["provider_id"] == provider.provider_id
        ]
        metrics = compute_run_metrics(matching)
        provider_rows.append(
            {
                "provider_id": provider.provider_id,
                "display_name": provider.display_name,
                "endpoint": str(provider.endpoint),
                "samples": len(matching),
                "latest_at": max((_completed_at(doc) for doc in matching), default=None),
                "metrics": metrics.model_dump(mode="json"),
            }
        )

    bucket_width = WINDOWS[window] / 7
    trend = []
    for index in range(7):
        lower = start + bucket_width * index
        upper = start + bucket_width * (index + 1)
        selected = [
            document
            for document in documents
            if lower <= _completed_at(document) < upper
            or (index == 6 and _completed_at(document) == end)
        ]
        distribution = compute_run_metrics(selected).total_latency_ms
        trend.append(
            {
                "started_at": lower.isoformat().replace("+00:00", "Z"),
                "samples": len(selected),
                "p50_ms": distribution.p50,
                "p95_ms": distribution.p95,
            }
        )
    return {
        "window": window,
        "sample_count": len(documents),
        "metrics": overall.model_dump(mode="json"),
        "providers": provider_rows,
        "trend": trend,
    }
