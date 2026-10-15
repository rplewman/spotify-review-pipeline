"""M11 `publish`: copy the dashboard's tables from SQLite (+ the saved evidence files) into Neon Postgres (PLAN §3.2).

Idempotent rebuild in ONE transaction: drop + recreate schemas `serving` (tables) and `dashboard` (read-only views), COPY
the rows, then grant the read-only role USAGE/SELECT on `dashboard` only. Readers see the old data until the commit.
The views are the dashboard's only source; the backend's role can read nothing else.

No API calls. Every number comes from `state/pipeline.db` or a file the pipeline already wrote.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql

RO_ROLE = "dashboard_ro"

TABLES = """
CREATE SCHEMA serving;
CREATE TABLE serving.records (
    review_id        text PRIMARY KEY,
    status           text NOT NULL,
    reason           text,
    scope            text,                 -- random_100k | frequency_addon | out_of_scope | empty_text
    completed_via    text,                 -- call | cache (exact-text reuse) | NULL for quarantined
    topic            text,
    intent           text,
    sentiment        real,
    severity         integer,
    needs_review     boolean,
    evidence_quote   text,
    cache_source_id  text,
    review_rating    text,
    review_timestamp text,
    app_version      text,
    review_text      text                  -- completed rows only
);
CREATE TABLE serving.issues (
    issue_id text PRIMARY KEY, topic text, subtopic text, area text, name text, description text,
    name_source text, member_count integer
);
CREATE TABLE serving.membership (issue_id text NOT NULL, review_id text NOT NULL, PRIMARY KEY (issue_id, review_id));
CREATE TABLE serving.ranking (
    rank integer PRIMARY KEY, issue_id text NOT NULL, complaint_count integer, severity_sum integer,
    mean_severity text, priority_score integer
);
CREATE TABLE serving.aggregates (
    issue_id text PRIMARY KEY, rank integer, name text, topic text, subtopic text, area text, complaint_count integer,
    cancellation_count integer, severity_sum integer, mean_severity text, priority_score integer,
    needs_review_count integer, share_of_members text
);
CREATE TABLE serving.area_aggregates (
    area text PRIMARY KEY, topics text, issue_count integer, complaint_count integer, cancellation_count integer,
    severity_sum integer, mean_severity text, share_of_members text, share_of_severity text, top_issue_id text
);
CREATE TABLE serving.claims (claim_id text PRIMARY KEY, issue_id text, metric text, value text);
CREATE TABLE serving.recommendations (position integer PRIMARY KEY, area text, title text, rationale text, body jsonb);
CREATE TABLE serving.run_summary (key text PRIMARY KEY, value jsonb NOT NULL);
CREATE TABLE serving.cost_summary (
    phase text, agent text, model text, tier text, calls integer, succeeded integer, failed integer,
    input_tokens bigint, output_tokens bigint, cost_usd numeric(12, 6)
);
"""

INDEXES = """
CREATE INDEX ix_membership_review ON serving.membership(review_id);
CREATE INDEX ix_records_status ON serving.records(status, reason);
"""

# Views = the dashboard's contract. Numbers are recomputed from the rows, so a smoke test can compare them to the saved files.
VIEWS = """
CREATE SCHEMA dashboard;
CREATE VIEW dashboard.v_overall AS
SELECT count(*)                                                      AS records,
       count(*) FILTER (WHERE status = 'completed')                  AS completed,
       count(*) FILTER (WHERE status = 'quarantined')                AS quarantined,
       count(*) FILTER (WHERE completed_via = 'cache')               AS cache_reused,
       count(*) FILTER (WHERE needs_review)                          AS needs_review,
       count(*) FILTER (WHERE review_text IS NULL OR status <> 'completed') AS no_text_published,
       round(count(*) FILTER (WHERE status = 'completed')::numeric / count(*), 6) AS completed_share,
       round(0.2 + 0.2 * 1.0 + 0.6 * count(*) FILTER (WHERE status = 'completed')::numeric
             / nullif(count(*) FILTER (WHERE reason IS DISTINCT FROM 'empty_review_text'), 0), 4) AS coverage_point_candidate,
       (SELECT count(*) FROM serving.issues)                         AS issues,
       (SELECT count(*) FROM serving.membership)                     AS members
FROM serving.records;
CREATE VIEW dashboard.v_status_reasons AS
SELECT status, coalesce(reason, '') AS reason, count(*) AS n FROM serving.records GROUP BY 1, 2 ORDER BY 1, 3 DESC;
CREATE VIEW dashboard.v_scope AS
SELECT coalesce(scope, '') AS scope, status, coalesce(completed_via, '') AS completed_via, count(*) AS n
FROM serving.records GROUP BY 1, 2, 3 ORDER BY 4 DESC;
CREATE VIEW dashboard.v_topic AS
SELECT topic AS label, count(*) AS n FROM serving.records WHERE status = 'completed' GROUP BY 1 ORDER BY 2 DESC;
CREATE VIEW dashboard.v_intent AS
SELECT intent AS label, count(*) AS n FROM serving.records WHERE status = 'completed' GROUP BY 1 ORDER BY 2 DESC;
CREATE VIEW dashboard.v_severity AS
SELECT severity::text AS label, count(*) AS n FROM serving.records WHERE status = 'completed' GROUP BY severity ORDER BY severity;
CREATE VIEW dashboard.v_area_rollup AS
SELECT * FROM serving.area_aggregates ORDER BY CASE WHEN area = 'outside' THEN 1 ELSE 0 END, severity_sum DESC;
CREATE VIEW dashboard.v_ranking AS
SELECT r.rank, r.issue_id, i.name, i.description, i.area, i.topic, i.subtopic, r.complaint_count, a.cancellation_count,
       r.severity_sum, r.mean_severity, r.priority_score, a.needs_review_count, a.share_of_members
FROM serving.ranking r JOIN serving.issues i USING (issue_id) JOIN serving.aggregates a USING (issue_id) ORDER BY r.rank;
CREATE VIEW dashboard.v_claims AS
SELECT c.claim_id, c.issue_id, c.metric, c.value,
       CASE c.metric WHEN 'complaint_count' THEN a.complaint_count::text WHEN 'severity_sum' THEN a.severity_sum::text
                     WHEN 'mean_severity' THEN a.mean_severity WHEN 'priority_score' THEN a.priority_score::text END AS computed,
       c.value = CASE c.metric WHEN 'complaint_count' THEN a.complaint_count::text WHEN 'severity_sum' THEN a.severity_sum::text
                     WHEN 'mean_severity' THEN a.mean_severity WHEN 'priority_score' THEN a.priority_score::text END AS matches
FROM serving.claims c LEFT JOIN serving.aggregates a USING (issue_id) ORDER BY c.claim_id;
CREATE VIEW dashboard.v_recommendations AS SELECT position, area, title, rationale, body FROM serving.recommendations ORDER BY position;
CREATE VIEW dashboard.v_run_summary AS SELECT key, value FROM serving.run_summary;
CREATE VIEW dashboard.v_cost_summary AS SELECT * FROM serving.cost_summary ORDER BY phase, agent, tier;
CREATE VIEW dashboard.v_reviews AS
SELECT r.review_id, m.issue_id, r.topic, r.intent, r.sentiment, r.severity, r.needs_review, r.evidence_quote,
       r.cache_source_id, r.review_rating, r.review_timestamp, r.app_version, r.review_text, r.scope
FROM serving.records r LEFT JOIN serving.membership m USING (review_id) WHERE r.status = 'completed';
"""

AGG_COLS = ["issue_id", "rank", "name", "topic", "subtopic", "area", "complaint_count", "cancellation_count", "severity_sum",
            "mean_severity", "priority_score", "needs_review_count", "share_of_members"]
AREA_COLS = ["area", "topics", "issue_count", "complaint_count", "cancellation_count", "severity_sum", "mean_severity",
             "share_of_members", "share_of_severity", "top_issue_id"]


def frozen_label_config(root):
    return json.loads((Path(root) / "grading" / "run.json").read_text(encoding="utf-8"))["label_config"]


def _copy(cur, table, columns, rows):
    n = 0
    with cur.copy(sql.SQL("COPY {} ({}) FROM STDIN").format(
            sql.Identifier("serving", table), sql.SQL(", ").join(map(sql.Identifier, columns)))) as cp:
        for row in rows:
            cp.write_row(row)
            n += 1
    return n


def _records(lite, cfg):
    q = """SELECT r.review_id, r.status, r.reason, s.scope,
                  CASE WHEN r.status <> 'completed' THEN NULL WHEN r.cache_source_id IS NOT NULL THEN 'cache' ELSE 'call' END,
                  r.topic, r.intent, r.sentiment, r.severity, r.needs_review, r.evidence_quote, r.cache_source_id,
                  s.review_rating, s.review_timestamp, s.app_version,
                  CASE WHEN r.status = 'completed' THEN s.review_text END
           FROM records r JOIN source_reviews s USING (review_id)
           WHERE r.status = 'quarantined' OR r.label_config = ?"""
    for row in lite.execute(q, (cfg,)):
        row = list(row)
        row[9] = None if row[9] is None else bool(row[9])
        yield row


def _json_rows(lite, table, key_cols, cfg, cols):
    for (row_json,) in lite.execute(f"SELECT row_json FROM {table} WHERE label_config = ? ORDER BY {key_cols}", (cfg,)):
        d = json.loads(row_json)
        yield [d.get(c) for c in cols]


def run_summary(lite, cfg, root):
    root = Path(root)
    load = lambda p: json.loads((root / p).read_text(encoding="utf-8"))  # noqa: E731
    verify = load("evals/verify/verify_report.json")
    golden = load("evals/golden/golden_eval.json")["summary"]
    recs = load("outputs/recommendations.json")
    return {
        "label_config": cfg,
        "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run": load("grading/run.json"),
        "overall_metrics_file": load("outputs/overall_metrics.json"),
        "question": recs.get("question"),
        "priority_area": recs.get("priority_area"),
        "memo": {k: recs.get(k) for k in ("model", "memo_prompt", "input_sha256")},
        "verification": {"agreement": verify["agreement"], "all_fields_agree": verify["all_fields_agree"],
                         "sample": verify.get("sample")},
        "golden": {k: golden[k] for k in ("evaluated", "topic", "intent", "severity", "sentiment")},
        "scope_note": ("Labels cover the official seeded random 100,000 reviews, the 25,000 most-duplicated other texts "
                       "(frequency add-on) and every exact-text duplicate of those (cache reuse). The other rows are "
                       "quarantined as out_of_scope_budget_100k: accounted for, not classified."),
    }


def cost_rows(lite):
    return lite.execute("""SELECT coalesce(phase, ''), agent, model, tier, count(*), sum(outcome = 'succeeded'),
                                  sum(outcome <> 'succeeded'), sum(coalesce(input_tokens, 0)), sum(coalesce(output_tokens, 0)),
                                  round(sum(coalesce(cost_usd, 0)), 6)
                           FROM calls GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4""").fetchall()


def ensure_ro_role(cur, password):
    """Create (or re-password) the backend's read-only login role. Neon allows CREATE ROLE for the project owner."""
    exists = cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (RO_ROLE,)).fetchone()
    verb = "ALTER" if exists else "CREATE"
    cur.execute(sql.SQL(verb + " ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(RO_ROLE), sql.Literal(password)))


def publish(lite, database_url, root=".", label_config=None, ro_password=None):
    """Rebuild the serving + dashboard schemas from `lite` (SQLite) in one Postgres transaction. Returns row counts."""
    cfg = label_config or frozen_label_config(root)
    counts = {}
    with psycopg.connect(database_url, connect_timeout=20) as pg:
        with pg.transaction(), pg.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS dashboard CASCADE")
            cur.execute("DROP SCHEMA IF EXISTS serving CASCADE")
            cur.execute(TABLES)
            counts["records"] = _copy(cur, "records", ["review_id", "status", "reason", "scope", "completed_via", "topic",
                                                       "intent", "sentiment", "severity", "needs_review", "evidence_quote",
                                                       "cache_source_id", "review_rating", "review_timestamp",
                                                       "app_version", "review_text"], _records(lite, cfg))
            counts["issues"] = _copy(cur, "issues", ["issue_id", "topic", "subtopic", "area", "name", "description",
                                                     "name_source", "member_count"], lite.execute(
                "SELECT issue_id, topic, subtopic, area, name, description, name_source, member_count FROM issues "
                "WHERE label_config = ? ORDER BY issue_id", (cfg,)))
            counts["membership"] = _copy(cur, "membership", ["issue_id", "review_id"], lite.execute(
                "SELECT issue_id, review_id FROM membership WHERE label_config = ?", (cfg,)))
            counts["ranking"] = _copy(cur, "ranking", ["rank", "issue_id", "complaint_count", "severity_sum",
                                                       "mean_severity", "priority_score"], lite.execute(
                "SELECT rank, issue_id, complaint_count, severity_sum, mean_severity, priority_score FROM ranking "
                "WHERE label_config = ? ORDER BY rank", (cfg,)))
            counts["aggregates"] = _copy(cur, "aggregates", AGG_COLS, _json_rows(lite, "aggregates", "issue_id", cfg, AGG_COLS))
            counts["area_aggregates"] = _copy(cur, "area_aggregates", AREA_COLS,
                                              _json_rows(lite, "area_aggregates", "area", cfg, AREA_COLS))
            counts["claims"] = _copy(cur, "claims", ["claim_id", "issue_id", "metric", "value"], lite.execute(
                "SELECT claim_id, issue_id, metric, value FROM claims WHERE label_config = ? ORDER BY claim_id", (cfg,)))
            recs = []
            for pos, row_json in lite.execute("SELECT position, row_json FROM recommendations WHERE label_config = ? "
                                              "ORDER BY position", (cfg,)):
                d = json.loads(row_json)
                recs.append([pos, d.get("area"), d.get("title"), d.get("rationale"), json.dumps(d)])
            counts["recommendations"] = _copy(cur, "recommendations", ["position", "area", "title", "rationale", "body"], recs)
            summary = run_summary(lite, cfg, root)
            counts["run_summary"] = _copy(cur, "run_summary", ["key", "value"],
                                          ([k, json.dumps(v)] for k, v in summary.items()))
            counts["cost_summary"] = _copy(cur, "cost_summary", ["phase", "agent", "model", "tier", "calls", "succeeded",
                                                                 "failed", "input_tokens", "output_tokens", "cost_usd"],
                                           cost_rows(lite))
            cur.execute(INDEXES)
            cur.execute(VIEWS)
            if ro_password:
                ensure_ro_role(cur, ro_password)
            if cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (RO_ROLE,)).fetchone():
                ro = sql.Identifier(RO_ROLE)
                cur.execute(sql.SQL("GRANT USAGE ON SCHEMA dashboard TO {}").format(ro))
                cur.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA dashboard TO {}").format(ro))
                cur.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(ro))
                cur.execute(sql.SQL("ALTER ROLE {} SET statement_timeout = '15s'").format(ro))
                counts["ro_role"] = RO_ROLE
    return {"label_config": cfg, "counts": counts}


def ro_url(owner_url, password):
    """The owner's connection URL with the read-only role's name and password swapped in."""
    return re.sub(r"://[^:/@]+:[^@]*@", f"://{RO_ROLE}:{password}@", owner_url, count=1)
