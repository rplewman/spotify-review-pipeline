# STATUS — handoff log

## Last completed: M3a (Queue, cache & control engine) — ✅ gate passed 2026-10-14 (no spend)

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

### Next step
M3b (Enrichment agents A3–A7): "Read CLAUDE.md, STATUS.md and PLAN.md §5 M3b, then do M3b."

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

