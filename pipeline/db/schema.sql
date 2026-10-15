-- Operational store (SQLite, WAL). PLAN §3.2. Applied idempotently by A1 Intake.
-- Downstream tables (verifier_outputs, batch job columns, analysis_cache, issues … recommendations) were added in M4.

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
    provider_request_id TEXT,             -- the provider's own ID when it returns one (Anthropic); Celeris has none
    ended_at        TEXT
);
CREATE INDEX IF NOT EXISTS ix_calls_run ON calls(run_id, outcome);

-- Intermediate agent outputs per distinct text (M3b): A3 decision + every probability, A4 route, A5 escalation answer.
-- Saved in the same transaction as the call that produced them, so a retry or resume never re-pays a finished step.
CREATE TABLE IF NOT EXISTS agent_outputs (
    text_sha256  TEXT NOT NULL,
    label_config TEXT NOT NULL,
    agent        TEXT NOT NULL CHECK (agent IN ('A3', 'A4', 'A5')),
    request_id   TEXT,                       -- the call that produced it (NULL for A4, which is code)
    output_json  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (text_sha256, label_config, agent)
);

-- Anthropic Message Batches jobs (A8 verifier). Inserted as 'building' (provider_job_id = 'pending:<job_key>') BEFORE
-- submission, so a crash is visible; the real provider ID replaces it right after submit. Resumed by ID, never resubmitted.
CREATE TABLE IF NOT EXISTS batch_jobs (
    provider_job_id TEXT PRIMARY KEY,
    item_ids_json   TEXT NOT NULL,              -- {request_id (= custom_id): [work_item ids]}
    status          TEXT NOT NULL,              -- building | submitted | ended | committed | abandoned
    submitted_at    TEXT,
    ended_at        TEXT,
    job_key         TEXT,                       -- local ID (lease owner of the job's items)
    role            TEXT,
    label_config    TEXT,
    run_id          TEXT,
    request_counts_json TEXT,                   -- provider's request_counts at the last poll
    committed_at    TEXT
);

-- A8 verifier labels per distinct text, keyed by the enrichment label_config they check and the verifier config.
-- The verifier never saw the first prediction; agreement is computed in code (pipeline/agents/verifier.py).
CREATE TABLE IF NOT EXISTS verifier_outputs (
    text_sha256   TEXT NOT NULL,
    label_config  TEXT NOT NULL,
    verify_config TEXT NOT NULL,
    topic         TEXT NOT NULL,
    intent        TEXT NOT NULL,
    severity      INTEGER NOT NULL,
    needs_review  INTEGER NOT NULL,
    request_id    TEXT,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (text_sha256, label_config, verify_config)
);

-- Downstream model outputs cached by an input hash (A9 names, A11 memo): a warm rerun makes zero calls.
CREATE TABLE IF NOT EXISTS analysis_cache (
    agent        TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    model        TEXT NOT NULL,
    output_json  TEXT NOT NULL,
    request_id   TEXT,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (agent, input_sha256, model)
);

-- Analysis outputs (A9-A11), replaced per label_config by each command; the files in outputs/ are the evidence.
CREATE TABLE IF NOT EXISTS issues (
    issue_id     TEXT NOT NULL,
    label_config TEXT NOT NULL,
    topic        TEXT NOT NULL,
    subtopic     TEXT NOT NULL,
    area         TEXT NOT NULL,
    name         TEXT NOT NULL,
    description  TEXT,
    name_source  TEXT NOT NULL,                 -- model | default
    member_count INTEGER NOT NULL,
    PRIMARY KEY (issue_id, label_config)
);
CREATE TABLE IF NOT EXISTS membership (
    issue_id     TEXT NOT NULL,
    review_id    TEXT NOT NULL,
    label_config TEXT NOT NULL,
    PRIMARY KEY (label_config, issue_id, review_id)
);
CREATE TABLE IF NOT EXISTS ranking (
    label_config    TEXT NOT NULL,
    rank            INTEGER NOT NULL,
    issue_id        TEXT NOT NULL,
    complaint_count INTEGER NOT NULL,
    severity_sum    INTEGER NOT NULL,
    mean_severity   TEXT NOT NULL,
    priority_score  INTEGER NOT NULL,
    PRIMARY KEY (label_config, issue_id)
);
CREATE TABLE IF NOT EXISTS aggregates (
    label_config TEXT NOT NULL,
    issue_id     TEXT NOT NULL,
    row_json     TEXT NOT NULL,
    PRIMARY KEY (label_config, issue_id)
);
CREATE TABLE IF NOT EXISTS area_aggregates (
    label_config TEXT NOT NULL,
    area         TEXT NOT NULL,
    row_json     TEXT NOT NULL,
    PRIMARY KEY (label_config, area)
);
CREATE TABLE IF NOT EXISTS claims (
    label_config TEXT NOT NULL,
    claim_id     TEXT NOT NULL,
    issue_id     TEXT NOT NULL,
    metric       TEXT NOT NULL,
    value        TEXT NOT NULL,
    PRIMARY KEY (label_config, claim_id)
);
CREATE TABLE IF NOT EXISTS recommendations (
    label_config TEXT NOT NULL,
    position     INTEGER NOT NULL,
    row_json     TEXT NOT NULL,
    request_id   TEXT,
    PRIMARY KEY (label_config, position)
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
