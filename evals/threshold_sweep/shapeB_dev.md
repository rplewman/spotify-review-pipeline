# Shape A vs B on dev_100

Accepted-label matches. Only the adjudicated rows are a fair test: the 77 model-agreement references come from shape A + Haiku, so 'all' is biased toward A.

| Variant | Rows | topic | intent | severity |
|---|---|---|---|---|
| A top-1 | adjudicated (23) | 19 | 20 | 13 |
| A top-1 | all (100) | 96 | 97 | 90 |
| frozen (A + Haiku severity < 0.70) | adjudicated (23) | 19 | 20 | 15 |
| frozen (A + Haiku severity < 0.70) | all (100) | 96 | 97 | 92 |
| B top-1 | adjudicated (23) | 20 | 16 | 11 |
| B top-1 | all (100) | 88 | 88 | 71 |
| B + Haiku severity (A-gated, approx.) | adjudicated (23) | 20 | 16 | 15 |
| B + Haiku severity (A-gated, approx.) | all (100) | 88 | 88 | 92 |
