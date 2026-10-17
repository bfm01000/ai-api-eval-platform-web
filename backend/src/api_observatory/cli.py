"""Command-line interface for running AI API performance evaluation benchmarks.

Usage:
    python -m api_observatory.cli --provider northstar_demo --samples 5 --mode streaming
    python -m api_observatory.cli --list-providers
    python -m api_observatory.cli --run-id <run_id> --report
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Literal

import httpx
from dotenv import dotenv_values

from api_observatory.config import (
    AppSettings,
    CredentialResolutionError,
    ProviderConfigError,
    load_provider_catalog,
    resolve_provider,
)
from api_observatory.providers.openai_compatible import (
    ChatMessage,
    CompletionParameters,
)
from api_observatory.storage import DEFAULT_DB_PATH, ResultStore
from api_observatory.worker import RunExecutor, RunPlan, RunSummary

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _build_environment() -> dict[str, str]:
    env_file = BACKEND_ROOT / ".env"
    local_values = (
        {k: v for k, v in dotenv_values(env_file).items() if v is not None}
        if env_file.exists()
        else {}
    )
    return {**local_values, **os.environ}


def _format_duration(value_ms: float | None) -> str:
    if value_ms is None:
        return "N/A"
    if value_ms >= 1000:
        return f"{value_ms / 1000:.2f}s"
    return f"{value_ms:.1f}ms"


def _format_rate(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.1f} tokens/s"


def print_metrics_table(summary: RunSummary) -> None:
    metrics = summary.metrics
    print("\n" + "=" * 65)
    print(f" Performance Run Report: {summary.run_id}")
    print("=" * 65)
    print(f" Status:        {summary.status.upper()}")
    print(f" Total Samples: {summary.total_samples}")
    print(f" Succeeded:     {summary.succeeded}")
    print(f" Failed:        {summary.failed}")
    if metrics:
        print(f" Success Rate:  {metrics.success_rate * 100:.1f}%")
        if metrics.error_counts:
            err_str = ", ".join(f"{k}: {v}" for k, v in metrics.error_counts.items())
            print(f" Errors:        {err_str}")

        print("-" * 65)
        print(" Latency & Throughput Distribution (Succeeded Samples):")
        print("-" * 65)
        lat = metrics.total_latency_ms
        l_min, l_avg, l_max = (
            _format_duration(lat.min),
            _format_duration(lat.mean),
            _format_duration(lat.max),
        )
        print(f"   Total Latency:  Min: {l_min} | Avg: {l_avg} | Max: {l_max}")
        p50, p90, p95, p99 = (
            _format_duration(lat.p50),
            _format_duration(lat.p90),
            _format_duration(lat.p95),
            _format_duration(lat.p99),
        )
        print(f"                   P50: {p50} | P90: {p90} | P95: {p95} | P99: {p99}")
        if lat.stddev is not None:
            print(f"                   StdDev: {lat.stddev:.1f}ms")

        if metrics.ttft_ms and metrics.ttft_ms.count > 0:
            ttft = metrics.ttft_ms
            t_min, t_avg, t_max = (
                _format_duration(ttft.min),
                _format_duration(ttft.mean),
                _format_duration(ttft.max),
            )
            print(f"   TTFT:           Min: {t_min} | Avg: {t_avg} | Max: {t_max}")
            tp50, tp95, tp99 = (
                _format_duration(ttft.p50),
                _format_duration(ttft.p95),
                _format_duration(ttft.p99),
            )
            print(f"                   P50: {tp50} | P95: {tp95} | P99: {tp99}")

        if metrics.tokens_per_second and metrics.tokens_per_second.count > 0:
            tps = metrics.tokens_per_second
            s_min, s_avg, s_max = (
                _format_rate(tps.min),
                _format_rate(tps.mean),
                _format_rate(tps.max),
            )
            print(f"   Speed:          Min: {s_min} | Avg: {s_avg} | Max: {s_max}")
            sp50, sp95 = _format_rate(tps.p50), _format_rate(tps.p95)
            print(f"                   P50: {sp50} | P95: {sp95}")

        if metrics.total_tokens is not None:
            in_tok = metrics.total_input_tokens
            out_tok = metrics.total_output_tokens
            tot = metrics.total_tokens
            print(f"   Tokens Usage:   Total: {tot} (In: {in_tok}, Out: {out_tok})")

    print("=" * 65 + "\n")


async def async_main(args: argparse.Namespace) -> int:
    settings = AppSettings(_env_file=BACKEND_ROOT / ".env")
    env = _build_environment()
    config_path = settings.provider_config_path
    if not config_path.is_absolute():
        config_path = BACKEND_ROOT / config_path

    try:
        catalog = load_provider_catalog(config_path)
    except ProviderConfigError as exc:
        print(f"Error loading providers config: {exc}", file=sys.stderr)
        return 1

    if args.list_providers:
        print("Configured Providers:")
        for p in catalog.providers:
            print(f"  - ID: {p.provider_id:20} Name: {p.display_name:25} Model: {p.default_model}")
        return 0

    store = ResultStore.open(Path(args.db) if args.db else DEFAULT_DB_PATH)

    if args.report:
        run_metrics = store.get_run_metrics(args.report)
        if not run_metrics:
            print(f"No run found with ID: {args.report}", file=sys.stderr)
            store.close()
            return 1
        run_doc = store.get_run(args.report) or {}
        if args.json:
            print(json.dumps(run_metrics.model_dump(), indent=2, ensure_ascii=False))
        else:
            summary = RunSummary(
                run_id=args.report,
                status=run_doc.get("status", "completed"),
                total_samples=run_metrics.total_samples,
                succeeded=run_metrics.succeeded_samples,
                failed=run_metrics.failed_samples,
                cancelled=run_metrics.cancelled_samples,
                metrics=run_metrics,
            )
            print_metrics_table(summary)
        store.close()
        return 0

    # Select provider
    provider_id = args.provider
    if not provider_id:
        if not catalog.providers:
            print("No providers configured in catalog.", file=sys.stderr)
            store.close()
            return 1
        provider_id = catalog.providers[0].provider_id

    try:
        definition = catalog.get(provider_id)
    except KeyError:
        print(f"Provider not found: {provider_id}", file=sys.stderr)
        store.close()
        return 1

    try:
        resolved = resolve_provider(definition, env)
    except CredentialResolutionError as exc:
        print(f"Credential error: {exc}", file=sys.stderr)
        store.close()
        return 1

    model = args.model or definition.default_model
    mode: Literal["standard", "streaming"] = args.mode
    samples = args.samples
    prompt = args.prompt

    plan = RunPlan(
        provider=definition,
        resolved=resolved,
        model=model,
        mode=mode,
        messages=(ChatMessage(role="user", content=prompt),),
        parameters=CompletionParameters(
            temperature=args.temperature,
            max_output_tokens=args.max_tokens,
        ),
        samples=samples,
    )

    if not args.json:
        print(f"Starting performance run against [{definition.display_name}]...")
        print(f"  Model:   {model}")
        print(f"  Mode:    {mode}")
        print(f"  Samples: {samples}")

    async with httpx.AsyncClient(trust_env=False) as client:
        executor = RunExecutor(store=store, client=client)
        summary = await executor.run(plan)

    if args.json:
        print(json.dumps(summary.model_dump(), indent=2, ensure_ascii=False))
    else:
        print_metrics_table(summary)

    store.close()
    if summary.status == "completed" and summary.failed == 0:
        return 0
    return 0 if summary.succeeded > 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="AI API Observatory Performance Runner")
    parser.add_argument("--provider", "-p", help="Provider ID to benchmark")
    parser.add_argument("--model", "-m", help="Model name override")
    parser.add_argument(
        "--mode",
        choices=["standard", "streaming"],
        default="standard",
        help="Request mode",
    )
    parser.add_argument(
        "--samples",
        "-n",
        type=int,
        default=3,
        help="Number of sequential sample requests (default: 3)",
    )
    parser.add_argument("--prompt", default="Reply with only OK.", help="Prompt text for test")
    parser.add_argument("--temperature", type=float, default=0.0, help="Sampling temperature")
    parser.add_argument("--max-tokens", type=int, default=16, help="Max completion tokens")
    parser.add_argument("--db", help="Path to SQLite database file")
    parser.add_argument("--list-providers", action="store_true", help="List configured providers")
    parser.add_argument("--report", help="Print report for an existing run ID")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")

    args = parser.parse_args()
    sys.exit(asyncio.run(async_main(args)))


if __name__ == "__main__":
    main()
