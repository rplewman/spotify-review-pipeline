# Golden 50: M6 vs M6b

- M6: `celeris-1-decision+shapeA+gate0.70+esc-claude-haiku-5-5-0.40+escfields-severity+extract-code-v1+prompt-v1+schema-v1`
- M6b: `celeris-1-decision+shapeA+gate1.00+esc-claude-haiku-5-5-1.00+escfields-severity+extract-code-v1+prompt-v1+schema-v1+sentiment-mode-v1`

| Metric | M6 | M6b |
|---|---|---|
| topic accepted / exact | 90% / 78% | 88% / 78% |
| intent accepted / exact | 90% / 86% | 90% / 86% |
| severity accepted / exact | 90% / 82% | 80% / 70% |
| severity MAE | 0.18 | 0.30 |
| sentiment MAE / within ±0.25 | 0.308 / 52% | 0.305 / 62% |
| needs_review precision / recall | 100% / 15% | 67% / 11% |

The sentiment change was motivated by the golden 50 (M6), so its golden gain is not independent evidence; the held-out 30 (`evals/heldout_30/heldout_eval.md`) is.
