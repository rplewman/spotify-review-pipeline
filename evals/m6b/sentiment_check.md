# Sentiment: probability-weighted (mean) vs most probable level (mode)

From the saved Celeris probabilities (no new calls). golden-driven: the golden 50 showed the pull toward 0 (M6), so this is disclosed and re-checked on the fresh held-out 30 (heldout-score).

| Set | mean (M6) | mode (M6b) |
|---|---|---|
| golden_50 | MAE 0.308, within ±0.25 52% (n=50) | MAE 0.285, within ±0.25 64% (n=50) |
| dev_100 | MAE 0.076, within ±0.25 95% (n=100) | MAE 0.138, within ±0.25 96% (n=100) |

dev_100's sentiment reference is mostly the old model pre-fill, so it favours `mean`; read golden and the held-out 30 instead.
