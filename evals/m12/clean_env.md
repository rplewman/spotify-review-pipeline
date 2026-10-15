# M12 — clean-environment test (2026-10-15, no spend)

Fresh `git clone` of commit `e44a6cf` + the ranker LF fix, new Python 3.12.10 venv, `pip install -r requirements.txt`,
**no `.env` and no API keys in the environment**.

| Step | Command | Result |
|---|---|---|
| Install | `python -m venv .venv` · `pip install -r requirements.txt` | OK. ⚠ On Windows the clone path must be short (or Windows long paths enabled): one `anthropic` SDK file exceeds 260 characters, and a clone under a deep temp folder failed with `OSError … Long Path support`. |
| Calculator replay (pilot) | `python cost/calculator.py --out <tmp>` | `report.md` **byte-identical** to `cost/report_pilot.md`; no API calls |
| Calculator replay (100k, frozen config) | `python cost/calculator.py --usage cost/m6b/usage.csv --measured cost/m6b/measured.json --calls cost/m6b/calls.jsonl --max-fallback-fraction 1.0 --verify-rate 0.2 --out <tmp>` | `report.md` **byte-identical** to `cost/report.md` |
| Ranking from saved outputs | `python -m pipeline.cli rank` (twice) | `"model_calls": 0`; `ranking.csv`, `aggregates.csv`, `area_aggregates.csv`, `overall_metrics.json`, `comparison_random_100k_{ranking,areas}.csv` **byte-identical** to the committed files. Reads `grading/records.jsonl.gz` because `outputs/enriched.jsonl.gz` is not in git. |
| Tests | `python -m pytest -q -m "not data"` | 127 passed, 8 skipped (Neon tests: no `TEST_DATABASE_URL`), 1 failed → the golden firewall test caught a path mention in `evals/m12/trace_ambiguous.md`; reworded, now passes |
| Checker | `check_submission.py reference …` + `check …` (dataset from the course ZIP) | `review_required`, only `unfinished_classification` 381,090; valid completed 279,519; accounted 1.0; coverage point candidate 0.6539; 4,552 calls — identical to the M10 run |
| Live dashboard vs saved files | `python scripts/smoke_dashboard.py --base-url https://spotify-review-pipeline.onrender.com` | **12/12 equal** (`smoke_live_clean_clone.json`) |

Bugs found by this test and fixed in M12:
1. `rank` needed the gitignored `outputs/enriched.jsonl.gz` → now falls back to `grading/records.jsonl.gz` (identical on every
   field the ranker reads; test `test_rank_in_fresh_clone_reads_grading_export`).
2. `overall_metrics.json` was written with CRLF on Windows → now always LF (byte-identical across OSes).

`requirements.txt` pins direct dependencies only; a fresh resolve picked newer `pluggy`, `starlette` and `truststore` patch
versions. `requirements-lock.txt` is the full `pip freeze` of the environment that produced the results.
