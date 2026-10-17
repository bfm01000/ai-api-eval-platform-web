import argparse
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from api_observatory.cli import _build_environment, _format_duration, _format_rate, async_main
from api_observatory.config import ProviderCatalog, ProviderDefinition, ResolvedProvider
from api_observatory.metrics import MetricDistribution, RunMetrics
from api_observatory.storage import ResultStore
from api_observatory.worker import RunSummary


def test_format_helpers() -> None:
    assert _format_duration(None) == "N/A"
    assert _format_duration(500.0) == "500.0ms"
    assert _format_duration(2500.0) == "2.50s"

    assert _format_rate(None) == "N/A"
    assert _format_rate(45.67) == "45.7 tokens/s"


def test_build_environment_returns_dict() -> None:
    env = _build_environment()
    assert isinstance(env, dict)


@pytest.mark.anyio
async def test_cli_list_providers(capsys) -> None:
    args = argparse.Namespace(
        list_providers=True,
        report=None,
        db=None,
        provider=None,
        model=None,
        mode="standard",
        samples=1,
        prompt="hi",
        temperature=0.0,
        max_tokens=16,
        json=False,
    )
    code = await async_main(args)
    assert code == 0
    captured = capsys.readouterr()
    assert "Configured Providers:" in captured.out


@pytest.mark.anyio
async def test_cli_report_existing_run(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "cli_test.sqlite3"
    store = ResultStore.open(db_path)
    run_id = "run_CLI_REPORT_01"
    store.create_run(run_id)

    fake_metrics = RunMetrics(
        run_id=run_id,
        total_samples=1,
        succeeded_samples=1,
        failed_samples=0,
        cancelled_samples=0,
        success_rate=1.0,
        failure_rate=0.0,
        error_counts={},
        total_latency_ms=MetricDistribution(
            count=1,
            min=120.0,
            max=120.0,
            mean=120.0,
            p50=120.0,
            p90=120.0,
            p95=120.0,
            p99=120.0,
            stddev=0.0,
        ),
    )

    with (
        patch.object(store, "get_run_metrics", return_value=fake_metrics),
        patch("api_observatory.cli.ResultStore.open", return_value=store),
    ):
        args = argparse.Namespace(
            list_providers=False,
            report=run_id,
            db=str(db_path),
            provider=None,
            model=None,
            mode="standard",
            samples=1,
            prompt="hi",
            temperature=0.0,
            max_tokens=16,
            json=False,
        )
        code = await async_main(args)
        assert code == 0
        captured = capsys.readouterr()
        assert "Performance Run Report:" in captured.out
        assert run_id in captured.out

    store.close()


@pytest.mark.anyio
async def test_cli_run_execution(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "cli_run.sqlite3"
    fake_summary = RunSummary(
        run_id="run_CLI_EXEC_01",
        status="completed",
        total_samples=2,
        succeeded=2,
        failed=0,
        cancelled=0,
        metrics=RunMetrics(
            run_id="run_CLI_EXEC_01",
            total_samples=2,
            succeeded_samples=2,
            failed_samples=0,
            cancelled_samples=0,
            success_rate=1.0,
            failure_rate=0.0,
            error_counts={},
            total_latency_ms=MetricDistribution(
                count=2,
                min=100.0,
                max=200.0,
                mean=150.0,
                p50=150.0,
                p90=190.0,
                p95=195.0,
                p99=199.0,
                stddev=70.7,
            ),
        ),
    )

    args = argparse.Namespace(
        list_providers=False,
        report=None,
        db=str(db_path),
        provider="worker_test",
        model="test-model",
        mode="standard",
        samples=2,
        prompt="Hello",
        temperature=0.0,
        max_tokens=16,
        json=True,
    )

    definition = ProviderDefinition(
        provider_id="worker_test",
        display_name="Test",
        endpoint="https://api.example.com/v1/chat/completions",
        protocol="openai-chat-completions",
        credential_ref="env:WORKER_TEST_API_KEY",
        adapter="openai-compatible",
        default_model="test-model",
        timeout_ms=5000,
    )
    resolved = ResolvedProvider(definition=definition, api_key=SecretStr("test-secret"))
    catalog = ProviderCatalog(schema_version="provider-config.v1", providers=(definition,))

    with (
        patch("api_observatory.cli.load_provider_catalog", return_value=catalog),
        patch("api_observatory.cli.resolve_provider", return_value=resolved),
        patch.object(ResultStore, "open", return_value=ResultStore.open(db_path)),
        patch(
            "api_observatory.cli.RunExecutor.run",
            new_callable=AsyncMock,
            return_value=fake_summary,
        ),
    ):
        code = await async_main(args)
        assert code == 0
        captured = capsys.readouterr()
        out_json = json.loads(captured.out)
        assert out_json["run_id"] == "run_CLI_EXEC_01"
        assert out_json["status"] == "completed"
        assert out_json["total_samples"] == 2
        assert out_json["metrics"]["total_latency_ms"]["p50"] == 150.0
