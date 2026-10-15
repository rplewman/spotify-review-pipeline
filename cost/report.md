# Cost report — seed ranks 1–10,050 (golden 50 + analysis_10000) under the frozen config + projections

Offline replay: `python cost/calculator.py` (usage.csv × rates.csv; no API calls). List prices; credits and cash are shown apart; local compute is not metered (unknown, not 0).

## Warnings

- **OVER BUDGET: full_with_reuse (base) $19.26 > budget $10.00**
- **OVER BUDGET: full_with_reuse (conservative) $29.28 > budget $10.00**
- **OVER BUDGET: full_no_reuse (base) $26.27 > budget $10.00**
- **OVER BUDGET: full_no_reuse (conservative) $39.95 > budget $10.00**

## Measured (seed ranks 1–10,050 (golden 50 + analysis_10000) under the frozen config)

| Item | Value |
|---|---|
| Input | `analysis_10000.csv (+ golden ranks 1–50) = seed ranks 1–10050` sha256 `eaa62ca6d44d7173…` (manifest match: True) |
| Review IDs | 10050 (ids sha256 `72522ad15de1c3e1…`) |
| Records completed / quarantined | 10050 / 0 |
| Unique texts | 8492 |
| Exact-text cache hits (rows completed by reuse) | 1558 |
| A4 routes | {"escalate": 8492} |
| Verification sample | rate 0.2 (declared pilot rate), 25 texts verified, standard (real time) |
| label_config | `celeris-1-decision+shapeA+gate1.00+esc-claude-haiku-5-5-1.00+escfields-severity+extract-code-v1+prompt-v1+schema-v1+sentiment-mode-v1` |
| Cold run | 5.5 min, 340 calls, $0.2958 |
| Warm rerun | 0.0 s, None calls (None enrichment), incremental $0.000000 |
| Throughput (cold) | 30.29 rows/s |
| API subtotal (list) | **$0.2958** |
| Credits applied (prepaid celeris) | $0.0937 |
| Cash spent | $0.2021 |
| Unknown-billing attempts (ledger estimate, not in subtotal) | 0 ($0.000000) |
| Cost per 1,000 input rows | $0.0294 |
| Cost per completed record | $0.000029 |
| Local compute | unknown (local laptop CPU and SQLite; not billed, not metered) |

### By stage

| Stage | Config | Units | Attempts (ok/failed) | Usage | Cost (list) |
|---|---|---|---|---|---|
| A3 | provider=celeris, model=celeris-1-decision, effort=n/a (decision model), prompt_version=prompt-v1, schema_version=schema-v1, shape=A, batch_size=50, workers=[4] | 8492 distinct text decided | 170 (170/0) | input_tokens: 2,341,892 | $0.0937 |
| A5 | model=claude-haiku-5-5, effort=low, prompt_version=escalation-prompt-v1, gate=0.6, esc_fraction_cap=0.15, batch_size=≤ 50 (the batch's escalated texts), workers=1 | 8492 escalated text | 170 (170/0) | input_tokens: 544,843<br>cache_write_5m_tokens: 5,320<br>output_tokens: 280,248<br>cache_read_tokens: 220,780 | $0.1975 |
| A8 | model=claude-haiku-5-5, effort=low, prompt_version=verifier-prompt-v1, batch_size=50, workers=1, tier=standard | 25 verified text | 1 (1/0) | input_tokens: 1,082<br>cache_write_5m_tokens: 1,315<br>output_tokens: 2,262 | $0.001404 |
| A9 | model=claude-haiku-5-5, effort=low, prompt_version=grouper-prompt-v1 | 1 run | 1 (1/0) | input_tokens: 4,621<br>output_tokens: 1,062 | $0.000993 |
| A11 | model=claude-haiku-5-5, effort=low, prompt_version=memo-prompt-v1 | 1 run | 1 (1/0) | input_tokens: 5,263<br>cache_write_5m_tokens: 1,157<br>output_tokens: 3,159 | $0.002250 |

### Stage times (cold → warm)

| Stage | Cold | Warm | Cold calls | Warm calls |
|---|---|---|---|---|
| ingest | 2.0 s | unknown | 0 | — |
| enrich | 4.9 min | unknown | 340 | — |
| verify | 10.1 s | unknown | 1 | — |
| group | 6.9 s | unknown | 1 | — |
| rank | 0.1 s | unknown | 0 | — |
| memo | 18.4 s | unknown | 1 | — |

### Rates used (rates.csv)

| Provider/model/tier/item | USD per 1M | Source | Checked |
|---|---|---|---|
| anthropic/claude-haiku-5-5/standard_le100k/cache_read_tokens | 0.0100 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-10-12 |
| anthropic/claude-haiku-5-5/standard_le100k/cache_write_5m_tokens | 0.1250 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-10-12 |
| anthropic/claude-haiku-5-5/standard_le100k/input_tokens | 0.1000 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-10-12 |
| anthropic/claude-haiku-5-5/standard_le100k/output_tokens | 0.5000 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-10-12 |
| celeris/celeris-1-decision/standard/input_tokens | 0.0400 | https://docs.celeris.ai/pricing | 2026-10-12 |

## Estimated (projections)

Controls: budget $10.00, output-token cap 4000 per request, max concurrency 4, max fallback fraction 1.0, verification 20% at the batch tier. Fixed work (group naming, memo) is counted once.
Conservative = tokens ×1.25, output ×1.5 (≤ cap), retries ≥ 10%, fallback at the cap, fixed work ×2.0, concurrency efficiency 50%, Batch API 24 h (base 1 h).

| Scenario | Rows / outputs / quarantined / paid distinct | Mode | A3 | A5 | A8 | Fixed | **Total API** | Per 1,000 rows | Elapsed | Rows/s |
|---|---|---|---|---|---|---|---|---|---|---|
| full_with_reuse | 660,622 / 660,609 / 13 / 484,189 | base | $5.3411 | $11.2598 | $2.6547 | $0.003244 | **$19.2589** | $0.0292 | 2.2 h | 83.1 |
| full_with_reuse | 660,622 / 660,609 / 13 / 484,189 | conservative | $7.3440 | $17.6793 | $4.2526 | $0.007136 | **$29.2831** | $0.0443 | 26.4 h | 7.0 |
| full_no_reuse | 660,622 / 660,609 / 13 / 660,609 | base | $7.2872 | $15.3624 | $3.6220 | $0.003244 | **$26.2749** | $0.0398 | 2.6 h | 69.7 |
| full_no_reuse | 660,622 / 660,609 / 13 / 660,609 | conservative | $10.0199 | $24.1210 | $5.8021 | $0.007136 | **$39.9501** | $0.0605 | 27.2 h | 6.7 |
| scope_100k | 100,000 / 100,000 / 0 / 78,099 | base | $0.8615 | $1.8162 | $0.4282 | $0.003244 | **$3.1091** | $0.0311 | 72.0 min | 23.1 |
| scope_100k | 100,000 / 100,000 / 0 / 78,099 | conservative | $1.1846 | $2.8516 | $0.6859 | $0.007136 | **$4.7293** | $0.0473 | 24.4 h | 1.1 |

`full_no_reuse` pays every non-empty row; `full_with_reuse` pays each distinct text once (exact-text cache). `scope_100k` is our submission scope (PLAN §1.1); its out-of-scope cache fill costs $0.

