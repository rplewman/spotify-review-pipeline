-- Operational store (SQLite, WAL). PLAN §3.2. Applied idempotently by A1 Intake.
-- Analysis tables (issues, membership, aggregates, ranking, claims, recommendations) are added in M4.

-- All 660,622 source rows. scope_rank = 1-based rank in the official seed order
-- (lowest SHA-256("berkeley-fall-2026-assignment-5-v1:" + review_id)) among unique, nonempty, valid-rating rows;
-- NULL for ineligible rows. scope: random_100k | frequency_addon | out_of_scope | empty_text.
CREATE TABLE IF NOT EXISTS source_reviews (
    review_id        TEXT PRIMARY KEY,
    csv_row          INTEGER NOT NULL UNIQUE,   -- 1-based record number in file order
    source_sha256    TEXT NOT NULL,             -- checker row_sha
    text_sha256      TEXT NOT NULL,             -- sha256 of the exact review_text (UTF-8)
    seed_hash        TEXT NOT NULL,             -- hex sha256(seed + ':' + review_id); hex order = numeric order
    review_text      TEXT NOT NULL,
    review_rating    TEXT NOT NULL,
    review_likes     TEXT NOT NULL,
    app_version      TEXT NOT NULL,
    review_timestamp TEXT NOT NULL,
    eligible         INTEGER NOT NULL,
    scope            TEXT,
    scope_rank       INTEGER UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_source_text ON source_reviews(text_sha256);
CREATE INDEX IF NOT EXISTS ix_source_scope ON source_reviews(scope, scope_rank);

-- One row per distinct in-scope text. original_review_id = the lowest-ranked in-scope row with this text
-- (the ID that is sent to models; duplicates are completed via cache_source_id -> this ID).
CREATE TABLE IF NOT EXISTS texts (
    text_sha256        TEXT PRIMARY KEY,
    review_text        TEXT NOT NULL,
    original_review_id TEXT NOT NULL REFERENCES source_reviews(review_id),
    scope_rank         INTEGER NOT NULL,
    rows_in_scope      INTEGER NOT NULL,
    rows_total         INTEGER NOT NULL
);

-- The queue (§3.4).
CREATE TABLE IF NOT EXISTS work_items (
    id               INTEGER PRIMARY KEY,
    text_sha256      TEXT NOT NULL REFERENCES texts(text_sha256),
    stage            TEXT NOT NULL,
    label_config     TEXT NOT NULL,
    scope_rank       INTEGER NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'leased', 'done', 'failed', 'quarantined')),
    lease_owner      TEXT,
    lease_expires_at TEXT,
    attempts         INTEGER NOT NULL DEFAULT 0,  -- claims (incremented on every lease)
    last_error       TEXT,
    available_at     TEXT,                        -- backoff: not claimable before this (ISO UTC)
    invalid_attempts INTEGER NOT NULL DEFAULT 0,  -- invalid outputs; the 2nd quarantines the item
    UNIQUE (text_sha256, stage, label_config)
);
CREATE INDEX IF NOT EXISTS ix_work_claim ON work_items(stage, label_config, status, scope_rank);

-- The result cache (§3.6).
CREATE TABLE IF NOT EXISTS results (
    text_sha256     TEXT NOT NULL,
    label_config    TEXT NOT NULL,
    topic           TEXT NOT NULL,
    intent          TEXT NOT NULL,
    sentiment       REAL NOT NULL,
    severity        INTEGER NOT NULL,
    entities_json   TEXT NOT NULL,
    evidence_quote  TEXT NOT NULL,
    needs_review    INTEGER NOT NULL,
    decision_source TEXT NOT NULL,             -- decision | escalation
    probs_json      TEXT,
    request_id      TEXT,
    UNIQUE (text_sha256, label_config)
);

-- Final record per review_id (completed or quarantined).
CREATE TABLE IF NOT EXISTS records (
    review_id       TEXT PRIMARY KEY REFERENCES source_reviews(review_id),
    source_sha256   TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('completed', 'quarantined')),
    reason          TEXT,
    topic           TEXT,
    intent          TEXT,
    sentiment       REAL,
    severity        INTEGER,
    entities_json   TEXT,
    evidence_quote  TEXT,
    needs_review    INTEGER,
    label_config    TEXT,
    cache_source_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_records_cfg ON records(label_config, status);

-- Every model-call attempt.
CREATE TABLE IF NOT EXISTS calls (
    request_id      TEXT PRIMARY KEY,
    role            TEXT NOT NULL,
    agent           TEXT NOT NULL,
    model           TEXT NOT NULL,
    tier            TEXT NOT NULL DEFAULT 'standard',
    phase           TEXT,
    outcome         TEXT NOT NULL,
    label_config    TEXT,
    review_ids_json TEXT NOT NULL,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cached_tokens   INTEGER,
    cost_usd        REAL,
    latency_ms      INTEGER,
    error           TEXT,
    run_id          TEXT,
    started_at      TEXT,
    usage_available INTEGER,                  -- 0 = provider gave no usage (tokens exported as 0 + flag)
    est_usd         REAL,                     -- the ledger reservation for this attempt
    ended_at        TEXT
);
CREATE INDEX IF NOT EXISTS ix_calls_run ON calls(run_id, outcome);

CREATE TABLE IF NOT EXISTS batch_jobs (
    provider_job_id TEXT PRIMARY KEY,
    item_ids_json   TEXT NOT NULL,
    status          TEXT NOT NULL,
    submitted_at    TEXT,
    ended_at        TEXT
);

CREATE TABLE IF NOT EXISTS ledger (
    id          INTEGER PRIMARY KEY,
    request_id  TEXT,
    provider    TEXT NOT NULL,
    bucket      TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('reserve', 'settle', 'release')),
    usd         REAL NOT NULL,
    created_at  TEXT NOT NULL,
    run_id      TEXT
);
CREATE INDEX IF NOT EXISTS ix_ledger_req ON ledger(request_id, kind);

-- Shared rate limiter (token buckets per model; every worker and process uses the same row). Times are epoch seconds.
CREATE TABLE IF NOT EXISTS rate_limits (
    model         TEXT PRIMARY KEY,
    rpm           REAL NOT NULL,
    tpm           REAL NOT NULL,
    req_level     REAL NOT NULL,
    tok_level     REAL NOT NULL,
    updated_at    REAL NOT NULL,
    blocked_until REAL NOT NULL DEFAULT 0     -- set from Retry-After on a 429
);

CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    phase       TEXT,                         -- initial | resume
    started_at  TEXT,
    ended_at    TEXT,
    stop_reason TEXT,
    heartbeat_at TEXT
);

CREATE TABLE IF NOT EXISTS checkpoints (
    id                 INTEGER PRIMARY KEY,
    run_id             TEXT NOT NULL,
    label              TEXT NOT NULL,
    completed_ids_json TEXT NOT NULL,
    created_at         TEXT NOT NULL
);
