# Scale runs (state/pipeline.db, frozen config)

`celeris-1-decision+shapeA+gate1.00+esc-claude-haiku-5-5-1.00+escfields-severity+extract-code-v1+prompt-v1+schema-v1+sentiment-mode-v1`

| Run | Workers | max_rank | Stop | Seconds | Calls (A3/A5) | Texts decided | Texts/s | Requests/min | 429s | Failed |
|---|---|---|---|---|---|---|---|---|---|---|
| bfbe36 | 4 | 10050 | drained | 294 | 340 (170/170) | 8492 | 28.9 | 69.3 | 0 | 0 |

1-worker seconds per decided text: 0.035. Routes: {"escalate": 8492}. Records: {"completed": 10050, "quarantined": 0, "rows": 10050, "completed_via_exact_text_reuse": 1558}.
