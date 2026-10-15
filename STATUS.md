# STATUS — handoff log

## Last completed: M12 (README, evidence & publish) — ✅ local work done 2026-10-15 (no spend); 👤 publish + submit pending Rory

### Done (M12)
- **`README.md`**: rubric → evidence map; results summary (accounted vs completed, cache reuse, golden, verifier, cost/time
  measured vs estimated); mermaid architecture (6 stages, code/model boundaries, stop/retry, SQLite → Neon → FastAPI →
  dashboard) + role table (why each model call); labels/models/settings; ranking rules + area table + random-100k
  comparison; memo summary with the **two v3 mean_severity slips disclosed**; trace; golden; verification/planted/injection;
  resume; cost (pilot, calculator replay commands, spend list/credit/cash); dashboard + DB setup; grading export
  (`unfinished_classification`, **344 left-out M6-config calls**, 50.8 MB records file); setup/run/replay commands;
  limitations; dataset link + SHA-256.
- **Traces** (`scripts/trace_review.py`, read-only): `evals/m12/trace_ok.md` (review `8c256cff…` → A3/A5 → record →
  calls → verifier (topic disagrees: access) → `usability-ads` → rank 2 → claims C02a/C02b → memo + recommendation 2);
  `evals/m12/trace_ambiguous.md` (`7ad378f0…` mixed praise/complaint, needs_review, + the failed A8 max_tokens request).
- **Clean-environment test** (`evals/m12/clean_env.md`): fresh clone, no keys → calculator replays byte-identical
  (pilot + 100k), `rank` byte-identical (6 files), checker identical to M10, live smoke 12/12. Fixed two bugs it found:
  `rank` now falls back to `grading/records.jsonl.gz` when `outputs/enriched.jsonl.gz` is absent (new test); 
  `overall_metrics.json` written with LF. Added `requirements-lock.txt` (full pip freeze). Windows: clone into a short path.
- **Secret hygiene:** `.env` ignored (`.gitignore:2`), only `.env.example` tracked/ever committed; every `.env` value
  (incl. URL passwords) absent from all tracked files (gz decompressed) and from full `git log -p`; the only pattern hit is the
  fake `npg_secret` test fixture. Personal details: commit author email, and Windows paths with the local username in
  `cost/full_corpus_decision.json`, `evals/golden/golden_eval*.json`, `evals/m9/verify_run1_crash.log` (left as is).
- Self-grade follow-ups: hand-added **Reviewer note** at the end of `outputs/memo.md` (corrects the two mean_severity slips;
  generated text unchanged); README: "Grading in 5 minutes" block, coverage-point formula, explicit "no full-corpus run",
  claims check corrected to 11/11.
- Fast suite **136 passed** (with `.env`); clean clone 127 passed / 8 skipped (no Neon URL) after the firewall fix.

### 👤 Open for Rory (outward-facing, not done by Claude)
0. Upload the resume screen recording to Google Drive (anyone with the link can view) and replace the 3 `RECORDING_URL`
   placeholders in `README.md` with the link.
1. `git push` (commits since `9e1aed2`), then make the GitHub repo **public** (Settings → General → Change visibility).
2. Open README + evidence links signed out (private window): README, `grading/`, `outputs/memo.md`, `cost/report_pilot.md`,
   live dashboard.
3. Optional: delete the owner `DATABASE_URL` from Render (read-only URL is the only one needed).
4. Submit the repo URL via the course portal (https://submissions-portal-eight.vercel.app).

### Spend
- M12: **$0**. Project total unchanged ≈ **$4.3298** list (Celeris ≈ $1.2492 credit, Anthropic ≈ $3.0807 cash).

### Next step
None — project complete once Rory publishes and submits.

---

## Previously completed: M11 (Publish, backend, dashboard) — ✅ gate passed 2026-10-15 (no spend)

### Done (M11) — details `docs/DASHBOARD.md`
- **Publish:** `pipeline/db/publish.py` + `pipeline.cli publish [--target main|test]`. One transaction: drop/recreate schemas
  `serving` (tables) + `dashboard` (views: v_overall, v_status_reasons, v_scope, v_topic/intent/severity, v_area_rollup,
  v_ranking, v_claims (+ recomputed `matches`), v_recommendations, v_run_summary, v_cost_summary, v_reviews). 660,622 records
  (text for completed rows only) in ~40 s. Read-only login role **`dashboard_ro`** (SELECT on `dashboard.*` only,
  `default_transaction_read_only`, 15 s statement timeout); password `DASHBOARD_RO_PASSWORD` in `.env`, plus
  `DASHBOARD_DATABASE_URL` / `TEST_DASHBOARD_DATABASE_URL`.
- **Backend:** `backend/app.py` read-only endpoints (`/api/overview|areas|ranking|claims|recommendations|cost`,
  `/api/reviews/{id}`, `/api/issues/{id}/reviews`, `/api/health`, `/api/docs`); one shared connection, 5-min cache.
  `DASHBOARD_TARGET=test` reads the test branch. **Dashboard:** `dashboard/index.html` (static, served at `/`), checked at
  desktop + 375 px (no horizontal scroll).
- **Tests:** `tests/test_m11_publish.py` (4; the Neon one publishes a fixture to `test` twice, checks RO role can't read
  `serving.*` / can't create, endpoint numbers, planted claim mismatch → ✗). `test_m2_labels` allowlist + `publish.py`
  (reads golden_eval.json scores only). Fast suite **135 passed**.
- **Smoke:** `scripts/smoke_dashboard.py --base-url …` compares the API with `outputs/` → all 12 checks equal on the local
  backend over the full data on Neon `test` (`evals/m11/smoke_local_test_branch.json`).

### Live (2026-10-15)
- Published to Neon **main** (660,622 records; `dashboard_ro` granted) and pushed to GitHub (Render redeployed).
  Rory set `DASHBOARD_DATABASE_URL` in Render. **https://spotify-review-pipeline.onrender.com** serves the dashboard signed out;
  `/api/health` → `published_at 2026-10-15T21:08:18Z`.
- **Live smoke: all 12 checks equal `outputs/`** (`evals/m11/smoke_live.json`). Access instructions: `docs/DASHBOARD.md`.
- Not verified from outside: which role the live backend logs in as (the pooled connection doesn't show up in pg_stat_activity).
  Rory: delete the owner `DATABASE_URL` from Render so the read-only URL is the only one there (the backend falls back to it).
- Push warning: `grading/records.jsonl.gz` is 50.83 MB (> GitHub's 50 MB recommendation, < 100 MB limit). Accepted; mention in M12.

### Notes for M12
- README links `docs/DASHBOARD.md` + the live URL; DQ4/WR3 evidence = dashboard recommendations + `evals/m11/smoke_live.json`.
- After any DB change: `pipeline.cli export` (M10) **and** `pipeline.cli publish --target main`, then re-run the live smoke.

### Spend
- M11: **$0** (no API calls). Project total unchanged ≈ **$4.3298** list (Celeris ≈ $1.2492 credit, Anthropic ≈ $3.0807 cash).

### Next step
**M12 — README, evidence & publish.**
"Read CLAUDE.md, STATUS.md and PLAN.md §5 M12, then do M12."

---

## Previously completed: M10 (Grading export + checker) — ✅ gate passed 2026-10-15 (no spend)

### Done (M10) — details `evals/m10/notes.md`
- **Code:** `pipeline/export.py` + `pipeline.cli export` (records + calls gz with mtime 0, run.json, copies of
  membership/ranking/claims and the M8 checkpoints; side report `evals/m10/export.json`). Test `tests/test_m10_export.py`.
  Fast suite **139 passed**.
- **grading/** built: 660,622 records; 4,552 calls (all real outcomes incl. 2+9+2 failed); **344 succeeded M6-config enrich
  calls left out** (superseded label_config → would be `call_config_mismatch`; listed in `evals/m10/export.json`).
- **Checker:** `review_required` with only **`unfinished_classification` 381,090** (out-of-scope quarantines, by design);
  valid completed 279,519, quarantined 381,103, accounted 1.0, **coverage point candidate 0.6539**. `ingestion.json`
  re-profiled byte-identical. `local-reference.json` / `self-check.json` at repo root (gitignored).

### Notes for M11/M12
- **README (M12) must explain:** `unfinished_classification` (accounted, not classified); the 344 left-out M6-config calls;
  the two memo v3 mean_severity slips (M9); injection-sentence evidence quotes (M7).
- `grading/records.jsonl.gz` is 53 MB (GitHub warns > 50 MB, limit 100 MB). Re-run `pipeline.cli export` + the three checker
  commands (PLAN §9.7) after any DB change; the export is byte-deterministic.

### Spend
- M10: **$0** (no API calls). Project total unchanged ≈ **$4.3298** list (Celeris ≈ $1.2492 credit, Anthropic ≈ $3.0807 cash).

### Next step
**M11 — Publish, backend, dashboard.**
"Read CLAUDE.md, STATUS.md and PLAN.md §5 M11, then do M11."

---

## Previously completed: M9 (Analysis: verify, group, rank, memo) — ✅ gate passed 2026-10-15 (💰 $0.4330 list, approved by Rory)

### Done (M9) — details `evals/m9/notes.md`
- **Scope change (Rory OK):** the contract ranking groups all **101,649** completed complaint/cancellation records (checker
  `ungrouped_complaints`); random-100k comparison (47,920) in `outputs/comparison_random_100k_*.csv`; same area order.
- **A8 verify** (Batch, 20%, seed verify-v1): 20,683 sampled → 20,483 verified, **200 unverified** (4 requests truncated at
  max_tokens 5,000 twice; rerun would need batch ≤ 25). Agreement topic 79.7% / intent 84.6% / severity 82.3% (±1 99.4%);
  planted 3/3. Investigation: usability stays #1 under verifier labels; playback ↔ billing/support swap 2nd/3rd.
- **A9/A10:** 33 issues (model-named); `rank` byte-identical, zero calls; `scripts/m9_rank_check.py` (checker algorithm) PASS.
- **A11 memo** (memo-prompt-v3: scope disclosure, wording rules, Robustness section, no "every measure" claims; v2 output
  had that slip and was regenerated once on Rory's yes): priority **usability**, 3 recs, 11 claims; code-generated number
  trace. 👤 **Open:** v3 has two mean_severity wording slips (says playback leads — access does; says the order differs in
  the random 100k — it doesn't). **Decided (Rory): keep v3 as generated; M12 README must disclose both slips.**
- **Bug fixed:** stale asyncio loop with the async Anthropic client (`providers.base.run_sync`; batch_runner, oneshot);
  poll tolerates transient errors; FakeBatchProvider enforces one loop. Fast suite **138 passed**.

### Spend (list price)
- M9: **$0.4330** = verify $0.4173 + group $0.0027 + memo $0.0101 + memo v3 $0.0029 (Anthropic Haiku, cash). Estimate $0.57.
- **Project total ≈ $4.3298** (Celeris ≈ $1.2492 credit, Anthropic ≈ $3.0807 cash). pipeline.db ledger total ≈ $4.1172.

### Notes for M10
- Export membership/ranking/claims from `outputs/` as-is (they pass the checker's ranking/claim logic); run.json
  `allow_multi_issue: false`. Calls: include the 9 failed verify calls and the 2 failed memo calls (real outcomes); 3 succeeded memo calls exist (v2 + v3)
  — claims.csv/memo.md are from v3.
- `outputs/enriched.jsonl.gz` is gitignored (53 MB); rebuild with `pipeline.cli group` (cache hit, zero calls) if missing.
- Earlier M8 notes below still apply (export only final-label_config enrich calls; checkpoints into grading/).

### Next step
**M10 — Grading export + checker.**
"Read CLAUDE.md, STATUS.md and PLAN.md §5 M10, then do M10."

---

## Previously completed: M8 (Main 100k run + 25k add-on) — ✅ gate passed 2026-10-15 (💰 $3.2117 list, approved by Rory)

### Done (M8)
- **Resume demo, screen-recorded by Rory** (`evals/recovery/`): initial run interrupted after 4 A3 batches (exit 130,
  2 in-flight calls closed failed at estimate) → checkpoint_before **130,221** → resume → checkpoint_after **130,382** (+161)
  → checker resume rules **PASS**, and PASS again when the resume run drained. 👤 Rory keeps the recording (needed in M10/M12).
- **100k:** resume run drained 69,607 texts in 38.7 min, 2,779 calls, 0 failed. **Add-on** (`--scope addon`, top 25,000
  out-of-scope texts = 41,577 rows): 13.2 min, 1,000 calls, 0 failed. Details: `evals/m8/notes.md`.
- **New code:** graceful pause (`state/PAUSE` next to the DB → workers finish in-flight calls, stop `paused`, exit 0;
  remove the file and `resume`); `pipeline/finalize.py` + `pipeline.cli finalize` (exact-text cache fill over all texts,
  out-of-scope rows → `out_of_scope_budget_100k`, unreached in-scope rows refused unless `--allow-unreached`, then
  reconciliation → `evals/m8/finalize.json`). Tests +2 (`tests/test_m7_system.py`); fast suite **138 passed**.
- **Gate:** 660,622 source rows = 660,622 records (0 missing, 0 orphans; ingestion.json ✔). Completed **279,519**
  (random_100k 100,000 · add-on 41,577 · out-of-scope via cache fill 137,942; 176,420 by cache reuse), all under the
  frozen label_config. Quarantined 381,103 = `out_of_scope_budget_100k` 381,090 + `empty_review_text` 13.

### Spend (list price)
- M8: **$3.2117** = 100k $2.3989 (interrupted initial $0.0119 + resume $2.3870) + add-on $0.8128;
  Celeris $1.0442 (prepaid credit) + Anthropic Haiku $2.1675 (cash). Estimate was $3.29 base.
- **Project total ≈ $3.8968** (Celeris ≈ $1.2492 credit, Anthropic ≈ $2.6477 cash). pipeline.db ledger: decisions ≈ $1.23 / 2.00,
  escalation ≈ $2.45 / 5.00, total $3.6842. Remaining under the $9.00 working limit ≈ $5.10.

### Notes for M9/M10
- M9 verification at 20%: ≈ 15.6k texts (100k) + ≈ 5k (add-on); bucket `verification` cap $1.00. Ranking excludes add-on rows
  (`outputs/ranking_excluded.txt`).
- M10: export calls only under the final label_config (the M6-config calls are still in the DB, see M7 notes); the two
  interrupted calls of run-20261015-094806-7e9c37 are real failed outcomes and stay in `calls.jsonl`.
- `outputs/run_log.jsonl` was appended by the runs (tracked file, committed).


---

## Previously completed: M7 (System tests & recovery evidence) — ✅ gate passed 2026-10-15 (💰 $0.0020 list, injection run approved by Rory)

### Done (M7)
- **Code:** `pipeline/recovery.py` (checkpoint files `{"completed_ids": [...]}` + `resume_rules` = local copy of the checker's
  resume block over the DB); `pipeline/synthetic.py` (SYNTH- corpora in their own fresh DB); `pipeline.cli` maps
  **Ctrl-Break → Ctrl-C** on Windows (`ctrl_break_as_ctrl_c`), so a run in its own process group stops cleanly.
  `scripts/resume_demo.py` (enrich → N A3 calls + ≥ 1 committed batch → interrupt → checkpoint_before → status → resume →
  checkpoint_after → rules → wait for the resume run → rules again). `scripts/system_tests.py` (12 offline scenarios +
  `injection_real`, paid). Tests `tests/test_m7_system.py` (5). Fast suite **128 passed**; full suite **136 passed** (7 min).
- **Rehearsal** (`evals/recovery/rehearsal/`, FakeProvider, 2,000 synthetic rows): interrupt → exit 130 `interrupted`,
  2 in-flight calls closed failed, 0 reserved → before 431 → resume → after 631 → rules **PASS**; at the end **PASS**,
  0 resume calls with a before ID. Bug found and fixed while rehearsing: "N succeeded A3 calls" alone could leave
  `checkpoint_before` empty (A5 still in flight with 2+ workers) → the demo now also waits for committed records.
- **System tests** (`evals/system_tests/results.{md,json}`, all PASS): planted errors (5/5 caught by the verifier
  comparison on a test copy; 12/12 contract violations caught by `validate_record`; a planted resume re-send flagged);
  injection with a model that obeys (invalid label → retry → quarantined with reason; an answer for an unsent review →
  call bisected → culprit quarantined; quotes always cut by code); A9/A11 invented IDs/numbers rejected and retried;
  malformed output (bisect + retry; missing twice → quarantined, logged in quarantine.jsonl); empty texts quarantined
  `empty_review_text` and never sent; ambiguous text (low top prob saved; **needs_review stays false** under the frozen
  config — known limitation); 429/408 retried, 402 global stop → resume, 400 stop, max_attempts → failed; ledger stops
  before the cap, paid without `--paid` refused, fake on pipeline.db refused; hard kill → crashed → resume, rules pass;
  real failures so far = 4 (2 Anthropic connection errors settled at estimate $0.0132 + $0.1135; 2 pilot 400s at $0);
  no SYNTH- rows in state/pipeline.db / outputs/ / grading/.
- `state/pipeline.db` checked read-only: all 8,492 non-cached completed records are in succeeded `initial` calls with the
  frozen label_config (0 unlogged) → the M8 `checkpoint_before` will satisfy the checker.

### Notes for M8/M10
- **M8 first ~10 minutes, recording on:** `.venv/Scripts/python scripts/resume_demo.py --out evals/recovery --enrich-args
  "--provider celeris --escalation anthropic --bucket decisions --workers 4 --paid --max-spend X"` (the resume run is the
  rest of the main run; Ctrl-C in the demo window is forwarded and stops it cleanly). Then the add-on with `--scope addon`.
- **M10 export risk:** `state/pipeline.db` also holds 344 succeeded enrich calls under the **M6** label_config for the same
  10k reviews. The checker flags `call_config_mismatch` for any succeeded enrich call whose label_config differs from the
  record's → export only calls under the final label_config (and disclose the M6 calls in the run log/README), or decide in M10.
- Copy `evals/recovery/checkpoint_{before,after}.json` into `grading/` in M10 (extra provenance keys are ignored by the checker).

- **Real-model injection** (`evals/system_tests/injection_real_notes.md`; 6 injections + 4 controls, Celeris + Haiku +
  Haiku verifier): **no label followed an injected instruction**, no output for unsent reviews, format intact.
  Weakness: for 2 reviews the code-cut evidence_quote is the injection sentence itself (valid substring, weak evidence);
  not fixed (extract-code-v1 frozen) → **disclose in the README (M12)**.

### Spend (list price)
- M7: **$0.0020** = Celeris $0.0001 (prepaid credit) + Anthropic $0.0019 (cash), DB `state/m7_system/injection_real.db`.
- **Project total $0.6851** (Celeris $0.2050 credit, Anthropic $0.4802 cash). M10/M12 spend roll-ups must include
  `state/m7_system/injection_real.db` (the other m7 DBs are fake, $0 real).

### Next step
**M8 — Main 100k run.**
"Read CLAUDE.md, STATUS.md and PLAN.md §5 M8, then do M8."

---

## Previously completed: M6b (Fidelity upgrade + re-freeze) — ✅ gate passed 2026-10-15 (💰 $0.4572 list, approved by Rory)

### Frozen label_config (CLI default; supersedes M6)
`celeris-1-decision+shapeA+gate1.00+esc-claude-haiku-5-5-1.00+escfields-severity+extract-code-v1+prompt-v1+schema-v1+sentiment-mode-v1`
= shape A; **Haiku severity for every review** (gate 1.00 = always, cap 1.00); **sentiment = most probable level** (ties → nearer 0);
topic/intent = Celeris. Rationale, results and disclosures: `evals/m6_notes.md` § M6b.
M8: `python -m pipeline.cli enrich --provider celeris --escalation anthropic --bucket decisions --workers 4 --paid --max-spend …`
(default `--scope random_100k` caps at rank 100,000), then the add-on with `--scope addon` (builds the top 25,000 on first use).

### Done (M6b)
- **Code:** `decision.sentiment_value` + `--sentiment mean|mode` (`+sentiment-mode-v1` in label_config only for mode; a resumed
  pre-M6b run keeps `mean`); router gate ≥ 1.00 = escalate every gated field; `enrich --scope random_100k|addon --addon-size`
  (`intake.build_addon`: top-N out-of-scope distinct texts by row count, rows tagged `scope='frequency_addon'`,
  `texts.scope_rank = 100,000 + frequency rank`); grouper leaves add-on rows out of membership and writes
  `outputs/ranking_excluded.txt` (ranker accepts them; `excluded_from_ranking` in overall metrics); verify default rate
  **0.20** (seed verify-v1, superset of 5%); ledger + calculator BUCKET_CAPS = PLAN §4 v4.2 (pilots 0.50, decisions 2.00,
  escalation 5.00, verification 1.00, analysis 0.25, reserve 1.25); `ClaudeProvider` prices per model (Sonnet 5.5 added to
  `cost/rates.csv`); `scripts/m6b.py` (sentiment-check, heldout-prepare/-enrich/-score, topic-test/-score, golden-compare);
  `label_tool.py heldout`. Tests `tests/test_m6b.py` (12); queue tests count paid IDs per agent (A5 now sees every review).
  Fast suite: **131 passed**.
- **Held-out 30** (ranks 10,051–10,080, labeled blind by Rory): sentiment mean MAE 0.171 / ±0.25 77% → mode 0.175 / **90%**
  (fewer big misses, no MAE gain); severity Celeris 24/30 → Haiku 25/30. `evals/heldout_30/`.
- **Sonnet 5.5 topic test** on dev_100 (all topics): gain 0 on the adjudicated rows, cost far over the limit → **not adopted**,
  topic stays with Celeris. `evals/topic_test/`.
- **10k re-run** (`state/pipeline.db`, ranks 1–10,050): 8,492 texts, 0 quarantined, 340 calls, 0 failed, 294 s @ 4 workers.
  Records for those rows now carry the M6b config (M6's A3/A4/A5 outputs remain in agent_outputs).
- **Golden 50 M6 → M6b:** topic 90→88, intent 90→90, **severity 90→80 (MAE 0.18→0.30)**, sentiment ±0.25 52→62%.
  Disclosed as **Haiku run-to-run noise** (27% of its severity answers changed between runs on the same texts; Celeris
  severity on golden is 40/50 in both). On the 80 independent reviews: Celeris 64, Haiku<0.70 65, Haiku-all 65.
  👤 Rory chose to keep the M6b freeze anyway (2026-10-15).
- **Projection** (`cost/report.*` = 100k under M6b; `cost/m6b/report_100k_plus_addon.*`; M6 report kept as `cost/report_m6.*`):
  100k $3.11 / $4.73; 100k + add-on $4.10 / **$6.24** (base / conservative). **Gate: $0.68 spent + $6.24 = $6.92 ≤ $9.00 ✔.**

### Spend (list price)
- M6b: **$0.4572** = held-out $0.0016 + Sonnet topic test $0.1645 (incl. one attempt with a client connection error settled
  at its estimate **$0.1135**, billing unknown, likely $0 — check the Console) + 10k re-run $0.2912 (Celeris $0.0937 credit,
  Haiku $0.1975 cash).
- **Project total $0.6831** (Celeris $0.2049 prepaid credit, Anthropic $0.4783 cash), de-duplicated over all state DBs
  (`m6.spend_to_date`, now also `state/m6b_heldout.db`).

### Notes for M7/M8/M12
- README (M12) must disclose: sentiment change is golden-driven (held-out result above); golden severity drop = Haiku noise;
  Sonnet topic test not adopted; Celeris also varies run to run (top-1 changed topic 14%, intent 7%, severity 17% between runs).
- M8 escalation: ~78k A5 calls' worth of reviews at the 100k (every review); `--max-spend` must leave room for the $0.68 spent.
  Verification at 20% ≈ 15.6k texts (100k) + ≈ 5k (add-on).
- The old `label_tool.py dev` processes (PIDs 31656/31032) were stopped this session.

### Next step
**M7 — System tests & recovery evidence.** "Read CLAUDE.md, STATUS.md and PLAN.md §5 M7, then do M7."

---

## Previously completed: M6 (Scale checkpoints + golden evaluation) — ✅ gate passed 2026-10-15 (💰 $0.2090 list, approved by Rory)

### Frozen label_config (CLI default from now on; `pipeline/cli.py` STAGE_DEFAULTS)
`celeris-1-decision+shapeA+gate0.70+esc-claude-haiku-5-5-0.40+escfields-severity+extract-code-v1+prompt-v1+schema-v1`
= shape A, **escalate severity only**, gate 0.70 on the raw top probability, cap 0.40. Chosen on dev_100 (option B, Rory
2026-10-15). Rationale + caveats: `evals/m6_notes.md`. M8 runs plain `enrich --provider celeris --escalation anthropic
--bucket decisions --workers 4 --paid --max-spend …` (defaults = the frozen config).

### Done (M6)
- **Code:** `enrich --max-rank N` (claims only scope_rank ≤ N; the A4 cap counts only the prefix, so a 10k step gets 40% of
  10k, not 40% of 78k up front; saved in the run config → resume inherits it). `--esc-fields` (gated fields; in label_config
  as `+escfields-…` only when not all three, so older strings are unchanged). Pilot / dev-enrich pin the M5 settings.
  `scripts/m6.py` (dev-db, dev-enrich, dev-escalate, dev-prepare, sweep, golden, measure, decide, compare-b);
  `scripts/label_tool.py dev --disagreements` (only model disagreements, agreed fields pre-filled, both answers shown).
  Calculator title follows the measured file's `label`. Tests `tests/test_m6_eval.py` (7). Fast suite: **111 passed**.
- **checkpoint_500** (`state/m6_dev.db` = pilot.db copy + 500 rows): 479 texts, the pilot's 100 reused (cache), 379 new;
  pilot config: 309 accept / 71 escalate / 99 capped.
- **dev_100:** Haiku on every field of all 100 (`evals/dev_100/model_answers.json`); 23 disagreements (severity 15,
  topic 10, intent 2) adjudicated by Rory; 77 model-agreement references (marked `[model-agreement]` in notes, not
  hand-checked) → `evals/dev_100/dev_100_labeled.csv` complete.
- **Sweep** (`evals/threshold_sweep/sweep.md`): on the 23 adjudicated, A3-only topic 83% vs 74% with Haiku at 0.60; severity
  57% → 65% (0.70) → 78% (0.80). Escalation on 500 (any field): 0.50 17%, 0.60 36%, 0.70 54%; severity only at 0.70: 37%.
  Q11: Celeris `confidence` as the signal is no better than the raw top probability.
- **10k under the frozen config** (`state/pipeline.db`, seed ranks 1–10,050 = golden + analysis_10000, 8,492 texts,
  10,050 records, 1,558 by exact-text reuse, 0 quarantined): A4 accept 5,363 / escalate 3,129 (36.8%), 0 capped;
  0 failed calls, 0 429s. Warm rerun: 0 calls, 0.03 s. `cost/m6/scale_runs.md`:
  1 worker 9.8 texts/s (24 req/min), 2 workers 22.2 (54), 4 workers 44.4 (108) → **use 4 workers in M8** (8 untested).
  M8 continues the same queue: these 8,492 are done (69,607 pending).
- **Calculator refreshed** from the 10k (`cost/report.{md,html,json}`; pilot version kept as `cost/report_pilot.*`;
  inputs `cost/m6/{usage.csv,calls.jsonl,measured.json}` = 10k enrich + the pilot's A8/A9/A11):
  **100k base $1.78, conservative $2.73** (A3 $0.86/$1.19, A5 $0.81/$1.37, A8 $0.11/$0.17) — inside every PLAN §4 bucket.
- **Golden 50** (`evals/golden/golden_eval.md`, every disagreement in `golden_disagreements.csv`): topic 90%, intent 90%,
  severity 90% / MAE 0.18 (accepted alternatives counted; exact 78/86/82%); sentiment MAE 0.31, 52% within ±0.25
  (**known limitation: pulled toward 0**); quotes 100% substrings / 98% overlap gold; needs_review precision 100%,
  recall 15%; 22 ambiguous cases. Shape B observation (golden 96/96/82) **not confirmed on dev** (adjudicated 20/16/11 vs
  19/20/13) → shape A kept; disclosed in `evals/m6_notes.md`.
- **Full-corpus decision (§1.2): NO-GO** (`cost/full_corpus_decision.md`, also appended to `cost/report.md`): conservative
  projection $16.86 > $9.00 (conditions 2 and 3 fail; time would fit). **Frequency add-on recommendation: top 25,000
  distinct texts** (≤ $0.82 upper end, +41,577 rows, coverage point 0.6161 → 0.6539). 👤 **NO-GO confirmed by Rory
  2026-10-15**; add-on **25,000 confirmed** (PLAN v4.2).

### Spend (list price)
- M6: **$0.2090** = Celeris $0.1012 (prepaid credit) + Anthropic $0.1079 (cash). Includes one Haiku attempt with a client
  connection error after 4 ms, settled at its estimate **$0.0132** (billing unknown, likely $0) — check the Console.
  Breakdown: dev-enrich $0.0059, dev-escalate $0.0052 (+$0.0132 unknown), golden shape B $0.0011, 10k $0.1814,
  dev shape B $0.0022.
- **Project total $0.2259** (Celeris $0.1108, Anthropic $0.1151). `state/pipeline.db`'s own ledger holds $0.1814; the rest is
  in the pilot/M6 DBs (`scripts/m6.py decide` sums them, de-duplicated by request_id). M8's caps must leave room for it.

### Notes for M7/M8
- M8 add-on: needs an `enrich --scope addon`-style path (frequency-ranked texts beyond rank 100k) — not built yet.
- Escalation cap 0.40 is counted over the prefix in reach; at the 100k it allows 31,239 escalations (~28.7k expected).
- `state/m6_dev.db`, `m6_dev_B.db`, `m6_golden_B.db` are gitignored; `evals/` and `cost/m6/` hold the evidence.
- Old `label_tool.py dev` process (PID 31656, port 8765) was still running on 2026-10-15 with a pre-M6 copy of the dev
  CSV — don't save from it.

### Next step (then)
M6b — done 2026-10-15 (see above).

---

## Previously completed: M5 (Cost calculator + 100-review pilot) — ✅ gate passed 2026-10-14 (💰 $0.0169 list, approved by Rory)

### Done (M5)
- **`pilot` command** (`pipeline/pilot.py`, `cli pilot`): verifies `cost_100.csv` against the manifest checksum, refuses an existing
  DB (cold = empty cache), 1 worker, every stage through the normal CLI (ingest → enrich A3–A7 → verify A8 **standard tier, declared
  pilot rate 0.2** → group → rank → memo), then a warm rerun. Exports `cost/{pilot_records.jsonl, pilot_calls.jsonl, usage.csv,
  pilot_measured.json}` + stage outputs in `cost/pilot_run/`. `calls.cache_write_tokens` added (Anthropic cache writes priced apart).
- **Calculator** (`cost/calculator.py`, stdlib only, offline): usage.csv × rates.csv → `cost/report.{md,html,json}`; measured table
  (§9.6), projections full-with-reuse / full-no-reuse / 100k scope × base + conservative, controls (budget, output-token cap,
  concurrency, fallback fraction, verify rate/tier, volumes), budget + PLAN §4 bucket warnings. `cost/README.md` = replay vs pilot commands.
- **Comparisons** (`scripts/pilot_compare.py` → `cost/comparisons.{md,json}`); `MagnusProvider` (chat completions at
  `…/celeris-1-magnus/v1`, JSON mode — endpoint confirmed by a live 200).
- Claude client sends `anthropic-workspace-id` from `ANTHROPIC_WORKSPACE_ID` (Rory's key is not workspace-scoped; now in `.env`).
- Tests `tests/test_m5_cost.py` (10): the gate (2× rates → 2× API subtotal, measured and projected; times/counts/local unchanged;
  volume change → measured identical), pilot evidence + 100 IDs = cost_100 order, refusals, billing items mutually exclusive,
  comparisons offline, magnus on a mocked transport. Fast suite: **104 passed**.

### Measured (pilot, `state/pilot.db`)
- 100/100 completed, 0 quarantined, 100 distinct texts (no duplicates in cost_100). Cold **42 s, 6 calls, $0.006352**
  (Celeris $0.0011 = 275 input tokens/review; Haiku A5 $0.0006, A8 $0.0014, A9 $0.0010, A11 $0.0023). **Warm 0.3 s, 0 calls.**
- A4 at gate 0.60: accept 63, **escalate 15 (the 0.15 cap), capped 22** → 37% of reviews are below the gate. A8 agreement on
  25 texts: topic 84%, intent 80%, severity 76%. Memo passed validation first try (priority: playback, pilot data only).
- Haiku: structured output + effort low works; cache writes ~1.1–1.3k tokens/call, 0 reads in the pilot (one call per stage).
- **Shape A vs B**: B = 555 tokens/review vs A 275 (2×), 5× slower; top-label agreement topic 82%, intent 85%, severity 73%.
  A stays the default; whether A or B is *right* where they differ is for the M6 golden eval (B on golden 50 ≈ $0.001).
- **Escalation candidates** (37 low-conf reviews, 44 questions): Haiku-low $0.0009, Haiku-thinking-off $0.0011 (cache write),
  identical output tokens (~1.2k) and 95% agreement → effort low barely thinks; **magnus $0.0053 (6×), 23 s, 80% agreement** →
  Haiku stays. Not run (modeled only): Sonnet, Haiku-only enrichment, Batch vs standard for other stages.

### Projections (cost/report.md, defaults: concurrency 4, fallback cap 0.15, verify 5% batch)
- **100k scope: base $1.44, conservative $2.07** (A3 $0.86 / A5 $0.47 / A8 $0.11) → fits every PLAN §4 bucket.
- Full corpus with reuse: base $8.92, conservative $12.81 (over $10); no reuse: $12.17 / $17.48. Input to the §1.2 decision in M6:
  at the current A5 fraction the full corpus does not fit with margin; it only fits if escalation share/cost drops.

### Spend to date (list price; see `cost/report_reconciliation.md`)
- M5: **$0.016881** = Celeris $0.009669 (prepaid credit) + Anthropic $0.007212 (cash). Includes 2 failed pilot attempts
  ($0.00055 each, Celeris only; Haiku 400 workspace error not billed) kept in `state/pilot_attempt{1,2}.db`.
- Project total ≈ $0.0169. **The ledger cap is per DB: `state/pipeline.db` does not see this spend** — M8 must leave room for it.
- Provider dashboards not yet reconciled (Rory: compare Celeris + Anthropic Console usage for 2026-10-14 with the note).

### Notes for M6
- Gate sweep: 37% below 0.60 vs a 0.15 cap → 22% of pilot reviews ended needs_review (capped). `results.probs_json` has a3/a4/a5.
- Consider Haiku `thinking: disabled` for A5 (same answers in the pilot, no thinking tokens risk) — decide on golden accuracy.
- Comparisons need `state/pilot.db`, `state/pilot_shapeB.db`, `state/pilot_compare.db` (gitignored; `cost/` holds the evidence).

### Next step (then)
M6 — done 2026-10-15 (see above).

---

## Previously completed: M4 (Downstream stages, thin versions) — ✅ gate passed 2026-10-14 (no spend)

### Done (M4)
- **A8 Verifier** (`agents/verifier.py`, `prompts/verifier_system_v1.md`): declared seeded sample = distinct texts with a
  result where sha256(`seed:text_sha256`)/2^256 < rate (default **0.05**, seed `verify-v1`; a higher rate = superset;
  PLAN §4 updated). Request built from texts only (test proves the stored labels can't change it). Labels topic/intent/
  severity/needs_review via structured output, effort low. Queue stage `verify`, key `<label_config>#<verify_config>`
  (`verify_config` = `<model>+verifier-prompt-v1+effort-low`); results in table `verifier_outputs`, never in records.
  Invalid → retry once → item quarantined = "unverified" in the report (no record quarantine).
  Report: `evals/verify/{verify_report.json, verifier_predictions.jsonl, disagreements.csv}`; **planted errors**:
  `evals/verify/planted_errors.json` (synthetic copy, topic flipped on 3 agreeing reviews → all flagged).
- **Batch API runner** (`pipeline/batch_runner.py` + `ClaudeBatchProvider` in `providers/claude.py`, batch rates
  $0.05/$0.25): claim → ≤ 50 reviews/request → reserve (bucket `verification`) → `calls` rows (tier batch, custom_id =
  request_id) → `batch_jobs` 'building' → submit → 'submitted' → poll → commit per result (ONE transaction each) →
  errored/canceled/expired/missing = not billed, requeued → next round (≤ 3). Open job → resumed by ID, never resubmitted
  (`--max-wait-minutes` exit = 'waiting'; rerun `verify` to resume). Crash before the provider ID is saved → 'abandoned',
  settled at the estimate, items requeued. `recover()` no longer touches in-flight batch calls. `--standard` = real-time
  through the orchestrator (stage hooks `cached/reuse/save/quarantine` added to `Stage`; enrich behaviour unchanged).
  SDK path tested on a mocked httpx2 transport (create → retrieve → results JSONL).
- **A9 Grouper** (`agents/grouper.py`, `labels/subtopics.yaml` subtopics-v1, `prompts/grouper_system_v1.md`): members =
  every completed complaint/cancellation record (incl. cache reuses / out-of-scope fills); issue_id = `<topic>-<subtopic>`
  (first subtopic term in the text, else `-general`), exactly one per review. One `group` call names issues from ≤ 5
  quotes each (unknown IDs / names with digits rejected → code default name); cached by input hash in `analysis_cache`.
  Saves DB `issues`/`membership` + `outputs/{issues.json, membership.csv, enriched.jsonl.gz}` (gzip mtime 0 → deterministic).
- **A10 Ranker** (`agents/ranker.py`): from saved files only → `outputs/{ranking.csv, aggregates.csv, area_aggregates.csv,
  overall_metrics.json}`; validates membership (unknown/duplicate/multi/ungrouped → RankError); uses the checker's
  `mean_string`; area roll-up via `labels/areas.yaml` (4 areas + outside, shares 6 dp). Test = the checker's own algorithm.
- **A11 Memo** (`agents/memo.py`, `prompts/memo_system_v1.md`): one `memo` call over `outputs/memo_input.json` (area
  roll-up, totals, top 10 issues with claim IDs `C<rank><a-d>`, ≤ 3 quotes each). Code rejects unknown issue (backticked)
  / review (UUID) / claim IDs and any number not in the input → failed call → one retry with the errors → else CallRejected.
  Writes `outputs/{memo.md, claims.csv (cited claims, values from ranking), recommendations.json}` + DB tables; cached.
- **One-shot calls** (`pipeline/oneshot.py`): ledger reserve (bucket `analysis`) → calls row → validate → settle.
- **CLI**: `verify [--standard] [--sample-rate] [--seed] [--plant]`, `group`, `rank` (zero calls, `--db` optional),
  `memo`, `analyze` (group → rank → memo); `run --input <csv>` now runs ingest → enrich → verify → group → rank → memo.
  Downstream `--provider fake|anthropic`; anthropic needs `--paid --max-spend`; fake refused on `state/pipeline.db`.
- Tests `tests/test_m4_downstream.py` (16, ~4 s), incl. the gate: `run` on FakeProvider → all 17 output files, the four
  succeeded roles, `rank` twice byte-identical with zero calls, warm `analyze` = zero calls. Full fast suite: **94 passed**.
  Real `state/pipeline.db` migrated (new tables only; 78,099 pending kept); manifest `schema_sha256` updated.

### Expected paid cost of the downstream stages (estimates, nothing run)
- Verify at 100k (≈ 3,900 texts, 78 batch requests): ≈ $0.02–0.12 list (upper end = full max_tokens). Pilot (100 reviews,
  use `--sample-rate 0.2 --standard` if batch is slow): < $0.01. Group naming ≈ $0.003; memo ≤ $0.004 per attempt.

### Notes for M5
- The pilot must run every stage on cost_100: `enrich … --limit 100`, then `verify --provider anthropic --paid
  --max-spend 0.02 --sample-rate 0.2` (declare the pilot rate in the report), then `analyze --provider anthropic --paid
  --max-spend 0.02`. Run downstream on a DB/root holding only the pilot records (or a separate `--root`), so the memo is
  about the pilot only.
- Haiku structured output + effort `low` on group/memo/verify is untested against the live API; M5's first paid call
  will show it. If the memo's number check rejects twice, read the call `error` column (it lists every rejected token).
- `outputs/enriched.jsonl.gz` replaces the planned `enriched.jsonl` (size); M10 exports `grading/records.jsonl.gz` itself.

### Next step
M5 (Cost calculator + 100-review pilot, 💰 ≈ cents): "Read CLAUDE.md, STATUS.md and PLAN.md §5 M5, then do M5."

---

## Previously completed: M3b (Enrichment agents A3–A7) — ✅ gate passed 2026-10-14 (no spend)

### Done (M3b)
- **Chain protocol** (`pipeline/stages.py`): a stage now yields `Call`s (provider, payload, estimate, parser, bucket) for a leased
  batch; the orchestrator runs each call (reserve → limiter → `calls` row → provider → ONE transaction: outcome + usage, ledger,
  `stage.absorb`), then `stage.finish` → ONE fenced transaction for results/fan-out/queue. `PassthroughStage` (M3a) is a one-call
  stage; M3a tests unchanged and green. New table `agent_outputs` (A3 decision with every probability, A4 route, A5 answer per
  text + label_config, saved with the call) → an A5 failure, lost lease or resume never re-pays A3. `calls.provider_request_id` added.
- **A3 Decision** (`agents/decision.py`, `prompts/decision_state_v1.md`): shape A = definitions + ≤ 50 `[R<n>]` reviews in `state`,
  4 bare questions per review (topic/intent choice, severity/sentiment score); shape B (`--shape B --batch-size 1`) = one review,
  definitions in option descriptions. Severity = most probable level (+1); sentiment = probability-weighted level → −1..1.
  `[R<n>]` inside review text is neutralised. Missing/malformed answer → that review invalid; a **foreign key** or unparseable
  body → the call is invalid and the batch is **bisected** (halves re-sent at once) so only the culprit gets retry-once → quarantine.
- **A4 Router** (`agents/router.py`): gate on top probability (topic/intent/severity, default 0.60); cap = floor(esc_fraction ×
  queue size), counted atomically in the DB; over the cap → `capped` (A3 label, needs_review). $ sub-cap = ledger bucket
  `escalation` (`--esc-max-spend`): a refused A5 reservation → `capped_budget`, run continues.
- **A5 Escalation** (`agents/escalation.py`, `prompts/escalation_system_v1.md`, `providers/claude.py`): Haiku 5.5 via the SDK
  (`max_retries=0`), effort `low` (adaptive thinking), structured output (JSON schema), one request per batch's escalated texts,
  single-field questions with A3's top-2. Missing/invalid answer or unusable body → A3's value kept with needs_review (no quarantine).
  Usage: cache reads/writes priced separately. `celeris-1-magnus` client **not built** (PLAN §4 defaults to Haiku; build in M5 only if
  the comparison is still wanted).
- **A6 Extractor** (`agents/extractor.py`, keywords-v1): shortest sentence with a topic term, else full text (always a slice);
  entities as written, longest match first. **A7 Validator** (`agents/validator.py`) = schema-v1 + label_config.
- **Providers**: `providers/celeris.py` (httpx, `X-Client-Trace-Id` = our request_id, Retry-After, §9.9 error classes, usage or None,
  exact code-fence strip in `base.parse_json_text`); 529 → transient. Fake provider answers in Celeris / escalation shapes (+ `foreign`).
- **CLI**: `enrich [--provider fake|celeris] [--escalation fake|anthropic|none] [--stage chain|passthrough] [--shape A|B] [--gate]
  [--esc-fraction] [--esc-max-spend]`; any paid part (celeris or anthropic) needs `--paid --max-spend`. Default paid label_config =
  `make_label_config(...)` e.g. `celeris-1-decision+shapeA+gate0.60+esc-claude-haiku-5-5-0.15+extract-code-v1+prompt-v1+schema-v1`.
  Resume inherits the stage settings from the run's provider spec.
- Tests `tests/test_m3b_agents.py` (31, ~4 s): confident → accepted; low-conf → escalated (A3's 2nd candidate wins); fraction cap
  (4 workers) and $ sub-cap → needs_review; no escalation model → needs_review; A5 malformed → fallback; A5 408/429 → A3 not re-paid;
  crash between A3 and A5 → resume reuses decisions; missing `[R<n>]` → retry → quarantine; foreign `[R<n>]` → only the culprit
  quarantined (bisection); malformed singleton → retry → quarantine; injected label → rejected → retried; all records pass §9.8 and
  quotes are exact substrings; decision parser on the measured smoke response; Celeris/Claude clients on mocked transports.
  Full fast suite: **78 passed** (`-m "not data"`). Real `state/pipeline.db` migrated (78,099 pending kept); manifest `schema_sha256` updated.

### Measured offline (no calls)
- Shape A, first 50 queue texts: ≈ 14.7k input tokens upper-end estimate (definitions ≈ 1.1k) → ≈ 293 tokens/review, ≈ $0.0006/request;
  shape B ≈ 720 tokens/review. 100k scope at shape A ≈ 78k × 293 × $0.04/M ≈ **$0.92 upper end** (M5 measures the real number).

### Notes for M4/M5
- Haiku's minimum cacheable prefix is likely above our ~1k-token escalation system prompt, so expect `cache_read_input_tokens = 0`
  (harmless; report as "not cached"). Verify in M5.
- M5 paid commands (after Rory's yes), e.g. `enrich --provider celeris --escalation anthropic --paid --max-spend 0.05 --bucket pilots
  --label-config <pilot cfg> --limit 100` on a DB/queue restricted to cost_100 (M5 must enqueue only the 100 pilot texts).
- `results.probs_json` holds `{a3, a3_confidence, a4, a5}` for the M6 gate sweep.

### Next step (then)
M4 — done 2026-10-14 (see above).

---

## Previously completed: M3a (Queue, cache & control engine) — ✅ gate passed 2026-10-14 (no spend)
### Done (M3a)
- `pipeline/db/store.py`: `connect` (WAL, busy_timeout, explicit transactions), `immediate()` = `BEGIN IMMEDIATE`, `migrate()`
  (applies `schema.sql` + adds columns an older DB lacks; the real `state/pipeline.db` is migrated, nothing lost).
  Schema additions: `work_items.available_at/invalid_attempts`, `calls.usage_available/est_usd/ended_at`, `ledger.run_id`,
  `runs.stop_reason/heartbeat_at`, table `rate_limits`, index `ix_records_cfg`. (`outputs/data_manifest.json` re-generated: only `schema_sha256` changed.)
- `pipeline/db/queue.py`: lease claim under `BEGIN IMMEDIATE` (pending in rank order + expired leases, two index walks: 100 calls / 5,000
  items in 2.7 s on the real 78k queue), fenced `held()`, done / requeue with backoff+jitter / fail after `max_attempts` /
  invalid → retry once → quarantined, `release_owner`.
- `pipeline/agents/cache.py` (A2): `cached()` pre-call check, `write_result` (ON CONFLICT DO NOTHING), `fan_out` (records for every
  source row with the text, in or out of scope; `cache_source_id` → direct original; replaced only under a different label_config),
  `quarantine_texts`.
- `pipeline/db/ledger.py`: reserve (checks project $10, bucket cap from PLAN §4, run `--max-spend`) → settle | release.
  Unknown billing (timeouts, no usage, interrupted) settles at the estimate, never 0. `pipeline/db/limiter.py`: DB token buckets per
  model (rpm/tpm, 10 s burst), 429 Retry-After blocks the model for every worker.
- `pipeline/providers/base.py` (Request/Response/ProviderError, HTTP classification: 402 quota, 408/429/5xx transient with 429/502 not
  charged, others fatal) + `fake.py` (valid, low_conf, malformed, timeout, 429, 402, fatal, injected, no_usage, hang).
- `pipeline/stages.py`: Stage interface (payload / estimate / parse) + `PassthroughStage` (validates labels, ignores unknown IDs).
  **M3b plugs A3–A7 in here**; the orchestrator does not change.
- `pipeline/orchestrator.py` (A0): N async workers, each with its own connection; claim → pre-call check → reserve → limiter →
  `calls` row `in_flight` → call → one transaction (call outcome + usage, ledger, fenced cache + fan-out, queue). Stops: drained, limit,
  time, budget, quota_402, fatal_<status>, interrupted (Ctrl-C) → releases leases, closes attempts, checkpoint (count + sha256 of
  completed IDs), `outputs/run_log.jsonl` + `outputs/quarantine.jsonl`. Heartbeat; `recover()` closes crashed runs (heartbeat older than the lease).
- CLI: `enrich [--provider fake|celeris|anthropic] [--paid --max-spend] [--workers --batch-size --limit --max-minutes --lease-seconds --bucket]`,
  `resume`, `status`, `run --input <csv>`; `export`/`publish` print "arrives in M10/M11". Paid providers are refused without `--paid`
  and `--max-spend` (and do not exist yet); the fake provider is refused on `state/pipeline.db` (use `--db`).
- Tests `tests/test_m3a_queue.py` (15, ~5 s): 4 threads + 4 async workers + **two processes** → no item twice, none lost;
  **kill -9 mid-call → resume** (phase `resume`, crashed run closed, hung call failed "interrupted", 0 re-paid IDs, 0 open reservations);
  Ctrl-C; expired lease reclaimed + old owner fenced; ledger stops before the cap; cache hit = no call; scripted 429/408/malformed/
  no_usage/injected/402/max_attempts; limiter; calls rows fit §9.4; CLI money guards; migration. Full suite: 55 passed (incl. data tests).

### Notes for M3b
- Write `CelerisProvider` / `AnthropicProvider` against `providers/base.py` (send `request_id` as `X-Client-Trace-Id`; return usage or None).
- Replace `PassthroughStage` with the A3→A7 chain; `estimate_tokens/usd` should use the real request size (state + questions).
- The real DB still has only `provisional-v0` items (78,099 pending); fake runs never touch it.

---

## Previously completed: M2 (Labels, schema & golden set) — ✅ code gate passed 2026-10-13 (no spend); 👤 hand labels outstanding

### Done (M2)
- `labels/taxonomy.md` (contract tables verbatim + our conventions: sentiment scale, entities, evidence quote, needs_review,
  accepted alternatives), `labels/examples.yaml` (18 worked examples from analysis_10000 minus dev_100, one per rule),
  `labels/areas.yaml` (areas-v1), `labels/keywords.yaml` (keywords-v1: topic terms + entity terms).
- `pipeline/schema.py` (schema-v1): `validate_record` / `validate_labels` mirror the checker's per-record rules.
- Labeling tool: `.venv/Scripts/python scripts/label_tool.py golden|dev` → local page at 127.0.0.1:8765 (text only, metadata hidden);
  validates and saves atomically on every label to `evals/golden/golden_50_labeled.csv` / `evals/dev_100/dev_100_labeled.csv`
  (template's 13 columns + accepted_topics/intents/severities, notes, labeled_at). Resumable.
- Tests `tests/test_m2_labels.py` (28): schema fixtures, label files, firewalls (only `scripts/label_tool.py` may reference the
  labeled files — M6 adds its sweep/eval scripts to `LABEL_READERS`; no golden/dev_100 text ≥ 20 chars in `prompts/` or `labels/`),
  tool round trip. Full suite fast: 32 passed (`-m "not data"`).

### 👤 Rory (in parallel with M3a–M5)
- Golden 50: ✅ labeled 2026-10-14 (`evals/golden/golden_50_labeled.csv`). Reviewed against the contract with Claude before any
  model saw it (premium-only controls → billing, offline → downloads, lyrics → catalog, crashes → playback, translated 4 non-English
  reviews, sentiment recalibrated); entities redone as explicit feature terms, earlier free-form tags kept in `notes`. Not tuning → no disclosure needed.
- dev_100: **not hand-labeled in full** (decided 2026-10-14, PLAN v4.1). In M6 Celeris + the Haiku escalation run on all 100;
  Rory adjudicates only their disagreements (~20–30). M6 adds a disagreements-only mode to `scripts/label_tool.py dev`.

---

## Previously completed: M1 (Ingest) — ✅ gate passed 2026-10-12 (no spend)

### Done (M1)
- `pipeline/db/schema.sql` (source_reviews, texts, work_items, results, records, calls, batch_jobs, ledger, runs, checkpoints;
  analysis tables come in M4). `pipeline/agents/intake.py` = A1; `pipeline/cli.py ingest` runs it (~1.5 min, no API calls).
- Command: `.venv/Scripts/python -m pipeline.cli ingest` → `state/pipeline.db`, `grading/ingestion.json` (via the checker's
  `profile`, unchanged), `outputs/ingestion_report.json`, `outputs/data_manifest.json`, `evals/dev_100/dev_100_to_label.csv`.
- 31/31 verification checks pass (all README/manifest numbers, dev-file checksums, golden = seed ranks 1–50,
  analysis_10000 = 51–10,050, checkpoint/cost prefixes, checker profile = our counts). Malformed rows abort with the record number.
- Tests: `tests/test_m1_ingest.py` (3 fast synthetic + 6 full-data tests marked `data`, ~3 min; skip with `-m "not data"`).
  Re-run on the same DB is idempotent and outputs are byte-identical to the committed ones. Full suite: 12 passed.
- Queue: 78,099 `work_items` (stage `enrich`) under the placeholder `label_config = provisional-v0`; M3a/M6 enqueue the real
  config with `intake.enqueue(conn, stage, cfg)` (idempotent). The 13 empty texts are in `records` as `empty_review_text`.

### Measured in M1
- Scope (seed ranks 1–100,000): **78,099 distinct texts** (21,901 duplicate rows), 7.28M chars. Month shares match the full file
  within 0.16 pp (max abs diff).
- **Exact-text cache fill: +137,942 out-of-scope rows** completable at $0 (7,055 in-scope texts reused). Coverage point:
  100k only 0.4908 → with fill **0.6161**.
- Frequency add-on (remaining 406,090 distinct texts; 397,407 are singletons):
  top 1k → +6,916 rows (12k chars, cov 0.6224); 5k → +17,894 (72k chars, 0.6324); 10k → +26,577 (256k chars, 0.6403);
  25k → +41,577 (1.76M chars, 0.6539); all 406,090 → +422,667 rows (40.4M chars, cov 1.0).
  So the full corpus is ≈ 5.6× the 100k's text volume (47.7M vs 7.3M chars) — input for the §1.2 gate in M6.
- dev_100 = checkpoint_500 rows 101–200 (seed ranks 151–250), blank label columns, disjoint from cost_100.

---

## Previously completed: M0 (Setup & spec intake) — ✅ gate passed 2026-10-12

### Done (2026-10-12)
- Dataset extracted to `data/raw/` (11 files). Full CSV SHA-256 verified: `1fc85de6…2fcef6` (matches manifest/§9.1).
- `git init` (branch `main`), `.gitignore`, `.env.example` (OPENAI/ANTHROPIC/TYPESAFE/CELERIS keys + DATABASE_URL/TEST_DATABASE_URL).
- `CLAUDE.md`; `docs/BRIEF_TEXT.md` (images stripped: 238 KB → 40 KB); `docs/SPEC_NOTES.md` (= PLAN §9, plus new §9.10 Anthropic facts).
- `pipeline/check_submission.py` copied unchanged (byte-identical to the ZIP's).
- `cost/rates.csv`: Celeris (decision, magnus) + Haiku 5.5 standard/batch, dated source links.
- Python 3.12 venv, pinned `requirements.txt`; pytest skeleton (`tests/test_m0_setup.py`, 3 passing).
- Neon: project `spotify-review-pipeline` (`blue-thunder-14117447`, aws-us-east-2, PG 18, 0.25 CU, 1 GiB branch limit) + branch `test`
  (`br-little-glade-b4u6duc0`). `DATABASE_URL` / `TEST_DATABASE_URL` written to `.env`; both connect. A `hello` table (1 row) exists on main.
- Host (Q2): **Render**, one FastAPI service. `backend/app.py` (`/api/health` reads the Neon `hello` row; tested locally) + `render.yaml` blueprint.
- `scripts/celeris_smoke.py` ready (refuses without `--paid --max-spend`).
- PLAN.md: `[CONFIRM]` tags for Neon storage, Batch API discount/limits, and Haiku pricing resolved; §9.10 added.

### Facts confirmed in M0
- Spend so far: Celeris $0.00001224 (smoke test). Anthropic $0.
- Haiku 5.5 `claude-haiku-5-5`: $0.10 in / $0.50 out per MTok (≤ 100K prompt), cache read $0.01; batch 50% off ($0.05 / $0.25).
  Thinking adaptive by default; disable only at effort ≤ high; default effort `medium`.
- Batch API: ≤ 100k requests / 256 MB per batch; usually < 1 h, max 24 h.
- Celeris prices unchanged from §9.9; card top-ups $5–$1,000; credit expires 30 days after grant.

### M0 items (all done)
1. ~~API keys~~ — done: `CELERIS_API_KEY` and `ANTHROPIC_API_KEY` are in `.env`.
2. ~~Celeris Console~~ — done 2026-10-12: monthly spend limit **$10** (plan suggested $8), credit balance **$10**, Console shows **no expiry date** (docs say credit expires 30 days after grant; recheck in M5). Usage export: not yet checked (needed for the M5 reconciliation).
   Note: the $10 project budget covers Celeris + Anthropic together, so the pipeline's ledger cap (≤ $4.50 Celeris bucket, §4) is the binding limit, not the Console limit.
3. ~~Celeris smoke test~~ — done 2026-10-12 (approved by Rory): HTTP 200, 306 input tokens, **$0.00001224** list price.
   Response format recorded in PLAN §9.9 / SPEC_NOTES. No server request ID exists → our `X-Client-Trace-Id` is the `request_id`.
4. ~~GitHub repo~~ — done: https://github.com/rplewman/spotify-review-pipeline (**private**; make public before submission).
   Original brief is gitignored (stripped copy `docs/BRIEF_TEXT.md` is tracked).
5. ~~Render~~ — done: Blueprint `spotify-review-pipeline` → https://spotify-review-pipeline.onrender.com ; `/api/health` returns `{"ok":true,"db":"hello from Neon"}`. `DATABASE_URL` set in Render (main branch). Health check reports redacted connection diagnostics on failure.

### Still open (non-blocking)
- Celeris Console: check whether a usage export exists (needed for the M5 reconciliation).
- Make the GitHub repo public before submission.

