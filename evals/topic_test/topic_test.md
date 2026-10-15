# Stronger-model topic test (claude-sonnet-5-5, effort low) on dev_100

the pipeline's A5 request (prompts/escalation_system_v1.md) with all 8 topics as candidates, sorted by Celeris's probability (shown). Test cost $0.1645 list (3 calls; $0.00164/review). Reference: dev_100 (23 adjudicated by Rory; the rest are model-agreement references between Celeris and Haiku, which favour Celeris).

Celeris top-1: adjudicated 19/23, all 96/100.

| Gate (model below) | Escalated | Adjudicated ok | Gain | All 100 ok | Projected 100k+add-on (cons.) | Passes |
|---|---|---|---|---|---|---|
| 0.50 | 4% | 19/23 | +0 | 95/100 | $10.18 | no |
| 0.60 | 12% | 19/23 | +0 | 93/100 | $30.53 | no |
| 0.70 | 24% | 19/23 | +0 | 93/100 | $61.05 | no |
| 0.80 | 39% | 19/23 | +0 | 93/100 | $99.21 | no |
| 0.90 | 56% | 19/23 | +0 | 92/100 | $142.46 | no |
| 1.01 | 100% | 19/23 | +0 | 92/100 | $254.39 | no |

Rule: adopt only if it beats Celeris top-1 on the adjudicated dev rows by ≥ 2 reviews AND the projected conservative cost ≤ $0.72 (= escalation $5.00 + reserve $1.25 − Haiku severity on all $4.53 − $1.00 floor).

**Decision: NOT adopted — topic stays with Celeris.**
