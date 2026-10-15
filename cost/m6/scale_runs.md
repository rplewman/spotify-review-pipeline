# Scale runs (state/pipeline.db, frozen config)

`celeris-1-decision+shapeA+gate0.70+esc-claude-haiku-5-5-0.40+escfields-severity+extract-code-v1+prompt-v1+schema-v1`

| Run | Workers | max_rank | Stop | Seconds | Calls (A3/A5) | Texts decided | Texts/s | Requests/min | 429s | Failed |
|---|---|---|---|---|---|---|---|---|---|---|
| 507b1b | 1 | 2050 | drained | 184 | 74 (37/37) | 1817 | 9.8 | 24.1 | 0 | 0 |
| 0e40de | 2 | 6050 | drained | 152 | 136 (68/68) | 3360 | 22.2 | 53.9 | 0 | 0 |
| ebc60b | 4 | 10050 | drained | 75 | 134 (67/67) | 3315 | 44.4 | 107.8 | 0 | 0 |
| 3191ff | 4 | 10050 | drained | 0 | 0 (0/0) | 0 | 0.0 | 0.0 | 0 | 0 |

1-worker seconds per decided text: 0.102. Routes: {"accept": 5363, "escalate": 3129}. Records: {"completed": 10050, "quarantined": 0, "rows": 10050, "completed_via_exact_text_reuse": 1558}.
