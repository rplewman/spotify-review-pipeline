"""M12: outputs/run_summary.json (run manifest + per-run timing, statuses, calls, usage, spend, limits, resume evidence)
and outputs/quarantine.jsonl.gz (every quarantined record with its reason and attempts). Read-only on
state/pipeline.db, zero model calls.

    .venv/Scripts/python scripts/run_summary.py
"""
import gzip
import hashlib
import json
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.db.ledger import BUCKET_CAPS, PROJECT_CAP  # noqa: E402
DB = ROOT / "state/pipeline.db"
GRADING = ["run.json", "ingestion.json", "records.jsonl.gz", "calls.jsonl.gz", "membership.csv", "ranking.csv",
           "claims.csv", "checkpoint_before.json", "checkpoint_after.json"]
OUTPUTS = ["memo.md", "memo_input.json", "claims.csv", "recommendations.json", "ranking.csv", "aggregates.csv",
           "area_aggregates.csv", "issues.json", "membership.csv", "overall_metrics.json", "quarantine.jsonl.gz"]


def sha(path):
    """sha256 of the file as git stores it (text files with LF), so the value matches a fresh clone."""
    b = path.read_bytes()
    if path.suffix not in {".gz"}:
        b = b.replace(b"\r\n", b"\n")
    return hashlib.sha256(b).hexdigest()


def write_quarantine(conn):
    rows = conn.execute("""SELECT r.review_id, r.reason, s.scope, s.scope_rank, s.text_sha256
                           FROM records r JOIN source_reviews s USING(review_id)
                           WHERE r.status='quarantined' ORDER BY r.review_id""").fetchall()
    attempts = {t: (a, e) for t, a, e in conn.execute(
        "SELECT text_sha256, MAX(attempts), MAX(last_error) FROM work_items WHERE stage='enrich' GROUP BY text_sha256")}
    path = ROOT / "outputs/quarantine.jsonl.gz"
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as f:
        for rid, reason, scope, rank, th in rows:
            a, err = attempts.get(th, (0, None))
            f.write((json.dumps({"review_id": rid, "reason": reason, "scope": scope, "scope_rank": rank,
                                 "model_attempts": a or 0, "last_error": err, "sent_to_model": bool(a)},
                                sort_keys=True) + "\n").encode("utf-8"))
    return Counter(r[1] for r in rows)


def run_rows(conn, final_cfg):
    calls = defaultdict(lambda: {"outcomes": Counter(), "input_tokens": 0, "output_tokens": 0, "spend_usd_list": 0.0,
                                 "spend_estimated_usd_list": 0.0, "by_agent": Counter(), "by_model": Counter()})
    for run, agent, model, outcome, it, ot, cost, est, usage in conn.execute(
            "SELECT run_id, agent, model, outcome, input_tokens, output_tokens, cost_usd, est_usd, usage_available "
            "FROM calls"):
        c = calls[run]
        c["outcomes"][outcome] += 1
        c["by_agent"][agent] += 1
        c["by_model"][model] += 1
        c["input_tokens"] += it or 0
        c["output_tokens"] += ot or 0
        if usage:
            c["spend_usd_list"] += cost or 0
        else:  # billing unknown: settled at the ledger estimate (never 0)
            c["spend_estimated_usd_list"] += (cost if cost else est) or 0
    out = []
    for run_id, cfg_json, phase, start, end, stop in conn.execute(
            "SELECT run_id, config_json, phase, started_at, ended_at, stop_reason FROM runs ORDER BY started_at"):
        cfg = json.loads(cfg_json or "{}")
        c = calls.get(run_id)
        lc = cfg.get("label_config", "")
        out.append({
            "run_id": run_id, "stage": cfg.get("stage"), "phase": phase, "started_at": start, "ended_at": end,
            "stop_reason": stop, "label_config": lc,
            "final_config": lc.split("#")[0] == final_cfg,
            "settings": {k: cfg.get(k) for k in ("workers", "batch_size", "max_spend", "bucket", "max_rank",
                                                  "max_attempts", "lease_seconds", "model", "transport") if k in cfg},
            "calls": dict(c["outcomes"]) if c else {}, "calls_by_agent": dict(c["by_agent"]) if c else {},
            "calls_by_model": dict(c["by_model"]) if c else {},
            "input_tokens": c["input_tokens"] if c else 0, "output_tokens": c["output_tokens"] if c else 0,
            "spend_usd_list_measured": round(c["spend_usd_list"], 6) if c else 0.0,
            "spend_usd_list_estimated": round(c["spend_estimated_usd_list"], 6) if c else 0.0,
        })
    return out


def main():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    run = json.loads((ROOT / "grading/run.json").read_text(encoding="utf-8"))
    final_cfg = run["label_config"]
    quarantine = write_quarantine(conn)
    runs = run_rows(conn, final_cfg)
    rc = json.loads((ROOT / "evals/recovery/resume_check.json").read_text(encoding="utf-8"))
    summary = {
        "generated_by": "scripts/run_summary.py (read-only on state/pipeline.db, zero model calls)",
        "manifest": {
            "source": {"file": "spotify_reviews_18months.csv", "rows": run["analysis_count"],
                       "sha256": run["analysis_sha256"]},
            "code_version": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                           cwd=ROOT).stdout.strip(),
            "label_config": final_cfg,
            "verify_config": "claude-haiku-5-5+verifier-prompt-v1+effort-low",
            "models": {"A3": "celeris-1-decision", "A5": "claude-haiku-5-5 (effort low)",
                       "A8": "claude-haiku-5-5 (Batch API, effort low)", "A9": "claude-haiku-5-5",
                       "A11": "claude-haiku-5-5 (memo-prompt-v3)"},
            "prompts_sha256": {p.name: sha(p) for p in sorted((ROOT / "prompts").glob("*.md"))},
            "labels_sha256": {p.name: sha(p) for p in sorted((ROOT / "labels").iterdir()) if p.is_file()},
            "grading_sha256": {n: sha(ROOT / "grading" / n) for n in GRADING},
            "outputs_sha256": {n: sha(ROOT / "outputs" / n) for n in OUTPUTS},
        },
        "final_run": {
            "note": "Final classification run = random 100k (initial, interrupted on purpose → resume) + frequency "
                    "add-on, then finalize (no calls), verify, group, memo. Not a full-corpus classification run.",
            "run_ids": ["run-20261015-094806-7e9c37", "run-20261015-094822-868f46", "run-20261015-102812-b0aa2c"],
        },
        "records": json.loads((ROOT / "evals/m8/finalize.json").read_text(encoding="utf-8")),
        "quarantine": {"file": "outputs/quarantine.jsonl.gz", "by_reason": dict(quarantine),
                       "runtime_validation_quarantines": 0,
                       "note": "outputs/quarantine.jsonl is the runtime log of reviews quarantined after failed "
                               "model output; it is empty for this DB (none failed twice)."},
        "spending_limits": {"project_cap_usd_list": PROJECT_CAP, "bucket_caps_usd_list": BUCKET_CAPS,
                            "per_run_max_spend": "settings.max_spend on each run",
                            "ledger_by_bucket": {b: {"reserved_usd": round(r or 0, 6), "settled_usd": round(s or 0, 6)}
                                                 for b, r, s in conn.execute(
                                "SELECT bucket, SUM(CASE WHEN kind='reserve' THEN usd END), "
                                "SUM(CASE WHEN kind='settle' THEN usd END) FROM ledger GROUP BY bucket")}},
        "resume_evidence": {"checkpoint_before_ids": len(json.loads((ROOT / "grading/checkpoint_before.json")
                                                                 .read_text(encoding="utf-8"))["completed_ids"]),
                            "checkpoint_after_ids": len(json.loads((ROOT / "grading/checkpoint_after.json")
                                                                .read_text(encoding="utf-8"))["completed_ids"]),
                            "resume_check": rc, "details": "evals/recovery/README.md"},
        "runs": runs,
        "notes": ["Spend = list price from provider usage; calls without usage are settled at their ledger estimate "
                  "(spend_usd_list_estimated), never 0.",
                  "Runs whose label_config is not final_config are the superseded M6 10k runs; their 344 calls are "
                  "left out of grading/calls.jsonl.gz (evals/m10/export.json).",
                  "Pilot, M6/M6b side DBs and system-test DBs are separate (cost/, evals/); project spend roll-up in "
                  "README § Cost."],
    }
    (ROOT / "outputs/run_summary.json").write_text(json.dumps(summary, indent=1, sort_keys=False) + "\n",
                                                  encoding="utf-8", newline="\n")
    print(json.dumps({"quarantine": dict(quarantine), "runs": len(runs)}, indent=1))


if __name__ == "__main__":
    main()
