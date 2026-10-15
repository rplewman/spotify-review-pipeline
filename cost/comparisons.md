# Pilot comparisons (cost_100)

## Celeris request shape A vs B (A3 only)

100 reviews. Agreement of the top label: topic 82%, intent 85%, severity 73%; sentiment mean |Δ| 0.335.

| Shape | Requests | Input tokens | Tokens / review | Cost (list) | Latency / review |
|---|---|---|---|---|---|
| A | 2 | 27,509 | 275 | $0.001100 | 23 ms |
| B | 100 | 55,487 | 555 | $0.002219 | 118 ms |

## Escalation candidates on the low-confidence reviews

37 reviews, 44 questions (A3's top-2 per uncertain field).

| Variant | Model | Answered | = A3 top-1 | Attempts (ok) | In / out tokens | Cost (list) | $ / review | Latency |
|---|---|---|---|---|---|---|---|---|
| haiku-low | claude-haiku-5-5 | 44/44 | 64% | 1 (1) | 4,179 / 1,213 | $0.000905 | $0.000024 | 4.9 s |
| haiku-off | claude-haiku-5-5 | 44/44 | 68% | 1 (1) | 4,178 / 1,211 | $0.001057 | $0.000029 | 5.0 s |
| magnus-low | celeris-1-magnus | 44/44 | 57% | 1 (1) | 2,792 / 6,702 | $0.005250 | $0.000142 | 23.4 s |

Pairwise agreement: haiku-low vs haiku-off 95%, haiku-low vs magnus-low 80%, haiku-off vs magnus-low 80%

## Not run (disclosed, modeled only)

- sonnet_escalation: not run; modeled only (higher per-token price than Haiku, PLAN §4)
- haiku_only_enrichment: not run; modeled: every review through Haiku ≈ the contract's GPT-6 Luna illustration order of magnitude (PLAN §4 reference point)
- batch_vs_standard_other_stages: not run; Batch API = 50% of standard token prices (rates.csv), only A8 uses it
