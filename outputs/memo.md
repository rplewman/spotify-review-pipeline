# Decision memo: next quarter's product priority

> Where should Spotify put the next quarter of product effort: access, usability, playback, or billing/support?
>
> **Priority: usability.** Generated once by `claude-haiku-5-5` (memo-prompt-v3, request `d242c731f47f42b2adfc27cced81aa51`) from `outputs/memo_input.json` (sha256 `d757947d1247f406…`); label_config `celeris-1-decision+shapeA+gate1.00+esc-claude-haiku-5-5-1.00+escfields-severity+extract-code-v1+prompt-v1+schema-v1+sentiment-mode-v1`. Every number below was checked by code against the saved aggregates; claim IDs resolve in `outputs/claims.csv`.

## Recommendation
Put the next quarter into usability. It leads the four areas on complaint_count (20720), severity_sum (51168) and share_of_members (20.4%), and it stays first on those three measures in the random sample. Playback leads on mean_severity (3.335726 vs 2.469498 for usability), and access has the highest mean_severity overall (3.637452) on the smallest volume, so both belong on the watch list rather than leading the plan.

## Supporting evidence
The largest usability issues are `usability-ads` with 8131 complaints [C02a] and severity_sum 18508 [C02b], and `usability-general` with 6087 complaints [C03a] and severity_sum 14578 [C03b]. `usability-interface` has 2678 complaints [C07a], and `usability-playback_controls` has 2377 complaints [C06a] with mean_severity 3.043332 [C06c]. Review 41082441-be8d-4508-9630-4ff7b05cb5c2 reports that skip and play buttons no longer show after an update. Review 8c256cff-407f-4f2c-ad6e-dabdeef1bf0d reports that an ad timer kept counting after the ad ended.

## Alternatives considered
Playback: 9055 complaints, severity_sum 30205, mean_severity 3.335726, the highest mean_severity of the four areas after access. Its top issue is `playback-general` with 4288 complaints [C04a]. It trails usability on volume and severity_sum. Billing/support: 8176 complaints, severity_sum 21907, and 350 cancellation intents. Its top issue is `billing-subscription` with 3636 complaints [C05a]. Access: 2339 complaints, severity_sum 8508, and share_of_members 2.3%. Its top issue is `access-login` with 1283 complaints [C08a] and mean_severity 4.246298 [C08c].

## Robustness
In comparison_random_100k, usability still leads complaint_count (13525), severity_sum (34936) and share_of_members (28.2%), ahead of playback (6976, 23350, 14.6%) and billing/support (6311, 17130, 13.2%). Access has the highest mean_severity there (3.959812), so the mean_severity order differs from the main ranking. The recommendation holds in both samples on volume and severity.

## Limitations
Of 660622 records, 279519 were completed. A random sample was classified by models. Other records were completed only when their text exactly matched a classified text, or through a batch of the most-duplicated texts. The remaining 381103 were quarantined (381090 out_of_scope_budget_100k, 13 empty_review_text), and 17708 need review. The main ranking over-weights short duplicated texts, so the random check is the unbiased view. Classification is model-based. Severity is a 1–5 classifier label, not measured impact, and the ranking weighs severity rather than business value. Reviews show what customers report, not why it happens. Cancellation counts are expressed intent to cancel, not churn. The outside bucket (60.4% of members) is excluded from the four areas.

## Recommendations

1. **Fix playback controls and interface regressions after updates** (usability): Customers report missing skip, play and shuffle controls and a broken interface after updates; playback_controls has the higher mean_severity among the usability issues. — issues `usability-playback_controls`, `usability-interface`; claims C06a, C06c, C07a; reviews 41082441-be8d-4508-9630-4ff7b05cb5c2, 4dd98e33-718c-417e-964d-bfa1419e09ca
2. **Review the ad experience, including timer behaviour** (usability): Ads and repeated songs are the largest usability issue by complaint count and severity_sum, though its mean_severity is lower. — issues `usability-ads`; claims C02a, C02b; reviews 8c256cff-407f-4f2c-ad6e-dabdeef1bf0d
3. **Investigate app freezing and failure to open** (usability): Customers report the app stuck on the loading screen or not opening, and this is the second-largest usability issue by severity_sum. — issues `usability-general`; claims C03a, C03b; reviews 2bfdb397-8572-4648-b75b-7be421da488a

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
| C06c | `usability-playback_controls` | mean_severity | 3.043332 |
| C07a | `usability-interface` | complaint_count | 2678 |
| C08a | `access-login` | complaint_count | 1283 |
| C08c | `access-login` | mean_severity | 4.246298 |

## Number trace

Every number in the memo and recommendations, mapped by code to a claim ID or to its field in `outputs/memo_input.json` (area-level numbers and totals have no contract claim metric).

| number | source |
|---|---|
| 20720 | areas[usability].complaint_count |
| 51168 | areas[usability].severity_sum |
| 20.4 | areas[usability].share_of_members |
| 3.335726 | areas[playback].mean_severity |
| 2.469498 | areas[usability].mean_severity |
| 3.637452 | areas[access].mean_severity |
| 8131 | claim C02a |
| 18508 | claim C02b |
| 6087 | claim C03a |
| 14578 | claim C03b |
| 2678 | claim C07a |
| 2377 | claim C06a |
| 3.043332 | claim C06c |
| 9055 | areas[playback].complaint_count |
| 30205 | areas[playback].severity_sum |
| 4288 | claim C04a |
| 8176 | areas[billing/support].complaint_count |
| 21907 | areas[billing/support].severity_sum |
| 350 | areas[billing/support].cancellation_count |
| 3636 | claim C05a |
| 2339 | areas[access].complaint_count |
| 8508 | areas[access].severity_sum |
| 2.3 | areas[access].share_of_members; top_issues[usability-playback_controls].share_of_members |
| 1283 | claim C08a |
| 4.246298 | claim C08c |
| 13525 | comparison_random_100k.areas[usability].complaint_count |
| 34936 | comparison_random_100k.areas[usability].severity_sum |
| 28.2 | comparison_random_100k.areas[usability].share_of_members |
| 6976 | comparison_random_100k.areas[playback].complaint_count |
| 23350 | comparison_random_100k.areas[playback].severity_sum |
| 14.6 | comparison_random_100k.areas[playback].share_of_members |
| 6311 | comparison_random_100k.areas[billing/support].complaint_count |
| 17130 | comparison_random_100k.areas[billing/support].severity_sum |
| 13.2 | comparison_random_100k.areas[billing/support].share_of_members |
| 3.959812 | comparison_random_100k.areas[access].mean_severity |
| 660622 | totals.records |
| 279519 | totals.completed |
| 381103 | totals.quarantined |
| 381090 | quarantine_reasons.out_of_scope_budget_100k |
| 13 | quarantine_reasons.empty_review_text |
| 17708 | totals.needs_review |
| 1 | label_config; label_config; top_issues[other-general].rank |
| 5 | label_config; label_config; areas[usability].issue_count … |
| 60.4 | areas[outside].share_of_members |

## Reviewer note (added by hand after generation, M12 — the generated text above is unchanged)

Two sentences above compare `mean_severity` wrongly. Code checks every number and ID in the memo, but not comparisons,
so these slipped through. Corrections, from `outputs/area_aggregates.csv` and `outputs/comparison_random_100k_areas.csv`:

1. *Recommendation:* "Playback leads on mean_severity (3.335726 …)". **Access leads** among the four areas (3.637452);
   playback is second (3.335726), then billing/support (2.679428) and usability (2.469498). The same sentence then says
   access is highest, so the sentence contradicts itself.
2. *Robustness:* "…so the mean_severity order differs from the main ranking". **It does not.** The order is
   access > playback > billing/support > usability in both the main ranking and the random 100k (3.959812 > 3.347190 >
   2.714308 > 2.583068).

Neither correction changes the recommendation. It rests on complaint_count, severity_sum and share_of_members, where
usability leads in both samples. Usability has the lowest mean_severity of the four areas: it is the largest problem by
volume, not the most severe per complaint. Rerunning `pipeline.cli memo` rewrites this file from cache without this note.
