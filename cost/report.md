# Cost report — seed ranks 1–10,050 (golden 50 + analysis_10000) under the frozen config + projections

Offline replay: `python cost/calculator.py` (usage.csv × rates.csv; no API calls). List prices; credits and cash are shown apart; local compute is not metered (unknown, not 0).

## Warnings

- **OVER BUDGET: full_with_reuse (base) $11.01 > budget $10.00**
- **OVER BUDGET: full_with_reuse (conservative) $16.91 > budget $10.00**
- **OVER BUDGET: full_no_reuse (base) $15.02 > budget $10.00**
- **OVER BUDGET: full_no_reuse (conservative) $23.07 > budget $10.00**

## Measured (seed ranks 1–10,050 (golden 50 + analysis_10000) under the frozen config)

| Item | Value |
|---|---|
| Input | `analysis_10000.csv (+ golden ranks 1–50) = seed ranks 1–10050` sha256 `eaa62ca6d44d7173…` (manifest match: True) |
| Review IDs | 10050 (ids sha256 `72522ad15de1c3e1…`) |
| Records completed / quarantined | 10050 / 0 |
| Unique texts | 8492 |
| Exact-text cache hits (rows completed by reuse) | 1558 |
| A4 routes | {"accept": 5363, "escalate": 3129} |
| Verification sample | rate 0.2 (declared pilot rate), 25 texts verified, standard (real time) |
| label_config | `celeris-1-decision+shapeA+gate0.70+esc-claude-haiku-5-5-0.40+escfields-severity+extract-code-v1+prompt-v1+schema-v1` |
| Cold run | 15.0 min, 344 calls, $0.1861 |
| Warm rerun | 0.2 s, 0 calls (0 enrichment), incremental $0.000000 |
| Throughput (cold) | 11.17 rows/s |
| API subtotal (list) | **$0.1861** |
| Credits applied (prepaid celeris) | $0.0937 |
| Cash spent | $0.0924 |
| Unknown-billing attempts (ledger estimate, not in subtotal) | 0 ($0.000000) |
| Cost per 1,000 input rows | $0.0185 |
| Cost per completed record | $0.000019 |
| Local compute | unknown (local laptop CPU and SQLite; not billed, not metered) |

### By stage

| Stage | Config | Units | Attempts (ok/failed) | Usage | Cost (list) |
|---|---|---|---|---|---|
| A3 | provider=celeris, model=celeris-1-decision, effort=n/a (decision model), prompt_version=prompt-v1, schema_version=schema-v1, shape=A, batch_size=50, workers=[1, 2, 4] | 8492 distinct text decided | 172 (172/0) | input_tokens: 2,343,114 | $0.0937 |
| A5 | model=claude-haiku-5-5, effort=low, prompt_version=escalation-prompt-v1, gate=0.6, esc_fraction_cap=0.15, batch_size=≤ 50 (the batch's escalated texts), workers=1 | 3129 escalated text | 172 (172/0) | input_tokens: 246,837<br>cache_write_5m_tokens: 1,330<br>output_tokens: 121,175<br>cache_read_tokens: 227,430 | $0.0877 |
| A8 | model=claude-haiku-5-5, effort=low, prompt_version=verifier-prompt-v1, batch_size=50, workers=1, tier=standard | 25 verified text | 1 (1/0) | input_tokens: 1,082<br>cache_write_5m_tokens: 1,315<br>output_tokens: 2,262 | $0.001404 |
| A9 | model=claude-haiku-5-5, effort=low, prompt_version=grouper-prompt-v1 | 1 run | 1 (1/0) | input_tokens: 4,621<br>output_tokens: 1,062 | $0.000993 |
| A11 | model=claude-haiku-5-5, effort=low, prompt_version=memo-prompt-v1 | 1 run | 1 (1/0) | input_tokens: 5,263<br>cache_write_5m_tokens: 1,157<br>output_tokens: 3,159 | $0.002250 |

### Stage times (cold → warm)

| Stage | Cold | Warm | Cold calls | Warm calls |
|---|---|---|---|---|
| ingest | 2.0 s | unknown | 0 | — |
| enrich | 14.4 min | 0.2 s | 344 | 0 |
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

Controls: budget $10.00, output-token cap 4000 per request, max concurrency 4, max fallback fraction 0.4, verification 5% at the batch tier. Fixed work (group naming, memo) is counted once.
Conservative = tokens ×1.25, output ×1.5 (≤ cap), retries ≥ 10%, fallback at the cap, fixed work ×2.0, concurrency efficiency 50%, Batch API 24 h (base 1 h).

| Scenario | Rows / outputs / quarantined / paid distinct | Mode | A3 | A5 | A8 | Fixed | **Total API** | Per 1,000 rows | Elapsed | Rows/s |
|---|---|---|---|---|---|---|---|---|---|---|
| full_with_reuse | 660,622 / 660,609 / 13 / 484,189 | base | $5.3439 | $5.0011 | $0.6637 | $0.003244 | **$11.0119** | $0.0167 | 4.5 h | 41.2 |
| full_with_reuse | 660,622 / 660,609 / 13 / 484,189 | conservative | $7.3479 | $8.4963 | $1.0632 | $0.007136 | **$16.9145** | $0.0256 | 30.9 h | 5.9 |
| full_no_reuse | 660,622 / 660,609 / 13 / 660,609 | base | $7.2910 | $6.8233 | $0.9055 | $0.003244 | **$15.0230** | $0.0227 | 5.7 h | 32.2 |
| full_no_reuse | 660,622 / 660,609 / 13 / 660,609 | conservative | $10.0251 | $11.5920 | $1.4505 | $0.007136 | **$23.0748** | $0.0349 | 33.4 h | 5.5 |
| scope_100k | 100,000 / 100,000 / 0 / 78,099 | base | $0.8620 | $0.8067 | $0.1071 | $0.003244 | **$1.7789** | $0.0178 | 93.8 min | 17.8 |
| scope_100k | 100,000 / 100,000 / 0 / 78,099 | conservative | $1.1852 | $1.3704 | $0.1715 | $0.007136 | **$2.7343** | $0.0273 | 25.1 h | 1.1 |

`full_no_reuse` pays every non-empty row; `full_with_reuse` pays each distinct text once (exact-text cache). `scope_100k` is our submission scope (PLAN §1.1); its out-of-scope cache fill costs $0.

## Full-corpus decision point (PLAN §1.2)

**NO-GO** (computed 2026-10-15; Rory confirms before M8).

| Condition | Value | OK |
|---|---|---|
| 1. shape A adopted | `celeris-1-decision+shapeA+gate0.70+esc-claude-haiku-5-5-0.40+escfields-severity+extract-code-v1+prompt-v1+schema-v1` | True |
| 2. spend to date $0.2259 + 475,697 texts × $0.0000327 + downstream $1.070 ≤ $9.00 | $16.86 | False |
| 3. escalation rate ≤ 15% (or the cap makes it fit) | 37% below the gate; cap 0.40 | False |
| 4. finish ≥ 2 days before 2026-10-20 | 6.7 h enrichment (conservative) + 24.0 h lead → 2026-10-16T11:04+00:00 (latest 2026-10-18T00:00+00:00) | True |

100k scope projected total (conservative, incl. spend to date): $2.68; headroom under $9.00: $6.32.

Frequency add-on (NO-GO path, §1.1), upper-end cost at the measured per-text cost:

| Top distinct texts | + rows | coverage point | ≤ cost |
|---|---|---|---|
| 1,000 | 6,916 | 0.6224 | $0.03 |
| 5,000 | 17,894 | 0.6324 | $0.16 |
| 10,000 | 26,577 | 0.6403 | $0.33 |
| 25,000 | 41,577 | 0.6539 | $0.82 |
| 406,090 | 422,667 | 1.0 | $13.29 |

Recommended size: **25,000** (largest step whose upper-end cost (per-text cost, no length discount) ≤ half the headroom under $9.00).
