# M9 — Analysis: verify, group, rank, memo (2026-10-15)

label_config `celeris-1-decision+shapeA+gate1.00+esc-claude-haiku-5-5-1.00+escfields-severity+extract-code-v1+prompt-v1+schema-v1+sentiment-mode-v1`.

## Scope decision (approved by Rory, this session)
PLAN §1.1 left the frequency add-on out of the ranking, but `check_submission.py` flags `ungrouped_complaints` for any
completed complaint/cancellation record missing from `membership.csv` (and ranking/claims are recomputed from it).
So the **contract ranking groups every completed complaint/cancellation record: 101,649** (random 100k 47,920 ·
out-of-scope cache fills 36,336 · add-on 17,393). The unbiased view is kept as a code-only comparison:
`outputs/comparison_random_100k_{ranking,areas}.csv` (47,920 members), shown to the memo, which must report it.
Among the four areas the order is the same in both: usability > playback > billing/support > access (by severity_sum).
The duplicated short texts mostly inflate `other-general` (outside the four areas): 57,823 of 101,649 members.

## A8 verification (Batch API, 20%, seed verify-v1)
- Sample 20,683 distinct texts (15,658 random 100k + 5,025 add-on) → **20,483 verified, 200 unverified (1.0%)**.
  Job `msgbatch_01KBzEx9evSNE9nUkNj4b4G3` (414 requests) + retry job `msgbatch_01FSTTmboXgqgy3aYHDb1wgD` (5 requests).
- Unverified: 9 failed requests all hit `max_tokens` (5,000 output tokens) → unparseable; 50 reviews × ~90 tokens sits
  close to the cap. The retry kept the batch size, so 4 requests failed again. Fix for any rerun: smaller batches
  (≤ 25) for the requeued items. Not rerun (1% of the sample; disclosed).
- Agreement: topic **79.7%**, intent **84.6%**, severity **82.3%** (within ±1: 99.4%); all three fields 58.2%.
  Planted-error test (synthetic copy): 3/3 detected. Files: `evals/verify/` (report, disagreements.csv, predictions).
- Investigation (`scripts/m9_verify_investigate.py` → `verify_investigation.json`, code only):
  - Topic confusions are mostly boundary cases into `other`: catalog→other 715, usability→other 480, playback→other 401;
    and **usability→billing 412** (ads / premium-upsell complaints the verifier files under billing).
  - Intent: complaint→unclear 791, complaint→cancellation 614, praise→unclear 592, praise→complaint 398.
  - Severity: the verifier is milder (−1: 2,743 vs +1: 767).
  - Agreement falls with length: <40 chars 68.3%, 40–149 54.1%, 150+ 40.2% (all three fields). The add-on (short
    duplicated texts) agrees more (64.0%) than the random 100k (56.3%).
  - **Does the conclusion move?** On the verified sample (distinct texts, complaint+cancellation), severity_sum by area:
    first labels usability 8,379 > playback 5,754 > billing/support 4,215 > access 1,527; verifier labels usability
    6,767 > billing/support 5,609 > playback 5,144 > access 1,561. **Usability stays first; 2nd/3rd swap**, so the memo's
    "playback is the closest alternative" depends on the labeler. Worth one sentence in the README (M12).

## A9 group, A10 rank
- 33 issues, all named by the model (1 call). `rank` from saved files: byte-identical on rerun, zero model calls.
- `scripts/m9_rank_check.py` (the checker's ranking/claim algorithm on the saved files) → `rank_check.json`: 0 ungrouped,
  0 membership problems, 33/33 ranking rows and 10/10 claims match. **PASS.**
- `outputs/enriched.jsonl.gz` (53 MB, the rank input snapshot) is gitignored; `group` rebuilds it from the DB.

## A11 memo (memo-prompt-v2)
- Priority **usability**; 3 recommendations (usability-general, usability-ads, usability-playback_controls).
  Code checks passed (claim IDs, issue/review IDs, every number in the input). New: a code-generated **number trace**
  table in `memo.md` maps all 36 numbers to a claim ID or a `memo_input.json` field (area numbers have no contract claim).
- Calls: 3 = rejected by the code check ("number 100 is not in the input") + a connection error + accepted.
- 👤 **For Rory's review:** "largest area roll-up on every measure" is wrong for mean severity (usability 2.469498 is
  the lowest of the four; access 3.637452 is highest). Decide: accept with a note, or regenerate (≈ $0.005).
- **Regenerated once (Rory, cap $0.02) with memo-prompt-v3** (adds: no "every measure" claims; name the metric per
  comparison). 1 call, accepted first time, $0.0029; 11 claims; rank check PASS. The "every measure" slip is gone, but two
  new mean_severity slips (not code-checkable): "Playback leads on mean_severity" (access leads; playback is 2nd) and
  "the mean_severity order differs from the main ranking" (it does not: access > playback > billing/support > usability
  in both). **Decided (Rory): keep v3 as generated; disclose both slips in the README (M12).**

## Bug found and fixed: stale event loop
`asyncio.run()` per call closes the loop while the async Anthropic client keeps pooled connections bound to it → the
next call fails with "Event loop is closed" → `APIConnectionError`. It crashed the first verify poll (the job kept
running; the rerun resumed it by ID) and cost one memo attempt. It is probably also behind the earlier "connection
errors" (M7 notes). Fix: `providers.base.run_sync` (one loop per provider) in `batch_runner` and `oneshot`; polls now
tolerate transient errors. `FakeBatchProvider` now rejects a changed loop like the real client (the old runner fails 2 tests).

## Spend (list price; all Anthropic Haiku 5.5 = cash)
verify $0.4173 (419 calls: 410 succeeded, 9 failed at max_tokens) + group $0.0027 + memo $0.0101 (3 calls)
+ memo v3 regeneration $0.0029 = **$0.4330**.
Estimate was $0.57 base. Project total ≈ **$4.3298** (Celeris ≈ $1.2492 credit, Anthropic ≈ $3.0807 cash).
