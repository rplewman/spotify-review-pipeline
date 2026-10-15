# Golden evaluation (50 hand-labeled reviews)

label_config `celeris-1-decision+shapeA+gate0.70+esc-claude-haiku-5-5-0.40+escfields-severity+extract-code-v1+prompt-v1+schema-v1`; 50 evaluated, 0 missing. Accepted = the primary label or a recorded accepted alternative.

| Metric | Value |
|---|---|
| topic (exact / accepted) | 78% / 90% |
| intent (exact / accepted) | 86% / 90% |
| severity (exact / accepted / MAE) | 82% / 90% / 0.18 |
| sentiment (MAE / within ±0.25) | 0.308 / 52% |
| evidence quote (exact substring / overlaps gold quote) | 100% / 98% |
| entities (mean Jaccard, informational) | 0.81 |
| needs_review as an error flag (precision / recall) | 100% / 15% (2 flagged, 13 with an error) |
| needs_review = gold needs_review | 94% |
| ambiguous cases (gold needs_review or accepted alternatives) | 22 |

By route (all three fields accepted):

- accept: 27/33
- escalate: 10/17

Variant `state\m6_golden_B.db` (A3 top-1 only, 50 reviews), accepted: topic 96%, intent 96%, severity 82%

Topic confusion (gold → predicted, off-diagonal only):

- billing → usability: 1
- catalog → usability: 1
- other → billing: 1
- other → catalog: 1
- other → playback: 1
- other → usability: 1
- playback → support: 1
- playback → usability: 1
- usability → billing: 2
- usability → catalog: 1

Every disagreement: `evals/golden/golden_disagreements.csv` (39 rows, sentiment beyond ±0.25 included). Golden labels were not used for tuning.
