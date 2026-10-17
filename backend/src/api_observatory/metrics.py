"""Derived performance metrics and percentile aggregation.

According to PERFORMANCE_CONTRACT.md and ADR-003, raw result documents are the
source of truth. P50, P95, P99, error distributions, and success rates are
derived metrics recomputed from raw records on demand.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MetricDistribution(BaseModel):
    """Statistical distribution of a numerical metric across multiple samples."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    count: int = Field(ge=0, description="Number of valid data points in the sample")
    min: float | None = Field(default=None, description="Minimum observed value")
    max: float | None = Field(default=None, description="Maximum observed value")
    mean: float | None = Field(default=None, description="Arithmetic mean")
    p50: float | None = Field(default=None, description="50th percentile (median)")
    p90: float | None = Field(default=None, description="90th percentile")
    p95: float | None = Field(default=None, description="95th percentile")
    p99: float | None = Field(default=None, description="99th percentile")
    stddev: float | None = Field(default=None, description="Sample standard deviation")


class RunMetrics(BaseModel):
    """Aggregated performance and reliability metrics for a test run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str | None = Field(default=None, description="Run identifier")
    total_samples: int = Field(ge=0, description="Total sample attempts recorded")
    succeeded_samples: int = Field(ge=0, description="Count of succeeded samples")
    failed_samples: int = Field(ge=0, description="Count of failed samples")
    cancelled_samples: int = Field(ge=0, description="Count of cancelled samples")
    success_rate: float = Field(ge=0.0, le=1.0, description="Ratio of succeeded to total samples")
    failure_rate: float = Field(ge=0.0, le=1.0, description="Ratio of failed to total samples")
    error_counts: dict[str, int] = Field(
        default_factory=dict, description="Counts of errors keyed by error category"
    )

    # Latency distribution over succeeded samples (in milliseconds)
    total_latency_ms: MetricDistribution = Field(
        description="Total response latency distribution across succeeded samples"
    )

    # Streaming-specific metrics (only present when streaming samples exist)
    ttft_ms: MetricDistribution | None = Field(
        default=None, description="Time to first text content token (TTFT) distribution"
    )
    generation_duration_ms: MetricDistribution | None = Field(
        default=None, description="Token generation window duration distribution"
    )
    tokens_per_second: MetricDistribution | None = Field(
        default=None, description="Output tokens per second distribution"
    )

    # Token usage summaries
    total_input_tokens: int | None = Field(
        default=None, description="Sum of input tokens across all available samples"
    )
    total_output_tokens: int | None = Field(
        default=None, description="Sum of output tokens across all available samples"
    )
    total_tokens: int | None = Field(
        default=None, description="Sum of total tokens across all available samples"
    )


def compute_percentile(sorted_values: Sequence[float], percentile: float) -> float:
    """Compute a percentile using linear interpolation between closest ranks.

    Matches NumPy's default linear interpolation method:
    index = (N - 1) * (percentile / 100).
    """
    if not sorted_values:
        raise ValueError("Cannot compute percentile of empty sequence")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    if not (0.0 <= percentile <= 100.0):
        raise ValueError(f"Percentile must be between 0 and 100, got {percentile}")

    k = (len(sorted_values) - 1) * (percentile / 100.0)
    lower_idx = math.floor(k)
    upper_idx = math.ceil(k)
    if lower_idx == upper_idx:
        return float(sorted_values[lower_idx])
    fraction = k - lower_idx
    return float(
        sorted_values[lower_idx] + fraction * (sorted_values[upper_idx] - sorted_values[lower_idx])
    )


def summarize_distribution(
    values: Sequence[float],
    *,
    precision: int = 3,
) -> MetricDistribution:
    """Compute summary statistics and percentiles for a list of floats."""
    clean_values = [float(v) for v in values if v is not None and not math.isnan(v)]
    if not clean_values:
        return MetricDistribution(count=0)

    clean_values.sort()
    count = len(clean_values)
    min_val = clean_values[0]
    max_val = clean_values[-1]
    mean_val = sum(clean_values) / count

    p50 = compute_percentile(clean_values, 50.0)
    p90 = compute_percentile(clean_values, 90.0)
    p95 = compute_percentile(clean_values, 95.0)
    p99 = compute_percentile(clean_values, 99.0)
    stddev = statistics.stdev(clean_values) if count >= 2 else 0.0

    return MetricDistribution(
        count=count,
        min=round(min_val, precision),
        max=round(max_val, precision),
        mean=round(mean_val, precision),
        p50=round(p50, precision),
        p90=round(p90, precision),
        p95=round(p95, precision),
        p99=round(p99, precision),
        stddev=round(stddev, precision),
    )


def compute_run_metrics(
    documents: Sequence[Mapping[str, Any]],
    *,
    run_id: str | None = None,
) -> RunMetrics:
    """Compute P50, P95, P99, and reliability metrics from raw performance-result.v1 documents."""
    total_samples = len(documents)
    succeeded_docs: list[Mapping[str, Any]] = []
    failed_docs: list[Mapping[str, Any]] = []
    cancelled_docs: list[Mapping[str, Any]] = []
    error_counter: Counter[str] = Counter()

    for doc in documents:
        status = doc.get("status")
        if status == "succeeded":
            succeeded_docs.append(doc)
        elif status == "failed":
            failed_docs.append(doc)
            error_obj = doc.get("error")
            if isinstance(error_obj, Mapping):
                cat = error_obj.get("category", "unknown")
                error_counter[str(cat)] += 1
            else:
                error_counter["unknown"] += 1
        elif status == "cancelled":
            cancelled_docs.append(doc)
            error_obj = doc.get("error")
            if isinstance(error_obj, Mapping):
                cat = error_obj.get("category", "cancelled")
                error_counter[str(cat)] += 1
            else:
                error_counter["cancelled"] += 1

    succeeded_count = len(succeeded_docs)
    failed_count = len(failed_docs)
    cancelled_count = len(cancelled_docs)

    success_rate = round(succeeded_count / total_samples, 4) if total_samples > 0 else 0.0
    failure_rate = round(failed_count / total_samples, 4) if total_samples > 0 else 0.0

    # Extract timing and usage metrics from succeeded samples
    latencies: list[float] = []
    ttfts: list[float] = []
    generation_durations: list[float] = []
    tokens_per_seconds: list[float] = []

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    has_usage_data = False

    for doc in succeeded_docs:
        timing = doc.get("timing", {})
        if isinstance(timing, Mapping):
            completed_ms = timing.get("response_completed_ms")
            if isinstance(completed_ms, (int, float)):
                latencies.append(float(completed_ms))

            first_content_ms = timing.get("first_content_ms")
            if isinstance(first_content_ms, (int, float)):
                ttfts.append(float(first_content_ms))
                if isinstance(completed_ms, (int, float)):
                    gen_duration = float(completed_ms) - float(first_content_ms)
                    if gen_duration > 0:
                        generation_durations.append(gen_duration)

        usage = doc.get("usage", {})
        if isinstance(usage, Mapping) and usage.get("source") == "provider":
            in_tok = usage.get("input_tokens")
            out_tok = usage.get("output_tokens")
            tot_tok = usage.get("total_tokens")
            if isinstance(in_tok, int) and isinstance(out_tok, int):
                total_input_tokens += in_tok
                total_output_tokens += out_tok
                total_tokens += tot_tok if isinstance(tot_tok, int) else (in_tok + out_tok)
                has_usage_data = True

                # Compute Tokens/s according to PERFORMANCE_CONTRACT.md §4:
                # output_tokens / (generation_duration_ms / 1000)
                # only when first_content_ms exists and generation_duration_ms > 0
                if (
                    isinstance(timing, Mapping)
                    and isinstance(timing.get("first_content_ms"), (int, float))
                    and isinstance(timing.get("response_completed_ms"), (int, float))
                ):
                    gen_dur_ms = float(timing["response_completed_ms"]) - float(
                        timing["first_content_ms"]
                    )
                    if gen_dur_ms > 0:
                        tps = out_tok / (gen_dur_ms / 1000.0)
                        tokens_per_seconds.append(tps)

    latency_dist = summarize_distribution(latencies, precision=3)
    ttft_dist = summarize_distribution(ttfts, precision=3) if ttfts else None
    gen_dist = (
        summarize_distribution(generation_durations, precision=3) if generation_durations else None
    )
    tps_dist = (
        summarize_distribution(tokens_per_seconds, precision=2) if tokens_per_seconds else None
    )

    return RunMetrics(
        run_id=run_id,
        total_samples=total_samples,
        succeeded_samples=succeeded_count,
        failed_samples=failed_count,
        cancelled_samples=cancelled_count,
        success_rate=success_rate,
        failure_rate=failure_rate,
        error_counts=dict(error_counter),
        total_latency_ms=latency_dist,
        ttft_ms=ttft_dist,
        generation_duration_ms=gen_dist,
        tokens_per_second=tps_dist,
        total_input_tokens=total_input_tokens if has_usage_data else None,
        total_output_tokens=total_output_tokens if has_usage_data else None,
        total_tokens=total_tokens if has_usage_data else None,
    )
