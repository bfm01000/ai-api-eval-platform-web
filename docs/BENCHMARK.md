# Capability Benchmark Prototype

`capability-core.v1` currently contains six deterministic prompts across arithmetic, logic and instruction following. The catalog endpoint returns its version and SHA-256 hash. Changing a prompt, expected answer or category requires a new version; old reports retain the version and hash used at execution.

Each case runs in standard Chat Completions mode, temperature 0, at most 64 output tokens, sequentially for 1–3 samples. The exact prompt, parameters, response body, timing, usage and failures live in the gitignored local SQLite database. Scoring uses Unicode NFKC, trimming and case folding for exact answers. The JSON item is parsed and checked for `{"ok": true}`. A failed or missing attempt earns zero. The overall score divides passes by planned attempts. It is a narrow, transparent capability probe, not a broad model-quality score.

The user supplies input and output prices in CNY per million tokens. The estimate counts UTF-8 bytes plus 128 input-token allowance per case and 64 output tokens per case. The API rejects a batch whose estimate exceeds its chosen cap and rejects new benchmark batches once local recorded benchmark estimates would exceed 100 CNY. This is **not a billing guarantee**: provider pricing may be wrong or change, tokenization/overheads can differ, and ordinary tests or comparison calls are not part of the benchmark ledger. No automatic paid sampling is started on page load.

Reference answers can be imported only in local mode using a JSON file matching the exact version and SHA-256:

```json
{
  "benchmark_version": "capability-core.v1",
  "benchmark_sha256": "<hash shown in the local UI>",
  "answers": [
    { "case_id": "arithmetic-01", "answer": "391" }
  ]
}
```

Download the current template from the Reference section and fill the answer values. The importer also asks for a reference model name and source description. These are user-supplied claims, not verified provenance. Reports calculate normalized exact agreement only for available successful outputs and expose the denominator. Even 100% agreement on this small dataset **does not identify a model**. Official same-prompt/same-parameter data, repeated sampling, discriminative features, uncertainty estimates and out-of-sample validation remain future research work.
