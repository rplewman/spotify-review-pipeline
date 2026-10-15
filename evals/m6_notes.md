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

---

# M6b fidelity upgrade + re-freeze (2026-10-15; supersedes the M6 freeze above)

## Frozen label_config (CLI default, `pipeline/cli.py` STAGE_DEFAULTS)

`celeris-1-decision+shapeA+gate1.00+esc-claude-haiku-5-5-1.00+escfields-severity+extract-code-v1+prompt-v1+schema-v1+sentiment-mode-v1`

- **Sentiment = the most probable level** (−1, −0.5, 0, +0.5, +1; ties → the level nearer 0) instead of Celeris's
  probability-weighted score. **Golden-driven** (the M6 golden 50 showed the pull toward 0), so disclosed here and checked on
  fresh held-out reviews.
- **Haiku 5.5 decides severity for every review** (gate 1.00 = always, cap 1.00), from Celeris's top-2 candidates. Chosen
  in PLAN v4.2 from dev_100 (adjudicated 65% → 78%). Topic and intent stay with Celeris.
- Topic: a stronger model (Sonnet 5.5) was tested and **not adopted** (below).

## Held-out 30 (fresh: seed ranks 10,051–10,080, labeled blind by Rory, never used for tuning) — `evals/heldout_30/`

| Check | M6 config | M6b config |
|---|---|---|
| sentiment | mean: MAE 0.171, within ±0.25 77% | mode: MAE 0.175, within ±0.25 90% |
| severity accepted | Celeris top-1 24/30 | Haiku 25/30 |

Sentiment: the average error does **not** improve on fresh data; what improves is the share within the predeclared
±0.25 tolerance (fewer large misses). Report it that way. n = 30.

## Sonnet 5.5 topic test (dev_100, every topic option) — `evals/topic_test/`

Celeris top-1 19/23 on the adjudicated rows; Sonnet below any gate 19/23 (gain 0; all 100: 95→92). Projected cost at
gate 0.50 already $10.18 conservative against a $0.72 limit. **Not adopted.** Test cost $0.1645 list, of which $0.1135 is
one Sonnet attempt that failed with a client connection error and was settled at its estimate (billing unknown, likely $0).

## Golden 50, M6 vs M6b — `golden_m6_vs_m6b.md` in the golden eval folder (M6 copy: `golden_eval_m6.*`)

Topic 90→88%, intent 90→90%, **severity 90→80% (MAE 0.18→0.30)**, sentiment within ±0.25 52→62% (MAE 0.308→0.305).

**The severity drop is run-to-run noise in Haiku, not the "every review" change.** Celeris's severity top-1 on the golden 50
is 40/50 in both runs. In M6, Haiku (escalated below 0.70) corrected 5 of them; in the M6b run Haiku gave different answers
on 6 golden reviews with the same model, prompt and candidates, and corrected none. Across the 3,129 texts Haiku judged in
both runs, **27.2% of its severity answers changed**. M6b's Haiku agrees with Celeris top-1 more often (71% vs 66% on the
p < 0.70 texts; 86% over all texts) — possibly because a batch of 50 now mixes confident and uncertain reviews (untested).
Scoring the three policies on the **same** M6b-run answers over the 80 independent reviews (golden 50 + held-out 30):
Celeris only 64, Haiku below 0.70 65, Haiku on every review 65 — no measured gain from "every review" on independent
data; dev_100 is the only evidence for it. Rory kept the M6b freeze (2026-10-15) knowing this; the extra conservative
cost (~$2.4 at 100k + add-on) fits the budget.

Celeris itself also varies run to run (batch composition): between the M6 and M6b runs on the same 8,492 texts, top-1
changed for topic 14.3%, intent 7.4%, severity 17.1%.

## 10k re-run and projection — `cost/m6b/`, `cost/report.*` (M6 report kept as `cost/report_m6.*`)

Seed ranks 1–10,050 under the M6b freeze: 8,492 texts, 10,050 records, 0 quarantined, 340 calls (170 A3 / 170 A5),
0 failed, 0 429s, 294 s at 4 workers; $0.2912 list (Celeris $0.0937 credit, Haiku $0.1975 cash).
Projection (calculator, fallback 1.00, verification 20% batch): 100k $3.11 base / $4.73 conservative;
**100k + add-on 25k** (103,099 texts, 141,577 rows) $4.10 / **$6.24** (`cost/m6b/report_100k_plus_addon.md`).
**Gate:** spend to date $0.6831 + $6.24 = **$6.92 ≤ $9.00** ✔ (conservative; counts the 8,492 done texts again).
