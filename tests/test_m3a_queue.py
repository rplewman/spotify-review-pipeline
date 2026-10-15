"""M3a gate (PLAN §5): queue, cache, ledger, limiter and orchestrator with the FakeProvider. Offline, no spend.

- 4 threads and 4 async workers (and one test with two processes) on one queue → no item processed twice, none lost
- kill -9 mid-run → resume with no re-paid IDs; an expired lease is reclaimed; Ctrl-C saves and exits
- the ledger refuses work before the cap; a cache hit makes no call; scripted provider failures follow the policy
"""
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import pytest

from pipeline import cli
from pipeline.agents import cache, intake
from pipeline.check_submission import FIELDS
from pipeline.db import ledger, limiter, queue
from pipeline.db.store import connect, immediate, migrate
from pipeline.orchestrator import Orchestrator, RunConfig
from pipeline.providers.fake import FakeProvider, fake_labels
from pipeline.schema import validate_record
from pipeline.stages import PassthroughStage

ROOT = Path(__file__).resolve().parents[1]
CFG = "test-v1"
FAST = dict(backoff_base=0.01, idle_poll=0.02, lease_seconds=30)


def build_db(tmp_path, n=150, scope=110):
    """Synthetic corpus: n rows, ~1/5 exact duplicates (some outside the scope), 2 empty texts."""
    rows = []
    for i in range(n):
        if i in (7, 77):
            text = "  "
        elif i % 5 == 4:
            text = f"Duplicate text number {i % 3}. Ads are annoying"
        else:
            text = f"Review {i}: the app {'crashes' if i % 2 else 'is great'}.\nSecond line {i}"
        rows.append([f"id{i:04d}", text, str(1 + i % 5), "0", "8.1", f"2022-{6 + i % 6:02d}-01 10:00:00"])
    csv_path = tmp_path / "mini.csv"
    import csv
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(FIELDS)
        w.writerows(rows)
    db = tmp_path / "t.db"
    conn = intake.connect(db)
    intake.apply_schema(conn)
    intake.load_source(conn, csv_path)
    intake.assign_scope(conn, size=scope)
    intake.build_texts(conn)
    intake.quarantine_empty(conn)
    intake.enqueue(conn, "enrich", CFG)
    conn.close()
    return db


def run(db, provider, tmp_path=None, **kw):
    cfg = RunConfig(label_config=CFG, **{**FAST, **kw})
    orch = Orchestrator(db, PassthroughStage(provider, CFG), cfg, root=tmp_path)
    return asyncio.run(orch.run())


def succeeded_ids(conn, agent="A3"):
    """Review IDs per succeeded call of one agent (M6b: A5 now sees every review too, in its own call)."""
    ids = Counter()
    for (j,) in conn.execute("SELECT review_ids_json FROM calls WHERE outcome='succeeded' AND agent=?", (agent,)):
        ids.update(json.loads(j))
    return ids


def assert_complete_and_unique(conn):
    statuses = queue.counts(conn, "enrich", CFG)
    assert statuses == {"done": conn.execute("SELECT COUNT(*) FROM texts").fetchone()[0]}, statuses
    ids = succeeded_ids(conn)
    assert ids and max(ids.values()) == 1, [k for k, v in ids.items() if v > 1]
    a5 = succeeded_ids(conn, "A5")
    assert not a5 or max(a5.values()) == 1, [k for k, v in a5.items() if v > 1]
    originals = {r[0] for r in conn.execute("SELECT original_review_id FROM texts")}
    assert set(ids) == originals  # every distinct text was paid for exactly once, via its original ID


# ---------- queue ----------
def test_threads_claiming_concurrently_never_share_an_item(tmp_path):
    db = build_db(tmp_path)
    got, errors = [], []

    def worker(n):
        conn = connect(db)
        try:
            while items := queue.claim(conn, f"t{n}", "enrich", CFG, 7):
                with immediate(conn):
                    mine = queue.held(conn, f"t{n}", [i["id"] for i in items])
                    queue.mark_done(conn, mine)
                got.extend(mine)
        except Exception as e:  # pragma: no cover - surfaced below
            errors.append(e)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    conn = connect(db)
    assert not errors
    assert len(got) == len(set(got)) == conn.execute("SELECT COUNT(*) FROM work_items").fetchone()[0]


def test_expired_lease_is_reclaimed_and_the_old_owner_is_fenced_out(tmp_path):
    db = build_db(tmp_path)
    conn = connect(db)
    a = queue.claim(conn, "A", "enrich", CFG, 3, lease_seconds=-1)  # A "crashed": its lease is already expired
    b = queue.claim(conn, "B", "enrich", CFG, 3, lease_seconds=60)
    assert [i["id"] for i in a] == [i["id"] for i in b]
    assert b[0]["attempts"] == 2
    with immediate(conn):
        assert queue.held(conn, "A", [i["id"] for i in a]) == set()  # A's late commit would change nothing
        assert queue.held(conn, "B", [i["id"] for i in b]) == {i["id"] for i in b}
    assert queue.claim(conn, "C", "enrich", CFG, 3)[0]["id"] not in {i["id"] for i in b}


# ---------- orchestrator ----------
def test_four_async_workers_process_every_item_once_and_fan_out(tmp_path):
    db = build_db(tmp_path)
    s = run(db, FakeProvider(latency=0.005), tmp_path, workers=4, batch_size=6)
    assert s["stop_reason"] == "drained"
    conn = connect(db)
    assert_complete_and_unique(conn)
    # every nonempty row (in scope or not) has a completed record; duplicates point directly at the original
    texts = dict(conn.execute("SELECT text_sha256, original_review_id FROM texts"))
    n_rows = 0
    for rid, sha, src, text, *rec in conn.execute("""
            SELECT s.review_id, s.text_sha256, s.source_sha256, s.review_text, r.status, r.topic, r.intent, r.sentiment,
                   r.severity, r.entities_json, r.evidence_quote, r.needs_review, r.label_config, r.cache_source_id
            FROM source_reviews s JOIN records r USING (review_id) WHERE s.scope != 'empty_text'"""):
        n_rows += 1
        status, topic, intent, sentiment, sev, ents, quote, nr, cfg, src_id = rec
        d = {"review_id": rid, "source_sha256": src, "status": status, "topic": topic, "intent": intent,
             "sentiment": sentiment, "severity": sev, "entities": json.loads(ents), "evidence_quote": quote,
             "needs_review": bool(nr), "label_config": cfg}
        if src_id:
            d["cache_source_id"] = src_id
        assert validate_record(d, text) == []
        assert src_id == (None if rid == texts[sha] else texts[sha])
    assert n_rows == conn.execute("SELECT COUNT(*) FROM source_reviews WHERE text_sha256 IN "
                                  "(SELECT text_sha256 FROM texts)").fetchone()[0]
    assert conn.execute("SELECT COUNT(*) FROM records WHERE cache_source_id IS NOT NULL AND review_id IN "
                        "(SELECT review_id FROM source_reviews WHERE scope='out_of_scope')").fetchone()[0] > 0
    t = ledger.totals(conn)
    assert t["reserved"] == 0 and t["spent"] > 0
    assert json.loads((tmp_path / "outputs" / "run_log.jsonl").read_text().splitlines()[-1])["stop_reason"] == "drained"


def test_two_processes_on_one_queue(tmp_path):
    db = build_db(tmp_path, n=300, scope=260)
    cmd = [sys.executable, "-m", "pipeline.cli", "enrich", "--db", str(db), "--root", str(tmp_path),
           "--label-config", CFG, "--workers", "2", "--batch-size", "4", "--fake-latency", "0.01"]
    procs = [subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT) for _ in range(2)]
    outs = [p.communicate(timeout=120)[0].decode() for p in procs]
    assert all(p.returncode == 0 for p in procs), outs
    conn = connect(db)
    assert_complete_and_unique(conn)
    per_run = dict(conn.execute("SELECT run_id, COUNT(*) FROM calls GROUP BY run_id").fetchall())
    assert len(per_run) == 2 and min(per_run.values()) > 0, per_run  # both processes really did work


def wait_for(pred, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


def test_kill_mid_run_then_resume_repays_nothing(tmp_path):
    db = build_db(tmp_path)
    cmd = [sys.executable, "-m", "pipeline.cli", "enrich", "--db", str(db), "--root", str(tmp_path),
           "--label-config", CFG, "--workers", "1", "--batch-size", "5", "--lease-seconds", "1.5",
           "--fake-script", "valid,valid,valid,hang"]
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    conn = connect(db)
    hung = lambda: conn.execute("SELECT COUNT(*) FROM calls WHERE outcome='in_flight'").fetchone()[0] == 1  # noqa: E731
    try:
        assert wait_for(hung), "the 4th call never started"
    finally:
        proc.kill()  # hard kill: no cleanup runs
        proc.wait()
    before = conn.execute("SELECT COUNT(*) FROM records WHERE status='completed' AND label_config=?", (CFG,)).fetchone()[0]
    assert before > 0 and queue.counts(conn, "enrich", CFG).get("leased") == 5
    time.sleep(2.0)  # heartbeat goes stale
    assert cli.main(["resume", "--db", str(db), "--root", str(tmp_path), "--lease-seconds", "1.5"]) == 0
    first, second = conn.execute("SELECT run_id, phase, stop_reason FROM runs ORDER BY started_at").fetchall()
    assert first[1:] == ("initial", "crashed") and second[1:] == ("resume", "drained")
    assert conn.execute("SELECT outcome, error FROM calls WHERE run_id=? ORDER BY started_at DESC LIMIT 1",
                        (first[0],)).fetchone() == ("failed", "interrupted (no response recorded)")
    assert {r[0] for r in conn.execute("SELECT DISTINCT phase FROM calls WHERE run_id=?", (second[0],))} == {"resume"}
    assert_complete_and_unique(conn)  # the 15 IDs paid before the kill were not paid again
    assert ledger.totals(conn)["reserved"] == 0
    labels = [r[0] for r in conn.execute("SELECT label FROM checkpoints ORDER BY id")]
    assert labels == ["resume_start", "resume_end:drained"]


def test_ctrl_c_releases_leases_and_closes_the_attempt(tmp_path):
    db = build_db(tmp_path)
    cfg = RunConfig(label_config=CFG, workers=2, batch_size=5, **FAST)
    orch = Orchestrator(db, PassthroughStage(FakeProvider(script=["valid", "hang", "hang"]), CFG), cfg)

    async def main():
        task = asyncio.create_task(orch.run())
        conn = connect(db)
        while conn.execute("SELECT COUNT(*) FROM calls WHERE outcome='in_flight'").fetchone()[0] < 2:
            await asyncio.sleep(0.02)
        task.cancel()  # what asyncio.run does on Ctrl-C
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    conn = connect(db)
    assert orch.summary["stop_reason"] == "interrupted"
    assert "leased" not in queue.counts(conn, "enrich", CFG)
    assert dict(conn.execute("SELECT outcome, COUNT(*) FROM calls GROUP BY 1").fetchall()) == {"succeeded": 1, "failed": 2}
    assert ledger.totals(conn)["reserved"] == 0  # interrupted attempts settled at their estimate, never left open


def test_ledger_refuses_work_before_the_cap(tmp_path):
    db = build_db(tmp_path)
    provider = FakeProvider()
    conn = connect(db)
    one = PassthroughStage(provider, CFG).estimate_usd(queue.claim(conn, "probe", "enrich", CFG, 5))
    queue.release_owner(conn, "probe")
    cap = one * 3.5
    s = run(db, provider, workers=2, batch_size=5, max_spend=cap)
    assert s["stop_reason"] == "budget"
    t = ledger.totals(conn)
    assert t["reserved"] == 0 and 0 < t["spent"] <= cap
    assert conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0] >= 3
    assert queue.counts(conn, "enrich", CFG)["pending"] > 0 and "leased" not in queue.counts(conn, "enrich", CFG)
    # unit: a reservation that would pass a cap is refused and writes nothing
    with pytest.raises(ledger.BudgetExceeded):
        ledger.reserve(conn, "x", "fake", "pilots", 1.0, ledger.caps_for("pilots", "r", max_spend=0.5), "r")
    assert conn.execute("SELECT COUNT(*) FROM ledger WHERE request_id='x'").fetchone()[0] == 0


def test_cache_hit_makes_no_call(tmp_path):
    db = build_db(tmp_path)
    conn = connect(db)
    sha, rid, text = conn.execute("SELECT text_sha256, original_review_id, review_text FROM texts ORDER BY scope_rank "
                                  "LIMIT 1").fetchone()
    with immediate(conn):
        cache.write_result(conn, sha, CFG, fake_labels(text), "earlier-run")
    provider = FakeProvider()
    run(db, provider, batch_size=5)
    assert rid not in {r for _, ids, _ in provider.calls for r in ids}
    assert queue.counts(conn, "enrich", CFG) == {"done": conn.execute("SELECT COUNT(*) FROM texts").fetchone()[0]}
    assert conn.execute("SELECT status FROM records WHERE review_id=?", (rid,)).fetchone() == ("completed",)
    # and a different label_config never reuses it
    with immediate(conn):
        assert cache.cached(conn, [sha], CFG) == {sha} and cache.cached(conn, [sha], "other-cfg") == set()


def test_scripted_failures_follow_the_policy(tmp_path):
    db = build_db(tmp_path)
    conn = connect(db)
    # the last two texts in claim order, so the call-level script (first 5 calls) cannot touch them
    first = [r[0] for r in conn.execute("SELECT original_review_id FROM texts ORDER BY scope_rank DESC LIMIT 2")]
    provider = FakeProvider(script=["429", "timeout", "malformed", "no_usage", "low_conf"],
                            per_review={first[0]: ["injected", "injected"], first[1]: ["malformed", "valid"]})
    s = run(db, provider, tmp_path, batch_size=5)
    assert s["stop_reason"] == "drained"
    calls = conn.execute("SELECT outcome, error, usage_available, input_tokens, cost_usd, est_usd FROM calls "
                         "ORDER BY started_at").fetchall()
    assert calls[0][:2] == ("failed", "429 transient: fake 429") and calls[0][4] == 0.0  # not charged → released
    assert calls[1][0] == "failed" and calls[1][4] == calls[1][5]                       # 408: billing unknown → estimate
    assert calls[2][:2] == ("failed", "unparseable output")
    no_usage = [c for c in calls if c[0] == "succeeded" and c[2] == 0]
    assert no_usage and no_usage[0][3] == 0 and no_usage[0][4] == no_usage[0][5]        # tokens 0 + flag, cost = estimate
    assert "ignored unknown id 'INJECTED-UNKNOWN-ID'" in (conn.execute(
        "SELECT error FROM calls WHERE error LIKE '%INJECTED%' LIMIT 1").fetchone() or [""])[0]
    # the injected review is invalid twice → quarantined (with its duplicates); the once-malformed one recovered
    assert conn.execute("SELECT status, reason FROM records WHERE review_id=?", (first[0],)).fetchone()[0] == "quarantined"
    assert conn.execute("SELECT status FROM records WHERE review_id=?", (first[1],)).fetchone() == ("completed",)
    q = [json.loads(x) for x in (tmp_path / "outputs" / "quarantine.jsonl").read_text().splitlines()]
    assert [x["review_id"] for x in q] == [first[0]] and q[0]["invalid_attempts"] == 2
    assert conn.execute("SELECT COUNT(*) FROM results WHERE probs_json LIKE '%0.4%'").fetchone()[0] > 0  # low_conf kept
    assert ledger.totals(conn)["reserved"] == 0


def test_402_stops_everything_and_saves(tmp_path):
    db = build_db(tmp_path)
    s = run(db, FakeProvider(script=["valid", "402"]), workers=1, batch_size=5)
    conn = connect(db)
    assert s["stop_reason"] == "quota_402"
    c = queue.counts(conn, "enrich", CFG)
    assert c["done"] == 5 and "leased" not in c and c["pending"] > 0
    assert conn.execute("SELECT attempts FROM work_items WHERE status='pending' ORDER BY scope_rank LIMIT 1"
                        ).fetchone() == (0,)  # the refused batch's claim did not count as an attempt


def test_transient_errors_give_up_after_max_attempts(tmp_path):
    db = build_db(tmp_path, n=6, scope=6)
    s = run(db, FakeProvider(default="timeout"), batch_size=50, max_attempts=3)
    conn = connect(db)
    assert s["stop_reason"] == "drained"
    assert set(queue.counts(conn, "enrich", CFG)) == {"failed"}
    assert conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 3


# ---------- limiter, calls shape, CLI guards, migration ----------
def test_rate_limiter_is_shared_and_honours_retry_after(tmp_path):
    conn = connect(tmp_path / "l.db")
    migrate(conn)
    t = 1000.0
    grants = [limiter.acquire(conn, "m", 10, rpm=60, tpm=1e6, now=t) for _ in range(12)]
    assert grants[:10] == [0.0] * 10 and grants[10] > 0  # burst = 10 s of 60 rpm
    other = connect(tmp_path / "l.db")
    assert limiter.acquire(other, "m", 10, rpm=60, tpm=1e6, now=t) > 0  # a second connection sees the same bucket
    assert limiter.acquire(other, "m", 10, rpm=60, tpm=1e6, now=t + 1.0) == 0.0
    with immediate(conn):
        limiter.block(conn, "m", 5.0, now=t + 2)
    assert 2.9 < limiter.acquire(conn, "m", 10, rpm=60, tpm=1e6, now=t + 4) <= 3.0


def test_calls_rows_fit_the_contract_shape(tmp_path):
    db = build_db(tmp_path)
    run(db, FakeProvider(script=["429"]), batch_size=50)
    conn = connect(db)
    rows = conn.execute("SELECT request_id, role, review_ids_json, model, phase, outcome, label_config, input_tokens, "
                        "output_tokens, tier FROM calls").fetchall()
    assert len({r[0] for r in rows}) == len(rows)
    for rid, role, ids, model, phase, outcome, cfg, tin, tout, tier in rows:
        ids = json.loads(ids)
        assert rid and role == "enrich" and model and phase in ("initial", "resume") and cfg == CFG
        assert outcome in ("succeeded", "failed") and tier in ("standard", "batch")
        assert 1 <= len(ids) <= 50 and len(set(ids)) == len(ids)
        assert type(tin) is int and type(tout) is int and tin >= 0 and tout >= 0


def test_cli_refuses_paid_provider_without_flags_and_fake_on_main_db():
    with pytest.raises(SystemExit, match="--paid and --max-spend"):
        cli.main(["enrich", "--provider", "celeris", "--db", "x.db"])
    with pytest.raises(SystemExit, match="--paid and --max-spend"):
        cli.main(["enrich", "--provider", "celeris", "--paid", "--db", "x.db"])
    with pytest.raises(SystemExit, match="own --db"):
        cli.main(["enrich", "--provider", "fake", "--db", str(ROOT / "state" / "pipeline.db")])


def test_migrate_adds_new_columns_to_an_old_db(tmp_path):
    conn = connect(tmp_path / "old.db")
    conn.execute("CREATE TABLE calls (request_id TEXT PRIMARY KEY, role TEXT NOT NULL, agent TEXT NOT NULL, "
                 "model TEXT NOT NULL, outcome TEXT NOT NULL, review_ids_json TEXT NOT NULL, run_id TEXT, started_at TEXT)")
    migrate(conn)
    migrate(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(calls)")}
    assert {"usage_available", "est_usd", "ended_at", "tier", "phase"} <= cols
