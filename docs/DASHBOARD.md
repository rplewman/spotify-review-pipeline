# Dashboard, backend and serving database (M11)

**Live:** https://spotify-review-pipeline.onrender.com — public, no sign-in. Read-only API docs: `/api/docs`.
The free Render instance sleeps when idle; the first request after a pause can take ~1 minute.

## What it shows
- Scope disclaimer: which rows carry labels (random 100k + frequency add-on + exact-text reuse) and which are quarantined
  as `out_of_scope_budget_100k` (accounted for, not classified).
- Overall metrics: source rows, completed / quarantined (by reason), cache reuse, `needs_review`, coverage point (checker formula),
  topic / intent / severity distributions.
- Area roll-up answering the four-way question (access, usability, playback, billing/support; "outside" = catalog + other).
- The 33-issue ranking (click a row for its most severe member reviews).
- The three AI recommendations from the memo agent (A11, written once in M9). Each supporting claim is re-checked against
  the published aggregates (`dashboard.v_claims.matches`), and each evidence quote opens the full review with its labels.
- Verifier agreement, golden-set accuracy, and the main run's model calls and list-price cost.

The dashboard and backend never call a model.

## How it is built
```
state/pipeline.db (SQLite) + outputs/ + evals/ ──publish──▶ Neon Postgres ──▶ FastAPI (backend/app.py) ──▶ dashboard/index.html
                                                   serving.*   (tables, owner only)
                                                   dashboard.* (views, role dashboard_ro: SELECT only, read-only transactions)
```
- `python -m pipeline.cli publish [--target main|test]` (`pipeline/db/publish.py`): drops and rebuilds the `serving` and
  `dashboard` schemas in **one transaction** (idempotent; readers see the old data until the commit), COPYs 660,622 records
  (review text for completed rows only), issues, membership, ranking, aggregates, area roll-up, claims, recommendations,
  run summary and cost summary, then creates/updates the read-only role `dashboard_ro` from `DASHBOARD_RO_PASSWORD`.
  No API calls; ~40 s.
- Backend: `backend/app.py`, read-only endpoints `/api/health`, `/api/overview`, `/api/areas`, `/api/ranking`, `/api/claims`,
  `/api/recommendations`, `/api/reviews/{id}`, `/api/issues/{id}/reviews`, `/api/cost`. It connects with
  `DASHBOARD_DATABASE_URL` (the `dashboard_ro` role), set as a Render environment variable; responses are cached 5 minutes.
- Local run: `.venv/Scripts/python -m uvicorn backend.app:app --port 8765` (add `DASHBOARD_TARGET=test` to read the Neon `test` branch).

## Checks
- `tests/test_m11_publish.py`: publish to the Neon `test` branch twice (idempotent), the read-only role can read the views but
  not `serving.*` and cannot create tables, and every endpoint returns the published numbers (incl. a planted claim mismatch
  shown as ✗).
- Smoke test, dashboard API vs the saved files in `outputs/` (no keys needed):
  `python scripts/smoke_dashboard.py --base-url https://spotify-review-pipeline.onrender.com --out evals/m11/smoke_live.json`
