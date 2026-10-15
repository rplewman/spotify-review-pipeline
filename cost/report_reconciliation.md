# Reconciliation: M5 spend (2026-10-14, list price)

Ledger totals from every M5 database (`state/*.db`, settled rows; `calls.cost_usd` agrees). Usage × `rates.csv`.

| Run | DB | Celeris (prepaid credit) | Anthropic (cash) | Notes |
|---|---|---|---|---|
| Pilot attempt 1 | `pilot_attempt1.db` | $0.000550 (1 call, 13,738 in) | $0 | Haiku 400 "key not scoped to a workspace", not billed; run stopped |
| Pilot attempt 2 | `pilot_attempt2.db` | $0.000550 (1 call) | $0 | same 400 (`.env` not yet saved) |
| Shape B comparison | `pilot_shapeB.db` | $0.002219 (100 calls, 55,487 in) | — | |
| **Pilot (reported)** | `pilot.db` | $0.001100 (2 calls) | $0.005251 (4 calls) | cold 6 calls, warm 0 |
| Escalation comparison | `pilot_compare.db` | $0.005250 (magnus, 1 call) | $0.001961 (2 calls) | |
| **M5 total** | | **$0.009669** | **$0.007212** | **$0.016881** |

Earlier: M0 Celeris smoke test $0.00001224. Project spend to date ≈ **$0.01689** list (cash ≈ $0.0072).

Notes:
- The $10 project cap in the ledger is per database. `state/pipeline.db` does not see the pilot spend. M8 must start
  with this total deducted (or pass `--max-spend` that leaves room for it).
- Provider dashboards: not checked from here. Compare the Celeris Console usage for 2026-10-14 (UTC 2026-10-15
  03:20–03:30) against $0.009669 / 112,756 decision input tokens + 2,792 magnus in / 6,702 out, and the Anthropic
  Console (workspace in `.env`) against $0.007212 / 24,555 input, 9,497 output tokens. Record the result here.
- Haiku prompt caching: cache writes happened (1.1–1.3k tokens per call, $0.125/M), with no reads, because each
  stage has its own system prompt and every pilot stage sent only one or two calls. At 100k scale A5 sends many calls with the same system prompt, so expect reads.
