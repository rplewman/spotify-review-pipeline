# Decision memo: next quarter's product priority

> Where should Spotify put the next quarter of product effort: access, usability, playback, or billing/support?
>
> **Priority: playback.** Generated once by `claude-haiku-5-5` (memo-prompt-v1, request `6eda06b0d72a41e0842f26b3db96456c`) from `outputs/memo_input.json` (sha256 `e24b6b7a9423f8d5…`); label_config `pilot-celeris-1-decision+shapeA+gate0.60+esc-claude-haiku-5-5-0.15+extract-code-v1+prompt-v1+schema-v1`. Every number below was checked by code against the saved aggregates; claim IDs resolve in `outputs/claims.csv`.

## Recommendation
Put the next quarter of product effort into playback. Playback carries the largest share of complaint severity among the four areas: 40 severity points, 27.0% of severity, from 10 complaints across 5 issues. Usability has more complaints (13) but lower severity (37, 25.0%), so playback is the stronger case on severity. This ranking weighs severity, not business value.

## Supporting evidence
- `playback-general` (playback freezes and bugs): 5 complaints [C03a], severity sum 19 [C03b], mean severity 3.8 [C03c]. Review 66a29b3c-c14d-440e-b722-b23e0c99ae82 reports the app stuck on the loading or logo screen (severity 5).
- `playback-stops_skips` (songs stop partway through): 2 complaints [C06a], severity sum 8 [C06b]. Review 6c257eba-3dca-49f6-b7f0-4c4484d81e26 describes music stopping halfway and not resuming where it left off.
- `downloads-offline_mode` (offline mode not working): 1 complaint [C10a], severity 5 [C10b]. Review dcff57c6-abd6-413b-b255-aec3dc34fcbd reports offline playback failing.

## Alternatives considered
Usability: 5 issues, 13 complaints, 1 cancellation, severity 37 (25.0%). Its top issue `usability-general` has 4 complaints [C04a], mostly about ads and restrictions, so usability is a credible second choice if the team prioritises volume over severity. Billing/support: 2 issues, 6 complaints, severity 23 (15.5%). `billing-premium_limits` has 5 complaints [C02a] and severity 19 [C02b], but it is closer to a pricing-policy decision than a product-effort one. Access: 2 issues, 2 complaints, severity 10 (6.8%). Its mean severity is the highest at 5.0, but its volume is small.

## Limitations
Area classification is model-based, and 23 records need review. The ranking weighs severity, not business value. Areas outside the four (catalog and other, severity 38) are excluded from this decision, even though `other-general` ranks first overall.

## Recommendations

1. **Triage playback freezes and loading-screen failures** (playback): Playback-general has the largest playback severity sum (19) across 5 complaints, and the top quote describes the app stuck on the loading screen. — issues `playback-general`; claims C03a, C03b; reviews 66a29b3c-c14d-440e-b722-b23e0c99ae82
2. **Fix resume behaviour when songs stop partway through** (playback): Songs stopping mid-track and not resuming is a high-severity pattern (mean severity 4.0) and is distinct from the general freeze reports. — issues `playback-stops_skips`; claims C06a, C06b; reviews 6c257eba-3dca-49f6-b7f0-4c4484d81e26
3. **Restore offline mode reliability** (playback): Offline mode failing is a severe single-complaint issue (severity 5) in the downloads topic that rolls up into playback. — issues `downloads-offline_mode`; claims C10a, C10b; reviews dcff57c6-abd6-413b-b255-aec3dc34fcbd

## Claims cited

| claim_id | issue_id | metric | value |
|---|---|---|---|
| C02a | `billing-premium_limits` | complaint_count | 5 |
| C02b | `billing-premium_limits` | severity_sum | 19 |
| C03a | `playback-general` | complaint_count | 5 |
| C03b | `playback-general` | severity_sum | 19 |
| C03c | `playback-general` | mean_severity | 3.800000 |
| C04a | `usability-general` | complaint_count | 4 |
| C06a | `playback-stops_skips` | complaint_count | 2 |
| C06b | `playback-stops_skips` | severity_sum | 8 |
| C10a | `downloads-offline_mode` | complaint_count | 1 |
| C10b | `downloads-offline_mode` | severity_sum | 5 |
