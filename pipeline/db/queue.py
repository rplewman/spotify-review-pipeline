"""The work queue (PLAN §3.4): lease-based claiming under BEGIN IMMEDIATE, lease expiry, fenced state changes.

A work item is one distinct text for one (stage, label_config). Status flow:
    pending ──claim──▶ leased ──done──▶ done
                         │ └─transient error──▶ pending (available_at = backoff) ──too many attempts──▶ failed
                         └─invalid output──▶ pending (retry once) ──2nd invalid──▶ quarantined
An expired lease (crashed worker) is claimable again. Every state change after a claim is fenced on the lease owner:
a worker that lost its lease changes nothing.

Functions other than `claim` and `release_owner` expect the caller to hold a write transaction (`store.immediate`),
so the call log, the ledger, the cache and the queue change together or not at all.
"""
import random

from pipeline.db.store import immediate, now_iso

CLAIM = """
UPDATE work_items SET status = 'leased', lease_owner = :owner, lease_expires_at = :expires, attempts = attempts + 1
WHERE id IN (
    SELECT id FROM (
        SELECT * FROM (SELECT id, scope_rank FROM work_items       -- pending, in rank order (index walk, stops at :n)
                       WHERE stage = :stage AND label_config = :cfg AND status = 'pending'
                         AND (available_at IS NULL OR available_at <= :now)
                       ORDER BY scope_rank LIMIT :n)
        UNION ALL
        SELECT * FROM (SELECT id, scope_rank FROM work_items       -- expired leases (crashed workers)
                       WHERE stage = :stage AND label_config = :cfg AND status = 'leased' AND lease_expires_at < :now
                       ORDER BY scope_rank LIMIT :n))
    ORDER BY scope_rank LIMIT :n)
RETURNING id
"""


def claim(conn, owner, stage, label_config, n, lease_seconds=300, run_id=None):
    """Atomically lease up to `n` items (lowest scope_rank first). Returns dicts with the text and its original ID."""
    if n <= 0:
        return []
    with immediate(conn):
        ids = [r[0] for r in conn.execute(CLAIM, {"owner": owner, "expires": now_iso(lease_seconds), "stage": stage,
                                                   "cfg": label_config, "now": now_iso(), "n": n})]
        if run_id:
            conn.execute("UPDATE runs SET heartbeat_at = ? WHERE run_id = ?", (now_iso(), run_id))
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = conn.execute(f"""SELECT w.id, w.text_sha256, t.original_review_id, t.review_text, w.attempts, w.scope_rank
                            FROM work_items w JOIN texts t ON t.text_sha256 = w.text_sha256
                            WHERE w.id IN ({marks}) ORDER BY w.scope_rank""", ids).fetchall()
    keys = ("id", "text_sha256", "review_id", "review_text", "attempts", "scope_rank")
    return [dict(zip(keys, r)) for r in rows]


def held(conn, owner, ids):
    """The subset of `ids` this owner still holds a lease on (the fence)."""
    if not ids:
        return set()
    marks = ",".join("?" * len(ids))
    return {r[0] for r in conn.execute(
        f"SELECT id FROM work_items WHERE id IN ({marks}) AND status = 'leased' AND lease_owner = ?", [*ids, owner])}


def _set(conn, ids, sql, *params):
    for i in ids:
        conn.execute(sql, (*params, i))


def mark_done(conn, ids):
    _set(conn, ids, "UPDATE work_items SET status='done', lease_owner=NULL, lease_expires_at=NULL, last_error=NULL "
                    "WHERE id = ?")


def requeue(conn, ids, error, delay_seconds=0.0):
    _set(conn, ids, "UPDATE work_items SET status='pending', lease_owner=NULL, lease_expires_at=NULL, last_error=?, "
                    "available_at=? WHERE id = ?", error, now_iso(delay_seconds) if delay_seconds > 0 else None)


def fail(conn, ids, error):
    _set(conn, ids, "UPDATE work_items SET status='failed', lease_owner=NULL, lease_expires_at=NULL, last_error=? "
                    "WHERE id = ?", error)


def quarantine(conn, ids, reason):
    _set(conn, ids, "UPDATE work_items SET status='quarantined', lease_owner=NULL, lease_expires_at=NULL, last_error=? "
                    "WHERE id = ?", reason)


def mark_invalid(conn, item_id, reason, max_invalid=2):
    """Invalid output for one item: retry once, quarantine on the second. Returns True if quarantined."""
    n = conn.execute("UPDATE work_items SET invalid_attempts = invalid_attempts + 1 WHERE id = ? "
                     "RETURNING invalid_attempts", (item_id,)).fetchone()[0]
    if n >= max_invalid:
        quarantine(conn, [item_id], f"invalid_output: {reason}")
        return True
    requeue(conn, [item_id], f"invalid_output: {reason}")
    return False


def backoff(attempts, base=1.0, cap=60.0, retry_after=None):
    """Exponential backoff with full jitter; a provider Retry-After is a floor."""
    delay = random.uniform(0, min(cap, base * 2 ** max(0, attempts - 1)))
    return max(delay, retry_after or 0.0)


def release(conn, ids, owner):
    """Give leased items back unprocessed (the claim does not count as an attempt)."""
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    conn.execute(f"""UPDATE work_items SET status='pending', lease_owner=NULL, lease_expires_at=NULL,
                     attempts = MAX(attempts - 1, 0)
                     WHERE id IN ({marks}) AND status='leased' AND lease_owner = ?""", [*ids, owner])


def release_owner(conn, owner_prefix):
    """Release every lease whose owner starts with `owner_prefix` (a run's workers). Own transaction."""
    with immediate(conn):
        return conn.execute("""UPDATE work_items SET status='pending', lease_owner=NULL, lease_expires_at=NULL,
                               attempts = MAX(attempts - 1, 0)
                               WHERE status='leased' AND substr(lease_owner, 1, ?) = ?""",
                            (len(owner_prefix), owner_prefix)).rowcount


def counts(conn, stage, label_config):
    rows = conn.execute("SELECT status, COUNT(*) FROM work_items WHERE stage=? AND label_config=? GROUP BY status",
                        (stage, label_config))
    return {s: n for s, n in rows}


def has_open_work(conn, stage, label_config):
    """True while anything is pending (maybe delayed) or leased: a worker should wait rather than exit."""
    return conn.execute("SELECT 1 FROM work_items WHERE stage=? AND label_config=? AND status IN ('pending','leased') "
                        "LIMIT 1", (stage, label_config)).fetchone() is not None
