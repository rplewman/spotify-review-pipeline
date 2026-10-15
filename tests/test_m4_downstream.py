"""M4 gate (PLAN §5): verify → group → rank → memo, offline with the FakeProvider / FakeBatchProvider.

- the end-to-end run (`pipeline.cli run`) produces every output file; `rank` twice is byte-identical, with zero calls
- A8: the verifier request never contains the first prediction; the declared sample is seeded and grows as a superset;
  the Batch API runner submits once, resumes an open job by ID, requeues errored/expired requests, retries invalid
  output once; the planted errors are all flagged; the disagreement report matches the stored labels
- A9: every completed complaint/cancellation record is in exactly one issue; IDs are deterministic; unknown issue IDs
  from the model are rejected; a warm rerun makes zero calls; calls.jsonl will have a succeeded `group` call
- A10: the ranking equals the checker's own recomputation (§9.5) and the area roll-up adds up
- A11: invented issue/review IDs and numbers are rejected (failed call) → retried → accepted; claims match the ranking
"""
import csv
import gzip
import json
from pathlib import Path

import pytest

from pipeline import batch_runner, cli
from pipeline.agents import grouper, intake, memo, ranker, verifier
from pipeline.check_submission import FIELDS, mean_string
from pipeline.db.store import connect, migrate
from pipeline.oneshot import CallRejected, start_run
from pipeline.providers.fake import FakeBatchProvider, FakeProvider

from tests.test_m3b_agents import CFG, build_db, run

OUTPUT_FILES = ["outputs/enriched.jsonl.gz", "outputs/membership.csv", "outputs/issues.json", "outputs/ranking.csv",
                "outputs/aggregates.csv", "outputs/area_aggregates.csv", "outputs/overall_metrics.json",
                "outputs/memo_input.json", "outputs/memo.md", "outputs/claims.csv", "outputs/recommendations.json",
                "outputs/run_log.jsonl", "outputs/quarantine.jsonl", "evals/verify/verify_report.json",
                "evals/verify/verifier_predictions.jsonl", "evals/verify/disagreements.csv",
                "evals/verify/planted_errors.json"]


@pytest.fixture
def enriched(tmp_path):
    db = build_db(tmp_path, n=60)
    s = run(db, FakeProvider(), FakeProvider(model="fake-escalation"))
    assert s["stop_reason"] == "drained"
    return db


def calls(db, role=None):
    conn = connect(db)
    q = "SELECT role, outcome, tier, review_ids_json, error FROM calls" + (" WHERE role=?" if role else "")
    rows = conn.execute(q, (role,) if role else ()).fetchall()
    conn.close()
    return rows


def fast_batch(**kw):
    return batch_runner.BatchConfig(poll_seconds=0.0, **kw)


# ---------- A8 verifier ----------
def test_verifier_payload_never_contains_the_first_prediction(enriched):
    conn = connect(enriched)
    texts = [r[0] for r in conn.execute("SELECT review_text FROM texts ORDER BY scope_rank LIMIT 5")]
    a = verifier.build_payload(texts)
    # the payload is a function of the texts alone: change every stored label and it is identical
    conn.execute("UPDATE results SET topic='other', intent='unclear', severity=1, probs_json=NULL")
    assert verifier.build_payload(texts) == a
    blob = json.dumps(a)
    assert CFG not in blob and "probabilit" not in blob and "candidate" not in blob.lower()
    conn.close()


def test_sample_is_declared_seeded_and_grows(enriched):
    conn = connect(enriched)
    key = verifier.queue_key(CFG, "v")
    _, small = verifier.enqueue_sample(conn, CFG, key, 0.2, "s1")
    small_set = {r[0] for r in conn.execute("SELECT text_sha256 FROM work_items WHERE stage='verify'")}
    added, big = verifier.enqueue_sample(conn, CFG, key, 0.6, "s1")
    big_set = {r[0] for r in conn.execute("SELECT text_sha256 FROM work_items WHERE stage='verify'")}
    assert small_set < big_set and added == big - small and 0 < small < big
    _, again = verifier.enqueue_sample(conn, CFG, key, 0.6, "s1")
    assert again == big  # deterministic
    conn.close()


def test_batch_runner_submits_once_resumes_by_id_and_requeues(enriched, tmp_path):
    conn = connect(enriched)
    prov = FakeBatchProvider(outcomes=["errored", "succeeded", "expired"], polls_to_end=2)
    stage = verifier.VerifyStage(prov, CFG)
    verifier.enqueue_sample(conn, CFG, stage.label_config, 1.0)
    n_texts = conn.execute("SELECT COUNT(*) FROM work_items WHERE stage='verify'").fetchone()[0]
    # 1st invocation: submit, poll once, give up waiting (as if the process stopped) → the job stays open
    s1 = batch_runner.run_verify_batch(conn, stage, fast_batch(batch_size=20, max_wait_seconds=0))
    assert s1["stop_reason"] == "waiting" and prov.submits == 1
    job_id = s1["jobs"][0]["job_id"]
    assert conn.execute("SELECT status FROM batch_jobs WHERE provider_job_id=?", (job_id,)).fetchone()[0] == "submitted"
    # 2nd invocation: the SAME job is polled by ID (no resubmission), committed; failures requeued → a 2nd job
    s2 = batch_runner.run_verify_batch(conn, stage, fast_batch(batch_size=20))
    assert s2["jobs"][0] == {**s2["jobs"][0], "job_id": job_id, "resumed": True}
    assert s2["stop_reason"] == "drained" and prov.submits == 2
    assert conn.execute("SELECT COUNT(*) FROM verifier_outputs").fetchone()[0] == n_texts
    rows = calls(enriched, "verify")
    assert all(t == "batch" for _, _, t, _, _ in rows)
    assert sum(o == "failed" for _, o, _, _, _ in rows) == 2  # errored + expired, not billed
    assert all(len(json.loads(ids)) <= 20 for _, _, _, ids, _ in rows)
    open_res = conn.execute("""SELECT COUNT(*) FROM ledger r WHERE kind='reserve' AND NOT EXISTS (SELECT 1 FROM ledger x
                               WHERE x.request_id=r.request_id AND x.kind IN ('settle','release'))""").fetchone()[0]
    assert open_res == 0
    conn.close()


def test_batch_invalid_output_retried_once_then_unverified(enriched):
    conn = connect(enriched)
    prov = FakeBatchProvider(outcomes=["malformed", "malformed"], polls_to_end=0)
    stage = verifier.VerifyStage(prov, CFG)
    verifier.enqueue_sample(conn, CFG, stage.label_config, 0.3)
    s = batch_runner.run_verify_batch(conn, stage, fast_batch(batch_size=50))
    assert s["stop_reason"] == "drained" and prov.submits == 2
    st = dict(conn.execute("SELECT status, COUNT(*) FROM work_items WHERE stage='verify' GROUP BY 1").fetchall())
    assert set(st) == {"quarantined"}
    assert conn.execute("SELECT COUNT(*) FROM records WHERE status='quarantined'").fetchone()[0] == 0  # not a record
    conn.close()


def test_building_job_is_abandoned_not_lost(enriched):
    conn = connect(enriched)
    prov = FakeBatchProvider(polls_to_end=0)
    stage = verifier.VerifyStage(prov, CFG)
    verifier.enqueue_sample(conn, CFG, stage.label_config, 0.3)

    async def crash(_):
        raise KeyboardInterrupt  # dies after the 'building' row, before the provider ID is saved
    prov.submit = crash
    with pytest.raises(KeyboardInterrupt):
        batch_runner.run_verify_batch(conn, stage, fast_batch())
    assert conn.execute("SELECT status FROM batch_jobs").fetchone()[0] == "building"
    del prov.submit
    s = batch_runner.run_verify_batch(conn, stage, fast_batch())
    assert s["stop_reason"] == "drained"
    assert dict(conn.execute("SELECT status, COUNT(*) FROM batch_jobs GROUP BY 1").fetchall()) == {
        "abandoned": 1, "committed": 1}
    assert set(r[0] for r in conn.execute("SELECT status FROM work_items WHERE stage='verify'")) == {"done"}
    conn.close()


def test_standard_mode_report_and_planted_errors(enriched, tmp_path):
    rc = cli.main(["verify", "--db", str(enriched), "--root", str(tmp_path), "--label-config", CFG, "--standard",
                   "--sample-rate", "1.0", "--plant", "3"])
    assert rc == 0
    rep = json.loads((tmp_path / "evals/verify/verify_report.json").read_text(encoding="utf-8"))
    assert rep["verified"] == rep["sample"]["size"] > 0 and rep["sample"]["rate"] == 1.0
    assert rep["disagreements"] > 0  # the fake verifier disagrees on every 4th text's topic
    with (tmp_path / "evals/verify/disagreements.csv").open(encoding="utf-8") as f:
        diffs = list(csv.DictReader(f))
    assert len(diffs) == rep["disagreements"]
    assert all(d["first_label"] != d["verifier_label"] for d in diffs)
    planted = json.loads((tmp_path / "evals/verify/planted_errors.json").read_text(encoding="utf-8"))
    assert planted["synthetic"] is True and len(planted["planted"]) == 3 and planted["detected"] == 3
    assert {r[2] for r in calls(enriched, "verify")} == {"standard"}
    assert not (tmp_path / "outputs" / "quarantine.jsonl").exists()  # verify never rewrites the record quarantine log


# ---------- A9 grouper + A10 ranker ----------
def test_group_membership_rules_and_naming(enriched, tmp_path):
    conn = connect(enriched)
    run_id = start_run(conn, "group", {})
    prov = FakeProvider(script=["injected"], model="fake-analysis")
    g = grouper.group(conn, CFG, prov, tmp_path, run_id)
    complaints = {r[0] for r in conn.execute("SELECT review_id FROM records WHERE status='completed' AND label_config=? "
                                             "AND intent IN ('complaint','cancellation')", (CFG,))}
    with (tmp_path / "outputs/membership.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert sorted(r["review_id"] for r in rows) == sorted(complaints)  # every one, exactly once
    issues = json.loads((tmp_path / "outputs/issues.json").read_text(encoding="utf-8"))
    assert "made-up-issue" not in {i["issue_id"] for i in issues["issues"]}
    assert any("rejected unknown issue id" in n for n in issues["naming"]["notes"])
    assert all(i["name_source"] == "model" for i in issues["issues"])
    assert all(r["issue_id"] == grouper.assign(i["topic"], "")[0] or r["issue_id"].startswith(i["topic"] + "-")
               for r in rows for i in issues["issues"] if i["issue_id"] == r["issue_id"])
    # deterministic + warm rerun: same files, zero new calls
    before = (tmp_path / "outputs/membership.csv").read_bytes(), len(calls(enriched, "group"))
    g2 = grouper.group(conn, CFG, prov, tmp_path, run_id)
    assert g2["naming"]["cache"] == "hit" and len(calls(enriched, "group")) == before[1] == 1
    assert (tmp_path / "outputs/membership.csv").read_bytes() == before[0]
    assert [o for _, o, *_ in calls(enriched, "group")] == ["succeeded"]
    conn.close()


def test_subtopic_assignment():
    assert grouper.assign("playback", "It crashes when I use bluetooth") == ("playback-crashes", "crashes")
    assert grouper.assign("playback", "my bluetooth speaker keeps dropping") == ("playback-connection", "connection")
    assert grouper.assign("billing", "Way too expensive now") == ("billing-price", "price")
    assert grouper.assign("other", "bad") == ("other-general", "general")


def test_rank_matches_checker_recomputation_and_is_byte_identical(enriched, tmp_path):
    conn = connect(enriched)
    grouper.group(conn, CFG, FakeProvider(model="fake-analysis"), tmp_path, start_run(conn, "group", {}))
    ranker.rank(tmp_path, conn)
    files = ["ranking.csv", "aggregates.csv", "area_aggregates.csv", "overall_metrics.json"]
    first = {f: (tmp_path / "outputs" / f).read_bytes() for f in files}
    assert cli.main(["rank", "--root", str(tmp_path)]) == 0  # from saved files only, no DB
    assert {f: (tmp_path / "outputs" / f).read_bytes() for f in files} == first
    # the checker's own algorithm (§9.5) over the same files
    recs = {json.loads(l)["review_id"]: json.loads(l) for l in gzip.open(tmp_path / "outputs/enriched.jsonl.gz", "rt",
                                                                    encoding="utf-8")}
    by_issue = {}
    for row in csv.DictReader((tmp_path / "outputs/membership.csv").open(encoding="utf-8")):
        by_issue.setdefault(row["issue_id"], []).append(recs[row["review_id"]]["severity"])
    calc = sorted(({"issue_id": i, "complaint_count": str(len(v)), "severity_sum": str(sum(v)),
                    "mean_severity": mean_string(sum(v), len(v)), "priority_score": str(sum(v))}
                   for i, v in by_issue.items()), key=lambda x: (-int(x["priority_score"]), x["issue_id"]))
    got = list(csv.DictReader((tmp_path / "outputs/ranking.csv").open(encoding="utf-8")))
    assert [{k: r[k] for k in calc[0]} for r in got] == calc
    assert [r["rank"] for r in got] == [str(n) for n in range(1, len(calc) + 1)]
    areas = list(csv.DictReader((tmp_path / "outputs/area_aggregates.csv").open(encoding="utf-8")))
    assert [a["area"] for a in areas] == ["access", "usability", "playback", "billing/support", "outside"]
    assert sum(int(a["complaint_count"]) for a in areas) == sum(int(r["complaint_count"]) for r in got)
    assert conn.execute("SELECT COUNT(*) FROM ranking WHERE label_config=?", (CFG,)).fetchone()[0] == len(got)
    conn.close()


def test_rank_rejects_bad_membership(enriched, tmp_path):
    conn = connect(enriched)
    grouper.group(conn, CFG, FakeProvider(model="fake-analysis"), tmp_path, start_run(conn, "group", {}))
    conn.close()
    path = tmp_path / "outputs/membership.csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines + [lines[1]]) + "\n")  # a duplicate pair
    with pytest.raises(ranker.RankError, match="duplicate pair"):
        ranker.rank(tmp_path)
    path.write_text("\n".join(lines[:1] + lines[2:]) + "\n")  # an ungrouped complaint
    with pytest.raises(ranker.RankError, match="not grouped"):
        ranker.rank(tmp_path)


# ---------- A11 memo ----------
def _analysed(enriched, tmp_path):
    conn = connect(enriched)
    grouper.group(conn, CFG, FakeProvider(model="fake-analysis"), tmp_path, start_run(conn, "group", {}))
    ranker.rank(tmp_path, conn)
    return conn


def test_memo_rejects_invented_ids_and_numbers_then_retries(enriched, tmp_path):
    conn = _analysed(enriched, tmp_path)
    prov = FakeProvider(script=["invented", "valid"], model="fake-analysis")
    out = memo.write_memo(conn, tmp_path, prov, start_run(conn, "memo", {}))
    rows = calls(enriched, "memo")
    assert [o for _, o, *_ in rows] == ["failed", "succeeded"]
    err = rows[0][4]
    assert "playback-teleport" in err and "98765" in err and "FAKE-REVIEW-ID" in err
    claims = list(csv.DictReader((tmp_path / "outputs/claims.csv").open(encoding="utf-8")))
    ranking = {r["issue_id"]: r for r in csv.DictReader((tmp_path / "outputs/ranking.csv").open(encoding="utf-8"))}
    assert claims and all(ranking[c["issue_id"]][c["metric"]] == c["value"] for c in claims)
    text = (tmp_path / "outputs/memo.md").read_text(encoding="utf-8")
    assert all(c["claim_id"] in text for c in claims) and out["priority_area"] in text
    recs = json.loads((tmp_path / "outputs/recommendations.json").read_text(encoding="utf-8"))
    assert recs["recommendations"][0]["supporting"] and recs["recommendations"][0]["evidence"]
    # warm rerun: zero calls, identical memo
    assert memo.write_memo(conn, tmp_path, prov, start_run(conn, "memo", {}))["cache"] == "hit"
    assert len(calls(enriched, "memo")) == 2 and (tmp_path / "outputs/memo.md").read_text(encoding="utf-8") == text
    conn.close()


def test_memo_gives_up_after_one_rejected_retry(enriched, tmp_path):
    conn = _analysed(enriched, tmp_path)
    with pytest.raises(CallRejected):
        memo.write_memo(conn, tmp_path, FakeProvider(script=["invented", "invented"], model="fake-analysis"),
                        start_run(conn, "memo", {}))
    assert not (tmp_path / "outputs/memo.md").exists()
    conn.close()


def test_memo_check_unit():
    pack = {"top_issues": [{"issue_id": "playback-crashes", "claims": {"complaint_count": "C01a"},
                            "evidence": [{"review_id": "000013df-e4de-493c-8d48-288d28cff20f", "quote": "q"}],
                            "complaint_count": "12"}], "areas": [], "totals": {"members": 40}}
    good = {"priority_area": "playback", "memo_markdown": "`playback-crashes` has 12 complaints [C01a] of 40.",
            "recommendations": [{"title": "Fix crashes", "area": "playback", "rationale": "12 [C01a]",
                                 "issue_ids": ["playback-crashes"], "claim_ids": ["C01a"],
                                 "review_ids": ["000013df-e4de-493c-8d48-288d28cff20f"]}]}
    assert memo.check(good, pack, {"playback-crashes"}) == []
    bad = json.loads(json.dumps(good))
    bad["memo_markdown"] += " Churn rose 37% [C09d]; see aaaaaaaa-e4de-493c-8d48-288d28cff20f."
    bad["priority_area"] = "catalog"
    errs = " | ".join(memo.check(bad, pack, {"playback-crashes"}))
    assert "37" in errs and "C09d" in errs and "aaaaaaaa" in errs and "priority_area" in errs


# ---------- the gate: every output file from one command ----------
def test_end_to_end_run_produces_every_output(tmp_path, monkeypatch):
    rows = [[f"rv{k:04d}", t, str(1 + k % 5), "0", "8.1", f"2022-0{1 + k % 9}-01 10:00:00"]
            for k, t in enumerate(["The app crashes every time I open it", "Too expensive, cancelling premium",
                                   "Cannot log in, password reset never arrives", "Ads after every song, awful",
                                   "Downloads disappeared again", "Love it", "Search never finds the song",
                                   "Customer service never replied", ""] * 4)]
    rows = [[r[0], f"{r[1]} ({i})" if r[1] and i % 3 else r[1], *r[2:]] for i, r in enumerate(rows)]
    csv_path = tmp_path / "in.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(FIELDS)
        w.writerows(rows)
    db = tmp_path / "e2e.db"

    def mini_ingest(args):
        # the real ingest also checks the dataset's manifest and dev files (M1 tests); same loading steps here
        conn = intake.connect(args.db)
        intake.apply_schema(conn)
        intake.load_source(conn, args.csv)
        intake.assign_scope(conn, size=len(rows))
        intake.build_texts(conn)
        intake.quarantine_empty(conn)
        conn.close()
        return 0
    monkeypatch.setattr(cli, "cmd_ingest", mini_ingest)
    base = ["--db", str(db), "--root", str(tmp_path)]
    # ONE command, input CSV path → every stage, no manual steps
    assert cli.main(["run", "--input", str(csv_path), *base, "--sample-rate", "0.5"]) == 0
    missing = [f for f in OUTPUT_FILES if not (tmp_path / f).exists()]
    assert not missing, missing
    roles = {r for r, o, *_ in calls(db) if o == "succeeded"}
    assert roles == {"enrich", "verify", "group", "memo"}  # the checker's missing_model_roles rule
    before = (tmp_path / "outputs/ranking.csv").read_bytes()
    n_calls = len(calls(db))
    assert cli.main(["rank", "--root", str(tmp_path)]) == 0
    assert cli.main(["rank", "--root", str(tmp_path)]) == 0
    assert (tmp_path / "outputs/ranking.csv").read_bytes() == before and len(calls(db)) == n_calls
    assert cli.main(["analyze", *base]) == 0 and len(calls(db)) == n_calls  # warm: cached names + memo


def test_claude_batch_client_on_mocked_http():
    """The real SDK path (create → retrieve → results JSONL) against a mocked transport: no network, no spend."""
    import asyncio

    import anthropic
    import httpx2 as httpx

    from pipeline.providers.claude import ClaudeBatchProvider
    seen = []
    batch = {"id": "msgbatch_01", "type": "message_batch", "processing_status": "ended",
             "request_counts": {"processing": 0, "succeeded": 1, "errored": 1, "canceled": 0, "expired": 1},
             "created_at": "2026-10-14T00:00:00Z", "expires_at": "2026-10-15T00:00:00Z", "ended_at": None,
             "archived_at": None, "cancel_initiated_at": None,
             "results_url": "https://api.anthropic.com/v1/messages/batches/msgbatch_01/results"}
    msg = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5", "stop_reason": "end_turn",
           "stop_sequence": None, "content": [{"type": "text", "text": '{"labels": []}'}],
           "usage": {"input_tokens": 900, "output_tokens": 100, "cache_read_input_tokens": 600,
                     "cache_creation_input_tokens": 0}}
    lines = [{"custom_id": "a" * 32, "result": {"type": "succeeded", "message": msg}},
             {"custom_id": "b" * 32, "result": {"type": "errored", "error": {"type": "error", "error": {
                 "type": "invalid_request_error", "message": "bad"}}}},
             {"custom_id": "c" * 32, "result": {"type": "expired"}}]

    def handler(request):
        seen.append((request.method, request.url.path))
        if request.url.path.endswith("/results"):
            return httpx.Response(200, text="\n".join(json.dumps(l) for l in lines) + "\n")
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["requests"][0]["custom_id"] == "a" * 32 and body["requests"][0]["params"]["model"]
        return httpx.Response(200, json=batch)

    client = anthropic.AsyncAnthropic(api_key="sk-test", max_retries=0,
                                      http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    p = ClaudeBatchProvider(client=client)
    assert asyncio.run(p.submit([("a" * 32, {"model": p.model, "max_tokens": 10, "messages": []})])) == "msgbatch_01"
    status, counts = asyncio.run(p.status("msgbatch_01"))
    assert status == "ended" and counts["expired"] == 1
    res = asyncio.run(p.results("msgbatch_01"))
    assert [(c[0][0], c[1]) for c in res] == [("a", "succeeded"), ("b", "errored"), ("c", "expired")]
    resp = res[0][2]
    assert resp.body == {"labels": []} and resp.usage["input_tokens"] == 1500 and resp.usage["cached_tokens"] == 600
    assert p.cost(resp.usage) == pytest.approx((900 * 0.05 + 600 * 0.005 + 100 * 0.25) / 1e6)  # batch = 50% off
    assert "bad" in res[1][3]
    assert ("POST", "/v1/messages/batches") in seen


def test_downstream_money_guards(enriched, tmp_path):
    for cmd in ("verify", "group", "memo", "analyze"):
        with pytest.raises(SystemExit, match="paid"):
            cli.main([cmd, "--db", str(enriched), "--provider", "anthropic"])
        with pytest.raises(SystemExit, match="own --db"):
            cli.main([cmd, "--provider", "fake"])
