# Cost report — 100-review pilot + projections

Offline replay: `python cost/calculator.py` (usage.csv × rates.csv; no API calls). List prices; credits and cash are shown apart; local compute is not metered (unknown, not 0).

## Warnings

- **OVER BUDGET: full_with_reuse (conservative) $12.81 > budget $10.00**
- **OVER BUDGET: full_no_reuse (base) $12.17 > budget $10.00**
- **OVER BUDGET: full_no_reuse (conservative) $17.48 > budget $10.00**

## Measured (pilot)

| Item | Value |
|---|---|
| Input | `cost_100.csv` sha256 `c884ac3b9be50669…` (manifest match: True) |
| Review IDs | 100 (ids sha256 `02066ea0d4aedb7e…`) |
| Records completed / quarantined | 100 / 0 |
| Unique texts | 100 |
| Exact-text cache hits (rows completed by reuse) | 0 |
| A4 routes | {"accept": 63, "capped": 22, "escalate": 15} |
| Verification sample | rate 0.2 (declared pilot rate), 25 texts verified, standard (real time) |
| label_config | `pilot-celeris-1-decision+shapeA+gate0.60+esc-claude-haiku-5-5-0.15+extract-code-v1+prompt-v1+schema-v1` |
| Cold run | 42.0 s, 6 calls, $0.006352 |
| Warm rerun | 0.3 s, 0 calls (0 enrichment), incremental $0.000000 |
| Throughput (cold) | 2.38 rows/s |
| API subtotal (list) | **$0.006352** |
| Credits applied (prepaid celeris) | $0.001100 |
| Cash spent | $0.005251 |
| Unknown-billing attempts (ledger estimate, not in subtotal) | 0 ($0.000000) |
| Cost per 1,000 input rows | $0.0635 |
| Cost per completed record | $0.000064 |
| Local compute | unknown (local laptop CPU and SQLite; not billed, not metered) |

### By stage

| Stage | Config | Units | Attempts (ok/failed) | Usage | Cost (list) |
|---|---|---|---|---|---|
| A3 | provider=celeris, model=celeris-1-decision, effort=n/a (decision model), prompt_version=prompt-v1, schema_version=schema-v1, shape=A, batch_size=50, workers=1 | 100 distinct text decided | 2 (2/0) | input_tokens: 27,509 | $0.001100 |
| A5 | model=claude-haiku-5-5, effort=low, prompt_version=escalation-prompt-v1, gate=0.6, esc_fraction_cap=0.15, batch_size=≤ 50 (the batch's escalated texts), workers=1 | 15 escalated text | 1 (1/0) | input_tokens: 1,430<br>cache_write_5m_tokens: 1,330<br>output_tokens: 590 | $0.000604 |
| A8 | model=claude-haiku-5-5, effort=low, prompt_version=verifier-prompt-v1, batch_size=50, workers=1, tier=standard | 25 verified text | 1 (1/0) | input_tokens: 1,082<br>cache_write_5m_tokens: 1,315<br>output_tokens: 2,262 | $0.001404 |
| A9 | model=claude-haiku-5-5, effort=low, prompt_version=grouper-prompt-v1 | 1 run | 1 (1/0) | input_tokens: 4,621<br>output_tokens: 1,062 | $0.000993 |
| A11 | model=claude-haiku-5-5, effort=low, prompt_version=memo-prompt-v1 | 1 run | 1 (1/0) | input_tokens: 5,263<br>cache_write_5m_tokens: 1,157<br>output_tokens: 3,159 | $0.002250 |

### Stage times (cold → warm)

| Stage | Cold | Warm | Cold calls | Warm calls |
|---|---|---|---|---|
| ingest | 0.0 s | unknown | 0 | — |
| enrich | 6.3 s | 0.1 s | 3 | 0 |
| verify | 10.1 s | 0.1 s | 1 | 0 |
| group | 6.9 s | 0.0 s | 1 | 0 |
| rank | 0.1 s | 0.0 s | 0 | 0 |
| memo | 18.4 s | 0.0 s | 1 | 0 |

### Rates used (rates.csv)

| Provider/model/tier/item | USD per 1M | Source | Checked |
|---|---|---|---|
| anthropic/claude-haiku-5-5/standard_le100k/cache_write_5m_tokens | 0.1250 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-10-12 |
| anthropic/claude-haiku-5-5/standard_le100k/input_tokens | 0.1000 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-10-12 |
| anthropic/claude-haiku-5-5/standard_le100k/output_tokens | 0.5000 | https://platform.claude.com/docs/en/about-claude/pricing | 2026-10-12 |
| celeris/celeris-1-decision/standard/input_tokens | 0.0400 | https://docs.celeris.ai/pricing | 2026-10-12 |

## Estimated (projections)

Controls: budget $10.00, output-token cap 4000 per request, max concurrency 4, max fallback fraction 0.15, verification 5% at the batch tier. Fixed work (group naming, memo) is counted once.
Conservative = tokens ×1.25, output ×1.5 (≤ cap), retries ≥ 10%, fallback at the cap, fixed work ×2.0, concurrency efficiency 50%, Batch API 24 h (base 1 h).

| Scenario | Rows / outputs / quarantined / paid distinct | Mode | A3 | A5 | A8 | Fixed | **Total API** | Per 1,000 rows | Elapsed | Rows/s |
|---|---|---|---|---|---|---|---|---|---|---|
| full_with_reuse | 660,622 / 660,609 / 13 / 484,189 | base | $5.3278 | $2.9257 | $0.6637 | $0.003244 | **$8.9205** | $0.0135 | 3.1 h | 58.3 |
| full_with_reuse | 660,622 / 660,609 / 13 / 484,189 | conservative | $7.3258 | $4.4157 | $1.0632 | $0.007136 | **$12.8117** | $0.0194 | 28.3 h | 6.5 |
| full_no_reuse | 660,622 / 660,609 / 13 / 660,609 | base | $7.2691 | $3.9917 | $0.9055 | $0.003244 | **$12.1695** | $0.0184 | 3.9 h | 46.9 |
| full_no_reuse | 660,622 / 660,609 / 13 / 660,609 | conservative | $9.9950 | $6.0245 | $1.4505 | $0.007136 | **$17.4772** | $0.0265 | 29.8 h | 6.2 |
| scope_100k | 100,000 / 100,000 / 0 / 78,099 | base | $0.8594 | $0.4719 | $0.1071 | $0.003244 | **$1.4416** | $0.0144 | 81.1 min | 20.5 |
| scope_100k | 100,000 / 100,000 / 0 / 78,099 | conservative | $1.1816 | $0.7122 | $0.1715 | $0.007136 | **$2.0725** | $0.0207 | 24.7 h | 1.1 |

`full_no_reuse` pays every non-empty row; `full_with_reuse` pays each distinct text once (exact-text cache). `scope_100k` is our submission scope (PLAN §1.1); its out-of-scope cache fill costs $0.

