# Golden evaluation (50 hand-labeled reviews)

label_config `celeris-1-decision+shapeA+gate1.00+esc-claude-haiku-5-5-1.00+escfields-severity+extract-code-v1+prompt-v1+schema-v1+sentiment-mode-v1`; 50 evaluated, 0 missing. Accepted = the primary label or a recorded accepted alternative.

| Metric | Value |
|---|---|
| topic (exact / accepted) | 78% / 88% |
| intent (exact / accepted) | 86% / 90% |
| severity (exact / accepted / MAE) | 70% / 80% / 0.30 |
| sentiment (MAE / within ±0.25) | 0.305 / 62% |
| evidence quote (exact substring / overlaps gold quote) | 100% / 98% |
| entities (mean Jaccard, informational) | 0.81 |
| needs_review as an error flag (precision / recall) | 67% / 11% (3 flagged, 18 with an error) |
| needs_review = gold needs_review | 96% |
| ambiguous cases (gold needs_review or accepted alternatives) | 22 |

By route (all three fields accepted):

- escalate: 32/50

Topic confusion (gold → predicted, off-diagonal only):

- billing → usability: 1
- catalog → usability: 1
- other → billing: 1
- other → catalog: 1
- other → playback: 1
- other → usability: 2
- playback → support: 1
- usability → billing: 2
- usability → catalog: 1

Every disagreement: `evals/golden/golden_disagreements.csv` (40 rows, sentiment beyond ±0.25 included). Golden labels were not used for tuning.
