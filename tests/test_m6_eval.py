"""M6 (offline): --max-rank prefix runs + the prefix escalation cap, dev_100 disagreement prep, threshold sweep, golden
evaluation metrics, spend-to-date de-duplication across DB copies, the label tool's disagreements mode."""
import csv
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from pipeline import cli
from pipeline.agents import router
from pipeline.db import queue
from pipeline.db.store import connect, migrate

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


m6 = load("m6", ROOT / "scripts" / "m6.py")
label_tool = load("label_tool", ROOT / "scripts" / "label_tool.py")


def make_csv(path, n):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["review_id", "review_text", "review_rating", "review_likes", "app_version", "review_timestamp"])
        for i in range(n):
            w.writerow([f"00000000-0000-0000-0000-{i:012d}", f"review number {i} the app keeps crashing", "3", "0",
                        "8.9.1", "2023-05-01 10:00:00"])
    return path


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "p.db"
    from pipeline import pilot
    pilot.ingest_pilot(path, make_csv(tmp_path / "in.csv", 120))
    return path


def test_max_rank_limits_claims_and_the_escalation_cap(db, tmp_path):
    rc = cli.main(["enrich", "--db", str(db), "--root", str(tmp_path), "--provider", "fake", "--max-rank", "40",
                   "--batch-size", "10", "--label-config", "cfg-x", "--fake-script", "low_conf"])
    assert rc == 0
    conn = connect(db)
    ranks = [r[0] for r in conn.execute("SELECT scope_rank FROM work_items WHERE label_config='cfg-x' AND status='done'")]
    assert ranks and max(ranks) <= 40 and len(ranks) == 40
    assert conn.execute("SELECT COUNT(*) FROM work_items WHERE label_config='cfg-x' AND status='pending'").fetchone()[0] == 80
    # the cap counts only the prefix: floor(0.15 × 40) = 6, not floor(0.15 × 120) = 18
    assert router.escalation_allowance(conn, "enrich", "cfg-x", 0.15, 40) == 6
    esc = conn.execute("SELECT COUNT(*) FROM agent_outputs WHERE label_config='cfg-x' AND agent='A4' "
                       "AND json_extract(output_json, '$.route')='escalate'").fetchone()[0]
    assert esc <= 6
    assert queue.claim(conn, "probe", "enrich", "cfg-x", 50, max_rank=40) == []
    assert not queue.has_open_work(conn, "enrich", "cfg-x", 40) and queue.has_open_work(conn, "enrich", "cfg-x")
    run = json.loads(conn.execute("SELECT config_json FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()[0])
    assert run["max_rank"] == 40  # resume inherits it
    conn.close()


def rev(rid, topic=("playback", 0.9), haiku=None, sev=("3", 0.8), intent=("complaint", 0.9)):
    def f(v):
        return {"top2": [[v[0], v[1]], ["other", 1 - v[1]]], "p_top": v[1]}
    return {"review_id": rid, "a3": {"topic": f(topic), "intent": f(intent), "severity": f(sev)},
            "a3_sentiment": -0.4, "a3_confidence": {"topic": topic[1], "intent": intent[1], "severity": sev[1]},
            "haiku": haiku or {"topic": topic[0], "intent": intent[0], "severity": sev[0]}, "haiku_needs_review": False}


def test_sweep_takes_haiku_below_the_gate_only():
    revs = [rev("a", topic=("playback", 0.55), haiku={"topic": "other", "intent": "complaint", "severity": "3"}),
            rev("b", topic=("billing", 0.65), haiku={"topic": "playback", "intent": "complaint", "severity": "3"})]
    refs = {"a": m6.reference({"topic": "other", "intent": "complaint", "severity": "3", "accepted_topics": "other",
                               "accepted_intents": "complaint", "accepted_severities": "3", "notes": ""}),
            "b": m6.reference({"topic": "billing", "intent": "complaint", "severity": "3", "accepted_topics": "billing",
                               "accepted_intents": "complaint", "accepted_severities": "3", "notes": "[model-agreement] x"})}
    assert refs["a"]["adjudicated"] and not refs["b"]["adjudicated"]
    g50, g60, g70 = (m6.sweep_rows(revs, refs, g)["topic"] for g in (0.50, 0.60, 0.70))
    assert (g50["accepted"], g60["accepted"], g70["accepted"]) == (1, 2, 1)  # a needs Haiku; b must keep A3
    assert (g50["escalated"], g60["escalated"], g70["escalated"]) == (0, 1, 2)
    assert g60["adjudicated_n"] == 1 and g60["adjudicated_acc"] == 1.0
    assert m6.disputed(revs[0]) == ["topic"] and m6.disputed(rev("c")) == []


def test_dev_prepare_writes_agreements_and_lists_disagreements(tmp_path, monkeypatch):
    src = tmp_path / "to_label.csv"
    with open(src, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(label_tool.SOURCE_FIELDS + label_tool.LABEL_COLUMNS)
        w.writerow(["r1", "The app keeps crashing on playback", "2", "0", "", "2023-01-01 00:00:00"] + [""] * 7)
        w.writerow(["r2", "Too expensive for students", "2", "0", "", "2023-01-01 00:00:00"] + [""] * 7)
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"reviews": [
        rev("r1"), rev("r2", topic=("billing", 0.58), haiku={"topic": "other", "intent": "complaint", "severity": "3"})]}))
    for k, v in {"DEV_TO_LABEL": src, "DEV_LABELED": tmp_path / "out.csv", "ANSWERS": answers,
                 "DISAGREEMENTS": tmp_path / "dis.json"}.items():
        monkeypatch.setattr(m6, k, v)
    assert m6.cmd_dev_prepare(None) == 0
    rows = {r["review_id"]: r for r in m6.read_csv(tmp_path / "out.csv")}
    assert rows["r1"]["topic"] == "playback" and rows["r1"]["notes"].startswith("[model-agreement]")
    assert rows["r1"]["evidence_quote"] in rows["r1"]["review_text"]
    assert rows["r2"]["topic"] == ""  # left for Rory
    dis = json.loads((tmp_path / "dis.json").read_text())["reviews"]
    assert [d["review_id"] for d in dis] == ["r2"] and dis[0]["hint"]["topic"] == {"celeris": "billing", "p": 0.58,
                                                                                   "haiku": "other"}
    assert "topic" not in dis[0]["prefill"] and dis[0]["prefill"]["intent"] == "complaint"
    # the label tool's disagreements mode shows only r2, with the hint and the pre-filled agreed fields
    store = label_tool.Store(src, tmp_path / "out.csv", {d["review_id"]: d for d in dis})
    items = store.items()
    assert [i["review_id"] for i in items] == ["r2"] and items[0]["hint"] and items[0]["labels"] is None
    # rerun keeps existing rows (never overwrites a saved label)
    assert m6.cmd_dev_prepare(None) == 0


def golden_row(rid, text, **kw):
    base = {"review_id": rid, "review_text": text, "topic": "playback", "intent": "complaint", "sentiment": "-0.5",
            "severity": "3", "entities": "[]", "evidence_quote": text[:10], "needs_review": "false",
            "accepted_topics": "playback", "accepted_intents": "complaint", "accepted_severities": "3|4"}
    return {**base, **kw}


def rec(**kw):
    base = {"status": "completed", "topic": "playback", "intent": "complaint", "sentiment": -0.5, "severity": 4,
            "entities_json": "[]", "needs_review": 0}
    return {**base, **kw}


def test_golden_metrics():
    t = "Songs stop playing after a minute every time"
    rows = [golden_row("g1", t), golden_row("g2", t, topic="billing", accepted_topics="billing|playback"),
            golden_row("g3", t, needs_review="true"), golden_row("g4", t)]
    recs = {"g1": rec(evidence_quote=t[:5]), "g2": rec(evidence_quote="not in text", topic="catalog", needs_review=1),
            "g3": rec(evidence_quote=t[20:30], sentiment=0.25)}
    s, per, dis = m6.golden_eval(rows, recs, {"g1": "accept", "g2": "escalate", "g3": "accept"})
    assert s["evaluated"] == 3 and s["missing"] == 1
    assert s["topic"]["accepted"] == pytest.approx(2 / 3)
    assert s["severity"]["exact"] == 0 and s["severity"]["accepted"] == 1 and s["severity"]["mae"] == 1
    assert s["sentiment"]["within_tolerance"] == pytest.approx(2 / 3)
    assert s["quote"]["membership"] == pytest.approx(2 / 3) and s["quote"]["support_overlaps_gold"] == pytest.approx(1 / 3)
    nr = s["needs_review_as_error_predictor"]
    assert (nr["flagged"], nr["reviews_with_an_error"], nr["flagged_and_wrong"]) == (1, 1, 1)
    assert s["ambiguous_cases"] == 3  # every row has an accepted alternative here
    assert {d["field"] for d in dis} == {"topic", "sentiment"}
    assert ["billing", "catalog", 1] in s["confusion"]["topic"]


def test_spend_to_date_counts_each_request_once(tmp_path):
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    for p in (a, b):
        conn = connect(p)
        migrate(conn)
        with conn:
            conn.execute("INSERT INTO ledger (request_id, provider, bucket, kind, usd, created_at) VALUES "
                         "('x','celeris','pilots','reserve',0.2,'t'), ('x','celeris','pilots','settle',0.1,'t')")
        conn.close()
    conn = sqlite3.connect(b)
    with conn:
        conn.execute("INSERT INTO ledger (request_id, provider, bucket, kind, usd, created_at) VALUES "
                     "('y','anthropic','pilots','reserve',0.05,'t'), ('z','anthropic','pilots','reserve',0.3,'t'), "
                     "('z','anthropic','pilots','release',0.3,'t')")
    conn.close()
    total, by = m6.spend_to_date([a, b, tmp_path / "missing.db"])
    assert total == pytest.approx(0.15) and by == {"celeris": pytest.approx(0.1), "anthropic": pytest.approx(0.05)}


def test_dev_escalate_refuses_without_money_flags():
    with pytest.raises(SystemExit, match="refused"):
        m6.main(["dev-escalate"])
    with pytest.raises(SystemExit, match="refused"):
        m6.main(["dev-enrich"])


def test_frozen_config_escalates_severity_only(db, tmp_path):
    from pipeline.stages import make_label_config
    old = make_label_config("celeris-1-decision", "A", 0.6, "claude-haiku-5-5", 0.15)
    assert "escfields" not in old  # earlier strings unchanged
    assert make_label_config("celeris-1-decision", "A", 0.6, "claude-haiku-5-5", 0.15, ("topic", "intent", "severity")) == old
    frozen = cli.default_label_config(type("A", (), {"provider": "celeris", "escalation": "anthropic", "shape": "A",
                                                     "gate": 0.70, "esc_fraction": 0.40, "esc_fields": "severity"})())
    assert frozen == ("celeris-1-decision+shapeA+gate0.70+esc-claude-haiku-5-5-0.40+escfields-severity"
                      "+extract-code-v1+prompt-v1+schema-v1")
    d = {"probs": {"topic": {"playback": 0.4, "other": 0.6}, "intent": {"complaint": 0.5, "praise": 0.5},
                   "severity": {"3": 0.55, "2": 0.45}}}
    assert router.uncertain_fields(d, 0.70, ("severity",)) == ["severity"]
    # end to end on the fake provider with the CLI defaults (= the frozen config): A5 only ever answers severity
    rc = cli.main(["enrich", "--db", str(db), "--root", str(tmp_path), "--provider", "fake", "--batch-size", "20",
                   "--label-config", "cfg-frozen", "--fake-script", "low_conf"])
    assert rc == 0
    conn = connect(db)
    routes = [json.loads(r[0]) for r in conn.execute("SELECT output_json FROM agent_outputs WHERE agent='A4' "
                                                     "AND label_config='cfg-frozen'")]
    assert routes and all(set(r["fields"]) <= {"severity"} for r in routes)
    assert {r["gate"] for r in routes} == {0.70}
    assert sum(r["route"] == "escalate" for r in routes) <= router.escalation_allowance(conn, "enrich", "cfg-frozen", 0.40)
    conn.close()
