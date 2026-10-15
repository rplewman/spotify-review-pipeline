# PLAN — Spotify Review Multi-Agent Pipeline (Final Assignment)

> Status: **v4.2** · Updated 2026-10-15 · Owner: Rory · Deadline: **2026-10-20 midnight**
> v4 changes: SQLite operational store + Neon serving DB; Celeris definitions shared once per request in `state`; cancellation Noul
> dropped; A6 extraction is code-only; A12 merged into A11; Batch API limited to the verifier; 100 hand-labeled dev reviews for the
> escalation-gate sweep; explicit `label_config` freeze; scripted interruption; early deployment skeleton; 8-topic → 4-area mapping;
> optional high-frequency-text scope add-on; date calendar replaced by a dependency-ordered sequence (§5.1).
> v4.1 (2026-10-14): `dev_100` reference labels = model labels + Rory adjudicates only Celeris/Haiku disagreements (§3.5), not 100 by hand.
> v4.1: full-corpus decision point (§1.2) at the end of M6; Haiku 5.5 is the default escalation model; Celeris Console limit $8.
> Sources read: the brief (`Final Assignment - Multi Agent Large Data Processing Pipeline.md`); from the dataset ZIP,
> `README.md`, `GRADING_CONTRACT.md` (v1, "a5-audit-v1"), `COST_CALCULATOR.md`, `check_submission.py`, `manifest.json`,
> `prepare_dataset.py`; and the Celeris docs (quickstart, decisions, pricing, rate limits, errors, models, known issues,
> API reference), checked 2026-10-12. The rules they set are digested in §9. **Not yet read:** the CSV contents (M1)
> Haiku pricing confirmed in M0 (see `cost/rates.csv`). Remaining unknowns are listed in STATUS.md.
> v4.2 (2026-10-15, after M6): full corpus **NO-GO** (confirmed by Rory). The leftover budget goes to **label fidelity** instead:
> new module **M6b** (sentiment fix, Haiku on severity for every review, a stronger-model topic test, 20% verification,
> frequency add-on top 25,000), then a re-freeze and a 10k re-run. §4 buckets re-balanced; target ≤ $9.00 conservative.

---

## 1. Goal

Answer the product question: **"Where should Spotify put the next quarter of product effort: access, usability,
playback, or billing/support?"** Back the answer with a pipeline whose every number traces to saved calculations
and source review IDs. Deliver it as **one public GitHub repo** with a deployed dashboard, while spending **≤ $10**.

### Agreed decisions

| Decision | Value |
|---|---|
| Deadline | **2026-10-20 midnight** (instructor extension) |
| Classification scope | **100,000 nonempty reviews** (the brief allows "at least 100,000"). All 660,622 rows are ingested, profiled and given exactly one export record (see §1.1). |
| Budget | **≤ $10 total at list price** for the project, pilots included (conservative reading; see Open Questions) |
| Fixed-label decisions | **`celeris-1-decision`** via `POST /v1/systemone` (Jev/System One format; the TypeSafe SDK works by changing base URL/key/model) with `CELERIS_API_KEY`. $0.04 per million input tokens (cached input is the same price), output free. $5 of free credits, expiring 2026-11-12. **Rory adds a card and sets a Console spend limit** (M0), so the workspace activates normally; the limit is a provider-side backstop, and our ledger cap (§4) stays the primary control. A `402` still means a global stop. |
| Language tasks + fallback | **Claude Haiku** (`claude-haiku-5-5`, `ANTHROPIC_API_KEY`). A console spend limit is set as a backstop. Candidate alternative to test in M5: **`celeris-1`** (Celeris's general chat model, $0.20 input / $0.02 cached / $0.70 output per million tokens, OpenAI-compatible), only if the credits cover it. |
| Escalation | **Confidence gate:** if Celeris's top probability for topic, intent or severity is **< 0.60**, the review goes to a more capable reasoning model (§3.5). Capped by record fraction and $; the threshold is tuned on **100 dev reviews, human-adjudicated where Celeris and Haiku disagree** (§3.5, M6). Sonnet 5 is out unless M5 shows a clear gap. **M6/M6b outcome:** Haiku answers severity for every review; topic and intent stay with Celeris unless the M6b stronger-model test wins on dev. |
| Architecture | A **standalone chain of agents** (§3.3) coordinated by code, with a lease-based **work queue in SQLite** (one process, async workers), a pre-call result cache, batching (≤ 50 per request), and the **Anthropic Batch API for the verifier only**. It runs without Claude. |
| One issue per complaint | `allow_multi_issue: false` (the contract default), so issue totals don't overlap |
| Classification input | `review_text` only (`classification_input_fields: ["review_text"]`). Stars, likes, version and date are never sent to models. |
| Database | **SQLite (`state/pipeline.db`, WAL mode) is the operational store** (queue, cache, results, calls, ledger, checkpoints): local, no network latency, no compute-hour limits, trivially testable. **Neon Postgres is the serving database**: a `publish` command loads processed records, aggregates, ranking, claims and recommendations into it, and the deployed backend reads only from Neon via `DATABASE_URL`. |
| Product question mapping | The 8 contract topics roll up to the brief's 4 effort areas: **access** ← access · **usability** ← usability · **playback** ← playback, downloads · **billing/support** ← billing, support. `catalog` and `other` are reported as "outside the four areas". The required baseline ranking stays per issue; the area roll-up is an additional, declared aggregate used by the memo and dashboard (M4, M9). Mapping is versioned in `labels/areas.yaml`. |
| Evaluation labels | **Golden 50** (evaluation only, hand-labeled by Rory — done 2026-10-14) **+ `dev_100`** (gate tuning only): reference = the label Celeris and the Haiku escalation agree on; Rory adjudicates every disagreement (§3.5). Not required by the brief; disclosed in the README. |
| Workspace | This folder, which becomes the git repo |

### 1.1 How the 100k scope is exported (contract-driven)

The contract requires **exactly one final record per source ID in `records.jsonl`, either `completed` or `quarantined`**.
`pending` is not an export status. So the ~560k rows outside our scope can't simply be left out (they'd be reported as missing).

**Decided (confirmed by reading `check_submission.py`):** the instructor builds **one reference for the whole cohort** with
`--analysis spotify_reviews_18months.csv`, and `run.json` must match it (`analysis_count` = 660622 and the full file's SHA).
Declaring a subset isn't possible. So the out-of-scope rows are exported as `quarantined` with the explicit reason
`out_of_scope_budget_100k` (the checker accepts any nonempty reason for nonempty texts), plus the 13 empty texts as `empty_review_text`
(the checker *requires* exactly that reason for empty texts). The contract allows this ("Other failures must be retained with reasons and reduce the
completed fraction. Accounting for a failure does not make it a successful classification."). The README, memo and
dashboard must state plainly that these rows were **accounted for, not classified**.

**Exact-text cache fill (proposed, $0):** the contract allows exact-duplicate texts to reuse a saved, validated result
via `cache_source_id` pointing directly at an original that has model-call evidence. Out-of-scope rows whose text
exactly matches a classified in-scope original can therefore be legitimately `completed` at no API cost, raising coverage.
The business ranking is reported both on the random 100k and on all completed rows; the choice is disclosed.

**High-frequency-text add-on (optional, gated in M6):** after the random 100k, also classify the most-duplicated distinct texts
outside the scope (ranked by how many out-of-scope rows share the text). Short duplicated texts cost almost nothing on Celeris, and each one
completes many rows via `cache_source_id`. Rows completed this way are tagged `scope="frequency_addon"` and **excluded from the primary business
ranking** (which stays on the unbiased random 100k); they count toward coverage only. M1 measures the gain curve (rows completed per distinct text
added); M6 decides the size from leftover budget. The add-on is only the fallback path if the full-corpus gate (§1.2) says no.
**Decided (M6, 2026-10-15): top 25,000 texts** (+41,577 rows, coverage point 0.6161 → 0.6539); built in M6b, run in M8 under
the same frozen `label_config` (so add-on texts also get the M6b severity escalation).

**Which 100k (decided):** continue the **official sampling order** from `prepare_dataset.py`, i.e. the lowest
`SHA-256("berkeley-fall-2026-assignment-5-v1:" + review_id)` among unique nonempty valid-rating rows. The first 50 are the golden set and
the next 10,000 are `analysis_10000` (containing the 500 and the 100), so **our scope = the first 100,000 in that order**: golden 50 +
analysis 10,000 + the next 89,950. That's a uniform random sample, reproducible from the course's own seed, and it automatically includes
every development file and the golden set. (All 660,622 ratings are valid 1–5 per the manifest.)

### Expected coverage score (honest math, using the checker's own formula)
`0.2 × (ingestion.json == reference) + 0.2 × (valid completed + quarantined) / 660,622 + 0.6 × valid completed nonempty / 660,609`
- 100k completed, the rest quarantined with reasons: 0.2 + 0.2 × 1.0 + 0.6 × 0.151 ≈ **0.49** of the coverage point
- Plus the cache fill and the add-on: the 0.6 part rises by (rows completed by them / 660,609). Measured in M1, decided in M6.
- **Full corpus (if the §1.2 gate passes):** all 484,189 distinct texts classified, every duplicate filled by `cache_source_id`, the 13 empty texts
  quarantined: 0.2 + 0.2 + 0.6 × (completed / 660,609) ≈ **1.0**, less any nonempty quarantines from model failures.

Every other rubric point is unaffected by corpus size.

### 1.2 Full-corpus decision point (extra credit; decided at the end of M6)

**Idea:** the 100k scope is the first 100,000 in the official seed order, so the full corpus is the same run **continued in that order** through
every remaining distinct nonempty text. No new code paths: same queue, same frozen `label_config`, same caps. Because the order is the seed order,
**any stopping point is still a uniform random sample**, so a run that has to stop early degrades gracefully into a larger-than-100k scope.

**Rough estimate (list prices, ~20 tokens per typical review inferred from file size; replaced by measurements in M5/M6):**

| Item | Full corpus (484,189 distinct texts) |
|---|---|
| Celeris decisions, shape A (~110–200 tokens/review) | $2.10–$3.90 |
| Celeris decisions, shape B (~1,000 tokens/review) | ~$19 → **not feasible** |
| Escalation, 10–15% on Haiku 5.5 ($0.10 in / $0.50 out per M; Batch API −50%), thinking off or low | ~$0.50–$2.00 |
| Verification sample, grouping, memo | ~$0.30 |
| Pilots and comparisons | ~$1.00 |
| **Total (shape A)** | **≈ $4–7** |

**Gate (all must hold, using the `analysis_10000` measurements under the frozen config):**
1. Shape A was adopted in M5 (shape B makes the full corpus unaffordable).
2. Projected conservative total = spend to date + the remaining distinct texts (≈ 384,189 = 484,189 − 100,000; exact count from M1) × measured Celeris $/text + measured escalation rate ×
   remaining texts × measured $/escalated review + downstream fixed costs **≤ $9.00** (keeps $1.00 reserve under the $10 cap).
3. Measured escalation rate ≤ 15%, or the escalation cap is set so the projection still fits.
4. Projected wall-clock (remaining requests ÷ measured sustained request rate, conservative) leaves at least **two working days** before
   2026-10-20 for M9–M12.

**GO:** M8 runs the 100k, then continues through the remaining distinct texts in seed order (`enrich --scope full`). The primary business
ranking, memo and dashboard use **all completed rows**; the 100k ranking is kept as a comparison. The add-on is dropped; no rows get
`out_of_scope_budget_100k`. If a cap or time limit stops the run early, everything not reached is quarantined `out_of_scope_budget` and the
primary ranking falls back to the completed seed-order prefix (still a uniform random sample); the README states the exact N.
**NO-GO:** stay with the 100k plan plus the frequency add-on (§1.1). Record the measured numbers that failed the gate in `cost/report.md`.
Either way the calculator's report shows both scenarios side by side.

---

## 2. Rubric → module map (what earns each point)

| Rubric item (pts) | Evidence | Module |
|---|---|---|
| **DQ1** Accessible code/setup + artifacts (1) | README setup, `.env.example`, pinned deps, clean-env test | M0, M12 |
| **DQ2** Clear architecture, shared schema, provenance (1) | Architecture diagram, schema doc, run manifest, `label_config`, `cache_source_id` | M1, M3, M12 |
| **DQ3** Memo numbers ↔ calculations ↔ evidence (1) | `claims.csv` (4 supported metrics), code-checked memo citations | M9 |
| **DQ4** Coherent recommendation, alternatives, limitations (1) | `memo.md`, dashboard recommendations | M9, M11 |
| **TE1** 50 human labels, per-field comparison, error analysis (1) | `evals/golden/` (+ `evals/dev_100/` for tuning) | M2, M6 |
| **TE2** Independent verification + planted-error + injection tests (1) | `evals/verify/`, `evals/system_tests/` | M4, M7 |
| **TE3** (a) real 100 cold/warm pilot, (b) correct offline calculator, (c) retry/spend/recovery controls (1) | `cost/` | M5, M7 |
| **WR1** Full ingestion, coverage, classification (1, proportional) | `grading/ingestion.json` (provided helper), `records.jsonl` | M1, M8, M10 |
| **WR2** Runnable staged program, bounded calls, saved handoffs, demonstrated resume (1) | CLI, `calls.jsonl`, checkpoints, recording | M3, M7, M8 |
| **WR3** Reproducible ranking + deployed dashboard/backend/DB with grounded AI recommendations (1) | `rank` command, live URL | M9, M11 |

Label quality is also checked against a **private instructor sample**. Missing or quarantined predictions there count as
wrong, so out-of-scope rows will score as misses on that diagnostic. This is a known cost of the 100k decision, to state in the README.

---

## 3. Architecture overview

### 3.1 Design principles (from class notes)
1. **The pipeline runs independently of Claude.** A standalone Python program plus its SQLite state file is the system of record.
   Claude Code only writes the code. Nothing in the pipeline depends on a chat session, the Neon MCP connector or a human pasting text.
   Any machine with the API keys can run, stop and resume it; `DATABASE_URL` is needed only for `publish`.
2. **A chain of specialized agents.** Each agent has one bounded job, versioned instructions, a typed input/output and saved handoffs (§3.3).
3. **Queues, caching, parsing, batching and Batch API**, each with a concrete mechanism (§3.4–3.6). Sized for the real load:
   100k reviews ÷ 50 per request ≈ 2,000 decision calls, so one process with async workers is enough.
4. **Confidence-gated escalation:** the cheap decision model answers first, with the label range and context. Only answers below a
   **60% confidence threshold** go to a more capable reasoning model (§3.5).

### 3.2 Databases: SQLite (operational) + Neon Postgres (serving)

**Operational store: `state/pipeline.db` (SQLite, WAL mode, gitignored; snapshots exported to files).**

| Table | Purpose | Key constraints |
|---|---|---|
| `source_reviews` | All 660,622 rows: `review_id`, `source_sha256`, `text_sha256`, `scope` (`random_100k` / `frequency_addon` / `out_of_scope`), `scope_rank`, metadata, text. | PK `review_id` |
| `texts` | One row per distinct in-scope text: `text_sha256`, `review_text`, `original_review_id` (the ID that is sent to models) | PK `text_sha256` |
| `work_items` | **The queue:** one row per (`text_sha256`, `stage`, `label_config`) with `status` (pending/leased/done/failed/quarantined), `lease_owner`, `lease_expires_at`, `attempts`, `last_error` | UNIQUE (`text_sha256`, `stage`, `label_config`) |
| `results` | **The cache:** validated output per (`text_sha256`, `label_config`): labels, probabilities, decision source (decision / escalation), quote, entities | UNIQUE (`text_sha256`, `label_config`) |
| `records` | The final record per `review_id` (completed via call or `cache_source_id`, or quarantined + reason) | PK `review_id` |
| `calls` | Every attempt: request ID, role, agent, model, tier (standard/batch), phase, outcome, review IDs, usage, latency, error | UNIQUE `request_id` |
| `batch_jobs` | Anthropic Batch API jobs (verifier only): provider job ID, item IDs, submitted/ended times, status | UNIQUE provider job ID |
| `ledger` | Spend reservations and settlements per call, per provider/bucket | — |
| `runs`, `checkpoints` | Run IDs, config, phase, `completed_ids` snapshots | — |
| `issues`, `membership`, `aggregates`, `area_aggregates`, `ranking`, `claims`, `recommendations` | Analysis outputs | `membership` PK (`issue_id`, `review_id`) |

**Serving store: Neon Postgres.** `pipeline publish` (idempotent, truncate-and-load in one transaction) copies the dashboard's tables:
`records` (completed + quarantined, with text for completed in-scope rows only), `issues`, `membership`, `aggregates`, `area_aggregates`, `ranking`,
`claims`, `recommendations`, `run_summary`, `cost_summary`. Plus read-only **views** for the dashboard and a read-only role for the backend.
Neon (checked M0, 2026-10-12): project `spotify-review-pipeline` (`blue-thunder-14117447`, aws-us-east-2, PG 18); branch logical size limit 1 GiB, 0.25 CU; estimated footprint < 150 MB.

- Every stage's output is **also exported to files** (`outputs/`, `grading/`, `cost/`), so graders can replay the calculator and the ranking
  offline with no database or keys. SQLite holds the working state; the files hold the evidence; Neon serves the dashboard.
- Tests: logic and queue tests run offline with a FakeProvider on a temp SQLite file; one `publish` test runs against a Neon `test` branch.

### 3.3 Agent chain

```
CSV ─▶ A1 Intake/Parser ─▶ A2 Cache ──hit──▶ record (cache_source_id) ───────────────────────────┐
                              │ miss                                                             │
                              ▼                                                                  │
                         [queue: work_items] ◀── leases ── Workers (1→N, shared limiter + ledger) │
                              │                                                                  │
                              ▼                                                                  │
                         A3 Decision (celeris-1-decision) ─▶ A4 Router (code)                    │
                                                              │ conf ≥ 0.60      │ conf < 0.60    │
                                                              │                  ▼                │
                                                              │       A5 Escalation (reasoning)   │
                                                              ▼                  ▼                │
                                                         A6 Extractor (code) ─▶ A7 Validator ─▶ results + records
                                                                                                  │
   A8 Verifier (independent sample, Batch API) ◀──────────────────────────────────────────────────┤
   A9 Grouper (code + model names) ─▶ A10 Ranker (code) ─▶ A11 Memo + recommendations ─▶ publish ─▶ Neon ─▶ API ─▶ Dashboard
```

| # | Agent | Model / code | Input | Output | Failure / stop rule |
|---|---|---|---|---|---|
| A0 | **Orchestrator** | code | run config, DB state | dispatch, leases, budgets, logs | stops on spend/time cap or `402`; always saves first |
| A1 | **Intake/Parser** | code | CSV path | `source_reviews`, `texts`, `work_items`, ingestion report | malformed row → abort with row number (never skip silently) |
| A2 | **Cache** | code | `text_sha256` + `label_config` | hit → completed record with `cache_source_id`; miss → enqueue | never reuses across a different `label_config` |
| A3 | **Decision** | `celeris-1-decision` | up to 50 review texts + **range & context** stated once in `state` (§3.8): allowed values, definitions, severity scale, precedence/tie rules, short dev examples | per review: topic, intent, severity (Choice), sentiment (Score), **with probabilities** (cancellation is an intent option; no separate Noul) | retry transient errors; invalid → retry once → quarantine |
| A4 | **Router** | code | A3 probabilities | route: accept, or escalate | escalation cap reached → accept with `needs_review=true` (never escalate unbounded) |
| A5 | **Escalation** | reasoning model (Haiku with low thinking or `celeris-1-magnus`; chosen in M5) | one or more reviews + definitions + the specific uncertain field(s) + A3's top-2 candidates | final label(s) (the quote comes from A6 code) | capped fraction + $ sub-cap; can't change the taxonomy or invent facts |
| A6 | **Extractor** | code only | text + final labels | `evidence_quote`: the shortest sentence containing a term from the chosen topic's keyword list, else the full text (always an exact substring); `entities`: matches from a fixed feature-term list | quote check fails → full text; never a model call |
| A7 | **Validator** | code | assembled record | committed `results` + `records` | schema/ID/quote checks (§9.8) |
| A8 | **Verifier** | Haiku (independent prompt), Anthropic Batch API | declared random sample of texts **without** first predictions | verifier labels → disagreement report | one batch job; resumed by job ID |
| A9 | **Grouper** | code + Haiku naming | completed complaint/cancellation records | `issues`, `membership` | one issue per review |
| A10 | **Ranker** | code | membership + records | `ranking`, `aggregates`, `area_aggregates` | deterministic; zero model calls |
| A11 | **Memo writer + recommender** | Haiku | aggregates + area roll-up + bounded evidence pack | `memo.md`, `claims.csv`, and the dashboard `recommendations` (one structured output) | code rejects unknown IDs or mismatched numbers; generated once, stored |

`calls.jsonl` roles: A3/A5 → `enrich` (same `label_config`), A8 → `verify`, A9 → `group`, A11 → `memo`.

**Code vs model boundary:** code owns record accounting, parsing, hashing, dedup, caching, queueing, validation, routing, budgets,
arithmetic, ranking and claim checks. Models only read messy language.

### 3.4 Queue (control system: no review is processed twice at the same time)
- Workers are **async tasks in one process** (1 → N, N set from measured rate limits). A second process is supported but not needed.
- Workers **claim** work atomically inside `BEGIN IMMEDIATE` (SQLite's single writer lock, so two claims can never interleave):
  ```sql
  UPDATE work_items SET status='leased', lease_owner=:worker, lease_expires_at=:now_plus_5min, attempts=attempts+1
  WHERE id IN (SELECT id FROM work_items
               WHERE stage=:stage AND label_config=:cfg
                 AND (status='pending' OR (status='leased' AND lease_expires_at < :now))
               ORDER BY scope_rank LIMIT :batch_size)       -- ≤ 50
  RETURNING id, text_sha256;
  ```
  The write lock means two workers can never grab the same item; an expired lease (a crashed worker) returns the item to the pool.
- **Commit is fenced:** results are written in one transaction only if the worker still holds the lease
  (`WHERE lease_owner=:worker`). Upserts use `ON CONFLICT DO NOTHING` on the unique keys, so a late duplicate can't double-count.
- **Check before calling (the cache):** before every request, a worker re-checks `results` for (`text_sha256`, `label_config`). If someone
  else already finished it, the item is marked done with no call. This saves money and time on retries, resumes and duplicates.
- Shared across all workers (in the DB, so a second process would see them too): the requests/tokens rate limiter per model, and the
  **spend ledger** (reserve before dispatch; stop admitting when spent + reserved + next > cap).
- Attempt policy: transient errors → backoff with jitter and requeue; invalid output → one retry → `quarantined` with reason and
  attempt count (written to `outputs/quarantine.jsonl`); `402` → global stop.

### 3.5 Decision model + confidence-gated escalation
- **Range & context for Celeris:** each question carries its **allowed range** (8 topics, 5 intents, a 5-level severity scale, a 5-level sentiment
  scale) and **context** (the contract definitions, precedence and tie rules, "metadata isn't evidence", and 1–2 short dev-set examples where they help).
  The criteria text is kept short, because Celeris bills input tokens including the question text (§9.9).
- **Gate:** `top_probability < 0.60` on **topic, intent or severity** → escalate (A5). The threshold is a declared, versioned part of
  `label_config` (e.g. `…+gate0.60+…`). Celeris's `confidence` field is a rescaled value (0 = even split), so we gate on the raw top probability
  and record both.
- **Tune against adjudicated labels (`dev_100`):** 100 dev reviews drawn deterministically from `checkpoint_500` rows 101–500
  (so the cost_100 pilot stays untouched). Every Celeris probability is saved, and **every** `dev_100` review is also run once through the
  A5 escalation (Haiku, < $0.01). Where Celeris and Haiku agree on topic, intent and severity, that label is the reference; Rory adjudicates
  every disagreement in the label tool (expected 20–30 reviews). This is sufficient for the sweep: the gate only changes the outcome on
  reviews where the two models disagree, and all of those carry a human label. Shared model errors are invisible to it (golden 50 covers that). M6 then replays gates 0.50 / 0.60 / 0.70 **offline**: for each gate, accuracy vs the human labels (Celeris-only label above
  the gate, escalated label below it) and the escalation rate measured on all 500 (probabilities only, no calls). Pick the gate with the
  best accuracy per escalated record, then **freeze `label_config`** (M6 gate).
- **`dev_100` rules:** used for gate tuning only; never used as prompt examples (that would bias the sweep); never mixed with the golden 50.
- **Caps:** an escalation fraction cap (e.g. ≤ 15% of records, set from M6) **and** a $ sub-cap. Beyond the cap, low-confidence records keep the Celeris label
  with `needs_review=true` and are reported.
- **Escalation model options, measured in M5:** Haiku with minimal thinking vs `celeris-1-magnus` (reasoning, cheap, Celeris credit), on the
  low-confidence reviews only. Sonnet 5 is out unless both clearly fail.

### 3.6 Caching, parsing, batching, Batch API
- **Caching (three separate mechanisms, reported separately):**
  1. *Result cache*: `results` keyed by (`text_sha256`, `label_config`); duplicates → `cache_source_id` (direct original only).
  2. *Pre-call check*: §3.4. 3. *Provider prompt caching*: the shared instruction prefix for Anthropic calls, counted only when the usage reports it.
     Downstream artifacts (groups, memo) are cached by an input hash too, so a warm rerun makes zero calls.
- **Parsing:** a streaming CSV parser that handles multiline text (`csv.DictReader(strict=True)`, `utf-8-sig`, the checker's `row_sha`); strict JSON parsing
  of model output (schema validation, exact-ID echo, the known Celeris code-fence strip only on exact matches); unparseable → retry once → quarantine.
- **Batching (three meanings, kept distinct):** CSV chunks for memory; **≤ 50 reviews per model request**; and the **provider Batch API**.
- **Batch API:** Anthropic's Message Batches API (asynchronous, **50% off input and output**; ≤ 100,000 requests or 256 MB per batch; most finish within 1 h, max 24 h; Haiku 5.5 batch $0.05/$0.25 per MTok — checked M0, 2026-10-12) for **A8 verification
  only**: one job per verification run, which isn't latency-sensitive. Flow: build the job (each request ≤ 50 reviews) → submit → `batch_jobs` row →
  poll → validate and commit each result → requeue failures; an interrupted job is resumed by its ID, never resubmitted.
  A5 escalation runs in real time (it's small and on the critical path; turnaround of up to 24h is a deadline risk). Celeris has no Batch API.
  The calculator records the **tier actually used** per call (standard vs batch); a hypothetical batch discount for other stages appears only in estimates.

### 3.7 `label_config`
One string per configuration, for example `celeris-1-decision+shapeA+gate0.60+esc-<model>+extract-code-v1+prompt-v1+schema-v1`.
Every enrichment call (A3, A5) and every record it completes carries the same string. The checker counts a completed record as
logged only if some **succeeded** `enrich` call contains its ID with the **identical** `label_config`. Any change to a model, prompt, schema
or threshold means a new `label_config` and new work, with no cache reuse.

**Freeze point:** the final `label_config` is frozen at the end of the M6 threshold sweep, **before** `analysis_10000` runs. The 100 and 500 dev
runs used a provisional config; under the frozen config they are re-run (≈ $0.03 on Celeris), so every record in the final export
shares one `label_config` and the dev work genuinely counts toward the main run.

### 3.8 Celeris request shape (the key cost question, decided by measurement in M5)
A decision request has one `state` (input) and up to 512 questions, and **billed input includes every question's criteria text.**
- **A (default). Shared definitions:** `state` holds the full label definitions, tie rules and examples **once**, followed by up to 50 reviews,
  each tagged `[R<n>]`. Each review gets 4 questions (topic, intent, severity, sentiment = ≤ 200 per request) whose criteria are only a few
  words ("Topic of [R7]") and whose options are bare label names (descriptions live in `state`). Definitions are paid once per 50 reviews,
  so the expected cost per review ≈ its text + ~40 question tokens. Risk: answers bleeding between reviews; M5 measures it against B.
- **B (fallback). One review per request:** the cleanest attribution; the definitions are resent every time. At ~1,000 input tokens per review,
  100k ≈ 100M tokens ≈ **$4.00**, right at the credit cap.
- M5 compares A and B on the same 100 (labels agreement + tokens per review). A wins unless its agreement with B is materially lower.

### Repo layout
```
data/raw/            dataset files (gitignored; checksum recorded in README)
pipeline/            cli.py, orchestrator.py, db/ (schema.sql, queue.py, ledger.py, publish.py), agents/ (intake, cache, decision,
                     router, escalation, extractor, validator, verifier, grouper, ranker, memo), providers/ (celeris,
                     anthropic, anthropic_batch, fake)
state/               pipeline.db (SQLite, gitignored)
prompts/             versioned instructions per agent
labels/              taxonomy.md (contract definitions + our examples), areas.yaml (8 topics → 4 areas), keywords.yaml
                     (topic terms for quotes + entity list), label_config registry
outputs/             enriched.jsonl, quarantine.jsonl, issues.json, aggregates.csv, ranking.csv, memo.md,
                     run_log.jsonl, run_summary.json, data_manifest.json, ingestion_report.json
evals/               golden/, dev_100/, verify/, system_tests/, recovery/, threshold_sweep/
cost/                calculator, README (offline replay + explicit pilot commands), rates.csv, usage.csv,
                     pilot_records.jsonl, pilot_calls.jsonl, report.md
grading/             run.json, ingestion.json, records.jsonl.gz, membership.csv, ranking.csv, claims.csv,
                     calls.jsonl.gz, checkpoint_before.json, checkpoint_after.json
backend/ dashboard/  FastAPI app (reads Postgres) + static dashboard
docs/                SPEC_NOTES.md, architecture diagram, BRIEF_TEXT.md
tests/               pytest suite (FakeProvider + temp SQLite; one publish test on a Neon branch)
scripts/             resume_demo (scripted interruption for M7/M8)
CLAUDE.md STATUS.md  session bootstrap + handoff log (see §6)
```

---

## 4. Budget plan (list price; refined after the M0 price check and each pilot)

| Bucket | Agent(s) | Provider / tier | Cap | Notes |
|---|---|---|---|---|
| Pilots + comparisons (100 / 500, A vs B, escalation candidates, `dev_100` escalations, M6b topic-model test) | all | mixed | $0.50 | ≈ $0.04 spent through M6; the M6/M6b 10k runs are under the frozen config and count in the buckets below |
| Decisions for the 100k + frequency add-on (25k) | A3 | Celeris, real time | $2.00 | Credit first, then the card. Conservative: 100k $1.19 + add-on ≈ $0.38 + the M6 10k already spent $0.09 (full corpus NO-GO, M6) |
| Escalation | A5 | Haiku 5.5, real time | $5.00 | **M6b: severity for every review** (100k ≈ $2.0 base / $3.4 conservative; add-on ≈ $0.6 / $1.1). A stronger topic model (M6b test) is added only if it fits here + reserve with ≥ $1.00 left |
| Verification sample | A8 | Haiku, Batch API | $1.00 | **M6b: 20% of distinct completed texts** (was 5% in M4), seed `verify-v1` (sha256(seed:text_sha256)/2^256 < 0.20; a superset of the 5% sample) ≈ 15,600 texts at the 100k + ≈ 5,000 add-on texts; ≤ 50 per request; ≈ $0.55 base / $0.90 conservative |
| Grouping names, memo + recommendations | A9, A11 | Haiku | $0.25 | Generated once and stored, never at page view |
| Reserve (retries, reruns) | — | — | $1.25 | Never planned into; the ledger's project cap ($10) is the hard stop |

**v4.2 projection (conservative, list):** spent $0.23 + 100k A3 $1.19 + severity escalation $3.43 + add-on ≈ $1.48 + verification 20% ≈ $0.90 + analysis ≈ $0.05 + M6b test/re-run overhead ≈ $0.20 ≈ **$7.5** (base ≈ $4.8). Any stronger topic model must fit in the remaining ≈ $1.5 while keeping the $1.00 floor below $10.
| **Total** | | | **≤ $10.00** | Cash spend ≈ everything except the Celeris bucket |

**Reference point from the contract (illustration, not measured):** 660,609 reviews × (100 input + 150 output tokens) on
GPT-6 Luna at $0.10/$0.50 per million ≈ $56.15, or ≈ $41.16 with exact-text reuse. That's why the design sends only uncertain or long
reviews to a token-generating model, and lets Celeris (output free) plus code handle the rest.

**How each class technique saves money:**
- **Queue + pre-call cache check:** no review is processed twice, and retries and resumes skip finished work.
- **Exact-text result cache:** each distinct text is paid for once; duplicates are free.
- **Confidence gate:** the expensive reasoning model only sees the uncertain minority.
- **Batching ≤ 50 per request with definitions in `state`:** label definitions are paid once per 50 reviews, not once per review.
- **No redundant questions:** cancellation is an intent option, not a separate Noul question.
- **Batch API:** a discount on the verifier, the one non-urgent Anthropic stage.
- **Parallel workers:** less wall-clock time (not lower cost), within shared limits.
- **Output minimization:** compact label codes; code fills in IDs, hashes, `label_config`, quotes and entities.

**Reporting rule:** usage is priced at **real list rates and the tier actually used**. Credits and subscriptions are never treated as free. The report
shows *list-price cost*, *credits applied* and *cash spent* separately; local compute is reported apart from API spend, and unmeasured costs are
marked "unknown", never 0. Doubling the API rates must double the API subtotal, leaving local costs and measured times unchanged.

**Paid-call rule:** every paid command requires an explicit `--paid` flag **and** `--max-spend`, and is only run
after Rory approves it in that session. All other commands (including the calculator) are offline by default; importing the calculator never calls an API.

---

## 5. Modules (build order)

Each module is sized for **one session** (M3 is split into M3a/M3b) (two at most), has an offline test gate, and ends with a STATUS.md update and a
git commit. 💰 = needs paid calls (approval required). 👤 = needs a human action from Rory.

### M0 — Setup & remaining spec intake (no spend)
- 👤 Put the keys in `.env`. Extract the ZIP into `data/raw/` (it's in the project root; **don't extract until this module**).
- 👤 In the Celeris Console: add a card, set a monthly spend limit (suggested **$8**: enough for the full-corpus scenario in §1.2, still under the $10 total),
  confirm the $5 credit balance and its expiry, and that the workspace is active. Note whether usage can be exported, for the reconciliation in M5.
  Record the limit in STATUS.md.
- `git init`; `.gitignore` (`.env`, `.env.*`, `!.env.example`, `data/raw/`, `state/`, the dataset ZIP, local reports); a blank
  `.env.example` with `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `TYPESAFE_API_KEY`, `CELERIS_API_KEY`.
- Write a short `CLAUDE.md` that points sessions to PLAN.md + STATUS.md and the token rules in §6.
- Create `docs/BRIEF_TEXT.md`: the brief with its embedded base64 images stripped (238 KB → ~48 KB).
- Copy §9 of this plan into `docs/SPEC_NOTES.md` (all specs and Celeris docs have now been read; don't reread them).
- Copy `check_submission.py` into the repo **unchanged** and import its `row_sha`, `canonical` and `mean_string` rather than reimplementing them.
- Check current Haiku pricing and model ID on the official page. Write `cost/rates.csv` with units and dated source links
  (Celeris rates from `docs.celeris.ai/pricing`, checked 2026-10-12).
- $0 smoke test: one `celeris-1-decision` request on a **synthetic** review to capture the exact System One response JSON
  (answer fields, `usage` names, `id`/request ID). The decisions docs don't publish a full example. Costs well under $0.0001.
- Python env with pinned `requirements.txt`; pytest skeleton.
- Neon: create the project + a `test` branch; put `DATABASE_URL` (and `TEST_DATABASE_URL`) in `.env`; check the plan limits.
- **Pick the host now (Q2)** — default: one FastAPI service on Render serving the API + static dashboard. **Deploy a hello-world**
  that reads one row from Neon and shows it. This retires the deployment risk on day one; M11 only fills it in.
- Anthropic: confirm the Message Batches API discount, limits and turnaround, and the extended-thinking controls for Haiku.
- **Gate:** every `[CONFIRM]` resolved, or listed as open in STATUS.md; PLAN.md updated if the facts differ.

### M1 — Ingest (no spend)
- `check_submission.py profile --full … --out grading/ingestion.json` (the contract requires the provided helper).
- Stream the CSV with a multiline-safe parser; never load it into chat context; scripts print summaries only.
- Per-row `source_sha256` via the provided `row_sha` (compact UTF-8 JSON of the 6 raw field strings, in order, with no normalization).
- **Verify against the dataset README:** 660,622 rows; 97,400,616 bytes; SHA-256 `1fc85de6…2fcef6`; 13 empty texts;
  159,701 missing app versions; 0 repeated IDs; 484,189 distinct nonempty texts; date window 2022-05-17 → 2023-11-17 (exclusive).
- Quarantine the 13 empty texts (`empty_review_text`).
- **Select the 100k** per §1.1 (the first 100,000 in the official SHA-256 seed order). Assert that the first 50 equal
  `golden_50_to_label.csv`, the next 10,000 equal `analysis_10000.csv`, and that the dev-file checksums match `manifest.json`.
  Report the month distribution vs the full file, the distinct texts needed, **how many out-of-scope rows the cache fill would complete** (§1.1),
  and the **frequency add-on gain curve**: out-of-scope rows completed if we add the top 1k / 5k / 10k / 25k most-duplicated distinct texts,
  with their total characters (a proxy for Celeris cost).
- Select `dev_100` (§3.5): the first 100 of `checkpoint_500` rows 101–500 in seed order; write `evals/dev_100/dev_100_to_label.csv`.
- Agent A1 (Intake/Parser): apply `db/schema.sql` to `state/pipeline.db`; load `source_reviews` (all 660,622) and `texts` (in-scope distinct texts); enqueue `work_items`
  for distinct texts; write `outputs/data_manifest.json` and `outputs/ingestion_report.json`. Re-running is idempotent (upserts).
- **Gate:** tests reproduce every number above; re-running gives byte-identical outputs.

### M2 — Labels, schema & golden set (no spend; 👤 labeling)
- `labels/taxonomy.md`: the contract's topic, intent and severity tables **verbatim** (§9.2), plus our worked examples from
  **development files only**: tie rules, premium-only controls → billing, paid-plan mention alone ≠ billing, ads → usability,
  connection/audio quality → playback, boycott slogans → unclear, general "bad app" → complaint/other with no invented defect,
  and cancellation not automatically raising severity.
- Record schema exactly as in §9.3, plus `cache_source_id` on reused rows.
- `labels/areas.yaml` (8 topics → 4 effort areas, §1) and `labels/keywords.yaml` (topic terms for quote selection; feature-term list for entities).
- A local labeling helper (simple local HTML or CSV) for `golden_50_to_label.csv` and `dev_100_to_label.csv`: shows one review, the definitions,
  dropdowns for topic/intent/severity, a sentiment slider, and an ambiguity note; saves accepted alternatives for ambiguous cases.
- 👤 Rory hand-labels **the golden 50** (done 2026-10-14). `dev_100` is not hand-labeled in full: Rory adjudicates only the
  Celeris/Haiku disagreements in M6 (v4.1, §3.5).
- **Firewalls:** golden labels live only in `evals/golden/`, dev labels only in `evals/dev_100/`. A test fails if any pipeline module or prompt
  reads either, or if a golden or `dev_100` text appears in prompt examples. Only the M6 sweep script may read `dev_100`; only the evaluation
  script may read the golden labels.
- **Gate:** the schema validator passes on hand-written fixtures; the firewall tests pass.

### M3a — Queue, cache & control engine (no spend)
- `db/schema.sql` (§3.2, SQLite, WAL); `queue.py` with **lease-based claiming under `BEGIN IMMEDIATE`**, lease expiry, fenced commits,
  `INSERT … ON CONFLICT DO NOTHING` upserts; the **pre-call cache check** (§3.4); A2 Cache agent with `cache_source_id` fan-out (direct original only).
- Shared DB-backed **rate limiter** per model and **spend ledger** (reserve → settle → release; stop when spent + reserved + next > cap).
- Orchestrator skeleton: async worker loop (1→N tasks in one process), stop conditions (spend cap, time cap, `402`, Ctrl-C → save and exit).
- Provider interface + a **`FakeProvider`** (scripted: valid, low-confidence, malformed, timeout, 429, 402, injected text).
- `calls` rows per attempt in the contract shape (§9.4): unique `request_id` (provider ID, else a local UUID also sent as `X-Client-Trace-Id`);
  integer usage, with `0` + `"usage_available": false` when the provider gives none; phase `initial`/`resume`; tier `standard`/`batch`.
- CLI: `run --input <csv>` (all stages), `ingest`, `enrich [--paid --max-spend X --workers N]`, `resume`, `status`, `export`, `publish`.
- **Gate (tests):** 2–4 concurrent fake workers (plus one test with two processes) on the same queue → **no item processed twice**, no lost items; kill mid-run → resume with
  no re-paid IDs; an expired lease is reclaimed; the ledger refuses work before the cap; a cache hit makes no call.

### M3b — Enrichment agents A3–A7 (no spend)
- A3 Decision: the Celeris System One client; definitions in `state` and short per-review questions (§3.8 shape A), shape B behind a flag;
  topic/intent/severity/sentiment only; every probability saved.
- A4 Router: the `< 0.60` gate per field (configurable, versioned in `label_config`); escalation fraction cap + $ sub-cap; over the cap →
  `needs_review=true`.
- A5 Escalation: a reasoning-model client (Haiku with low thinking / `celeris-1-magnus`), single-field questions, A3's top-2 candidates,
  strict JSON. Real time, not Batch API.
- A6 Extractor (code only): the shortest sentence containing a topic keyword, else the full text; entities from the feature-term list.
  A7 Validator per §9.8; one retry → quarantine.
- Error policy: Celeris per §9.9 (retry 408/429/502/503 honoring `Retry-After`, max 5; never retry 400/401/402/404/413/422; `402` = global stop);
  the same pattern for Anthropic. The known Celeris code-fence strip only on exact matches.
- **Gate (tests):** FakeProvider end to end: confident → accepted; low-confidence → escalated; escalation cap → `needs_review`; malformed →
  retry → quarantine; a shape-A response with a missing or foreign `[R<n>]` → retry once → quarantine; every quote is an exact substring;
  every output passes the §9.8 schema checks.

### M4 — Downstream stages, thin versions (no spend)
Needed before the pilot, because the 100-review pilot must include verification, grouping, ranking and the memo.
- **Verify:** a separate prompt; a declared seeded random sample rate; the verifier never sees the first prediction;
  code computes agreement and writes a disagreement report. Runs through the **Anthropic Batch API runner** (build job → submit → `batch_jobs`
  row → poll → validate/commit → requeue failures; resume an in-flight job by ID), with a `--standard` flag for the 100-review pilot if
  batch turnaround is slow.
- **Group:** deterministic issue IDs (e.g. topic + optional subtopic); every completed complaint/cancellation record in
  exactly one issue; no duplicate pairs; Haiku names issues from bounded examples; the accepted mapping is saved.
  **The group model call is mandatory:** the checker flags `missing_model_roles` unless `calls.jsonl` has a succeeded call for each of
  `enrich`, `verify`, `group` and `memo`.
- **Rank:** per §9.5, plus the declared **area roll-up** (`area_aggregates`: complaint count, severity sum, mean severity and share per effort area,
  via `labels/areas.yaml`). A `rank` command recomputes both from saved files with **zero model calls**.
- **Memo + recommendations (one A11 call):** gets aggregates + the area roll-up + an evidence pack only; outputs `memo.md`, `claims.csv`
  (supported metrics only) and the structured dashboard `recommendations`; code rejects unknown issue/review IDs or numbers that don't match
  saved calculations. The memo answers the brief's four-way question using the area roll-up and cites issue-level claim IDs.
- **Gate:** the end-to-end run on FakeProvider produces every output file; running `rank` twice gives byte-identical output.

### M5 — Cost calculator + 100-review pilot (💰 ≈ cents)
- Calculator: **offline replay is the default**; it reads saved usage + editable `rates.csv`; `cost/README.md` gives the
  separate replay and explicit pilot commands. It shows every row of the calculator spec's "measured" and "estimated" table (§9.6).
- Billing math per mutually exclusive item: `item_cost = billed_units × price_per_unit` (per-million prices ÷ 1,000,000);
  cached input subtracted before the uncached rate; reasoning tokens not double-counted; only the tier actually used.
- Projections: the **required full-run estimate** (660,622 rows / 660,609 outputs / 13 quarantines / 484,189 distinct, plus a
  no-reuse comparison) **and our 100k scope scenario**, each base + conservative, with fixed overhead counted once, per-stage
  extrapolation from that stage's own throughput, and budget-exceeded warnings.
- Controls: editable budget, output-token cap, max concurrency, max fallback fraction.
- 💰 `pilot --paid`: **cold** run on `cost_100.csv` unchanged (checksum verified against the manifest), empty result cache,
  1 worker, all stages. Then a **warm** rerun showing zero new enrichment calls, the warm time and the incremental cost; any downstream calls disclosed.
- 💰 (small) Two comparisons on the same 100, to justify the tool choice on quality, cost and runtime: Celeris request shape **A vs B** (§3.8:
  agreement + tokens per review), and the escalation candidates **Haiku-low vs `celeris-1-magnus`** on the low-confidence reviews only.
  Everything else (Sonnet, Batch vs standard for other stages, Haiku-only enrichment) is disclosed as "not run" with a modeled estimate.
  Measured input tokens per review decide whether the 100k fits the $4.50 Celeris bucket, and feed the first full-corpus projection (§1.2).
  Also measure Haiku escalation with thinking off vs low (output tokens dominate its cost).
- Save `pilot_records.jsonl` (separate from grading), `pilot_calls.jsonl` (run IDs, request IDs, timing, usage),
  `usage.csv`, `rates.csv` (every charged item mapped to the call log), `report.md`, plus a reconciliation note against the provider dashboards.
- **Gate:** doubling the API rates doubles the API subtotal with times and local costs unchanged; changing projected volume leaves the pilot results unchanged.

### M6 — Scale checkpoints 500 → 10,000 + golden evaluation (💰 small)
- 💰 Run `checkpoint_500` (the 100 are cached). Refresh the calculator. (Class 7 has already passed; this checkpoint is now only a development step.)
- Golden evaluation (needs M2's labels): topic/intent agreement, severity exact + MAE, sentiment MAE (predeclared tolerance),
  quote membership + support, `needs_review` as a prediction, a confusion table, an ambiguous-case count, every disagreement saved.
- 💰 (< $0.01) Run the A5 escalation on all of `dev_100`; list the Celeris/Haiku disagreements → 👤 Rory adjudicates them
  (`scripts/label_tool.py dev`, disagreements only) → `evals/dev_100/dev_100_labeled.csv`.
- **Threshold sweep (offline):** for gates 0.50 / 0.60 / 0.70, accuracy vs the `dev_100` reference labels per field and the escalation rate on all 500
  (`evals/threshold_sweep/`). Choose the gate and escalation cap.
- Tune prompts/thresholds **on development records only**. If golden results drive a revision, disclose it and add fresh held-out cases.
- **Freeze `label_config`** (§3.7) and record it in STATUS.md. Re-run the 100 and 500 under it (≈ $0.03) so the final export has one config.
- 💰 Run `analysis_10000` under the frozen config. Refresh the calculator, measure the rate limits, try 2 workers on the same inputs (the queue guarantees no double-processing), and decide the worker count.
- **Full-corpus decision point (§1.2):** compute the projection from the 10k measurements, check the four gate conditions, and record GO/NO-GO
  with the numbers in STATUS.md and `cost/report.md`. 👤 Rory confirms before M8.
- If NO-GO: **decide the frequency add-on size** from the M1 gain curve and the measured cost per text (§1.1); 0 is an acceptable answer.
  → **Decided: 25,000** (M6).
- **Gate (go/no-go):** the projected 100k cost (conservative) plus the spend so far stays ≤ budget; quality is acceptable or its limitations are documented.
- **Outcome (2026-10-15):** frozen shape A + Haiku severity escalation (gate 0.70, cap 0.40); 10k done ($0.18); golden topic/intent/
  severity 90/90/90%, sentiment 52% within ±0.25; full corpus NO-GO; add-on 25k. Details: STATUS.md, `evals/m6_notes.md`.

### M6b — Fidelity upgrade + re-freeze (💰 ≈ $0.5–0.7; decided by Rory 2026-10-15)
Goal: spend the budget the full corpus no longer needs on **label quality** for the 100k, staying ≤ $9.00 conservative.
1. **Sentiment = the most probable level** (−1, −0.5, 0, +0.5, +1; ties → nearer 0) instead of the probability-weighted value, which
   pulls toward 0 (golden: MAE 0.31, 52% within ±0.25). Code only ($0); a new version tag in `label_config` (e.g. `sentiment-mode-v1`).
   **Golden-driven, so disclosed** (README + `evals/m6_notes.md`) and checked on **fresh held-out cases**: 👤 Rory hand-labels 30 new
   reviews (seed ranks just after analysis_10000, inside the 100k, never used by any tuning step; ≈ 20 min with `label_tool.py`);
   old and new sentiment are both scored on them.
2. **Haiku on severity for every review** (not only below 0.70): dev_100 severity 92% → 95%, adjudicated 65% → 78%. Topic and intent
   keep Celeris's answer. Cap 1.00; escalation bucket $5.00 (§4).
3. **Stronger-model topic test** (💰 cents; priced from the current rate card first): a stronger escalation model (e.g. Sonnet) answers
   the topic question on all of `dev_100` with **every** topic option (not only Celeris's top-2). Adopt it for topic below a gate only
   if it beats Celeris top-1 on the adjudicated rows by ≥ 2 reviews **and** the projected cost fits in escalation + reserve with
   ≥ $1.00 left; the gate is then swept on dev. Otherwise topic stays with Celeris (recorded either way).
4. **Verification at 20%** (declared rate, seed `verify-v1`; a superset of the 5% sample). Changes the evidence, not the labels.
5. **Frequency add-on top 25,000** (§1.1): build the queue path (`enrich --scope addon`: frequency-ranked out-of-scope texts, tagged
   `scope="frequency_addon"`, excluded from the business ranking). The run itself is in M8.
6. **Re-freeze `label_config`**, set `pipeline/db/ledger.py` BUCKET_CAPS to §4, re-run seed ranks 1–10,050 under it (≈ $0.35–0.45),
   re-run the golden eval (M6 and M6b numbers side by side), refresh the calculator and the projection.
- **Gate:** projected conservative total (spend to date + 100k + add-on + verification + analysis) ≤ $9.00; the held-out sentiment
  result and the topic-test result are recorded (adopted or not); offline tests pass before any paid step.

### M7 — System tests & recovery evidence (no spend / tiny)
- Planted-error test: a deliberately wrong label in a **separate test copy**; the verifier/checks catch it.
- Injection test: a synthetic review containing instructions; record what actually happened.
- Malformed output, missing text, ambiguous label, temporary API failure (FakeProvider + any real transient failures).
- **Interruption/resume:** `checkpoint_before.json` (`completed_ids`) → kill → resume (phase `resume`) →
  `checkpoint_after.json` with additional completed IDs and **no new enrichment calls for already completed IDs**. Short screen recording.
  The checker requires: before is nonempty and a strict subset of after; after ⊆ valid completed; every non-cached "before" ID appears in a
  succeeded `initial` call; at least one new ID in a succeeded `resume` call; no `resume` call contains a "before" ID.
- **Script it, don't wait for it:** write `scripts/resume_demo` now (start the main run → wait for N completed batches → write
  `checkpoint_before.json` → SIGINT → `status` → `resume` → wait → `checkpoint_after.json` → run the checker's resume rules locally).
  It is executed in the first ~10 minutes of M8 with the screen recording running. Rehearse it once on FakeProvider here.
- Synthetic records are marked and excluded from business aggregates.
- **Gate:** all outcomes saved in `evals/system_tests/` and `evals/recovery/`, failures included.

### M8 — Main 100k run (💰 main spend)
- 💰 Start with `scripts/resume_demo` (M7) while recording; it launches `enrich --scope main --paid --max-spend <bucket> --workers <measured>`,
  interrupts once, and resumes. The run then continues to completion, resumable across sessions.
- Monitor via `status`. A credit-exhaustion or cap stop means save, report and decide; never auto-raise caps.
- Then, per the M6 decision: **GO** → continue through every remaining distinct text in seed order (`enrich --scope full`, §1.2);
  **NO-GO** → the frequency add-on, if sized > 0 (§1.1). Same `label_config` and caps either way.
  → **NO-GO confirmed (M6): the 100k, then the 25k add-on (`enrich --scope addon`), under the M6b frozen config.**
- Apply the exact-text cache fill to out-of-scope rows (§1.1); quarantine the remaining out-of-scope rows with `out_of_scope_budget_100k`.
- **Gate:** every one of the 660,622 IDs has one final record; the counts reconcile with `ingestion.json`.

### M9 — Analysis: verify, group, rank, memo (💰 ≈ cents)
- Verification on the declared sample → disagreement report + investigation notes.
- Grouping → `issues.json`, `membership.csv`; ranking → `aggregates.csv`, `ranking.csv`.
- Optional trend analysis: complaint shares in comparable periods with denominators (first/last months are partial).
- Area roll-up → `area_aggregates.csv`.
- 💰 One A11 call → `memo.md` + `claims.csv` + dashboard `recommendations`; code-checked; Rory reviews the argument.
- Wording rules: no revenue, no churn, no causal claims; cancellation language is *expressed intent*; disclose the scope,
  the quarantined/unclassified rows and their effect on conclusions; avoid repeating personal details from reviews.
- **Gate:** the `rank` command from saved outputs reproduces scores and order exactly; every memo number maps to a claim ID.

### M10 — Grading export + checker (no spend)
- Build `grading/` exactly per §9.3–9.5; gzip the large JSONL files.
- Run the three checker commands (§9.7); keep `local-reference.json` and `self-check.json` **outside** `grading/`; fix until clean.
- **Gate:** the checker passes, or any remaining `review_required` items are explained in the README.

### M11 — Publish, backend, dashboard (no spend; the recommendations already exist from M9)
- `pipeline publish`: load the serving tables into Neon (§3.2); add read-only **views** (overall metrics, topic/intent/severity distributions,
  area roll-up, ranking, claims, cost summary) and a read-only DB role for the backend.
- Fill in the M0 skeleton: FastAPI backend (read-only endpoints) that queries Neon; secrets in host env vars. The dashboard never triggers model calls.
- Dashboard: overall metrics (coverage, status and quarantine-reason counts, topic/intent/severity distributions), the area roll-up answering the
  four-way question, the issue ranking table, and the AI recommendations with linked supporting numbers and review evidence, plus a scope disclaimer.
- Redeploy; smoke test that dashboard numbers equal the saved calculations.
- **Gate:** live URL works signed out; access instructions written.

### M12 — README, evidence & publish (no spend)
- README: rubric → evidence map; results summary (source/completed/quarantined counts by reason, cache reuse, golden agreement,
  verifier disagreements, cost/time; measured vs estimated kept separate); architecture diagram with the DB/backend/dashboard;
  one real review traced source ID → enrichment → verification → membership → ranking → memo claim; one failed/ambiguous case;
  golden results; injection/planted outcomes; resume evidence; cost links; dataset link + SHA-256; setup/run/replay commands;
  live URL; limitations (scope, review bias, missing versions, unclear/unsupported-language handling).
- Secret hygiene: `git check-ignore .env`; `git ls-files -- .env '.env.*'`; scan history; rotate anything exposed.
- Clean-environment test: fresh clone → offline replay + `rank` + checker with no API keys.
- Publish the public repo; open every evidence link signed out; submit via the portal.

### 5.1 Sequence and dependencies (no fixed dates; everything finishes before 2026-10-20 midnight)

```
M0 ─▶ M1 ─▶ M2 ─▶ M3a ─▶ M3b ─▶ M4 ─▶ M5 ─▶ M6 ─▶ M6b ─▶ M7 ─▶ M8 ─▶ M9 ─▶ M10 ─▶ M11 ─▶ M12
            │                                 ▲
            └─ 👤 golden 50 (done) · dev_100 adjudication happens inside M6 ┘
M0 hello-world deploy ─────────────────────────────────────────────▶ filled in by M11
```

- **Critical path:** M5 (first real Celeris calls) → M6 (dev_100 model runs → Rory adjudicates ~20–30 disagreements, ≈ 30–60 min)
  → `label_config` freeze → M8. The golden 50 are done. **v4.2:** M6b (re-freeze + 10k re-run, 👤 30 held-out labels) sits
  between M6 and M7; about one session.
- **Wall-clock jobs:** M8 (≈ 2,000 decision calls, at rate limits measured in M6) and the A8 Batch API job (up to 24h turnaround) are the only
  long waits. Submit the verification batch as soon as M8 finishes, and work on M10–M11 while it runs.
- **Rough effort:** ~13 build sessions + ~4–5 hours of labeling + run time. That fits 7 days at about two sessions a day, leaving roughly
  a day of buffer. No external blockers remain once the Celeris card and limit are set in M0.
- **If time gets short, cut in this order:** the frequency add-on → the A vs B comparison (use A) → the escalation-model comparison (use Haiku)
  → the optional trend analysis. Never cut: grading export, golden evaluation, resume evidence, the deployed dashboard.

---

## 6. Session protocol (keeps context and token burn low)

1. **One module per session.** Start each new session in this folder with: *"Read CLAUDE.md, STATUS.md and PLAN.md §5 M<n>, then do M<n>."*
2. **STATUS.md is the handoff:** last completed module, artifacts produced, measured numbers (counts, cost, time),
   open issues and the exact next step. It's updated and committed at the end of every session.
3. **Never read large files into context:** not the raw CSV, not the full brief (use `docs/BRIEF_TEXT.md` sections),
   and not big JSONL outputs. Use scripts that print summaries or `head`.
4. **Read specs once:** §9 / `docs/SPEC_NOTES.md` replaces rereading the contract and calculator documents.
5. **Tests before spend:** every module passes its offline gate before any 💰 step.
6. **Paid steps are announced** with an estimated cost and need Rory's explicit "yes" in that session.
7. **Commit at the end of every session** so any session can restart from git.

---

## 7. Open questions

| # | Question | Default until answered |
|---|---|---|
| Q1 | Does the instructor's $10 include pilots, or only the 100k run? | Includes everything (conservative) |
| Q2 | Hosting for the backend/dashboard? | **Decide in M0** and deploy a hello-world; default Render (one FastAPI service serving API + static dashboard) |
| Q3 | Celeris rate limits (not published) and exact System One response JSON | Smoke test in M0; measure in M5/M6 |
| Q4 | ~~Out-of-scope rows~~ | **Resolved:** quarantine with `out_of_scope_budget_100k` (§1.1) |
| Q5 | Adopt the exact-text cache fill for out-of-scope rows? | Yes if M1 shows a meaningful gain; ranking reported both ways |
| Q6 | Get a Jev key to back up the "better than Jev" claim with a measured comparison? | Optional; skip unless cheap |
| Q7 | ~~Is 100k acceptable?~~ | **Resolved:** the instructor added the 100k allowance after the original brief, and it doesn't affect the grade. The README cites the brief's "or at least 100,000" and still states plainly that the out-of-scope rows were accounted for, not classified |
| Q8 | Celeris request shape A (shared definitions, ≤ 50 reviews) vs B (1 per request)? | Default A; confirm with the M5 comparison |
| Q9 | ~~Does Celeris work without a card?~~ | **Resolved:** Rory adds a card and a Console spend limit in M0 |
| Q10 | Which escalation model? | **M5:** Haiku over magnus. **M6b:** Haiku for severity (every review); a stronger model for topic only if the dev test says so |
| Q11 | ~~Is 60% the right gate, and on which signal?~~ | **Resolved (M6):** raw top probability, severity only (Haiku hurt topic on dev_100). **M6b:** severity escalated for every review (no gate) |
| Q12 | ~~When is Class 7?~~ | **Resolved:** already held; the only deadline is 2026-10-20 midnight |
| Q13 | Is the topic → area mapping right (downloads → playback; catalog and other outside the four areas)? | As in §1; Rory confirms in M2 |
| Q14 | Run the full corpus for extra credit? | **M6: NO-GO** ($16.86 conservative > $9.00); add-on top 25,000 recommended; Rory confirms before M8 |

## 8. Risks

| Risk | Mitigation |
|---|---|
| Celeris is new (released 2026-10-08) with only provider-reported benchmarks | The `dev_100` and golden evaluations (M6) measure it against human labels; keep a Haiku-only fallback config |
| Celeris doesn't produce `entities`/`evidence_quote` | Code extraction (A6) fills both |
| Celeris spend runs past the free credit onto the card | The ledger stops at the $4.50 bucket; the Console limit is a backstop; `402` causes a clean stop; resume |
| Out-of-scope rows hurt private-sample label diagnostics | State this in the README; the cache fill reduces it slightly |
| Rate limits make the 100k slow | Measure in M6; queue + async workers; start M8 as soon as the freeze allows |
| Golden or `dev_100` leakage | Firewall tests (M2); golden labels never in prompts/examples/thresholds; `dev_100` only in the sweep |
| Hashing mismatch with the checker | Import the provided `row_sha`; never reimplement |
| Celeris input tokens include the question criteria, so per-review overhead could exhaust the credit | Short criteria; measure A vs B in M5; the ledger stops at $4.50 |
| Celeris rate limits are unpublished; bursts are throttled even below the sustained rate | Start with 1 worker; shared limiter; honor `Retry-After` |
| Client timeouts may be billed (undocumented) | Flag unknown-outcome calls in the ledger; reconcile with Console usage |
| A missing `group`/`memo` model call fails the checker's role check | M4 makes both mandatory model calls |
| Too many reviews fall under the 60% gate, so escalation blows the budget | The escalation fraction + $ caps; overflow → `needs_review`; tune the gate in M6 |
| Neon free-tier limits | Neon only serves the dashboard (no queue traffic); text only for completed in-scope rows; check the plan limits in M0 |
| Two workers process the same review | `BEGIN IMMEDIATE` leases + fenced commits + unique keys; concurrency tests (tasks and two processes) in M3a |
| Shape A answers bleed between reviews | M5 compares A vs B on the same 100; fall back to B if agreement drops materially |
| Code-picked quotes don't support the label | Inspect quote support on the golden set (M6); the full text is always a valid fallback |
| Deployment problems found late | Hello-world deployed in M0; M11 only fills it in |
| `dev_100` adjudication delays M6 | Only disagreements need Rory (≈ 30–60 min); the tool shows them as a short queue |
| A `label_config` change after dev runs invalidates earlier records | Explicit freeze in M6; cheap re-run of the 100 and 500 under the frozen config |
| Context bloat across sessions | §6 protocol; STATUS.md handoffs |
| A secret is committed | `.gitignore` in the first commit; pre-publish checks in M12 |

---

## 9. Spec digest (from GRADING_CONTRACT.md v1, COST_CALCULATOR.md, dataset README.md)

### 9.1 Dataset facts
- `spotify_reviews_18months.csv`: 660,622 rows · 97,400,616 bytes · SHA-256 `1fc85de68a304dd8978b537cfa58793d5f41cbaf417fa32cb53899f83a2fcef6`.
- Window: 2022-05-17 (inclusive) to 2023-11-17 (exclusive); the last review is dated 2023-11-15. Text may contain line breaks (physical lines ≠ rows).
- Fields: `review_id, review_text, review_rating, review_likes, app_version, review_timestamp` (timezone unspecified).
- 13 empty texts, 159,701 missing app versions, 0 repeated IDs, 484,189 distinct nonempty texts.
- `cost_100` = the first 100 rows of `checkpoint_500` ⊂ `analysis_10000`; `golden_50` is disjoint from all three. The samples are unique nonempty reviews with valid ratings; order is SHA-256 with the seed in `manifest.json`.
- Source: Kaggle, BwandoWando, v2 (2023-11-17), CC0.

### 9.2 Common labels (exact)
- **Topics:** `access` (login, signup, password, account access) · `usability` (navigation, controls, layout, queue/playlist
  management, ad interruptions) · `playback` (failure, crashes, lag, connection failures, audio quality, resource use) ·
  `downloads` (downloading, saved music, offline, disappearing downloads) · `catalog` (missing songs/artists, search/discovery,
  recommendations, lyrics availability) · `billing` (price, charges, subscriptions, paywalls, premium entitlement; explicitly
  premium-only controls) · `support` (contacting support, support response) · `other` (general praise/criticism, unrelated, no supported specific topic).
- Pick the problem with the highest supported severity; on a tie, the first specific problem mentioned. Positive review → first specific praised
  feature; general praise → `other`. A paid-plan mention alone ≠ billing; a subscription not activating = billing; crashing for a paying user = playback.
- **Intent precedence:** `cancellation` (explicitly leaving/uninstalling/cancelling or threatening to) → `complaint` (negative, incl. mixed)
  → `request` (desired change, no reported failure) → `praise` → `unclear`. Bare boycott slogans and meaningless text = `unclear` unless there is a
  product complaint or an explicit personal departure. General "bad app" = complaint with no inferred defect.
- **Severity:** 1 no problem (praise, neutral/unclear, pure request) · 2 dislike/generic criticism/minor/cosmetic, no functional loss ·
  3 degraded/restricted function, some use or workaround remains · 4 clearly blocked core task (can't log in or play) · 5 explicit serious
  financial/privacy/data harm (price, a crash or angry language alone are insufficient). Cancellation doesn't raise severity. Missing context → `needs_review`.
- Classify the text itself; metadata never substitutes for interpretation.

### 9.3 Export: `grading/`
| File | Contents |
|---|---|
| `run.json` | `{"version":"a5-audit-v1","analysis_count":660622,"analysis_sha256":"1fc85de6…","classification_input_fields":["review_text"],"allow_multi_issue":false}` |
| `ingestion.json` | Output of `check_submission.py profile` (the provided helper) |
| `records.jsonl(.gz)` | Exactly one final record per source ID |
| `membership.csv` | `issue_id,review_id` |
| `ranking.csv` | `rank,issue_id,complaint_count,severity_sum,mean_severity,priority_score` |
| `claims.csv` | `claim_id,issue_id,metric,value` |
| `calls.jsonl(.gz)` | Model-call log |
| `checkpoint_before.json` / `checkpoint_after.json` | `completed_ids` before interruption / after resume |

- Completed record: `review_id, source_sha256, status:"completed", topic, intent, sentiment (−1..1), severity (1..5), entities [], evidence_quote (exact substring), needs_review, label_config` (+ `cache_source_id` when reused).
- Quarantined record: `review_id, source_sha256, status:"quarantined", reason` (nonempty). Retry details go in the run logs.
- `source_sha256` = SHA-256 of UTF-8 compact JSON of the 6 original field strings in field order; use the provided `row_sha`; no whitespace/accent normalization.
- Cache reuse: `cache_source_id` → a **direct** completed original with identical text, identical classification fields, identical `label_config`, and model-call evidence. Every ID stays a separate row and counts separately in aggregates. No alias chains.

### 9.4 `calls.jsonl` entry
`{"request_id","role","review_ids","model","phase","outcome","label_config","input_tokens","output_tokens"}`
- `role` ∈ `enrich, verify, group, memo`; enrichment `phase` ∈ `initial, resume`; `outcome` ∈ `succeeded, failed`.
- `review_ids` = the reviews actually sent; group/memo may be empty (identify the consumed artifacts in the run log). Cached reuse is never logged as a call.
- Enrichment calls name the same `label_config` as their completed records. Missing usage is marked unavailable, never invented.

### 9.5 Baseline ranking
- Members: completed `complaint` + `cancellation` records only; each in ≥ 1 issue (exactly 1 with `allow_multi_issue:false`); no duplicate pairs.
- `complaint_count` = members; `severity_sum` = Σ severity; `mean_severity` = sum / count; `priority_score` = `severity_sum`.
- Sort by score desc, then issue_id asc; ranks start at 1. The mean is exported to 6 dp with decimal half-up; all other numbers are integer strings.
- `claims.csv` metrics: `complaint_count`, `severity_sum`, `mean_severity`, `priority_score`; the memo cites claim IDs. Other quantities are explained separately.

### 9.6 Calculator requirements (COST_CALCULATOR.md)
- **Measured (100 pilot):** input checksum, 100 IDs, completed/failed, unique texts, cache hits; per stage: provider, exact model ID,
  effort, prompt/schema version, batch size, worker limit; requests/attempts incl. failures/retries/fallbacks/verification; usage,
  units, currency, dated sources, cost by stage; wall-clock + stage times, throughput, cost per 1,000 input rows and per completed record; cold vs warm.
- **Estimated (full run):** 660,622 rows / 660,609 outputs / 13 quarantines; 484,189 distinct with reuse + a no-reuse comparison; stage-specific
  verification/fallback rates, retry assumptions, fixed vs variable work; total cost, elapsed time, throughput; base + conservative;
  editable budget, output-token cap, max concurrency, max fallback fraction; over-budget warning.
- `cost/` contents: runnable calculator + README (offline replay vs explicit pilot command), `pilot_records.jsonl`, `pilot_calls.jsonl`,
  `rates.csv`, `usage.csv`, `report.md`/`.html`.
- Instructor checks: the 100 IDs match `cost_100.csv`; cost recomputes from usage × rates; offline replay runs; doubling rates doubles the API
  subtotal only; changing projected volume doesn't change the pilot results.

### 9.7 Checker commands (zero API)
```sh
python3 check_submission.py profile   --full spotify_reviews_18months.csv --out grading/ingestion.json
python3 check_submission.py reference --full spotify_reviews_18months.csv --analysis spotify_reviews_18months.csv --out local-reference.json
python3 check_submission.py check     --reference local-reference.json --submission grading --out self-check.json
```
Keep the reports outside `grading/`. `review_required` flags evidence issues; it doesn't set the grade. Passing doesn't prove the labels are correct.

### 9.8 What `check_submission.py` actually enforces (read 2026-10-12)
- **Hashing:** `row_sha(row) = sha256(canonical([6 fields in order]))`, where `canonical` = `json.dumps(ensure_ascii=False, separators=(",",":"), sort_keys=True)`.
  CSV read with `encoding="utf-8-sig"`, `newline=""`, `csv.DictReader(strict=True)`.
- **`ingestion.json`** must equal the reference's profile **exactly**: file SHA, parsed-rows digest, counts, `reviews_by_month`,
  `reviews_by_rating`, first/last review. Use the `profile` command; don't hand-edit it.
- **`run.json`** keys checked: `version`, `analysis_count` (= 660622), `analysis_sha256` (full file), `classification_input_fields` = `["review_text"]`;
  `allow_multi_issue` must be `true` for any review to be in more than one issue.
- **Records:** the `review_id` must be known; duplicates invalidate **both** rows; `source_sha256` must match. Quarantined rows need a nonempty `reason`,
  and empty texts need exactly `empty_review_text`. Completed rows need: `topic` ∈ 8 topics; `intent` ∈ 5 intents; `label_config` a nonempty string;
  `severity` a strict `int` 1–5; `sentiment` an int/float in [−1, 1]; `needs_review` a strict `bool`; `entities` a list of nonempty strings
  (an empty list is OK); `evidence_quote` a nonempty exact substring of the review text.
- **Cache reuse is valid only if:** the origin is itself a valid, non-cached completed record (not the row itself), has identical text, **and all of
  `topic, intent, sentiment, severity, entities, evidence_quote, needs_review, label_config` are identical**. Invalid reuse removes the row from valid.
- **Logged evidence:** every valid non-cached completed record must appear in a **succeeded `enrich`** call with the same `label_config`
  (otherwise `unlogged_completed_records`). `enrich` calls must have 1–50 unique known IDs; `phase` ∈ {`initial`, `resume`}.
- **Calls:** `request_id` unique and nonempty; `model` nonempty; `outcome` ∈ {`succeeded`, `failed`}; `review_ids` a list of known, unique IDs
  (it may be empty for verify/group/memo); `input_tokens` and `output_tokens` must be ints ≥ 0. Succeeded calls must cover all four roles.
- **Ranking/claims:** recomputed from membership (members must be valid complaint/cancellation records; every such record must be grouped);
  CSV values compared as strings. `mean_severity` = `Decimal` quantized to 6 dp half-up.
- **Coverage point candidate:** `0.2·ingestion_ok + 0.2·(valid + quarantined)/660,622 + 0.6·valid_nonempty/660,609`, rounded to 4 dp.
- **Gold:** the instructor's private sample uses `accepted_topic/intent/severity` lists (ambiguity allowed); missing or quarantined predictions count as wrong;
  severity MAE is only over valid predictions.
- **Official sampling** (`prepare_dataset.py`): rank = `int(sha256("berkeley-fall-2026-assignment-5-v1:" + review_id))`; lowest first among unique,
  nonempty, valid-rating rows; golden = ranks 1–50; analysis = 51–10,050 (checkpoint = its first 500; cost = its first 100).
  The golden CSV columns: the 6 fields + `topic, intent, sentiment, severity, entities, evidence_quote, needs_review`.

### 9.9 Celeris facts (docs.celeris.ai, checked 2026-10-12)
- **Decision model:** `celeris-1-decision`; base `https://inference.celeris.ai/celeris-1-decision/v1`; `POST /v1/systemone` (Jev/System One format,
  TypeSafe SDK compatible via `TYPESAFE_BASE_URL`, `TYPESAFE_API_KEY`, `TYPESAFE_DEFAULT_MODEL`) or `POST /v1/decisions` (OpenAI Decisions format).
  The same model, price and limits serve both. JSON only, no streaming. Auth: `Authorization: Bearer $CELERIS_API_KEY` (keys start `ck_`).
- **Question types:** `noul` (P(yes), optional true/false criteria), `choice` (≥ 2 options with descriptions → chosen option + per-option probabilities +
  `confidence`), `score` (≥ 2 levels, lowest first, numbered from 0 → probability-weighted level + per-level probabilities + `confidence`).
  Probabilities are rounded to 2 dp. `confidence` = the top probability rescaled (0 = even split, 1 = certain).
- **Limits:** ≤ 512 questions per request (64 with `x_celeris.explain: true`, which is slower); ≤ 512 choice options; ≤ 64 score levels; ≤ 8 MiB body
  (413 if exceeded); ≤ 100,000 JSON values. Context 131,072 tokens minus a reserved answer part. Unknown fields (including `stream`) → validation error.
- **Price:** input **$0.04 per million tokens** (cached the same), output free. Billed input = everything the model reads (including question text).
  The usage fields are `input_tokens` and `output_tokens`. Use the `Server-Timing` header for server time.
- **Other models:** `celeris-1` ($0.20 in / $0.02 cached / $0.70 out per million tokens; chat completions; `max_tokens` default 2,048; may wrap JSON in a
  Markdown fence; prefer `response_format`); `celeris-1-magnus` (same price; reasons unless `enable_thinking: false`).
- **Credits:** prepaid; credit expires 30 days after grant; shared across models. At a zero balance → `402 insufficient_quota`.
- **Errors:** 400 invalid (don't retry) · 401 bad key · 402 out of credit (stop) · 404 wrong model path · 408 timeout (retry) · 413 too large ·
  422 `/systemone` validation with a `detail` array (don't retry) · 429 `rate_limit_exceeded`/`service_busy` (retry after `Retry-After`) ·
  502/503 (retry). **429s and decision 502s are not charged**; billing for 408/client timeouts is undocumented. No idempotency keys.
  An optional `X-Client-Trace-Id` header is echoed back, so use it as our local request ID.
- **Rate limits:** not published; per workspace and model, shared by all keys; bursts can be throttled below the sustained rate. Use one shared
  client-side limit per model; the Console doesn't show 429s.
- **Measured response (M0 smoke test, 2026-10-12; saved in `evals/smoke/celeris_smoke_response.json`):** HTTP 200, body
  `{"model", "answers": {<name>: ...}, "usage": {"input_tokens", "output_tokens"}}`, with **no `id` field in the body and no request-ID header**.
  The only headers are `content-type`, `server-timing` (`processing;dur=34.860`, in ms) and the echoed `x-client-trace-id`, so **our
  `X-Client-Trace-Id` is the `request_id`**. Answers: `choice` → `{"type","choice","probabilities":{opt:p},"confidence"}`;
  `score` → `{"type","score" (float, probability-weighted),"probabilities":{"0":p,...},"confidence","legend":{"0":label,...}}`;
  `noul` → `{"type","noul": P(true)}`. One review + 4 short questions (bare labels) = 306 input tokens, 0 output = $0.00001224.

### 9.10 Anthropic facts (platform.claude.com pricing + docs, checked 2026-10-12)
- **Model:** `claude-haiku-5-5` (1M context, 128K max output). Prices per MTok for prompts ≤ 100K tokens: input $0.10, 5-min cache write $0.125,
  1-h cache write $0.20, cache read $0.01, output $0.50. Prompts > 100K tokens: $0.50 / $2.50 (+ cache equivalents). Tokenizer counts ~30% more tokens than Haiku 4.5.
- **Thinking:** adaptive only, on by default. `thinking: {type: "disabled"}` is accepted only at effort `high` or below; `budget_tokens` → 400.
  `output_config.effort` ∈ low/medium/high/xhigh/max, **default `medium`** (set it explicitly). Non-default sampling params and assistant prefill → 400.
  Use structured outputs (`output_config.format`) for JSON. Refusals return `stop_reason: "refusal"` with no server-side fallback on Haiku.
- **Message Batches API:** 50% off input and output (Haiku 5.5 batch: $0.05 / $0.25 per MTok); ≤ 100,000 requests or 256 MB per batch;
  most complete within 1 h, max 24 h; results arrive in any order (key by `custom_id`); `expired` results must be resubmitted.
- Min cacheable prefix on Haiku 5.5: 512 tokens (check the caching docs before relying on it).
