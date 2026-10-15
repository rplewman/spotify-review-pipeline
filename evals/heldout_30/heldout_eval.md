# Held-out 30 (fresh, hand-labeled by Rory)

fresh held-out reviews (seed ranks 10,051–10,080), labeled by Rory without model answers; never used for tuning.

| Check | M6 config | M6b config |
|---|---|---|
| sentiment | mean: MAE 0.171, within ±0.25 77% (n=30) | mode: MAE 0.175, within ±0.25 90% (n=30) |
| severity accepted | Celeris top-1 80% (24/30) | Haiku 83% (25/30) |
| topic / intent accepted (Celeris, unchanged) | 97% / 97% | same |

M6 severity was Haiku only below gate 0.70; the column shows Celeris everywhere as the no-escalation baseline.
