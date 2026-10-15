# Threshold sweep (dev_100 reference, escalation rate on checkpoint_500)

Reference: 100 reviews, 23 adjudicated by Rory (the model disagreements); the rest = Celeris and Haiku agree (not hand-checked, scored equal at every gate).
Prediction per field: Haiku's answer when A3's top probability < gate, else A3's top-1 (no cap applied). Accuracy counts the accepted alternatives.

| Gate | topic | intent | severity | adjudicated: topic / intent / severity | escalated reviews (500) | A5 $ at 100k (uncapped) |
|---|---|---|---|---|---|---|
| 0.00 (A3 only) | 96% | 97% | 90% | 83% / 87% / 57% | 0.0% | $0.00 |
| 0.40 | 95% | 97% | 90% | 78% / 87% / 57% | 3.3% | $0.11 |
| **0.50** | 93% | 97% | 90% | 70% / 87% / 57% | 16.9% | $0.53 |
| **0.60** | 94% | 97% | 91% | 74% / 87% / 61% | 35.5% | $1.12 |
| **0.70** | 94% | 97% | 92% | 74% / 87% / 65% | 53.7% | $1.69 |
| 0.80 | 94% | 97% | 95% | 74% / 87% / 78% | 70.1% | $2.21 |
| 1.01 (Haiku on all) | 95% | 98% | 95% | 78% / 91% / 78% | 100.0% | $3.15 |

Signal check (Q11): gating on Celeris `confidence` instead of the raw top probability:

| Gate | topic | intent | severity |
|---|---|---|---|
| 0.50 | 94% | 97% | 91% |
| 0.60 | 94% | 97% | 91% |
| 0.70 | 94% | 97% | 95% |
