import json
import sqlite3
from pathlib import Path

import pytest

from api_observatory.storage import (
    ContractViolationError,
    ResultStore,
    StorageError,
)

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "contracts" / "examples"
RUN_ID = "run_01JTEST0001"


def load_example(name: str) -> dict[str, object]:
    return json.loads((EXAMPLES_DIR / name).read_text("utf-8"))


@pytest.fixture
def store(tmp_path: Path) -> ResultStore:
    return ResultStore.open(tmp_path / "test.sqlite3")


def read_derived_columns(db_path: Path, result_id: str) -> sqlite3.Row:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "SELECT * FROM results WHERE result_id = ?", (result_id,)
        ).fetchone()
    finally:
        connection.close()


def test_open_creates_schema_and_run_lifecycle(store: ResultStore) -> None:
    store.create_run(RUN_ID, metadata={"label": "storage-unit"})
    run = store.get_run(RUN_ID)
    assert run is not None
    assert run["status"] == "pending"
    assert run["metadata"] == {"label": "storage-unit"}

    store.set_run_status(RUN_ID, "running", started_at="2026-09-20T12:00:00Z")
    store.set_run_status(RUN_ID, "completed", completed_at="2026-09-20T12:00:30Z")
    run = store.get_run(RUN_ID)
    assert run["status"] == "completed"
    assert run["started_at"] == "2026-09-20T12:00:00Z"
    assert run["completed_at"] == "2026-09-20T12:00:30Z"
    assert [r["run_id"] for r in store.list_runs()] == [RUN_ID]


def test_set_run_status_rejects_unknown_run(store: ResultStore) -> None:
    with pytest.raises(StorageError, match="Unknown run_id"):
        store.set_run_status("run_missing", "running")


def test_insert_result_round_trips_document(store: ResultStore) -> None:
    store.create_run(RUN_ID)
    document = load_example("result.standard-success.valid.json")
    store.insert_result(document)

    stored = store.get_result("res_01JTEST0003")
    assert stored == document
    assert [r["result_id"] for r in store.list_results(RUN_ID)] == ["res_01JTEST0003"]
    assert store.get_result("res_missing") is None


def test_insert_result_populates_derived_index_columns(store: ResultStore, tmp_path: Path) -> None:
    store.create_run(RUN_ID)
    standard = load_example("result.standard-success.valid.json")
    streaming = load_example("result.streaming-success.valid.json")
    timeout = load_example("result.timeout.valid.json")
    store.insert_result(standard)
    store.insert_result(streaming)
    store.insert_result(timeout)

    row = read_derived_columns(tmp_path / "test.sqlite3", "res_01JTEST0003")
    assert row["run_id"] == RUN_ID
    assert row["request_id"] == "req_01JTEST0003"
    assert row["sample_index"] == 3
    assert row["status"] == "succeeded"
    assert row["category"] is None
    assert row["provider_id"] == "northstar_demo"
    assert row["model"] == "gpt-5.2"
    assert row["mode"] == "standard"
    assert row["total_latency_ms"] == 920.0
    assert row["ttft_ms"] is None

    stream_row = read_derived_columns(tmp_path / "test.sqlite3", "res_01JTEST0001")
    assert stream_row["mode"] == "streaming"
    assert stream_row["ttft_ms"] == 428.3

    timeout_row = read_derived_columns(tmp_path / "test.sqlite3", "res_01JTEST0002")
    assert timeout_row["status"] == "failed"
    assert timeout_row["category"] == "timeout"


def test_list_results_orders_by_sample_index(store: ResultStore) -> None:
    store.create_run(RUN_ID)
    standard = load_example("result.standard-success.valid.json")
    streaming = load_example("result.streaming-success.valid.json")
    timeout = load_example("result.timeout.valid.json")
    store.insert_result(streaming)
    store.insert_result(standard)
    store.insert_result(timeout)

    ordered = store.list_results(RUN_ID)
    sample_indexes = [r["request_snapshot"]["sample_index"] for r in ordered]
    assert sample_indexes == sorted(sample_indexes)


def test_insert_result_rejects_document_missing_required_key(
    store: ResultStore,
) -> None:
    store.create_run(RUN_ID)
    document = load_example("result.standard-success.valid.json")
    del document["usage"]

    with pytest.raises(ContractViolationError, match="performance-result.v1"):
        store.insert_result(document)


def test_insert_result_rejects_success_with_error_attached(store: ResultStore) -> None:
    store.create_run(RUN_ID)
    document = load_example("result.standard-success.valid.json")
    document["error"] = {
        "category": "internal",
        "code": "INTERNAL.TEST",
        "phase": "internal",
        "message": "must not coexist with success",
        "retryable": False,
        "http_status": None,
        "provider_code": None,
    }

    with pytest.raises(ContractViolationError):
        store.insert_result(document)


def test_insert_result_rejects_wrong_cancelled_category(store: ResultStore) -> None:
    store.create_run(RUN_ID)
    document = load_example("result.timeout.valid.json")
    document["status"] = "cancelled"

    with pytest.raises(ContractViolationError):
        store.insert_result(document)


def test_insert_result_enforces_run_foreign_key(store: ResultStore) -> None:
    document = load_example("result.standard-success.valid.json")
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_result(document)


def test_closed_store_rejects_operations(store: ResultStore) -> None:
    store.close()
    with pytest.raises(StorageError, match="closed"):
        store.list_runs()
