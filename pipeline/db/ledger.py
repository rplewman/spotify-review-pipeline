"""Spend ledger (PLAN §3.4, §4): reserve → settle | release, shared by every worker and process through the DB.

Before a call, a worker reserves the call's estimated list-price cost. The reservation is refused (BudgetExceeded) when
spent + outstanding reservations + this estimate would pass any cap that applies: the bucket cap, the run's --max-spend,
and the project total. After the call the reservation is settled at the actual cost (or at the estimate when the provider
gives no usage or the billing of an error is unknown), or released when the provider says the attempt is not charged.
"""
from pipeline.db.store import immediate, now_iso

# PLAN §4 bucket caps (list price, USD; v4.2, set in M6b). The project total binds across all buckets.
BUCKET_CAPS = {"pilots": 0.50, "decisions": 2.00, "escalation": 5.00, "verification": 1.00, "analysis": 0.25,
               "reserve": 1.25}
PROJECT_CAP = 10.00


class BudgetExceeded(Exception):
    pass


def _spent_and_outstanding(conn, where="", params=()):
    """(settled USD, outstanding reserved USD) over ledger rows matching `where`."""
    spent = conn.execute(f"SELECT COALESCE(SUM(usd), 0) FROM ledger WHERE kind='settle' {where}", params).fetchone()[0]
    outstanding = conn.execute(f"""SELECT COALESCE(SUM(r.usd), 0) FROM ledger r
                                   WHERE r.kind='reserve' {where.replace('AND ', 'AND r.')}
                                     AND NOT EXISTS (SELECT 1 FROM ledger x WHERE x.request_id = r.request_id
                                                     AND x.kind IN ('settle', 'release'))""", params).fetchone()[0]
    return spent, outstanding


def totals(conn, bucket=None, run_id=None):
    where, params = "", []
    if bucket is not None:
        where += " AND bucket = ?"
        params.append(bucket)
    if run_id is not None:
        where += " AND run_id = ?"
        params.append(run_id)
    spent, outstanding = _spent_and_outstanding(conn, where, params)
    return {"spent": spent, "reserved": outstanding}


def caps_for(bucket, run_id, max_spend=None, bucket_cap=None):
    """The caps a reservation must respect: [(label, bucket, run_id, cap_usd)]."""
    caps = [("project", None, None, PROJECT_CAP)]
    cap = bucket_cap if bucket_cap is not None else BUCKET_CAPS.get(bucket)
    if cap is not None:
        caps.append((f"bucket:{bucket}", bucket, None, cap))
    if max_spend is not None:
        caps.append(("run:max_spend", None, run_id, max_spend))
    return caps


def reserve(conn, request_id, provider, bucket, usd, caps, run_id=None):
    """Atomically check every cap and record the reservation. Raises BudgetExceeded (nothing written) if any cap would pass."""
    with immediate(conn):
        for label, cap_bucket, cap_run, cap in caps:
            t = totals(conn, cap_bucket, cap_run)
            if t["spent"] + t["reserved"] + usd > cap + 1e-12:
                raise BudgetExceeded(f"{label}: spent {t['spent']:.6f} + reserved {t['reserved']:.6f} "
                                     f"+ next {usd:.6f} > cap {cap:.6f}")
        conn.execute("INSERT INTO ledger (request_id, provider, bucket, kind, usd, created_at, run_id) "
                     "VALUES (?, ?, ?, 'reserve', ?, ?, ?)", (request_id, provider, bucket, usd, now_iso(), run_id))


def _close(conn, request_id, kind, usd):
    row = conn.execute("SELECT provider, bucket, run_id FROM ledger WHERE request_id=? AND kind='reserve'",
                       (request_id,)).fetchone()
    if row is None or conn.execute("SELECT 1 FROM ledger WHERE request_id=? AND kind IN ('settle','release')",
                                   (request_id,)).fetchone():
        return  # unknown or already closed: closing twice must never double-count
    conn.execute("INSERT INTO ledger (request_id, provider, bucket, kind, usd, created_at, run_id) VALUES (?,?,?,?,?,?,?)",
                 (request_id, row[0], row[1], kind, usd, now_iso(), row[2]))


def settle(conn, request_id, usd):
    """Record the actual cost (caller holds the write transaction)."""
    _close(conn, request_id, "settle", usd)


def release(conn, request_id):
    """The attempt was not charged; the reservation no longer counts (caller holds the write transaction)."""
    _close(conn, request_id, "release", 0.0)
