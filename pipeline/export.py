"""M10 grading export (PLAN §5 M10, §9.3–9.5): build `grading/` from state/pipeline.db + outputs/. No API calls.

    run.json                    version, analysis count/SHA (from grading/ingestion.json), input fields, allow_multi_issue
    records.jsonl.gz            one final record per source row (completed or quarantined), sorted by review_id
    calls.jsonl.gz              every model-call attempt in pipeline.db, except succeeded enrich calls under another
                                label_config (the superseded M6 config; listed in the side report, not hidden)
    membership/ranking/claims   copied byte-for-byte from outputs/ (M9)
    checkpoint_before/after     copied from evals/recovery/ (M8 interrupt → resume demo)

Usage the provider did not return is exported as 0 with `"usage_available": false` (never invented).
gzip files are written with mtime 0, so the same DB always gives the same bytes.
"""
import gzip
import io
import json
import shutil
from collections import Counter
from pathlib import Path

VERSION = "a5-audit-v1"
COPIED = {"membership.csv": "outputs/membership.csv", "ranking.csv": "outputs/ranking.csv",
          "claims.csv": "outputs/claims.csv", "checkpoint_before.json": "evals/recovery/checkpoint_before.json",
          "checkpoint_after.json": "evals/recovery/checkpoint_after.json"}


def _gz_lines(path, rows):
    n = 0
    with gzip.GzipFile(path, "wb", mtime=0) as raw, io.TextIOWrapper(raw, encoding="utf-8", newline="\n") as f:
        for rec in rows:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    return n


def record_rows(conn):
    for (rid, sha, status, reason, topic, intent, sent, sev, ents, quote, nr, cfg, src) in conn.execute(
            """SELECT review_id, source_sha256, status, reason, topic, intent, sentiment, severity, entities_json,
                      evidence_quote, needs_review, label_config, cache_source_id FROM records ORDER BY review_id"""):
        if status == "quarantined":
            yield {"review_id": rid, "source_sha256": sha, "status": status, "reason": reason}
            continue
        rec = {"review_id": rid, "source_sha256": sha, "status": status, "topic": topic, "intent": intent,
               "sentiment": sent, "severity": sev, "entities": json.loads(ents), "evidence_quote": quote,
               "needs_review": bool(nr), "label_config": cfg}
        if src:
            rec["cache_source_id"] = src
        yield rec


def call_rows(conn, label_config):
    """(exported, excluded): exported in contract shape; excluded = succeeded enrich calls under another label_config."""
    exported, excluded = [], []
    for (rid, role, agent, model, tier, phase, outcome, cfg, ids, tin, tout, avail, run_id, started, ended,
         prid, error) in conn.execute(
            """SELECT request_id, role, agent, model, tier, phase, outcome, label_config, review_ids_json, input_tokens,
                      output_tokens, usage_available, run_id, started_at, ended_at, provider_request_id, error
               FROM calls ORDER BY started_at, request_id"""):
        rec = {"request_id": rid, "role": role, "review_ids": json.loads(ids), "model": model, "phase": phase,
               "outcome": outcome, "label_config": cfg, "input_tokens": tin or 0, "output_tokens": tout or 0,
               "usage_available": bool(avail) if avail is not None else tin is not None,
               "agent": agent, "tier": tier, "run_id": run_id, "started_at": started, "ended_at": ended}
        if prid:
            rec["provider_request_id"] = prid
        if outcome == "failed" and error:
            rec["error"] = error[:300]
        if role == "enrich" and outcome == "succeeded" and cfg != label_config:
            excluded.append(rec)
        else:
            exported.append(rec)
    return exported, excluded


def export(conn, label_config, root=Path("."), out=Path("grading"), allow_multi_issue=False):
    root, out = Path(root), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    ingestion = json.loads((out / "ingestion.json").read_text(encoding="utf-8"))
    run = {"version": VERSION, "analysis_count": ingestion["counts"]["records"],
           "analysis_sha256": ingestion["file_sha256"], "classification_input_fields": ["review_text"],
           "allow_multi_issue": allow_multi_issue, "label_config": label_config}
    (out / "run.json").write_text(json.dumps(run, indent=1) + "\n", encoding="utf-8", newline="\n")
    for name in ("records.jsonl", "calls.jsonl"):   # the checker flags a plain and a .gz copy side by side
        (out / name).unlink(missing_ok=True)
    n_records = _gz_lines(out / "records.jsonl.gz", record_rows(conn))
    exported, excluded = call_rows(conn, label_config)
    _gz_lines(out / "calls.jsonl.gz", exported)
    for name, src in COPIED.items():
        shutil.copyfile(root / src, out / name)
    count = Counter((c["role"], c["phase"], c["outcome"]) for c in exported)
    return {"records": n_records, "calls_exported": len(exported),
            "calls_by_role_phase_outcome": {"/".join(map(str, k)): v for k, v in sorted(count.items())},
            "calls_excluded_superseded_config": len(excluded),
            "excluded_label_configs": dict(Counter(c["label_config"] for c in excluded)),
            "excluded_calls": excluded}
