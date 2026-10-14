# STATUS — handoff log

## Last completed: M2 (Labels, schema & golden set) — ✅ code gate passed 2026-10-13 (no spend); 👤 hand labels outstanding

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
- Label `dev_100` first (M6 sweep needs it; finish before M5 ends), then the golden 50 (before M6's evaluation).
- Progress: golden 0/50, dev_100 0/100.

### Next step
M3a (Queue, cache & control engine): "Read CLAUDE.md, STATUS.md and PLAN.md §5 M3a, then do M3a."

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

