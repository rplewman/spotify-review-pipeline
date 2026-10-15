"""Serving API + static dashboard (M11). Read-only: every query hits a `dashboard.*` view in Neon; nothing here calls a model.

Connects with DASHBOARD_DATABASE_URL (role `dashboard_ro`: SELECT on the views only, read-only transactions), falling back
to DATABASE_URL for local runs; DASHBOARD_TARGET=test reads the Neon `test` branch (TEST_DASHBOARD_DATABASE_URL).
Secrets live in the host's env vars (Render) or `.env` locally.
"""
import os
import re
import threading
import time
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from psycopg.rows import dict_row

load_dotenv(".env")
app = FastAPI(title="Spotify Review Pipeline", docs_url="/api/docs", openapi_url="/api/openapi.json")
DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard"
CACHE_SECONDS = 300          # the data only changes on `publish`
_cache = {}
_shared = {}
_lock = threading.Lock()


def db_url():
    if os.environ.get("DASHBOARD_TARGET") == "test":
        return os.environ.get("TEST_DASHBOARD_DATABASE_URL")
    return os.environ.get("DASHBOARD_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _conn():
    """One shared autocommit connection (opening a TLS connection to Neon per query is the slow part)."""
    conn = _shared.get("conn")
    if conn is None or conn.closed:
        url = db_url()
        if not url:
            raise HTTPException(503, "database URL is not set")
        conn = _shared["conn"] = psycopg.connect(url, connect_timeout=15, autocommit=True, row_factory=dict_row)
    return conn


def query(sql, params=(), cache=True):
    key = (sql, params)
    hit = _cache.get(key)
    if cache and hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    with _lock:
        try:
            rows = _conn().execute(sql, params).fetchall()
        except psycopg.OperationalError:          # Neon closes idle connections when the compute suspends: reconnect once
            _shared["conn"] = None
            rows = _conn().execute(sql, params).fetchall()
    if cache:
        _cache[key] = (time.monotonic(), rows)
    return rows


def redact(e):
    first = str(e).strip().splitlines()[0] if str(e).strip() else ""
    return re.sub(r"npg_\w+|://[^@\s]*@", "<redacted>", first)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(DASHBOARD / "index.html")


@app.get("/api/health")
def health():
    try:
        row = query("SELECT value FROM dashboard.v_run_summary WHERE key = 'published_at'", cache=False)
    except HTTPException as e:
        return {"ok": False, "error": e.detail}
    except psycopg.Error as e:
        return {"ok": False, "error": type(e).__name__, "detail": redact(e)}
    return {"ok": True, "published_at": row[0]["value"] if row else None}


@app.get("/api/overview")
def overview():
    summary = {r["key"]: r["value"] for r in query("SELECT key, value FROM dashboard.v_run_summary")}
    return {
        "overall": query("SELECT * FROM dashboard.v_overall")[0],
        "status_reasons": query("SELECT * FROM dashboard.v_status_reasons"),
        "scope": query("SELECT * FROM dashboard.v_scope"),
        "topic": query("SELECT * FROM dashboard.v_topic"),
        "intent": query("SELECT * FROM dashboard.v_intent"),
        "severity": query("SELECT * FROM dashboard.v_severity"),
        "summary": {k: summary.get(k) for k in ("label_config", "published_at", "question", "priority_area", "memo",
                                                "verification", "golden", "scope_note")},
    }


@app.get("/api/areas")
def areas():
    return query("SELECT * FROM dashboard.v_area_rollup")


@app.get("/api/ranking")
def ranking():
    return query("SELECT * FROM dashboard.v_ranking")


@app.get("/api/claims")
def claims():
    return query("SELECT * FROM dashboard.v_claims")


@app.get("/api/recommendations")
def recommendations():
    """Each recommendation with its claims re-checked against the aggregates and its evidence reviews looked up."""
    claims_by_id = {c["claim_id"]: c for c in claims()}
    out = []
    for r in query("SELECT * FROM dashboard.v_recommendations"):
        body = r["body"]
        evidence = []
        for ev in body.get("evidence", []):
            rows = review_rows(ev["review_id"])
            evidence.append({**ev, "review": rows[0] if rows else None})
        out.append({"position": r["position"], "area": r["area"], "title": r["title"], "rationale": r["rationale"],
                    "issue_ids": body.get("issue_ids", []),
                    "claims": [claims_by_id.get(c, {"claim_id": c, "matches": False}) for c in body.get("claim_ids", [])],
                    "evidence": evidence})
    return out


def review_rows(review_id):
    return query("SELECT * FROM dashboard.v_reviews WHERE review_id = %s", (review_id,))


@app.get("/api/reviews/{review_id}")
def review(review_id: str):
    rows = review_rows(review_id)
    if not rows:
        raise HTTPException(404, "no completed review with that id")
    return rows[0]


@app.get("/api/issues/{issue_id}/reviews")
def issue_reviews(issue_id: str, limit: int = 5):
    """The most severe member reviews of an issue (ties: earliest review_id) — evidence for the ranking row."""
    return query("SELECT * FROM dashboard.v_reviews WHERE issue_id = %s ORDER BY severity DESC, review_id LIMIT %s",
                 (issue_id, max(1, min(limit, 20))))


@app.get("/api/cost")
def cost():
    return query("SELECT * FROM dashboard.v_cost_summary")
