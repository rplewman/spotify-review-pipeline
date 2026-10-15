"""M7 gate (PLAN §5 M7): resume evidence and the local copy of the checker's resume rules. Offline, no spend.

- recovery.resume_rules agrees with check_submission's resume block on good and planted-bad evidence
- scripts/resume_demo.py: real interrupt (Ctrl-Break on Windows / SIGINT) → checkpoint_before → resume → checkpoint_after
  → rules pass, and no resume call re-sends a checkpointed ID
- synthetic corpora are SYNTH- marked and refused on an existing DB
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

from pipeline import recovery, synthetic
from pipeline.db.store import connect, immediate
from pipeline.orchestrator import Orchestrator, RunConfig
from pipeline.providers.fake import FakeProvider
from pipeline.stages import PassthroughStage

ROOT = Path(__file__).resolve().parents[1]
CFG = "test-v1"
FAST = dict(backoff_base=0.01, idle_poll=0.02, lease_seconds=30)


def run(db, phase, limit=None):
    cfg = RunConfig(label_config=CFG, batch_size=5, phase=phase, limit=limit, **FAST)
    return asyncio.run(Orchestrator(db, PassthroughStage(FakeProvider(), CFG), cfg).run())


@pytest.fixture
def resumed(tmp_path):
    db = synthetic.build_db(tmp_path / "s.db", synthetic.default_texts(60), CFG)
    run(db, "initial", limit=20)
    conn = connect(db)
    before = list(recovery.completed(conn, CFG))
    run(db, "resume")
    return conn, before, list(recovery.completed(conn, CFG))


def test_resume_rules_pass_on_a_real_resume(resumed):
    conn, before, after = resumed
    r = recovery.resume_rules(conn, before, after)
    assert r["ok"], r
    assert r["before"] < r["after"] and r["before_unlogged_in_initial"] == 0 and r["new_ids_in_resume_calls"] > 0
    assert all(rid.startswith(synthetic.PREFIX) for rid in after)


def test_resume_rules_flag_planted_violations(resumed):
    conn, before, after = resumed
    assert "resume_snapshot_mismatch" in recovery.resume_rules(conn, [], after)["flags"]
    assert "resume_snapshot_mismatch" in recovery.resume_rules(conn, before, before)["flags"]  # nothing new
    assert "resume_snapshot_mismatch" in recovery.resume_rules(conn, before, after + ["SYNTH-99999"])["flags"]
    assert "duplicate_checkpoint_id (before)" in recovery.resume_rules(conn, before + before[:1], after)["flags"]
    with immediate(conn):  # a resume call that re-sends a checkpointed ID
        conn.execute("INSERT INTO calls (request_id, role, agent, model, phase, outcome, label_config, review_ids_json) "
                     "VALUES ('bad', 'enrich', 'A3', 'fake', 'resume', 'failed', ?, ?)", (CFG, json.dumps(before[:1])))
    assert any(f.startswith("reprocessed_checkpoint") for f in recovery.resume_rules(conn, before, after)["flags"])
    with immediate(conn):  # a "before" ID with no succeeded initial call
        conn.execute("DELETE FROM calls WHERE phase='initial'")
    assert "resume_call_evidence" in recovery.resume_rules(conn, before, after)["flags"]


def test_checkpoint_file_has_the_checker_key(tmp_path):
    body = recovery.write_checkpoint_file(tmp_path / "c.json", ["b", "a"], run_id="r")
    assert recovery.read_ids(tmp_path / "c.json") == ["a", "b"] and body["count"] == 2


def test_synthetic_db_is_built_fresh(tmp_path):
    db = synthetic.build_db(tmp_path / "s.db", ["one", "  "])
    with pytest.raises(SystemExit):
        synthetic.build_db(db, ["two"])
    conn = connect(db)
    assert dict(conn.execute("SELECT review_id, status FROM records").fetchall()) == {"SYNTH-00001": "quarantined"}


def test_resume_demo_interrupts_and_resumes_cleanly(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import resume_demo
    out = tmp_path / "recovery"
    code = resume_demo.main(["--rehearse", "400", "--db", str(tmp_path / "r.db"), "--out", str(out), "--batches", "2",
                             "--resume-batches", "2", "--timeout", "120",
                             "--enrich-args", "--provider fake --workers 2 --batch-size 10 --fake-latency 0.15"])
    result = json.loads((out / "resume_check.json").read_text(encoding="utf-8"))
    assert code == 0, result
    assert result["initial_run"]["stop_reason"] == "interrupted"  # a real interrupt, cleaned up (not a crash)
    assert result["resume_run"]["phase"] == "resume" and result["resume_run"]["stop_reason"] == "drained"
    assert result["check_at_after"]["ok"] and result["check_at_end"]["ok"]
    assert result["check_at_end"]["resume_calls_with_before_ids"] == []
    before, after = (recovery.read_ids(out / f"checkpoint_{k}.json") for k in ("before", "after"))
    assert before and set(before) < set(after)


def test_pause_file_stops_cleanly_and_resume_continues(tmp_path):
    """M8: a PAUSE file next to the DB stops the run before the next claim (no leases held); `resume` finishes it."""
    db = synthetic.build_db(tmp_path / "s.db", synthetic.default_texts(30), CFG)
    (tmp_path / "PAUSE").touch()
    assert run(db, "initial")["stop_reason"] == "paused"
    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) FROM work_items WHERE status='leased'").fetchone()[0] == 0
    (tmp_path / "PAUSE").unlink()
    assert run(db, "resume")["stop_reason"] == "drained"
    assert len(recovery.completed(conn, CFG)) == 30


def test_finalize_fills_cache_quarantines_out_of_scope_and_reconciles(tmp_path):
    """M8 close-out: shared texts fan out, the rest of out_of_scope → out_of_scope_budget_100k, one record per ID."""
    from pipeline import finalize
    texts = synthetic.default_texts(30)
    texts = texts + texts[:5] + ["never sent text A", "never sent text B"]  # 5 duplicates + 2 unique out-of-scope
    db = synthetic.build_db(tmp_path / "s.db", texts, CFG)
    conn = connect(db)
    oos = [f"{synthetic.PREFIX}{i:05d}" for i in range(30, len(texts))]
    with immediate(conn):
        conn.executemany("UPDATE source_reviews SET scope='out_of_scope' WHERE review_id=?", [(r,) for r in oos])
        conn.executemany("DELETE FROM records WHERE review_id=?", [(r,) for r in oos])
        conn.execute("DELETE FROM work_items WHERE text_sha256 IN (SELECT text_sha256 FROM source_reviews "
                     "WHERE review_id IN (?, ?))", oos[-2:])
    run(db, "initial")
    with immediate(conn):  # as if the out-of-scope duplicates were never fanned out
        conn.executemany("DELETE FROM records WHERE review_id=?", [(r,) for r in oos])
    with pytest.raises(finalize.FinalizeError):
        with immediate(conn):
            conn.execute("UPDATE runs SET ended_at=NULL")
        finalize.finalize(conn, CFG, ingestion_path=tmp_path / "none.json")
    with immediate(conn):
        conn.execute("UPDATE runs SET ended_at='x'")
    r = finalize.finalize(conn, CFG, ingestion_path=tmp_path / "none.json")
    assert r["cache_fill_rows_written"] >= 5 and r["quarantined_out_of_scope"] == 2
    rec = r["reconcile"]
    assert rec["ok"] and rec["records"] == rec["source_rows"] == len(texts)
    assert rec["quarantined_by_reason"].get(finalize.OUT_OF_SCOPE_REASON) == 2
    assert finalize.finalize(conn, CFG, ingestion_path=tmp_path / "none.json")["quarantined_out_of_scope"] == 0
