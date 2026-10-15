# Decision memo: next quarter's product priority

> Where should Spotify put the next quarter of product effort: access, usability, playback, or billing/support?
>
> **Priority: usability.** Generated once by `claude-haiku-5-5` (memo-prompt-v2, request `3177221b6bcb4a07a6a34dce5829fbd1`) from `outputs/memo_input.json` (sha256 `62ba8adbd945f70a…`); label_config `celeris-1-decision+shapeA+gate1.00+esc-claude-haiku-5-5-1.00+escfields-severity+extract-code-v1+prompt-v1+schema-v1+sentiment-mode-v1`. Every number below was checked by code against the saved aggregates; claim IDs resolve in `outputs/claims.csv`.

## Recommendation
Put the next quarter of effort into usability. It has the largest area roll-up on every measure here: 20720 complaints, 423 expressed cancellation intents, a severity_sum of 51168, and 20.4% share of members. Playback is second (9055 complaints, severity_sum 30205), billing/support third (8176 complaints, severity_sum 21907), and access last (2339 complaints, severity_sum 8508).

## Supporting evidence
The top usability issues are `usability-ads` (too many ads and repeated songs) with 8131 complaints [C02a] and severity_sum 18508 [C02b]; `usability-general` (app frozen or will not open) with 6087 complaints [C03a] and severity_sum 14578 [C03b]; `usability-interface` with 2678 complaints [C07a]; and `usability-playback_controls` with 2377 complaints [C06a]. Review 2bfdb397-8572-4648-b75b-7be421da488a says the app is completely frozen on the loading screen even after reinstalling. Review 41082441-be8d-4508-9630-4ff7b05cb5c2 says the skip and play buttons no longer show up.

## Alternatives considered
Playback is the closest alternative: 9055 complaints, severity_sum 30205, 8.9% of members. Its top issue `playback-general` has 4288 complaints [C04a]. Billing/support has 8176 complaints, severity_sum 21907, and 8.0% of members; its top issue `billing-subscription` has 3636 complaints [C05a]. Access has 2339 complaints, severity_sum 8508, and 2.3% of members. Its top issue `access-login` has 1283 complaints [C08a] and the highest issue mean severity, 4.246298 [C08c], but the smallest volume.

## Robustness
The order holds in the random comparison sample, which is the unbiased check. There usability has 13525 complaints and severity_sum 34936 (28.2% of members). Playback has 6976 complaints and severity_sum 23350. Billing/support has 6311 complaints and severity_sum 17130. Access has 1493 complaints and severity_sum 5912. Usability is first and access is last in both views, so the recommendation holds in both.

## Limitations
Of all source records, a random sample was classified by models. Other records were completed only when their text exactly matched an already-classified text, or through a batch of the most-duplicated texts. The remaining 381103 records were not classified and are quarantined. The main ranking uses all 279519 completed records and over-weights short duplicated texts, so the random comparison sample is the check that matters. Classification is model-based, and 17708 records need review. Severity is a classifier label, not measured impact. The ranking weighs severity, not business value. Reviews show what customers report, not why it happens. Cancellations are expressed intent in reviews, not actual churn or lost revenue. The large outside bucket (catalog and other, 60.4% of members) is excluded from the four areas.

## Recommendations

1. **Fix app freezes and launch failures** (usability): The second-largest usability issue by complaints, and the one customers describe as the app not opening or getting stuck on loading. — issues `usability-general`; claims C03a, C03b; reviews 2bfdb397-8572-4648-b75b-7be421da488a
2. **Reduce ad load and repeated-song complaints** (usability): The largest usability issue by complaints and severity, so it carries the most weight in the usability roll-up. — issues `usability-ads`; claims C02a, C02b; reviews 8c256cff-407f-4f2c-ad6e-dabdeef1bf0d
3. **Restore playback controls in podcasts and music** (usability): Customers report skip, play, and queue controls missing or broken, which affects core listening. — issues `usability-playback_controls`; claims C06a; reviews 41082441-be8d-4508-9630-4ff7b05cb5c2

## Claims cited

| claim_id | issue_id | metric | value |
|---|---|---|---|
| C02a | `usability-ads` | complaint_count | 8131 |
| C02b | `usability-ads` | severity_sum | 18508 |
| C03a | `usability-general` | complaint_count | 6087 |
| C03b | `usability-general` | severity_sum | 14578 |
| C04a | `playback-general` | complaint_count | 4288 |
| C05a | `billing-subscription` | complaint_count | 3636 |
| C06a | `usability-playback_controls` | complaint_count | 2377 |
| C07a | `usability-interface` | complaint_count | 2678 |
| C08a | `access-login` | complaint_count | 1283 |
| C08c | `access-login` | mean_severity | 4.246298 |

## Number trace

Every number in the memo and recommendations, mapped by code to a claim ID or to its field in `outputs/memo_input.json` (area-level numbers and totals have no contract claim metric).

| number | source |
|---|---|
| 20720 | areas[usability].complaint_count |
| 423 | areas[usability].cancellation_count |
| 51168 | areas[usability].severity_sum |
| 20.4 | areas[usability].share_of_members |
| 9055 | areas[playback].complaint_count |
| 30205 | areas[playback].severity_sum |
| 8176 | areas[billing/support].complaint_count |
| 21907 | areas[billing/support].severity_sum |
| 2339 | areas[access].complaint_count |
| 8508 | areas[access].severity_sum |
| 8131 | claim C02a |
| 18508 | claim C02b |
| 6087 | claim C03a |
| 14578 | claim C03b |
| 2678 | claim C07a |
| 2377 | claim C06a |
| 8.9 | areas[playback].share_of_members |
| 4288 | claim C04a |
| 8.0 | areas[billing/support].share_of_members; top_issues[usability-ads].share_of_members; top_issues[access-login].rank … |
| 3636 | claim C05a |
| 2.3 | areas[access].share_of_members; top_issues[usability-playback_controls].share_of_members |
| 1283 | claim C08a |
| 4.246298 | claim C08c |
| 13525 | comparison_random_100k.areas[usability].complaint_count |
| 34936 | comparison_random_100k.areas[usability].severity_sum |
| 28.2 | comparison_random_100k.areas[usability].share_of_members |
| 6976 | comparison_random_100k.areas[playback].complaint_count |
| 23350 | comparison_random_100k.areas[playback].severity_sum |
| 6311 | comparison_random_100k.areas[billing/support].complaint_count |
| 17130 | comparison_random_100k.areas[billing/support].severity_sum |
| 1493 | comparison_random_100k.areas[access].complaint_count |
| 5912 | comparison_random_100k.areas[access].severity_sum |
| 381103 | totals.quarantined |
| 279519 | totals.completed |
| 17708 | totals.needs_review |
| 60.4 | areas[outside].share_of_members |
