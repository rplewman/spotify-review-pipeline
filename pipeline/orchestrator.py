"""A0 Orchestrator (PLAN §3.3–3.4): async workers (1 → N tasks in one process) over the shared SQLite queue.

One worker iteration:
    claim ≤ batch_size items (lease) → pre-call cache check (hits: done, no call) → reserve the estimate in the ledger
    → shared rate limiter → log the attempt as in_flight → provider call → ONE transaction: call outcome + usage,
    ledger settle/release, fenced cache writes + record fan-out, queue state.

Stop conditions (checked before every claim; the stop reason is saved on the run):
    drained · limit (item cap) · time (--max-minutes) · budget (the ledger refuses the next reservation)
    · quota_402 · fatal_<status> · interrupted (Ctrl-C / cancel) — every path releases this run's leases, marks
    unfinished attempts failed and settles their reservations at the estimate, and writes a checkpoint before exiting.

A run killed hard (no cleanup) is recovered by `recover()`: once its heartbeat is older than the lease, its in-flight
attempts are marked failed ("interrupted"), their reservations settled at the estimate, and its leases released.
"""
import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from pipeline.db import ledger, limiter, queue
from pipeline.db.store import connect, immediate, iso, migrate, now_iso, utcnow
from pipeline.providers.base import ProviderError, Request


@dataclass
class RunConfig:
    label_config: str
    stage: str = "enrich"
    workers: int = 1
    batch_size: int = 50
    max_spend: float | None = None    # this run's cap (USD, list price)
    bucket: str | None = None         # ledger bucket; default = the stage's
    bucket_cap: float | None = None   # override PLAN §4's cap for the bucket
    max_seconds: float | None = None
    limit: int | None = None          # max items to claim this run (pilots)
    max_rank: int | None = None       # only texts with scope_rank ≤ this (a seed-order prefix, e.g. 10,050)
    lease_seconds: float = 300.0
    max_attempts: int = 6             # transient failures before an item is marked failed
    rpm: float = 600.0
    tpm: float = 2_000_000.0
    backoff_base: float = 1.0
    call_timeout: float = 120.0
    idle_poll: float = 0.2
    phase: str = "initial"            # initial | resume (written to every call)


class Orchestrator:
    def __init__(self, db_path, stage, cfg: RunConfig, root=None, run_id=None, provider_spec=None):
        self.db_path, self.stage, self.cfg = db_path, stage, cfg
        self.root = Path(root) if root else None
        self.run_id = run_id or f"run-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        self.bucket = cfg.bucket or stage.bucket
        self.caps = ledger.caps_for(self.bucket, self.run_id, cfg.max_spend, cfg.bucket_cap)
        self.provider_spec = provider_spec or {"name": stage.provider.name}
        self.stop_reason = None
        self.claimed = 0
        self.in_flight = {}  # request_id -> (owner, item ids)

    # ---------- lifecycle ----------
    def _start(self):
        conn = connect(self.db_path)
        migrate(conn)
        config = {**asdict(self.cfg), "provider": self.provider_spec, "model": self.stage.model, "bucket": self.bucket,
                  "stage_config": self.stage.describe()}
        with immediate(conn):
            conn.execute("INSERT INTO runs (run_id, config_json, phase, started_at, heartbeat_at) VALUES (?,?,?,?,?)",
                         (self.run_id, json.dumps(config), self.cfg.phase, now_iso(), now_iso()))
        if self.cfg.phase == "resume":
            write_checkpoint(conn, self.run_id, "resume_start", self.cfg.label_config)
        conn.close()

    def stop(self, reason):
        if self.stop_reason is None:
            self.stop_reason = reason

    async def run(self):
        self._start()
        t0 = time.monotonic()
        self.deadline = t0 + self.cfg.max_seconds if self.cfg.max_seconds else None
        beat = asyncio.create_task(self._heartbeat())
        try:
            await asyncio.gather(*(self._worker(n) for n in range(self.cfg.workers)))
        except asyncio.CancelledError:
            self.stop("interrupted")
            raise
        finally:
            beat.cancel()
            self.summary = self._finish(time.monotonic() - t0)
        return self.summary

    async def _heartbeat(self):
        conn = connect(self.db_path)
        try:
            while True:
                await asyncio.sleep(max(0.5, self.cfg.lease_seconds / 3))
                with immediate(conn):
                    conn.execute("UPDATE runs SET heartbeat_at=? WHERE run_id=?", (now_iso(), self.run_id))
                    # keep long calls' leases alive
                    conn.execute("UPDATE work_items SET lease_expires_at=? WHERE status='leased' "
                                 "AND substr(lease_owner,1,?)=?", (now_iso(self.cfg.lease_seconds),
                                                                   len(self.run_id) + 1, self.run_id + ":"))
        finally:
            conn.close()

    def _finish(self, elapsed):
        """Always runs: unfinished attempts → failed, reservations settled at estimate, leases released, checkpoint."""
        reason = self.stop_reason or "interrupted"
        conn = connect(self.db_path)
        with immediate(conn):
            for rid in list(self.in_flight):
                abandon(conn, rid)
        queue.release_owner(conn, self.run_id + ":")
        write_checkpoint(conn, self.run_id, f"{self.cfg.phase}_end:{reason}", self.cfg.label_config)
        with immediate(conn):
            conn.execute("UPDATE runs SET ended_at=?, stop_reason=? WHERE run_id=?", (now_iso(), reason, self.run_id))
        summary = run_summary(conn, self.run_id, self.cfg.stage, self.cfg.label_config, elapsed)
        if self.root:
            write_run_outputs(conn, self.root, summary, self.cfg.stage, self.cfg.label_config)
        conn.close()
        return summary

    # ---------- the worker ----------
    async def _worker(self, n):
        owner = f"{self.run_id}:w{n}"
        conn = connect(self.db_path)
        try:
            while self.stop_reason is None:
                if self.deadline and time.monotonic() >= self.deadline:
                    self.stop("time")
                    break
                want = self.cfg.batch_size
                if self.cfg.limit is not None:
                    want = min(want, self.cfg.limit - self.claimed)
                    if want <= 0:
                        self.stop("limit")
                        break
                items = queue.claim(conn, owner, self.cfg.stage, self.cfg.label_config, want,
                                    self.cfg.lease_seconds, self.run_id, self.cfg.max_rank)
                if not items:
                    if queue.has_open_work(conn, self.cfg.stage, self.cfg.label_config, self.cfg.max_rank):
                        await asyncio.sleep(self.cfg.idle_poll)  # others hold leases, or retries are backing off
                        continue
                    self.stop("drained")
                    break
                self.claimed += len(items)
                items = self._precall_check(conn, owner, items)
                if items:
                    await self._process(conn, owner, items)
                await asyncio.sleep(0)
        finally:
            conn.close()

    def _precall_check(self, conn, owner, items):
        """Items whose text already has a result under this label_config are done with no call."""
        hits = self.stage.cached(conn, [i["text_sha256"] for i in items])
        if not hits:
            return items
        with immediate(conn):
            mine = queue.held(conn, owner, [i["id"] for i in items if i["text_sha256"] in hits])
            self.stage.reuse(conn, [i["text_sha256"] for i in items if i["id"] in mine])
            queue.mark_done(conn, mine)
        return [i for i in items if i["text_sha256"] not in hits]

    async def _process(self, conn, owner, items):
        """Run the stage's calls for one leased batch, then commit its results in one fenced transaction."""
        ctx = self.stage.begin(conn, items)
        while (call := self.stage.next_call(conn, ctx)) is not None:
            if not await self._call(conn, owner, items, call, ctx):
                return  # items were requeued / released, or the run is stopping
        ok, bad = self.stage.finish(ctx)
        self._commit(conn, owner, items, ok, bad)

    async def _call(self, conn, owner, items, call, ctx):
        """One provider attempt. True = carry on with the batch; False = the batch is over (error or stop)."""
        ids = [i["id"] for i in items]
        request_id = uuid.uuid4().hex  # sent as X-Client-Trace-Id by real providers
        bucket = call.bucket or self.bucket
        cap = call.bucket_cap if call.bucket_cap is not None else (self.cfg.bucket_cap if bucket == self.bucket else None)
        provider, est = call.provider, call.est_usd
        try:
            ledger.reserve(conn, request_id, provider.name, bucket, est,
                           ledger.caps_for(bucket, self.run_id, self.cfg.max_spend, cap), self.run_id)
        except ledger.BudgetExceeded as e:
            if self.stage.on_budget(conn, ctx, call, str(e)):
                return True
            with immediate(conn):
                queue.release(conn, ids, owner)
            self.stop("budget")
            self.budget_message = str(e)
            return False
        self.in_flight[request_id] = (owner, ids)  # from here on, an interruption must close the reservation
        while (wait := limiter.acquire(conn, provider.model, call.est_tokens, self.cfg.rpm, self.cfg.tpm)) > 0:
            await asyncio.sleep(min(wait, 5.0))
        review_ids = [i["review_id"] for i in call.items]
        with immediate(conn):
            conn.execute("""INSERT INTO calls (request_id, role, agent, model, tier, phase, outcome, label_config,
                                               review_ids_json, run_id, started_at, est_usd)
                            VALUES (?,?,?,?,?,?,'in_flight',?,?,?,?,?)""",
                         (request_id, self.stage.role, call.agent, provider.model, provider.tier,
                          self.cfg.phase, self.cfg.label_config, json.dumps(review_ids), self.run_id, now_iso(), est))
        req = Request(request_id, provider.model, review_ids, [i["review_text"] for i in call.items],
                      call.payload, self.cfg.call_timeout, call.meta)
        t0 = time.perf_counter()
        try:
            resp = await asyncio.wait_for(provider.call(req), self.cfg.call_timeout)
        except ProviderError as e:
            self._on_error(conn, owner, items, request_id, call, e, t0)
            return False
        except asyncio.TimeoutError:
            self._on_error(conn, owner, items, request_id, call,
                           ProviderError("transient", None, "client timeout", billed=None), t0)
            return False
        self._on_response(conn, request_id, call, ctx, resp, t0)
        return True

    def _on_response(self, conn, request_id, call, ctx, resp, t0):
        """ONE transaction: the call's outcome + usage, the ledger settlement, and the stage's saved step outputs."""
        latency = resp.latency_ms or int((time.perf_counter() - t0) * 1000)
        usage_ok = resp.usage is not None
        u = resp.usage or {}
        cost = call.provider.cost(u) if usage_ok else call.est_usd
        call_error = None
        try:
            ok, bad, notes = call.parse(resp.body)
            outcome, error = "succeeded", "; ".join(notes) or None
        except ValueError as e:
            ok, bad, call_error = {}, {i["id"]: str(e) for i in call.items}, str(e)
            outcome, error = "failed", str(e)
        with immediate(conn):
            conn.execute("""UPDATE calls SET outcome=?, input_tokens=?, output_tokens=?, cached_tokens=?, cache_write_tokens=?, cost_usd=?,
                                             latency_ms=?, error=?, usage_available=?, provider_request_id=?, ended_at=?
                            WHERE request_id=?""",
                         (outcome, int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0)),
                          int(u.get("cached_tokens", 0) or 0),
                          int(u.get("cache_write_tokens", 0) or 0), cost, latency, error, int(usage_ok),
                          resp.provider_request_id, now_iso(), request_id))
            ledger.settle(conn, request_id, cost)
            self.stage.absorb(conn, ctx, call, ok, bad, request_id, call_error)
        self.in_flight.pop(request_id, None)

    def _commit(self, conn, owner, items, ok, bad):
        """ONE fenced transaction: results + record fan-out for the items this worker still holds; invalid → retry
        once → quarantine."""
        by_id = {i["id"]: i for i in items}
        with immediate(conn):
            mine = queue.held(conn, owner, list(by_id))
            done = [i for i in ok if i in mine]
            self.stage.save(conn, [(by_id[i], ok[i]) for i in done])
            queue.mark_done(conn, done)
            invalid = [i for i in bad if i in mine]
            self.stage.forget(conn, [by_id[i]["text_sha256"] for i in invalid])
            quarantined = {}
            for i in invalid:
                if queue.mark_invalid(conn, i, bad[i]):
                    quarantined[by_id[i]["text_sha256"]] = f"invalid_output: {bad[i]}"
            self.stage.quarantine(conn, quarantined)

    def _on_error(self, conn, owner, items, request_id, call, e, t0):
        ids = [i["id"] for i in items]
        attempts = max(i["attempts"] for i in items)
        est = call.est_usd
        with immediate(conn):
            conn.execute("""UPDATE calls SET outcome='failed', input_tokens=0, output_tokens=0, usage_available=0,
                                             cost_usd=?, latency_ms=?, error=?, ended_at=? WHERE request_id=?""",
                         (0.0 if e.billed is False else est, int((time.perf_counter() - t0) * 1000), str(e),
                          now_iso(), request_id))
            if e.billed is False:
                ledger.release(conn, request_id)
            else:
                ledger.settle(conn, request_id, est)  # billing unknown: count the estimate, never 0
            mine = list(queue.held(conn, owner, ids))
            if e.kind == "transient":
                if e.status == 429 and e.retry_after:
                    limiter.block(conn, call.provider.model, e.retry_after)
                if attempts >= self.cfg.max_attempts:
                    queue.fail(conn, mine, str(e))
                else:
                    queue.requeue(conn, mine, str(e), queue.backoff(attempts, self.cfg.backoff_base,
                                                                    retry_after=e.retry_after))
            else:
                queue.release(conn, mine, owner)
                self.stop("quota_402" if e.kind == "quota" else f"fatal_{e.status}")
        self.in_flight.pop(request_id, None)


# ---------- recovery, checkpoints, reporting ----------
def abandon(conn, request_id):
    """Close an attempt cut off by a stop or crash: the call (if it was sent) → failed; a reservation with no call → released."""
    if conn.execute("SELECT 1 FROM calls WHERE request_id=?", (request_id,)).fetchone():
        close_interrupted_call(conn, request_id)
    else:
        ledger.release(conn, request_id)


def close_interrupted_call(conn, request_id):
    """An attempt whose answer never arrived: failed, usage unavailable, reservation settled at the estimate."""
    row = conn.execute("SELECT est_usd FROM calls WHERE request_id=? AND outcome='in_flight'", (request_id,)).fetchone()
    if row is None:
        return
    conn.execute("""UPDATE calls SET outcome='failed', input_tokens=0, output_tokens=0, usage_available=0, cost_usd=?,
                                     error='interrupted (no response recorded)', ended_at=? WHERE request_id=?""",
                 (row[0], now_iso(), request_id))
    ledger.settle(conn, request_id, row[0] or 0.0)


def recover(conn, stale_seconds):
    """Close runs that died without cleanup (heartbeat older than `stale_seconds`). Returns their run IDs."""
    cutoff = iso(utcnow()) if stale_seconds <= 0 else now_iso(-stale_seconds)
    dead = [r[0] for r in conn.execute("SELECT run_id FROM runs WHERE ended_at IS NULL AND heartbeat_at < ?", (cutoff,))]
    for run_id in dead:
        with immediate(conn):
            for (rid,) in conn.execute("""SELECT request_id FROM ledger WHERE run_id=? AND kind='reserve'
                                          AND request_id NOT IN (SELECT request_id FROM calls WHERE tier='batch'
                                                                 AND outcome='in_flight')""",  # closed by the batch runner
                                       (run_id,)).fetchall():
                abandon(conn, rid)  # no-op for attempts that were already closed
            conn.execute("UPDATE runs SET ended_at=?, stop_reason='crashed' WHERE run_id=?", (now_iso(), run_id))
        queue.release_owner(conn, run_id + ":")
    return dead


def completed_ids(conn, label_config):
    return [r[0] for r in conn.execute("SELECT review_id FROM records WHERE status='completed' AND label_config=? "
                                       "ORDER BY review_id", (label_config,))]


def write_checkpoint(conn, run_id, label, label_config):
    """Checkpoint = count + SHA-256 of the sorted completed IDs (M7 exports the full before/after lists)."""
    ids = completed_ids(conn, label_config)
    digest = hashlib.sha256("\n".join(ids).encode()).hexdigest()
    with immediate(conn):
        conn.execute("INSERT INTO checkpoints (run_id, label, completed_ids_json, created_at) VALUES (?,?,?,?)",
                     (run_id, label, json.dumps({"count": len(ids), "sha256": digest}), now_iso()))


def run_summary(conn, run_id, stage, label_config, elapsed=None):
    phase, reason, started, ended = conn.execute(
        "SELECT phase, stop_reason, started_at, ended_at FROM runs WHERE run_id=?", (run_id,)).fetchone()
    calls = dict(conn.execute("SELECT outcome, COUNT(*) FROM calls WHERE run_id=? GROUP BY outcome", (run_id,)).fetchall())
    tokens = conn.execute("SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0) FROM calls "
                          "WHERE run_id=?", (run_id,)).fetchone()
    return {"run_id": run_id, "phase": phase, "stop_reason": reason, "started_at": started, "ended_at": ended,
            "elapsed_s": round(elapsed, 3) if elapsed is not None else None, "label_config": label_config,
            "calls": calls, "input_tokens": tokens[0], "output_tokens": tokens[1],
            "spend_usd_list": ledger.totals(conn, run_id=run_id), "queue": queue.counts(conn, stage, label_config)}


def write_run_outputs(conn, root, summary, stage, label_config):
    """Append the run summary to outputs/run_log.jsonl; rewrite outputs/quarantine.jsonl for this label_config."""
    out = Path(root) / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "run_log.jsonl").open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(summary, sort_keys=True) + "\n")
    if stage != "enrich":
        return  # the verifier's unverifiable texts go to its own report, not to the record quarantine log
    rows = conn.execute("""SELECT t.original_review_id, w.text_sha256, w.last_error, w.attempts, w.invalid_attempts
                           FROM work_items w JOIN texts t ON t.text_sha256 = w.text_sha256
                           WHERE w.stage=? AND w.label_config=? AND w.status='quarantined' ORDER BY w.scope_rank""",
                        (stage, label_config)).fetchall()
    with (out / "quarantine.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for rid, sha, err, att, inv in rows:
            f.write(json.dumps({"review_id": rid, "text_sha256": sha, "label_config": label_config, "reason": err,
                                "attempts": att, "invalid_attempts": inv}) + "\n")
