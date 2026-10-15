"""One-off model calls for the downstream agents A9 (group names) and A11 (memo), PLAN §3.3.

These are single requests, not queue work, but they follow the same money and evidence rules as the orchestrator:
reserve the estimate in the ledger (project cap, bucket cap, the command's --max-spend) → log the attempt as in_flight
→ call → the caller validates the body → ONE transaction: call outcome + usage + ledger settlement. Transient errors
are retried with backoff (each attempt is its own logged call); quota/fatal errors raise.
"""
import asyncio
import json
import time
import uuid
from datetime import datetime

from pipeline.db import ledger, queue
from pipeline.db.store import immediate, now_iso
from pipeline.providers.base import ProviderError, Request


class CallRejected(Exception):
    """Every attempt failed or was rejected by validation."""


def start_run(conn, stage, config):
    run_id = f"run-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    with immediate(conn):
        conn.execute("INSERT INTO runs (run_id, config_json, phase, started_at, heartbeat_at) VALUES (?,?,?,?,?)",
                     (run_id, json.dumps({"stage": stage, **config}, sort_keys=True), "initial", now_iso(), now_iso()))
    return run_id


def end_run(conn, run_id, reason):
    with immediate(conn):
        conn.execute("UPDATE runs SET ended_at=?, stop_reason=? WHERE run_id=?", (now_iso(), reason, run_id))


def estimate_usd(provider, payload):
    """Upper end: request bytes / 3 input tokens + the full output allowance, list price, 25% margin."""
    tokens = 50 + len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) // 3
    return tokens, provider.cost({"input_tokens": tokens, "output_tokens": payload.get("max_tokens", 2000)}) * 1.25


def call_validated(conn, provider, *, role, agent, label_config, payload, validate, run_id, bucket,
                   max_spend=None, review_ids=(), meta=None, attempts=3, retry_payload=None, timeout=180.0):
    """Send `payload`; `validate(body) -> (output, errors)`. A rejected answer is logged as a failed call; one retry
    with `retry_payload(errors)` follows (if given). Returns (output, request_id). Raises CallRejected / ProviderError
    / ledger.BudgetExceeded."""
    tries, last = 0, None
    current = payload
    while tries < attempts:
        tries += 1
        tokens, est = estimate_usd(provider, current)
        request_id = uuid.uuid4().hex
        ledger.reserve(conn, request_id, provider.name, bucket, est,
                       ledger.caps_for(bucket, run_id, max_spend), run_id)
        with immediate(conn):
            conn.execute("""INSERT INTO calls (request_id, role, agent, model, tier, phase, outcome, label_config,
                                               review_ids_json, run_id, started_at, est_usd)
                            VALUES (?,?,?,?,?,'initial','in_flight',?,?,?,?,?)""",
                         (request_id, role, agent, provider.model, provider.tier, label_config,
                          json.dumps(list(review_ids)), run_id, now_iso(), est))
        req = Request(request_id, provider.model, list(review_ids), [], current, timeout, dict(meta or {}))
        t0 = time.perf_counter()
        try:
            resp = asyncio.run(asyncio.wait_for(provider.call(req), timeout))
        except (ProviderError, asyncio.TimeoutError) as e:
            if isinstance(e, asyncio.TimeoutError):
                e = ProviderError("transient", None, "client timeout", billed=None)
            with immediate(conn):
                conn.execute("""UPDATE calls SET outcome='failed', input_tokens=0, output_tokens=0, usage_available=0,
                                cost_usd=?, latency_ms=?, error=?, ended_at=? WHERE request_id=?""",
                             (0.0 if e.billed is False else est, int((time.perf_counter() - t0) * 1000), str(e),
                              now_iso(), request_id))
                (ledger.release(conn, request_id) if e.billed is False else ledger.settle(conn, request_id, est))
            if e.kind != "transient":
                raise
            last = str(e)
            time.sleep(queue.backoff(tries, 0.5, 10.0, e.retry_after))
            continue
        usage_ok = resp.usage is not None
        u = resp.usage or {}
        cost = provider.cost(u) if usage_ok else est
        try:
            output, errors = validate(resp.body)
        except ValueError as e:
            output, errors = None, [str(e)]
        with immediate(conn):
            conn.execute("""UPDATE calls SET outcome=?, input_tokens=?, output_tokens=?, cached_tokens=?, cache_write_tokens=?, cost_usd=?,
                                             latency_ms=?, error=?, usage_available=?, provider_request_id=?, ended_at=?
                            WHERE request_id=?""",
                         ("failed" if errors else "succeeded", int(u.get("input_tokens", 0)),
                          int(u.get("output_tokens", 0)), int(u.get("cached_tokens", 0) or 0),
                          int(u.get("cache_write_tokens", 0) or 0), cost,
                          resp.latency_ms or int((time.perf_counter() - t0) * 1000),
                          ("rejected: " + "; ".join(errors))[:2000] if errors else None, int(usage_ok),
                          resp.provider_request_id, now_iso(), request_id))
            ledger.settle(conn, request_id, cost)
        if not errors:
            return output, request_id
        last = "; ".join(errors)
        if retry_payload is None or current is not payload:
            break  # one content retry only
        current = retry_payload(errors)
    raise CallRejected(f"{role}: no acceptable answer after {tries} attempt(s): {last}")
