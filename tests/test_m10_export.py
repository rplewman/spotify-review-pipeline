"""M10 gate (PLAN §5): the grading export has the contract shape, drops only superseded-config enrich successes,
marks missing usage as unavailable (0, never invented), copies the M9/M8 files unchanged and is byte-deterministic."""
import gzip
import json

from pipeline import export
from pipeline.db.store import connect, migrate

CFG, OLD = "cfg-final", "cfg-old"


def build(tmp_path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    for i, rid in enumerate(("a", "b", "c")):
        conn.execute("INSERT INTO source_reviews VALUES (?,?,?,?,?,?,?,?,?,?,1,'random_100k',?)",
                     (rid, i + 1, f"sha-{rid}", "t", "s", "great app", "5", "0", "1", "2023-01-01", i + 1))
    conn.execute("""INSERT INTO records VALUES ('a','sha-a','completed',NULL,'other','praise',0.5,1,'[]','great',0,?,NULL)""",
                 (CFG,))
    conn.execute("""INSERT INTO records VALUES ('b','sha-b','completed',NULL,'other','praise',0.5,1,'[]','great',0,?,'a')""",
                 (CFG,))
    conn.execute("INSERT INTO records (review_id, source_sha256, status, reason) VALUES ('c','sha-c','quarantined','x')")
    calls = [("r1", "enrich", "A3", "initial", "succeeded", CFG, 10, 5, 1),
             ("r0", "enrich", "A3", "initial", "succeeded", OLD, 10, 5, 1),
             ("r2", "enrich", "A3", "initial", "failed", CFG, None, None, 0),
             ("r3", "verify", "A8", "initial", "succeeded", CFG, 7, 3, 1)]
    for rid, role, agent, phase, outcome, cfg, tin, tout, avail in calls:
        conn.execute("""INSERT INTO calls (request_id, role, agent, model, phase, outcome, label_config, review_ids_json,
                        input_tokens, output_tokens, usage_available, started_at) VALUES (?,?,?,'m',?,?,?,'["a"]',?,?,?,?)""",
                     (rid, role, agent, phase, outcome, cfg, tin, tout, avail, rid))
    root = tmp_path / "root"
    for src in export.COPIED.values():
        (root / src).parent.mkdir(parents=True, exist_ok=True)
        (root / src).write_text(f"copy of {src}\n", encoding="utf-8")
    out = tmp_path / "grading"
    out.mkdir()
    (out / "ingestion.json").write_text(json.dumps({"counts": {"records": 3}, "file_sha256": "f"}), encoding="utf-8")
    return conn, root, out


def read_gz(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def test_export_shapes_filters_and_is_deterministic(tmp_path):
    conn, root, out = build(tmp_path)
    (out / "records.jsonl").write_text("stale\n", encoding="utf-8")
    r = export.export(conn, CFG, root, out)
    assert not (out / "records.jsonl").exists()  # a plain + .gz pair would be flagged ambiguous
    run = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert run["analysis_count"] == 3 and run["analysis_sha256"] == "f" and run["allow_multi_issue"] is False
    assert run["classification_input_fields"] == ["review_text"]
    recs = {x["review_id"]: x for x in read_gz(out / "records.jsonl.gz")}
    assert recs["a"]["needs_review"] is False and recs["a"]["entities"] == [] and type(recs["a"]["severity"]) is int
    assert "cache_source_id" not in recs["a"] and recs["b"]["cache_source_id"] == "a"
    assert recs["c"] == {"review_id": "c", "source_sha256": "sha-c", "status": "quarantined", "reason": "x"}
    calls = {c["request_id"]: c for c in read_gz(out / "calls.jsonl.gz")}
    assert set(calls) == {"r1", "r2", "r3"} and r["calls_excluded_superseded_config"] == 1
    assert calls["r2"]["input_tokens"] == 0 and calls["r2"]["usage_available"] is False
    assert calls["r1"]["usage_available"] is True
    for name, src in export.COPIED.items():
        assert (out / name).read_bytes() == (root / src).read_bytes()
    first = {n: (out / n).read_bytes() for n in ("records.jsonl.gz", "calls.jsonl.gz", "run.json")}
    export.export(conn, CFG, root, out)
    assert first == {n: (out / n).read_bytes() for n in first}
