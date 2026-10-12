# STATUS — handoff log

## Current: M0 (Setup & spec intake) — agent work done; 👤 steps open

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
- Haiku 5.5 `claude-haiku-5-5`: $0.10 in / $0.50 out per MTok (≤ 100K prompt), cache read $0.01; batch 50% off ($0.05 / $0.25).
  Thinking adaptive by default; disable only at effort ≤ high; default effort `medium`.
- Batch API: ≤ 100k requests / 256 MB per batch; usually < 1 h, max 24 h.
- Celeris prices unchanged from §9.9; card top-ups $5–$1,000; credit expires 30 days after grant.

### Open (blocks M0 gate) — 👤 Rory
1. Put `CELERIS_API_KEY` and `ANTHROPIC_API_KEY` (and OPENAI/TYPESAFE if used) into `.env` (Neon URLs are already there).
2. Celeris Console: add card, set monthly spend limit **$8**, confirm $5 credit balance + expiry date, workspace active, whether usage export exists. Record the limit here.
3. Approve the Celeris smoke test (est. < $0.0001): `.venv/Scripts/python scripts/celeris_smoke.py --paid --max-spend 0.001`
   → saves `evals/smoke/celeris_smoke_response.json`; then update §9.9 with the exact response JSON (resolves Q3 partly).
4. Render: create a GitHub repo, push, then "New → Blueprint" from `render.yaml` and set `DATABASE_URL` in Render; confirm `/api/health` returns the hello row.

### Next step
Finish the four open items above, then M1 (Ingest).
