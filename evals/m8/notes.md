# M8 — Main 100k run + frequency add-on (2026-10-15)

label_config (frozen, M6b): `celeris-1-decision+shapeA+gate1.00+esc-claude-haiku-5-5-1.00+escfields-severity+extract-code-v1+prompt-v1+schema-v1+sentiment-mode-v1`
DB: `state/pipeline.db`. Workers 4, batch 50. Approved by Rory: 100k cap $4.00, add-on cap $1.50.

| Run | Phase | Scope | Calls ok/failed | Wall-clock | Spend (list) | Stop |
|---|---|---|---|---|---|---|
| run-20261015-094806-7e9c37 | initial | random_100k | 7 / 2 (in flight at the deliberate interrupt, settled at estimate) | 9 s | $0.0119 | interrupted (exit 130) |
| run-20261015-094822-868f46 | resume | random_100k | 2,779 / 0 | 38.7 min | $2.3870 | drained |
| run-20261015-102812-b0aa2c | initial | addon (top 25,000 out-of-scope texts) | 1,000 / 0 | 13.2 min | $0.8128 | drained |

M8 total **$3.2117 list** = Celeris (A3, `decisions`) $1.0442 prepaid credit + Anthropic Haiku (A5, `escalation`) $2.1675 cash.
Estimate was $2.42 + $0.87 = $3.29 (base).

## Resume evidence (`evals/recovery/`, screen-recorded by Rory)
checkpoint_before 130,221 IDs → checkpoint_after 130,382 (+161); checker resume rules **PASS** at "after" and again at
the end of the resume run (0 resume calls with a before ID; 0 before IDs unlogged in initial calls). `resume_check.json`.

## Finalize (`python -m pipeline.cli finalize`, no API calls) → `finalize.json`
660,622 source rows = 660,622 records, 0 missing, 0 orphans; ingestion.json records 660,622 ✔.
- completed **279,519** (random_100k 100,000 · frequency_addon 41,577 · out_of_scope by exact-text cache fill 137,942);
  176,420 of them via cache reuse (`cache_source_id`); all under the frozen label_config.
- quarantined 381,103 = `out_of_scope_budget_100k` 381,090 + `empty_review_text` 13. These are accounted for, not classified.
