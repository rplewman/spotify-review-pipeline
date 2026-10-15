"""Shared rate limiter (PLAN §3.4): one token bucket per model in the DB, for requests and tokens per minute.

Every worker (and a second process) takes from the same row under BEGIN IMMEDIATE. A 429 with Retry-After blocks the
model for everyone. Celeris publishes no limits, so rpm/tpm are set from measurement (M5/M6).
"""
import time

from pipeline.db.store import immediate

BURST_SECONDS = 10.0  # bucket size = this many seconds of the sustained rate


def acquire(conn, model, tokens, rpm, tpm, now=None):
    """Take one request + `tokens` if available. Returns 0.0 when granted, else the seconds to wait before retrying."""
    now = time.time() if now is None else now
    req_cap, tok_cap = max(1.0, rpm / 60 * BURST_SECONDS), max(1.0, tpm / 60 * BURST_SECONDS)
    with immediate(conn):
        conn.execute("""INSERT INTO rate_limits (model, rpm, tpm, req_level, tok_level, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (model) DO UPDATE SET rpm=excluded.rpm, tpm=excluded.tpm""",
                     (model, rpm, tpm, req_cap, tok_cap, now))
        req, tok, updated, blocked = conn.execute(
            "SELECT req_level, tok_level, updated_at, blocked_until FROM rate_limits WHERE model=?", (model,)).fetchone()
        dt = max(0.0, now - updated)
        req = min(req_cap, req + dt * rpm / 60)
        tok = min(tok_cap, tok + dt * tpm / 60)
        need = min(float(tokens), tok_cap)  # one oversized request may run on a full bucket (the level goes negative)
        if blocked > now:
            wait = blocked - now
        elif req >= 1.0 and tok >= need:
            req, tok, wait = req - 1.0, tok - tokens, 0.0
        else:
            wait = max((1.0 - req) / (rpm / 60), (need - tok) / (tpm / 60), 0.01)
        conn.execute("UPDATE rate_limits SET req_level=?, tok_level=?, updated_at=? WHERE model=?", (req, tok, now, model))
    return wait


def block(conn, model, seconds, now=None):
    """After a 429: nobody calls `model` for `seconds` (caller holds the write transaction)."""
    until = (time.time() if now is None else now) + seconds
    conn.execute("UPDATE rate_limits SET blocked_until = MAX(blocked_until, ?) WHERE model = ?", (until, model))
