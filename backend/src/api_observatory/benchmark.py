"""Versioned, deterministic capability probes and auditable scoring."""

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from api_observatory.storage import ResultStore

BENCHMARK_VERSION = "capability-core.v1"


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    category: str
    prompt: str
    expected: str


CASES = (
    BenchmarkCase("arithmetic-01", "数学", "只输出 17 乘以 23 的整数结果。", "391"),
    BenchmarkCase("arithmetic-02", "数学", "只输出 144 除以 12 的整数结果。", "12"),
    BenchmarkCase("logic-01", "逻辑", "只回答 是 或 否：所有正方形都是矩形吗？", "是"),
    BenchmarkCase(
        "logic-02", "逻辑", "只回答 是 或 否：如果 A 大于 B 且 B 大于 C，C 大于 A 吗？", "否"
    ),
    BenchmarkCase(
        "format-01",
        "指令遵循",
        '只输出紧凑 JSON：对象包含键 "ok"，值为 true。不要 Markdown。',
        '{"ok":true}',
    ),
    BenchmarkCase(
        "format-02", "指令遵循", "只输出三个大写英文字母，依次是 A、B、C，不要空格。", "ABC"
    ),
)
CASE_BY_ID = {case.case_id: case for case in CASES}
BENCHMARK_SHA256 = hashlib.sha256(
    json.dumps([case.__dict__ for case in CASES], ensure_ascii=False, sort_keys=True).encode()
).hexdigest()


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip().casefold()


def score_answer(case: BenchmarkCase, answer: str) -> bool:
    if case.case_id == "format-01":
        try:
            return json.loads(answer) == {"ok": True}
        except json.JSONDecodeError:
            return False
    return normalize(answer) == normalize(case.expected)


def extract_answer(document: dict[str, Any]) -> str | None:
    if document["status"] != "succeeded":
        return None
    try:
        body = json.loads(document["raw_response"]["body_text"])
        content = body["choices"][0]["message"]["content"]
        return content if isinstance(content, str) else None
    except (ValueError, TypeError, KeyError, IndexError):
        return None


def estimated_max_cny(
    *, samples: int, input_cny_per_million: Decimal, output_cny_per_million: Decimal
) -> Decimal:
    # UTF-8 bytes plus a per-message allowance avoid undercounting common tokenizers.
    input_tokens = sum(len(case.prompt.encode("utf-8")) + 128 for case in CASES)
    output_tokens = len(CASES) * 64
    return (
        (input_cny_per_million * input_tokens + output_cny_per_million * output_tokens)
        * samples
        / Decimal(1_000_000)
    )


def build_report(store: ResultStore, batch_id: str) -> dict[str, Any] | None:
    runs = [
        run
        for run in store.list_runs()
        if run.get("metadata", {}).get("benchmark_batch_id") == batch_id
    ]
    if not runs:
        return None
    rows = []
    for run in runs:
        case_id = run["metadata"]["benchmark_case_id"]
        case = CASE_BY_ID.get(case_id)
        if case is None:
            continue
        for result in store.list_results(run["run_id"]):
            answer = extract_answer(result)
            rows.append(
                {
                    "case_id": case_id,
                    "category": case.category,
                    "result_id": result["result_id"],
                    "status": result["status"],
                    "answer": answer,
                    "passed": score_answer(case, answer) if answer is not None else False,
                    "latency_ms": result["timing"]["response_completed_ms"],
                    "usage": result["usage"],
                }
            )
    completed = [row for row in rows if row["status"] == "succeeded"]
    planned_samples = sum(run["metadata"]["samples"] for run in runs)
    catalog_matches = all(
        run["metadata"].get("benchmark_sha256") == BENCHMARK_SHA256 for run in runs
    )
    if not catalog_matches:
        for row in rows:
            row["passed"] = None
    categories = sorted({case.category for case in CASES})
    reference = store.get_reference_set()
    reference_rows = []
    if reference is not None and catalog_matches:
        by_case = {item["case_id"]: item["answer"] for item in reference["answers"]}
        for row in completed:
            if row["case_id"] in by_case and row["answer"] is not None:
                reference_rows.append(
                    normalize(row["answer"]) == normalize(by_case[row["case_id"]])
                )
    return {
        "batch_id": batch_id,
        "benchmark_version": runs[0]["metadata"]["benchmark_version"],
        "benchmark_sha256": runs[0]["metadata"]["benchmark_sha256"],
        "provider_id": runs[0]["metadata"]["provider_id"],
        "model": runs[0]["metadata"]["model"],
        "planned_samples": planned_samples,
        "observed_samples": len(rows),
        "success_rate": len(completed) / planned_samples if planned_samples else None,
        "capability_score": (
            sum(row["passed"] for row in rows) / planned_samples
            if planned_samples and catalog_matches
            else None
        ),
        "category_scores": {
            category: sum(row["passed"] for row in rows if row["category"] == category)
            / (
                len([case for case in CASES if case.category == category])
                * runs[0]["metadata"]["samples"]
            )
            for category in categories
        }
        if catalog_matches
        else {},
        "reference": None
        if reference is None or not catalog_matches
        else {
            "source": reference["source"],
            "reference_model": reference["reference_model"],
            "imported_at": reference["imported_at"],
            "agreement": sum(reference_rows) / len(reference_rows) if reference_rows else None,
            "compared_samples": len(reference_rows),
            "identity_verified": False,
        },
        "estimated_max_cny": runs[0]["metadata"]["estimated_max_cny"],
        "catalog_matches": catalog_matches,
        "rows": rows,
    }
