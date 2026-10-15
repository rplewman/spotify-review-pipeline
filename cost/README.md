# cost/ — pilot evidence and cost calculator

## Offline replay (default, no API calls)

```
.venv/Scripts/python cost/calculator.py
```

This reads `usage.csv` × `rates.csv` plus `pilot_measured.json` and `pilot_calls.jsonl`, then writes `report.md`, `report.html`
and `report.json`. It only needs the standard library. You can edit `rates.csv` and rerun it. You can also set these controls:

| Flag | Meaning | Default |
|---|---|---|
| `--budget` | budget used for the over-budget warnings (USD, list price) | 10 |
| `--output-token-cap` | max output tokens per request (caps the conservative output estimate) | 4000 |
| `--max-concurrency` | workers assumed in the elapsed-time projection | 4 |
| `--max-fallback-fraction` | cap on the escalation (A5) share | 0.15 |
| `--verify-rate`, `--verify-tier` | verification sample rate and tier (`batch` or `standard`) | 0.05, batch |
| `--full-rows`, `--full-distinct`, `--scope-rows`, `--scope-distinct` | projected volumes | contract / M1 figures |

How the costs are computed:
- **Billing math.** Each usage row is one billed item, and no row overlaps another: plain input, cache read, cache write or output.
  The cost of a row is `units × usd_per_unit / 1,000,000`. Cached input has already been taken out of plain input.
  Thinking tokens are part of output tokens and are counted once. Each row is priced at the tier the call actually used.
- **Calls with no usage.** If a provider returned no usage for an attempt, that attempt is listed as "unknown billing" at its
  ledger estimate. It is never shown as 0 and is not included in the API subtotal.
- **Projections.** They use the pilot's measured tokens per unit for each stage (not its dollar amounts). Volumes come from
  the contract: the full run is 660,622 rows, 660,609 outputs, 13 quarantined and 484,189 distinct texts. There is also a
  no-reuse comparison and our 100k scope (78,099 distinct texts). Each gets a base and a conservative estimate. Fixed work
  (naming the groups, the memo) is counted once.
- **Gate (tested in `tests/test_m5_cost.py`).** Doubling every rate doubles the API subtotal, both measured and projected,
  and leaves times, counts and local costs unchanged. Changing the projected volume leaves the measured pilot section unchanged.

## Explicit pilot (paid; needs `--paid` and `--max-spend`)

```
.venv/Scripts/python -m pipeline.cli pilot --provider celeris --paid --max-spend 0.05
```

The pilot runs **cold**: a new `state/pilot.db`, the unchanged `data/raw/cost_100.csv` (checksum checked against the
dataset manifest), 1 worker, and every stage (ingest → enrich A3–A7 → verify A8 at the standard tier with a declared rate of 0.2 →
group → rank → memo). It then reruns **warm** on the same DB. The expected result is zero new enrichment calls; any
downstream call is disclosed. Outputs: `pilot_records.jsonl` (separate from `grading/`), `pilot_calls.jsonl`, `usage.csv`,
`pilot_measured.json`, and the stage outputs under `pilot_run/`. Without `--provider celeris` the same command runs
offline on the fake provider.

## Comparisons (paid, small) — `scripts/pilot_compare.py`

```
.venv/Scripts/python scripts/pilot_compare.py shape-b --provider celeris --paid --max-spend 0.01
.venv/Scripts/python scripts/pilot_compare.py escalation --provider anthropic --paid --max-spend 0.03
.venv/Scripts/python scripts/pilot_compare.py report
```

- `shape-b` runs Celeris request shape B (one review per request) on the same 100 reviews in its own DB, so the pilot's
  records are never touched. The report compares it with shape A: label agreement, tokens per review and latency.
- `escalation` takes the pilot's low-confidence reviews and sends them to three candidates: Haiku 5.5 at effort low
  (= A5), Haiku 5.5 with thinking off, and `celeris-1-magnus`.
- `report` writes `comparisons.{md,json}`. Options that were not run are listed there as modeled only.

## Reconciliation

The ledger records list price. After a paid run, compare `report.md` with the Celeris Console usage and the
Anthropic Console usage for the same day. The reconciliation note lives in `report_reconciliation.md`.
