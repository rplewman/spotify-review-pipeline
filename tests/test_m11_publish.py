"""M11 gate (PLAN §5): `publish` rebuilds Neon's serving tables + dashboard views from SQLite, the read-only role can read
the views and nothing else, and the backend's numbers equal the published rows. The Neon test needs TEST_DATABASE_URL
(the `test` branch); the rest runs offline."""
import json
import os

import psycopg
import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from backend import app as backend
from pipeline import cli
from pipeline.db import publish
from pipeline.db.store import connect, migrate

load_dotenv(".env")
CFG = "cfg-final"
needs_neon = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")


def build(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    rows = [("a", "random_100k", "completed", None, "playback", "complaint", 4, None),
            ("b", "out_of_scope", "completed", None, "playback", "complaint", 4, "a"),
            ("c", "random_100k", "completed", None, "other", "praise", 1, None),
            ("d", "out_of_scope", "quarantined", "out_of_scope_budget_100k", None, None, None, None),
            ("e", "empty_text", "quarantined", "empty_review_text", None, None, None, None)]
    for i, (rid, scope, status, reason, topic, intent, sev, src) in enumerate(rows):
        conn.execute("INSERT INTO source_reviews VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)",
                     (rid, i + 1, f"sha-{rid}", "t", "s", f"text {rid}", "2", "0", "8.7", "2023-01-01", scope, i + 1))
        conn.execute("INSERT INTO records VALUES (?,?,?,?,?,?,?,?,'[]',?,0,?,?)",
                     (rid, f"sha-{rid}", status, reason, topic, intent, 0.1 if topic else None, sev,
                      f"quote {rid}" if topic else None, CFG if status == "completed" else None, src))
    conn.execute("INSERT INTO issues VALUES ('playback-general',?,'playback','general','playback','Will not play','d','model',2)",
                 (CFG,))
    for rid in ("a", "b"):
        conn.execute("INSERT INTO membership VALUES ('playback-general', ?, ?)", (rid, CFG))
    conn.execute("INSERT INTO ranking VALUES (?, 1, 'playback-general', 2, 8, '4.000000', 8)", (CFG,))
    agg = {"issue_id": "playback-general", "rank": 1, "name": "Will not play", "topic": "playback", "subtopic": "general",
           "area": "playback", "complaint_count": 2, "cancellation_count": 0, "severity_sum": 8, "mean_severity": "4.000000",
           "priority_score": 8, "needs_review_count": 0, "share_of_members": "1.000000"}
    conn.execute("INSERT INTO aggregates VALUES (?, 'playback-general', ?)", (CFG, json.dumps(agg)))
    area = {"area": "playback", "topics": "playback|downloads", "issue_count": 1, "complaint_count": 2, "cancellation_count": 0,
            "severity_sum": 8, "mean_severity": "4.000000", "share_of_members": "1.000000", "share_of_severity": "1.000000",
            "top_issue_id": "playback-general"}
    conn.execute("INSERT INTO area_aggregates VALUES (?, 'playback', ?)", (CFG, json.dumps(area)))
    conn.execute("INSERT INTO claims VALUES (?, 'C01a', 'playback-general', 'complaint_count', '2')", (CFG,))
    conn.execute("INSERT INTO claims VALUES (?, 'C01b', 'playback-general', 'severity_sum', '9')", (CFG,))  # planted mismatch
    rec = {"area": "playback", "title": "Fix playback", "rationale": "r", "issue_ids": ["playback-general"],
           "claim_ids": ["C01a", "C01b"], "evidence": [{"issue_id": "playback-general", "review_id": "a", "quote": "text a"}]}
    conn.execute("INSERT INTO recommendations VALUES (?, 1, ?, 'req')", (CFG, json.dumps(rec)))
    conn.execute("""INSERT INTO calls (request_id, role, agent, model, phase, outcome, label_config, review_ids_json,
                    input_tokens, output_tokens, cost_usd) VALUES ('r1','enrich','A3','m','initial','succeeded',?,'[]',10,5,0.5)""",
                 (CFG,))
    root = tmp_path / "root"
    files = {"grading/run.json": {"label_config": CFG}, "outputs/overall_metrics.json": {},
             "outputs/recommendations.json": {"question": "q?", "priority_area": "playback"},
             "evals/verify/verify_report.json": {"agreement": {}, "all_fields_agree": {}},
             "evals/golden/golden_eval.json": {"summary": {k: {} for k in ("evaluated", "topic", "intent", "severity",
                                                                            "sentiment")}}}
    for name, body in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(json.dumps(body), encoding="utf-8")
    return conn, root


def test_ro_url_swaps_only_the_credentials():
    url = "postgresql://neondb_owner:npg_secret@ep-x.aws.neon.tech/neondb?sslmode=require"
    assert publish.ro_url(url, "pw") == "postgresql://dashboard_ro:pw@ep-x.aws.neon.tech/neondb?sslmode=require"


def test_publish_without_url_refuses(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    assert cli.main(["publish", "--target", "test", "--db", str(tmp_path / "x.db")]) == 2


def test_dashboard_page_and_health_without_db(monkeypatch):
    for k in ("DASHBOARD_DATABASE_URL", "DATABASE_URL", "DASHBOARD_TARGET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(backend, "_shared", {})
    c = TestClient(backend.app)
    page = c.get("/")
    assert page.status_code == 200 and "Spotify Review Insights" in page.text
    assert c.get("/api/health").json()["ok"] is False


@needs_neon
def test_publish_to_neon_test_branch_and_serve(tmp_path, monkeypatch):
    lite, root = build(tmp_path)
    pw = os.environ.get("DASHBOARD_RO_PASSWORD") or "test-only-Pw-0123456789abcdef"
    r = publish.publish(lite, os.environ["TEST_DATABASE_URL"], root, ro_password=pw)
    assert r["counts"]["records"] == 5 and r["counts"]["membership"] == 2 and r["counts"]["ro_role"] == publish.RO_ROLE
    publish.publish(lite, os.environ["TEST_DATABASE_URL"], root, ro_password=pw)          # idempotent rebuild

    ro = publish.ro_url(os.environ["TEST_DATABASE_URL"], pw)
    with psycopg.connect(ro) as pg:                                                      # views only, read-only
        assert pg.execute("SELECT records, completed, cache_reused FROM dashboard.v_overall").fetchone() == (5, 3, 1)
        for stmt in ("SELECT count(*) FROM serving.records", "CREATE TABLE dashboard.x (a int)"):
            with pytest.raises(psycopg.Error):
                pg.execute(stmt)
            pg.rollback()

    monkeypatch.setenv("DASHBOARD_DATABASE_URL", ro)
    monkeypatch.delenv("DASHBOARD_TARGET", raising=False)
    monkeypatch.setattr(backend, "_shared", {})
    monkeypatch.setattr(backend, "_cache", {})
    c = TestClient(backend.app)
    ov = c.get("/api/overview").json()
    assert ov["overall"]["quarantined"] == 2 and ov["overall"]["members"] == 2
    assert {(x["reason"], x["n"]) for x in ov["status_reasons"]} == {("", 3), ("out_of_scope_budget_100k", 1),
                                                                     ("empty_review_text", 1)}
    assert [x["label"] for x in ov["severity"]] == ["1", "4"]
    recs = c.get("/api/recommendations").json()
    assert [(x["claim_id"], x["matches"]) for x in recs[0]["claims"]] == [("C01a", True), ("C01b", False)]
    assert recs[0]["evidence"][0]["review"]["review_text"] == "text a"
    assert c.get("/api/reviews/d").status_code == 404                                    # quarantined: no text served
    assert [x["review_id"] for x in c.get("/api/issues/playback-general/reviews").json()] == ["a", "b"]
    assert c.get("/api/ranking").json()[0]["priority_score"] == 8
    assert float(c.get("/api/cost").json()[0]["cost_usd"]) == 0.5
