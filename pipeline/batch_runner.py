"""Anthropic Message Batches runner for A8 verification (PLAN §3.6).

    open job? ──yes──▶ poll it by ID (never resubmitted)
       │ no
       ▼
    claim the pending verify items (lease owner = the job) → ≤ batch_size reviews per request → reserve each request's
    estimate (bucket `verification`) → `calls` rows (in_flight, tier batch, custom_id = request_id) → `batch_jobs` row
    'building' → submit → row gets the provider job ID ('submitted')
       ▼
    poll until `ended` (or --max-wait: exit 'waiting'; `verify` again resumes the same job by ID)
       ▼
    per result, ONE transaction: call outcome + usage at batch rates, ledger settle/release, verifier_outputs, queue
      succeeded + valid → done · invalid → retry once → quarantined (unverified) · errored/canceled/expired/missing →
      not billed, requeued (transient) → the next round builds a new job from them (≤ max_rounds)

A crash between submit and saving the provider ID leaves a 'building' row: it is marked 'abandoned', its reservations
are settled at the estimate (billing unknown) and its items are requeued. That window is one SQLite write.
"""
import asyncio
import json
import time
import uuid
from dataclasses import dataclass

from pipeline.db import ledger, queue
from pipeline.db.store import immediate, now_iso
from pipeline.oneshot import end_run, start_run
from pipeline.providers.base import ProviderError, run_sync as _run

LEASE = 26 * 3600  # a batch can take up to 24 h
POLL_TRANSIENT_LIMIT = 10  # consecutive connection errors while polling before giving up (rerun resumes by job ID)



@dataclass
class BatchConfig:
    batch_size: int = 50
    max_spend: float | None = None
    poll_seconds: float = 30.0
    max_wait_seconds: float = 6 * 3600
    max_rounds: int = 3
    max_attempts: int = 4


def _open_jobs(conn, key):
    return [r[0] for r in conn.execute("SELECT provider_job_id FROM batch_jobs WHERE label_config=? AND role='verify' "
                                       "AND status IN ('building','submitted','ended') ORDER BY submitted_at", (key,))]


def abandon_building(conn, key):
    """Jobs whose submission was never confirmed: settle at the estimate, close the calls, requeue the items."""
    for job_id, item_map, owner in conn.execute(
            "SELECT provider_job_id, item_ids_json, job_key FROM batch_jobs WHERE label_config=? AND status='building'",
            (key,)).fetchall():
        with immediate(conn):
            for rid, ids in json.loads(item_map).items():
                est = conn.execute("SELECT est_usd FROM calls WHERE request_id=?", (rid,)).fetchone()
                conn.execute("""UPDATE calls SET outcome='failed', input_tokens=0, output_tokens=0, usage_available=0,
                                cost_usd=?, error='batch submission not confirmed (abandoned)', ended_at=?
                                WHERE request_id=? AND outcome='in_flight'""", (est[0] if est else 0, now_iso(), rid))
                ledger.settle(conn, rid, est[0] if est else 0.0)
                queue.requeue(conn, list(queue.held(conn, owner, ids)), "batch abandoned")
            conn.execute("UPDATE batch_jobs SET status='abandoned', ended_at=? WHERE provider_job_id=?",
                         (now_iso(), job_id))


def build_and_submit(conn, stage, cfg, run_id):
    """Returns the provider job ID, or None when nothing is pending. Raises ledger.BudgetExceeded (items released)."""
    key = stage.label_config
    job_key = f"batch-{uuid.uuid4().hex[:12]}"
    items = queue.claim(conn, job_key, "verify", key, 10 ** 9, LEASE, run_id)
    if not items:
        return None
    provider = stage.provider
    chunks = [items[k:k + cfg.batch_size] for k in range(0, len(items), cfg.batch_size)]
    requests, item_map = [], {}
    try:
        for chunk in chunks:
            call = stage.make_call(chunk)
            rid = uuid.uuid4().hex
            ledger.reserve(conn, rid, provider.name, stage.bucket, call.est_usd,
                           ledger.caps_for(stage.bucket, run_id, cfg.max_spend), run_id)
            item_map[rid] = [i["id"] for i in chunk]
            requests.append((rid, {"model": provider.model, **call.payload}, chunk))
    except ledger.BudgetExceeded:
        with immediate(conn):
            for rid in item_map:
                ledger.release(conn, rid)
            queue.release(conn, [i["id"] for i in items], job_key)
        raise
    pending_id = f"pending:{job_key}"
    with immediate(conn):
        for rid, _, chunk in requests:
            conn.execute("""INSERT INTO calls (request_id, role, agent, model, tier, phase, outcome, label_config,
                                               review_ids_json, run_id, started_at, est_usd)
                            VALUES (?,?,?,?,?,'initial','in_flight',?,?,?,?,
                                    (SELECT usd FROM ledger WHERE request_id=? AND kind='reserve'))""",
                         (rid, stage.role, stage.agent, provider.model, provider.tier, key,
                          json.dumps([i["review_id"] for i in chunk]), run_id, now_iso(), rid))
        conn.execute("""INSERT INTO batch_jobs (provider_job_id, item_ids_json, status, job_key, role, label_config,
                                                run_id) VALUES (?,?, 'building', ?, 'verify', ?, ?)""",
                     (pending_id, json.dumps(item_map), job_key, key, run_id))
    job_id = _run(provider, provider.submit([(rid, params) for rid, params, _ in requests]))
    with immediate(conn):
        conn.execute("UPDATE batch_jobs SET provider_job_id=?, status='submitted', submitted_at=? WHERE provider_job_id=?",
                     (job_id, now_iso(), pending_id))
    return job_id


def poll(conn, provider, job_id, cfg):
    """Wait for the job to end. Returns True when ended, False when max_wait passed (the job stays open)."""
    deadline = time.monotonic() + cfg.max_wait_seconds
    transient = 0
    while True:
        try:
            status, counts = _run(provider, provider.status(job_id))
            transient = 0
        except ProviderError as e:  # the job lives on the provider's side: a failed poll changes nothing
            transient += 1
            if e.kind != "transient" or transient >= POLL_TRANSIENT_LIMIT:
                raise
            time.sleep(cfg.poll_seconds)
            continue
        with immediate(conn):
            conn.execute("UPDATE batch_jobs SET request_counts_json=? WHERE provider_job_id=?",
                         (json.dumps(counts, sort_keys=True), job_id))
        if status == "ended":
            with immediate(conn):
                conn.execute("UPDATE batch_jobs SET status='ended', ended_at=? WHERE provider_job_id=? "
                             "AND status='submitted'", (now_iso(), job_id))
            return True
        if time.monotonic() + cfg.poll_seconds >= deadline:
            return False
        time.sleep(cfg.poll_seconds)


def commit(conn, stage, job_id, cfg):
    """Validate and commit every result of an ended job; requeue what failed. Idempotent per request."""
    provider = stage.provider
    item_map, owner = conn.execute("SELECT item_ids_json, job_key FROM batch_jobs WHERE provider_job_id=?",
                                   (job_id,)).fetchone()
    item_map = json.loads(item_map)
    results = {cid: (kind, resp, err) for cid, kind, resp, err in _run(provider, provider.results(job_id))}
    tally = {"succeeded": 0, "invalid": 0, "requeued": 0, "failed": 0}
    for rid, ids in item_map.items():
        if conn.execute("SELECT outcome FROM calls WHERE request_id=?", (rid,)).fetchone()[0] != "in_flight":
            continue  # committed by an earlier (interrupted) commit
        kind, resp, err = results.get(rid, ("expired", None, "no result returned for this custom_id"))
        rows = {r[0]: r for r in conn.execute(
            f"""SELECT w.id, w.text_sha256, t.original_review_id, t.review_text, w.attempts FROM work_items w
                JOIN texts t ON t.text_sha256 = w.text_sha256 WHERE w.id IN ({','.join('?' * len(ids))})""", ids)}
        items = [dict(zip(("id", "text_sha256", "review_id", "review_text", "attempts"), rows[i])) for i in ids]
        with immediate(conn):
            mine = queue.held(conn, owner, ids)
            if kind != "succeeded":
                conn.execute("""UPDATE calls SET outcome='failed', input_tokens=0, output_tokens=0, usage_available=0,
                                cost_usd=0, error=?, ended_at=? WHERE request_id=?""",
                             (f"batch {kind}: {err}", now_iso(), rid))
                ledger.release(conn, rid)  # errored / canceled / expired batch requests are not billed
                late = [i["id"] for i in items if i["id"] in mine and i["attempts"] >= cfg.max_attempts]
                queue.fail(conn, late, f"batch {kind}")
                queue.requeue(conn, [i for i in mine if i not in late], f"batch {kind}: {err}")
                tally["requeued" if len(late) < len(mine) else "failed"] += 1
                continue
            usage_ok = resp.usage is not None
            u = resp.usage or {}
            est = conn.execute("SELECT est_usd FROM calls WHERE request_id=?", (rid,)).fetchone()[0]
            cost = provider.cost(u) if usage_ok else est
            call = stage.make_call(items)
            try:
                ok, bad, notes = call.parse(resp.body)
                outcome, error = "succeeded", "; ".join(notes) or None
            except ValueError as e:
                ok, bad = {}, {i["id"]: str(e) for i in items}
                outcome, error = "failed", str(e)
            conn.execute("""UPDATE calls SET outcome=?, input_tokens=?, output_tokens=?, cached_tokens=?, cache_write_tokens=?, cost_usd=?,
                                             error=?, usage_available=?, provider_request_id=?, ended_at=?
                            WHERE request_id=?""",
                         (outcome, int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0)),
                          int(u.get("cached_tokens", 0) or 0),
                          int(u.get("cache_write_tokens", 0) or 0), cost, error, int(usage_ok), resp.provider_request_id,
                          now_iso(), rid))
            ledger.settle(conn, rid, cost)
            by_id = {i["id"]: i for i in items}
            done = [i for i in ok if i in mine]
            stage.save(conn, [(by_id[i], {**ok[i], "request_id": rid}) for i in done])
            queue.mark_done(conn, done)
            for i in bad:
                if i in mine:
                    queue.mark_invalid(conn, i, bad[i])
                    tally["invalid"] += 1
            tally["succeeded"] += outcome == "succeeded"
    with immediate(conn):
        conn.execute("UPDATE batch_jobs SET status='committed', committed_at=? WHERE provider_job_id=?",
                     (now_iso(), job_id))
    return tally


def run_verify_batch(conn, stage, cfg: BatchConfig):
    """Resume open jobs by ID, then build/submit/poll/commit rounds until nothing is pending. Returns a summary."""
    key = stage.label_config
    run_id = start_run(conn, "verify", {"transport": "batch", "label_config": key, "model": stage.provider.model,
                                        "batch_size": cfg.batch_size, "max_spend": cfg.max_spend})
    summary = {"run_id": run_id, "jobs": [], "stop_reason": None}
    try:
        abandon_building(conn, key)
        rounds = 0
        while True:
            open_jobs = _open_jobs(conn, key)
            if open_jobs:
                job_id = open_jobs[0]
                summary["jobs"].append({"job_id": job_id, "resumed": True})
            else:
                if rounds >= cfg.max_rounds:
                    summary["stop_reason"] = "max_rounds"
                    break
                try:
                    job_id = build_and_submit(conn, stage, cfg, run_id)
                except ledger.BudgetExceeded as e:
                    summary["stop_reason"], summary["budget"] = "budget", str(e)
                    break
                if job_id is None:
                    summary["stop_reason"] = "drained"
                    break
                rounds += 1
                summary["jobs"].append({"job_id": job_id, "resumed": False})
            if not poll(conn, stage.provider, job_id, cfg):
                summary["stop_reason"] = "waiting"  # resume later with the same command: the job is polled by ID
                break
            summary["jobs"][-1]["tally"] = commit(conn, stage, job_id, cfg)
    finally:
        end_run(conn, run_id, summary["stop_reason"] or "interrupted")
    summary["spend_usd_list"] = ledger.totals(conn, run_id=run_id)
    return summary
