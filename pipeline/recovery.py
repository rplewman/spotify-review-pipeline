"""Interruption/resume evidence (PLAN §5 M7): checkpoint files and a local copy of the checker's resume rules.

    checkpoint_before.json / checkpoint_after.json = {"completed_ids": [...]} (+ provenance keys the checker ignores)

`resume_rules` mirrors `check_submission.audit`'s resume block (lines ~314-370) over the DB instead of exported files,
so a demo can be judged before M10 exists: before nonempty and a strict subset of after; after ⊆ completed; every
non-cached "before" ID in a succeeded `initial` enrich call with its record's label_config; at least one new non-cached
ID in a succeeded `resume` call; no `resume` enrich call (any outcome) sends a "before" ID.
"""
import json
from pathlib import Path

from pipeline.db.store import now_iso


def completed(conn, label_config=None):
    """{review_id: (label_config, cache_source_id)} for completed records (all configs when label_config is None)."""
    sql = "SELECT review_id, label_config, cache_source_id FROM records WHERE status='completed'"
    rows = conn.execute(sql + (" AND label_config=?" if label_config else ""),
                        (label_config,) if label_config else ()).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def write_checkpoint_file(path, ids, **meta):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"completed_ids": sorted(ids), "count": len(ids), "written_at": now_iso(), **meta}
    path.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8", newline="\n")
    return body


def read_ids(path):
    return json.loads(Path(path).read_text(encoding="utf-8")).get("completed_ids", [])


def resume_rules(conn, before, after):
    """The checker's resume checks against the DB. Returns {"ok", "flags", counts…}."""
    flags = []
    for name, ids in (("before", before), ("after", after)):
        if len(ids) != len(set(ids)):
            flags.append(f"duplicate_checkpoint_id ({name})")
    before, after = set(before), set(after)
    valid = completed(conn)
    cached = {rid for rid, (_, src) in valid.items() if src}
    if not before or not before < after or not after <= set(valid):
        flags.append("resume_snapshot_mismatch")
    initial_logged, resume_logged, reprocessed = set(), set(), []
    for request_id, ids_json, phase, outcome, cfg in conn.execute(
            "SELECT request_id, review_ids_json, phase, outcome, label_config FROM calls WHERE role='enrich'"):
        ids = json.loads(ids_json)
        if phase == "resume" and before.intersection(ids):
            reprocessed.append(request_id)
        if outcome != "succeeded":
            continue
        for rid in ids:
            if rid in valid and valid[rid][0] == cfg:
                (initial_logged if phase == "initial" else resume_logged if phase == "resume" else set()).add(rid)
    if reprocessed:
        flags.append(f"reprocessed_checkpoint ({len(reprocessed)} resume calls)")
    unlogged_before = before - cached - initial_logged
    new_in_resume = (after - before - cached) & resume_logged
    if unlogged_before or not new_in_resume:
        flags.append("resume_call_evidence")
    return {"ok": not flags, "flags": flags, "before": len(before), "after": len(after),
            "new_after_resume": len(after - before), "before_cached": len(before & cached),
            "before_unlogged_in_initial": len(unlogged_before), "new_ids_in_resume_calls": len(new_in_resume),
            "resume_calls_with_before_ids": reprocessed[:10]}
