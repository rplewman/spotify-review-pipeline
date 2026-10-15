## Full-corpus decision point (PLAN §1.2)

**NO-GO** (computed 2026-10-15; **confirmed by Rory 2026-10-15**).

| Condition | Value | OK |
|---|---|---|
| 1. shape A adopted | `celeris-1-decision+shapeA+gate0.70+esc-claude-haiku-5-5-0.40+escfields-severity+extract-code-v1+prompt-v1+schema-v1` | True |
| 2. spend to date $0.2259 + 475,697 texts × $0.0000327 + downstream $1.070 ≤ $9.00 | $16.86 | False |
| 3. escalation rate ≤ 15% (or the cap makes it fit) | 37% below the gate; cap 0.40 | False |
| 4. finish ≥ 2 days before 2026-10-20 | 6.7 h enrichment (conservative) + 24.0 h lead → 2026-10-16T11:04+00:00 (latest 2026-10-18T00:00+00:00) | True |

100k scope projected total (conservative, incl. spend to date): $2.68; headroom under $9.00: $6.32.

Frequency add-on (NO-GO path, §1.1), upper-end cost at the measured per-text cost:

| Top distinct texts | + rows | coverage point | ≤ cost |
|---|---|---|---|
| 1,000 | 6,916 | 0.6224 | $0.03 |
| 5,000 | 17,894 | 0.6324 | $0.16 |
| 10,000 | 26,577 | 0.6403 | $0.33 |
| 25,000 | 41,577 | 0.6539 | $0.82 |
| 406,090 | 422,667 | 1.0 | $13.29 |

Recommended size: **25,000** (largest step whose upper-end cost (per-text cost, no length discount) ≤ half the headroom under $9.00).
