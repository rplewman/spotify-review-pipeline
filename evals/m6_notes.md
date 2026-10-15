# M6 evaluation notes (2026-10-15)

## Frozen label_config

`celeris-1-decision+shapeA+gate0.70+esc-claude-haiku-5-5-0.40+escfields-severity+extract-code-v1+prompt-v1+schema-v1`

Chosen on **dev_100 only** (`evals/threshold_sweep/sweep.md`): on the 23 reviews where Celeris top-1 and Haiku disagreed
(adjudicated by Rory), escalating topic to Haiku made topic worse (83% → 74% at gate 0.60), while escalating severity
helped (57% → 65% at 0.70, 78% at 0.80). Option B (severity only, gate 0.70, cap 0.40) was preferred over gate 0.80 because
it keeps the conservative 100k escalation cost under the $2.00 bucket, and the gain of 0.80 over 0.70 rests on ~3 reviews.
The cap sits above the measured below-gate share (36.8% on 10k), so almost nothing is `capped` (needs_review by cap).

Reference caveat: 77 of the dev_100 references are model agreement (Celeris top-1 = Haiku on topic/intent/severity), not
hand-checked; on those rows every gate scores the same. Only the 23 adjudicated rows separate the options.

## Golden 50 (not used for tuning)

`golden/golden_eval.md` (in this folder): topic 90%, intent 90%, severity 90% (MAE 0.18) accepted; quotes 100% exact substrings,
98% overlap the gold quote; needs_review flags errors with 100% precision but 15% recall (2 flagged, 13 reviews with an error).

### Observation: shape A vs shape B (not acted on)

On the golden 50, Celeris shape B (one review per request, no escalation) scored topic 96% / intent 96% / severity 82%
vs the frozen shape A config's 90% / 90% / 90%. Following PLAN §5 M6 ("if golden results drive a revision, disclose it"),
we checked on dev_100 before changing anything ($0.0022): on the 23 adjudicated reviews B got topic 20 vs 19, intent 16 vs 20,
severity 11 vs 13 (`evals/threshold_sweep/shapeB_dev.md`). Dev does not confirm the golden gain; both samples are small and
point in opposite directions. Shape A stays (also half the Celeris cost, and the 10k is already done under it). The golden
B numbers are reported as an observation only.

### Known limitation: sentiment is pulled toward 0

Sentiment MAE 0.31, only 52% within the predeclared ±0.25. A3's sentiment is the probability-weighted level, so spread-out
probabilities shrink it toward 0 (gold −0.5 → predicted ≈ 0). Using the most probable level would likely help, but dev_100
cannot test it (its references carry A3's sentiment), so any fix would be golden-driven and need disclosure plus fresh
held-out cases. Not changed in M6; documented for the README.
