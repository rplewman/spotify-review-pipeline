# M10 — Grading export + checker (no spend)

Command: `.venv/Scripts/python -m pipeline.cli export` (`pipeline/export.py`; side report `evals/m10/export.json`).

## grading/
| File | Source | Size |
|---|---|---|
| `run.json` | built (count + SHA from `ingestion.json`; `allow_multi_issue: false`; extra key `label_config`) | 382 B |
| `ingestion.json` | `check_submission.py profile` (M1; re-profiled in M10, byte-identical) | 994 B |
| `records.jsonl.gz` | `records` table, 660,622 rows, sorted by review_id, gzip mtime 0 | 53.3 MB |
| `calls.jsonl.gz` | `calls` table, 4,552 attempts (see below) | 3.1 MB |
| `membership.csv`, `ranking.csv`, `claims.csv` | copied unchanged from `outputs/` (M9; claims = memo v3) | |
| `checkpoint_before.json` / `_after.json` | copied from `evals/recovery/` (M8): 130,221 → 130,382 IDs | 5.5 MB each |

## Calls exported (4,552)
enrich initial 1,347 succeeded + 2 failed (the interrupted run's in-flight calls); enrich resume 2,779 succeeded;
verify 410 succeeded + 9 failed; group 1; memo 2 succeeded (v2 + v3) + 2 failed. Failed calls without provider usage:
tokens 0 + `"usage_available": false`.

**Left out (344):** succeeded enrich calls under the superseded M6 label_config
`…gate0.70+esc-claude-haiku-5-5-0.40+escfields-severity+extract-code-v1+prompt-v1+schema-v1` (172 A3 + 172 A5). Their
reviews were re-done under the frozen config (those calls are exported); the checker would flag them as
`call_config_mismatch`. Full list in `evals/m10/export.json` → `excluded_calls`. **README (M12) must disclose this.**
M7 `injection_real.db` calls are system-test evidence, not part of the run, and are not in calls.jsonl.

## Checker (`self-check.json`, local, outside grading/)
- status **review_required**, single issue **`unfinished_classification` = 381,090** = the out-of-scope nonempty rows
  quarantined `out_of_scope_budget_100k` (accounted for, not classified; PLAN §1.1). Expected by design → explain in README.
- coverage: valid completed 279,519 (176,420 valid cache reuses), quarantined 381,103, missing 0, invalid 0,
  accounted 1.0; **working coverage point candidate 0.6539** (= M6 plan).
- reported calls 4,552; input tokens 39,123,254; output tokens 4,861,837.
- reference SHA `bb31d47d…`, audit script SHA `d60bd66d…`; input file SHAs in `self-check.json`.
