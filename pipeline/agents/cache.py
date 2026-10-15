"""A2 Cache (PLAN §3.3, §3.6): the result cache keyed by (text_sha256, label_config), the pre-call check, and the
`cache_source_id` fan-out.

- `results` holds one validated output per distinct text and label_config. It is never reused across label_configs.
- Fan-out: when a text has a result, every source row with that exact text gets a completed record. The original
  (`texts.original_review_id`, the ID that was sent to the model) has no `cache_source_id`; every other row points
  **directly** at the original (no alias chains). Rows outside the 100k scope are filled the same way, at $0.
- A record is only replaced when it was written under a different label_config, so a late duplicate commit under the
  same config changes nothing.

Write functions expect the caller to hold a write transaction (`store.immediate`).
"""
import json

RESULT_FIELDS = ("topic", "intent", "sentiment", "severity", "entities", "evidence_quote", "needs_review")


def cached(conn, text_shas, label_config):
    """The subset of `text_shas` that already has a result under `label_config` (the pre-call check)."""
    if not text_shas:
        return set()
    marks = ",".join("?" * len(text_shas))
    return {r[0] for r in conn.execute(f"SELECT text_sha256 FROM results WHERE label_config = ? AND text_sha256 IN ({marks})",
                                       [label_config, *text_shas])}


def write_result(conn, text_sha, label_config, res, request_id):
    """Insert a validated result; a second insert for the same key is a no-op (ON CONFLICT DO NOTHING)."""
    conn.execute("""INSERT INTO results (text_sha256, label_config, topic, intent, sentiment, severity, entities_json,
                                         evidence_quote, needs_review, decision_source, probs_json, request_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (text_sha256, label_config) DO NOTHING""",
                 (text_sha, label_config, res["topic"], res["intent"], res["sentiment"], res["severity"],
                  json.dumps(res["entities"], ensure_ascii=False), res["evidence_quote"], int(res["needs_review"]),
                  res.get("decision_source", "decision"),
                  json.dumps(res["probs"]) if res.get("probs") is not None else None, request_id))


def _shas_clause(text_shas):
    if text_shas is None:
        return "", []
    return f"AND s.text_sha256 IN ({','.join('?' * len(text_shas))})", list(text_shas)


def fan_out(conn, label_config, text_shas=None):
    """Write completed records for every source row whose text has a result (all texts when `text_shas` is None)."""
    if text_shas is not None and not text_shas:
        return 0
    clause, params = _shas_clause(text_shas)
    return conn.execute(f"""
        INSERT INTO records (review_id, source_sha256, status, reason, topic, intent, sentiment, severity, entities_json,
                             evidence_quote, needs_review, label_config, cache_source_id)
        SELECT s.review_id, s.source_sha256, 'completed', NULL, r.topic, r.intent, r.sentiment, r.severity,
               r.entities_json, r.evidence_quote, r.needs_review, r.label_config,
               CASE WHEN s.review_id = t.original_review_id THEN NULL ELSE t.original_review_id END
        FROM source_reviews s
        JOIN texts t ON t.text_sha256 = s.text_sha256
        JOIN results r ON r.text_sha256 = s.text_sha256 AND r.label_config = ?
        WHERE true {clause}
        ON CONFLICT (review_id) DO UPDATE SET
            status=excluded.status, reason=NULL, topic=excluded.topic, intent=excluded.intent,
            sentiment=excluded.sentiment, severity=excluded.severity, entities_json=excluded.entities_json,
            evidence_quote=excluded.evidence_quote, needs_review=excluded.needs_review,
            label_config=excluded.label_config, cache_source_id=excluded.cache_source_id
        WHERE records.label_config IS NOT excluded.label_config""", [label_config, *params]).rowcount


def quarantine_texts(conn, label_config, reasons):
    """Quarantined records for every source row of each text in `reasons` ({text_sha: reason})."""
    for sha, reason in reasons.items():
        conn.execute("""
            INSERT INTO records (review_id, source_sha256, status, reason, label_config)
            SELECT review_id, source_sha256, 'quarantined', ?, ? FROM source_reviews WHERE text_sha256 = ?
            ON CONFLICT (review_id) DO UPDATE SET status='quarantined', reason=excluded.reason, topic=NULL, intent=NULL,
                sentiment=NULL, severity=NULL, entities_json=NULL, evidence_quote=NULL, needs_review=NULL,
                label_config=excluded.label_config, cache_source_id=NULL
            WHERE records.label_config IS NOT excluded.label_config""", (reason, label_config, sha))
