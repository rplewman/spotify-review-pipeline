"""M8 close-out (PLAN §1.1, §5 M8): give every one of the 660,622 source rows exactly one final record. No API calls.

1. Exact-text cache fill: fan out every result under the frozen label_config to all source rows with that text
   (idempotent; out-of-scope rows that share a text with a classified one become completed, `cache_source_id` set).
2. Rows still without a record:
   - scope `out_of_scope` → quarantined `out_of_scope_budget_100k` (accounted for, not classified);
   - scope `random_100k` / `frequency_addon` (the run stopped before reaching them) → refused unless `allow_unreached`,
     then quarantined `out_of_scope_budget` (PLAN §1.2) and reported.
3. Reconcile: one record per source ID, counts by status/reason/scope, and the total against `grading/ingestion.json`.
"""
import json
from pathlib import Path

from pipeline.agents import cache
from pipeline.db.store import immediate

OUT_OF_SCOPE_REASON = "out_of_scope_budget_100k"
UNREACHED_REASON = "out_of_scope_budget"


class FinalizeError(Exception):
    pass


def missing_by_scope(conn):
    return dict(conn.execute("""SELECT s.scope, COUNT(*) FROM source_reviews s
                                LEFT JOIN records r ON r.review_id = s.review_id
                                WHERE r.review_id IS NULL GROUP BY s.scope""").fetchall())


def _quarantine_missing(conn, scopes, reason):
    marks = ",".join("?" * len(scopes))
    return conn.execute(f"""INSERT INTO records (review_id, source_sha256, status, reason)
                            SELECT s.review_id, s.source_sha256, 'quarantined', ? FROM source_reviews s
                            WHERE s.scope IN ({marks})
                              AND NOT EXISTS (SELECT 1 FROM records r WHERE r.review_id = s.review_id)""",
                        (reason, *scopes)).rowcount


def reconcile(conn, ingestion_path="grading/ingestion.json"):
    total_source = conn.execute("SELECT COUNT(*) FROM source_reviews").fetchone()[0]
    total_records = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    orphans = conn.execute("""SELECT COUNT(*) FROM records r
                              WHERE NOT EXISTS (SELECT 1 FROM source_reviews s WHERE s.review_id = r.review_id)""").fetchone()[0]
    by_status = dict(conn.execute("SELECT status, COUNT(*) FROM records GROUP BY 1").fetchall())
    by_reason = dict(conn.execute("SELECT reason, COUNT(*) FROM records WHERE status='quarantined' GROUP BY 1").fetchall())
    by_scope = {f"{scope}:{status}": n for scope, status, n in conn.execute(
        """SELECT s.scope, r.status, COUNT(*) FROM records r JOIN source_reviews s ON s.review_id = r.review_id
           GROUP BY 1, 2""")}
    configs = dict(conn.execute("SELECT label_config, COUNT(*) FROM records WHERE status='completed' GROUP BY 1").fetchall())
    cache_reused = conn.execute("SELECT COUNT(*) FROM records WHERE status='completed' AND cache_source_id IS NOT NULL"
                                ).fetchone()[0]
    expected = None
    p = Path(ingestion_path)
    if p.exists():
        expected = json.loads(p.read_text(encoding="utf-8"))["counts"]
    missing = missing_by_scope(conn)
    ok = (total_records == total_source and not missing and not orphans
          and (expected is None or expected.get("records", total_source) == total_source))
    return {"ok": ok, "source_rows": total_source, "records": total_records, "missing_by_scope": missing,
            "orphan_records": orphans, "by_status": by_status, "quarantined_by_reason": by_reason,
            "by_scope_status": by_scope, "completed_by_label_config": configs,
            "completed_by_cache_reuse": cache_reused, "ingestion_counts": expected}


def finalize(conn, label_config, allow_unreached=False, ingestion_path="grading/ingestion.json"):
    if conn.execute("SELECT 1 FROM runs WHERE ended_at IS NULL").fetchone():
        raise FinalizeError("a run is still open (live or crashed): let it finish, or `resume` it, first")
    with immediate(conn):
        filled = cache.fan_out(conn, label_config)
    unreached = {k: v for k, v in missing_by_scope(conn).items() if k in ("random_100k", "frequency_addon")}
    if unreached and not allow_unreached:
        raise FinalizeError(f"in-scope rows without a record {unreached}: finish the run (resume) "
                            f"or pass --allow-unreached to quarantine them as {UNREACHED_REASON!r}")
    with immediate(conn):
        q_unreached = _quarantine_missing(conn, ["random_100k", "frequency_addon"], UNREACHED_REASON) if unreached else 0
        q_out = _quarantine_missing(conn, ["out_of_scope"], OUT_OF_SCOPE_REASON)
    return {"cache_fill_rows_written": filled, "quarantined_unreached": q_unreached,
            "quarantined_out_of_scope": q_out, "unreached_before": unreached,
            "reconcile": reconcile(conn, ingestion_path)}
