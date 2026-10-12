# SPEC_NOTES — copied from PLAN.md §9 (2026-10-12). Read this instead of the contract/calculator/Celeris docs.

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

### 9.10 Anthropic facts (platform.claude.com pricing + docs, checked 2026-10-12)
- **Model:** `claude-haiku-5-5` (1M context, 128K max output). Prices per MTok for prompts ≤ 100K tokens: input $0.10, 5-min cache write $0.125,
  1-h cache write $0.20, cache read $0.01, output $0.50. Prompts > 100K tokens: $0.50 / $2.50 (+ cache equivalents). Tokenizer counts ~30% more tokens than Haiku 4.5.
- **Thinking:** adaptive only, on by default. `thinking: {type: "disabled"}` is accepted only at effort `high` or below; `budget_tokens` → 400.
  `output_config.effort` ∈ low/medium/high/xhigh/max, **default `medium`** (set it explicitly). Non-default sampling params and assistant prefill → 400.
  Use structured outputs (`output_config.format`) for JSON. Refusals return `stop_reason: "refusal"` with no server-side fallback on Haiku.
- **Message Batches API:** 50% off input and output (Haiku 5.5 batch: $0.05 / $0.25 per MTok); ≤ 100,000 requests or 256 MB per batch;
  most complete within 1 h, max 24 h; results arrive in any order (key by `custom_id`); `expired` results must be resubmitted.
- Min cacheable prefix on Haiku 5.5: 512 tokens (check the caching docs before relying on it).
