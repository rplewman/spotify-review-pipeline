# CLAUDE.md — session bootstrap

Spotify review multi-agent pipeline (final assignment). Deadline **2026-10-20 midnight**. Budget **≤ $10 total at list price**.

Start every session: read this file, `STATUS.md`, and only the module you're doing in `PLAN.md §5` ("Read CLAUDE.md, STATUS.md and PLAN.md §5 M<n>, then do M<n>").

## Token rules (PLAN §6)
- One module per session. Update `STATUS.md` and commit at the end of every session.
- Never read large files into context: not `data/raw/*.csv`, not the full brief (use sections of `docs/BRIEF_TEXT.md`), not big JSONL outputs. Use scripts that print summaries or `head`.
- Specs are digested in `docs/SPEC_NOTES.md` (= PLAN §9). Don't reread the contract, calculator or Celeris docs.
- `pipeline/check_submission.py` is the instructor's checker, copied **unchanged**. Import `row_sha`, `canonical`, `mean_string` from it; never edit it.

## Money rules
- Every paid command needs `--paid` **and** `--max-spend`, and runs only after Rory says "yes" in that session, with the estimated cost stated first.
- Tests before spend: the module's offline gate passes before any paid step.
- Report list price, credits applied and cash spent separately; never treat credits as free.

## Environment
- Python 3.12 venv in `.venv/` (`.venv/Scripts/python`), deps pinned in `requirements.txt`; run tests with `.venv/Scripts/python -m pytest -q`.
- Secrets in `.env` (gitignored; template `.env.example`). Load with `load_dotenv(".env")`.
- SQLite `state/pipeline.db` = operational store; Neon Postgres = serving DB (`DATABASE_URL`; `TEST_DATABASE_URL` = `test` branch).
