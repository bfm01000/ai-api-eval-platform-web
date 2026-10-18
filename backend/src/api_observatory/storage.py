"""SQLite persistence for performance runs and raw result documents.

``document_json`` is the source of truth: it holds the exact
performance-result.v1 contract document. The typed columns around it are
derived indexes for querying and are recomputed from the document on write.

The database lives under the repository's gitignored ``data/`` directory by
default so prompts, response bodies, and endpoint metadata never enter git.
"""

import json
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

RESULT_SCHEMA_URI = "https://api-observatory.local/contracts/performance-result.v1.schema.json"
REQUEST_SCHEMA_URI = "https://api-observatory.local/contracts/performance-request.v1.schema.json"

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CONTRACTS_DIR = _REPO_ROOT / "contracts"
DEFAULT_DB_PATH = _REPO_ROOT / "backend" / "data" / "api-observatory.sqlite3"

RunStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
_RUN_STATUSES: tuple[str, ...] = ("pending", "running", "completed", "failed", "cancelled")


class StorageError(RuntimeError):
    """Raised when the store cannot complete an operation."""


class ContractViolationError(StorageError):
    """Raised when a document fails contract validation before persistence."""


def _load_result_validator(contracts_dir: Path = _DEFAULT_CONTRACTS_DIR) -> Draft202012Validator:
    try:
        result_schema = json.loads(
            (contracts_dir / "performance-result.v1.schema.json").read_text("utf-8")
        )
        request_schema = json.loads(
            (contracts_dir / "performance-request.v1.schema.json").read_text("utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageError(f"Cannot load contract schemas from {contracts_dir}: {exc}") from exc

    registry: Registry = Registry().with_resources(
        [
            (
                RESULT_SCHEMA_URI,
                Resource.from_contents(result_schema, default_specification=DRAFT202012),
            ),
            (
                REQUEST_SCHEMA_URI,
                Resource.from_contents(request_schema, default_specification=DRAFT202012),
            ),
        ]
    )
    return Draft202012Validator(
        result_schema,
        registry=registry,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )


_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN {_RUN_STATUSES}),
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    document_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS results (
    result_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    request_id TEXT NOT NULL,
    sample_index INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('succeeded', 'failed', 'cancelled')),
    category TEXT,
    provider_id TEXT NOT NULL,
    model TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('standard', 'streaming')),
    total_latency_ms REAL NOT NULL,
    ttft_ms REAL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    document_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_results_run_id ON results(run_id);
CREATE INDEX IF NOT EXISTS idx_results_run_sample ON results(run_id, sample_index);

CREATE TABLE IF NOT EXISTS reference_sets (
    reference_id TEXT PRIMARY KEY,
    imported_at TEXT NOT NULL,
    document_json TEXT NOT NULL
);
"""


def _utc_now_rfc3339() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise StorageError(f"Result document field {field!r} must be a non-empty string")
    return value


def _require_number(value: Any, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise StorageError(f"Result document field {field!r} must be a number")
    return float(value)


def _extract_result_row(document: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = document.get("request_snapshot")
    execution = document.get("execution")
    timing = document.get("timing")
    error = document.get("error")
    if not isinstance(snapshot, Mapping):
        raise StorageError("Result document field 'request_snapshot' must be an object")
    if not isinstance(execution, Mapping):
        raise StorageError("Result document field 'execution' must be an object")
    if not isinstance(timing, Mapping):
        raise StorageError("Result document field 'timing' must be an object")

    category = error.get("category") if isinstance(error, Mapping) else None
    ttft = timing.get("first_content_ms")
    return {
        "result_id": _require_text(document.get("result_id"), "result_id"),
        "run_id": _require_text(snapshot.get("run_id"), "request_snapshot.run_id"),
        "request_id": _require_text(snapshot.get("request_id"), "request_snapshot.request_id"),
        "sample_index": int(
            _require_number(snapshot.get("sample_index"), "request_snapshot.sample_index")
        ),
        "status": _require_text(document.get("status"), "status"),
        "category": str(category) if category is not None else None,
        "provider_id": _require_text(
            snapshot.get("provider", {}).get("provider_id"), "provider_id"
        ),
        "model": _require_text(snapshot.get("model"), "model"),
        "mode": _require_text(snapshot.get("mode"), "mode"),
        "total_latency_ms": _require_number(
            timing.get("response_completed_ms"), "response_completed_ms"
        ),
        "ttft_ms": float(ttft) if ttft is not None else None,
        "started_at": _require_text(execution.get("started_at"), "execution.started_at"),
        "completed_at": _require_text(execution.get("completed_at"), "execution.completed_at"),
    }


class ResultStore:
    """Synchronous SQLite store. Methods are short and side-effect free per call."""

    def __init__(
        self,
        db_path: Path,
        *,
        contracts_dir: Path = _DEFAULT_CONTRACTS_DIR,
    ) -> None:
        self._db_path = db_path
        self._validator = _load_result_validator(contracts_dir)
        self._connection: sqlite3.Connection | None = None

    @classmethod
    def open(
        cls,
        db_path: Path = DEFAULT_DB_PATH,
        *,
        contracts_dir: Path = _DEFAULT_CONTRACTS_DIR,
    ) -> "ResultStore":
        store = cls(db_path, contracts_dir=contracts_dir)
        store._connect()
        return store

    def _connect(self) -> None:
        if self._connection is not None:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(_SCHEMA)
        self._connection = connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> "ResultStore":
        self._connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise StorageError("ResultStore is closed")
        return self._connection

    def _validate_result_document(self, document: Mapping[str, Any]) -> None:
        errors = sorted(self._validator.iter_errors(document), key=lambda e: list(e.absolute_path))
        if errors:
            location = ".".join(str(part) for part in errors[0].absolute_path) or "<root>"
            raise ContractViolationError(
                f"Result document violates performance-result.v1 at {location}: "
                f"{errors[0].message} ({len(errors)} error(s) total)"
            )

    def create_run(self, run_id: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        connection = self._require_connection()
        document: dict[str, Any] = {
            "run_id": run_id,
            "status": "pending",
            "created_at": _utc_now_rfc3339(),
        }
        if metadata:
            document["metadata"] = dict(metadata)
        connection.execute(
            "INSERT INTO runs (run_id, status, created_at, document_json) VALUES (?, ?, ?, ?)",
            (run_id, "pending", document["created_at"], json.dumps(document, ensure_ascii=False)),
        )
        connection.commit()

    def set_run_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        started_at: str | None = None,
        completed_at: str | None = None,
    ) -> None:
        connection = self._require_connection()
        row = connection.execute(
            "SELECT document_json FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise StorageError(f"Unknown run_id: {run_id}")
        document = json.loads(row["document_json"])
        document["status"] = status
        if started_at is not None:
            document["started_at"] = started_at
        if completed_at is not None:
            document["completed_at"] = completed_at
        connection.execute(
            "UPDATE runs SET status = ?, started_at = ?, completed_at = ?, document_json = ?"
            " WHERE run_id = ?",
            (
                status,
                document.get("started_at"),
                document.get("completed_at"),
                json.dumps(document, ensure_ascii=False),
                run_id,
            ),
        )
        connection.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        connection = self._require_connection()
        row = connection.execute(
            "SELECT document_json FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return json.loads(row["document_json"]) if row is not None else None

    def list_runs(self) -> list[dict[str, Any]]:
        connection = self._require_connection()
        rows = connection.execute(
            "SELECT document_json FROM runs ORDER BY created_at, run_id"
        ).fetchall()
        return [json.loads(row["document_json"]) for row in rows]

    def insert_result(self, document: Mapping[str, Any]) -> None:
        """Validate and persist one performance-result.v1 document."""

        connection = self._require_connection()
        self._validate_result_document(document)
        row = _extract_result_row(document)
        connection.execute(
            """
            INSERT INTO results (
                result_id, run_id, request_id, sample_index, status, category,
                provider_id, model, mode, total_latency_ms, ttft_ms,
                started_at, completed_at, document_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["result_id"],
                row["run_id"],
                row["request_id"],
                row["sample_index"],
                row["status"],
                row["category"],
                row["provider_id"],
                row["model"],
                row["mode"],
                row["total_latency_ms"],
                row["ttft_ms"],
                row["started_at"],
                row["completed_at"],
                json.dumps(document, ensure_ascii=False),
            ),
        )
        connection.commit()

    def get_result(self, result_id: str) -> dict[str, Any] | None:
        connection = self._require_connection()
        row = connection.execute(
            "SELECT document_json FROM results WHERE result_id = ?", (result_id,)
        ).fetchone()
        return json.loads(row["document_json"]) if row is not None else None

    def list_results(self, run_id: str) -> list[dict[str, Any]]:
        connection = self._require_connection()
        rows = connection.execute(
            "SELECT document_json FROM results WHERE run_id = ? ORDER BY sample_index, result_id",
            (run_id,),
        ).fetchall()
        return [json.loads(row["document_json"]) for row in rows]

    def list_results_since(self, completed_at: str) -> list[dict[str, Any]]:
        connection = self._require_connection()
        rows = connection.execute(
            "SELECT document_json FROM results WHERE completed_at >= ? ORDER BY completed_at",
            (completed_at,),
        ).fetchall()
        return [json.loads(row["document_json"]) for row in rows]

    def save_reference_set(self, document: Mapping[str, Any]) -> None:
        connection = self._require_connection()
        connection.execute(
            "INSERT OR REPLACE INTO reference_sets (reference_id, imported_at, document_json)"
            " VALUES ('active', ?, ?)",
            (_utc_now_rfc3339(), json.dumps(document, ensure_ascii=False)),
        )
        connection.commit()

    def get_reference_set(self) -> dict[str, Any] | None:
        row = (
            self._require_connection()
            .execute(
                "SELECT document_json, imported_at FROM reference_sets "
                "WHERE reference_id = 'active'"
            )
            .fetchone()
        )
        if row is None:
            return None
        return {**json.loads(row["document_json"]), "imported_at": row["imported_at"]}

    def get_run_metrics(self, run_id: str):
        """Compute aggregated metrics (P50, P95, P99, success rate) for a run."""
        from api_observatory.metrics import compute_run_metrics

        run = self.get_run(run_id)
        if run is None:
            return None
        results = self.list_results(run_id)
        return compute_run_metrics(results, run_id=run_id)
