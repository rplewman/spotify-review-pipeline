"""M5 pilot (PLAN §5 M5, COST_CALCULATOR.md): every stage on `cost_100.csv`, cold then warm, and the evidence files.

    python -m pipeline.cli pilot [--provider fake|celeris] [--paid --max-spend USD] [--db state/pilot.db] [--out cost]

Cold = a NEW database (empty result cache), the unchanged input (checksum verified against the dataset manifest),
1 worker, all stages: ingest → enrich (A3–A7) → verify (A8, real time, declared sample rate) → group → rank → memo.
Warm = the same commands again on the same database: zero new enrichment calls is the expected result; any
downstream call is disclosed. Each stage runs through `pipeline.cli` exactly as a user would run it; this module only
times the stages and exports the evidence:

    cost/pilot_records.jsonl   the 100 input rows' records (separate from grading/)
    cost/pilot_calls.jsonl     every attempt: run ID, request ID, stage, model, tier, timing, usage, outcome
    cost/usage.csv             one row per billed item per attempt, keyed to rates.csv (provider, model, tier, item)
    cost/pilot_measured.json   input checksum, counts, per-stage config, stage times cold and warm

The calculator (cost/calculator.py) prices usage.csv × rates.csv offline; nothing here computes the report.
"""
import csv
import hashlib
import json
import time
from pathlib import Path

from pipeline.agents import decision, escalation, extractor, grouper, intake, memo, verifier
from pipeline.db import ledger
from pipeline.db.store import connect, migrate, now_iso
from pipeline.schema import SCHEMA_VERSION

PILOT_FILE = "cost_100.csv"
PILOT_SAMPLE_RATE = 0.2  # declared pilot verification rate (100 reviews × 5% would verify ~4 texts)
USAGE_FIELDS = ["request_id", "run_id", "pilot_phase", "stage", "agent", "provider", "model", "tier", "item", "units",
                "unit", "usage_available", "outcome"]


class PilotError(Exception):
    pass


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_input(csv_path, raw_dir):
    """The pilot input must be the published cost_100.csv, byte for byte."""
    csv_path = Path(csv_path)
    manifest = json.loads((Path(raw_dir) / "manifest.json").read_text(encoding="utf-8"))
    expected = manifest["files"].get(csv_path.name, {}).get("sha256")
    actual = sha256_file(csv_path)
    if expected is None:
        raise PilotError(f"{csv_path.name} is not in the dataset manifest")
    if actual != expected:
        raise PilotError(f"{csv_path.name} checksum {actual} != manifest {expected} (input changed)")
    with open(csv_path, encoding="utf-8", newline="") as f:
        ids = [r["review_id"] for r in csv.DictReader(f)]
    return {"file": csv_path.name, "sha256": actual, "manifest_sha256": expected, "match": True, "rows": len(ids),
            "ids_sha256": hashlib.sha256("\n".join(ids).encode()).hexdigest()}


def ingest_pilot(db_path, csv_path):
    """A1 on the pilot file: every row is in scope (seed order), distinct texts, empty texts quarantined."""
    conn = intake.connect(db_path)
    try:
        intake.apply_schema(conn)
        stats = intake.load_source(conn, csv_path)
        intake.assign_scope(conn, size=stats["records"])
        intake.build_texts(conn)
        intake.quarantine_empty(conn)
        n_texts = conn.execute("SELECT COUNT(*) FROM texts").fetchone()[0]
    finally:
        conn.close()
    return {"rows": stats["records"], "distinct_texts": n_texts, "empty_review_text": stats["empty_review_text"]}


def _calls_count(db):
    conn = connect(db)
    migrate(conn)
    try:
        return conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
    finally:
        conn.close()


def _remaining(db, max_spend):
    if max_spend is None:
        return None
    conn = connect(db)
    try:
        spent = ledger.totals(conn)
    finally:
        conn.close()
    return round(max_spend - spent["spent"] - spent["reserved"], 6)


def _stage(log, name, fn, db):
    """Time one stage; record its new calls and the runs it started."""
    before, started = _calls_count(db), now_iso()
    t0 = time.perf_counter()
    rc = fn()
    seconds = time.perf_counter() - t0
    conn = connect(db)
    try:
        runs = [r[0] for r in conn.execute("SELECT run_id FROM runs WHERE started_at >= ? ORDER BY started_at",
                                           (started,))]
    finally:
        conn.close()
    log.append({"stage": name, "seconds": round(seconds, 3), "rc": rc, "new_calls": _calls_count(db) - before,
                "run_ids": runs})
    if rc:
        raise PilotError(f"stage {name} failed (exit {rc}); see the output above")
    return rc


def run_phase(opts, phase, cli_main):
    """One pass of every stage. Returns the stage log. `cli_main` = pipeline.cli.main (passed in to avoid a cycle)."""
    db, root = str(opts.db), str(opts.root)
    log = []

    def money():
        if not opts.paid:
            return []
        left = _remaining(opts.db, opts.max_spend)
        if left <= 0:
            raise PilotError(f"pilot --max-spend {opts.max_spend} used up before this stage")
        return ["--paid", "--max-spend", f"{left:.6f}"]

    common = ["--db", db, "--root", root]
    t0 = time.perf_counter()
    if phase == "cold":
        def ingest():
            opts.ingest_result.update(ingest_pilot(opts.db, opts.input))
            return 0
        _stage(log, "ingest", ingest, opts.db)
    enrich = ["enrich", *common, "--provider", opts.provider, "--escalation", opts.escalation, "--workers", "1",
              "--batch-size", str(opts.batch_size), "--bucket", "pilots", "--label-config", opts.label_config]
    if opts.provider == "fake":
        enrich += ["--fake-script", opts.fake_script]
    _stage(log, "enrich", lambda: cli_main(enrich + money()), opts.db)
    down = [*common, "--provider", opts.downstream, "--label-config", opts.label_config]
    _stage(log, "verify", lambda: cli_main(["verify", *down, "--standard", "--sample-rate", str(opts.sample_rate),
                                            *money()]), opts.db)
    _stage(log, "group", lambda: cli_main(["group", *down, *money()]), opts.db)
    _stage(log, "rank", lambda: cli_main(["rank", "--root", root, "--db", db]), opts.db)
    _stage(log, "memo", lambda: cli_main(["memo", *down, *money()]), opts.db)
    return {"stages": log, "total_seconds": round(time.perf_counter() - t0, 3),
            "new_calls": sum(s["new_calls"] for s in log),
            "new_enrich_calls": sum(s["new_calls"] for s in log if s["stage"] == "enrich")}


# ---------- evidence export ----------
def provider_of(model):
    if model.startswith("celeris"):
        return "celeris"
    if model.startswith("claude"):
        return "anthropic"
    return "fake"


def rate_tier(provider, tier):
    """calls.tier → the rates.csv tier key (Haiku prices are for prompts ≤ 100K tokens; ours are far below)."""
    if provider == "anthropic":
        return {"standard": "standard_le100k", "batch": "batch_le100k"}[tier]
    return tier


def usage_items(call):
    """One call row → billed items (mutually exclusive token classes; cached and cache-write input are NOT also counted
    as plain input; output tokens already include any thinking tokens, so they are counted once)."""
    provider = provider_of(call["model"])
    inp, cached = call["input_tokens"] or 0, call["cached_tokens"] or 0
    write = call["cache_write_tokens"] or 0
    if not call["usage_available"]:
        # no usage from the provider: billing unknown, the ledger settled the estimate → reported apart, never as 0
        return [("unmeasured_attempt_est_usd", call["cost_usd"] or 0.0, "USD (ledger estimate)")] if call["cost_usd"] else []
    if provider == "anthropic":
        items = [("input_tokens", inp - cached - write), ("cache_read_tokens", cached),
                 ("cache_write_5m_tokens", write), ("output_tokens", call["output_tokens"] or 0)]
    else:
        items = [("input_tokens", inp - cached), ("cached_input_tokens", cached), ("output_tokens", call["output_tokens"] or 0)]
    return [(item, units, "tokens") for item, units in items if units]


def export(opts, phases):
    out = Path(opts.out)
    out.mkdir(parents=True, exist_ok=True)
    conn = connect(opts.db)
    conn.row_factory = __import__("sqlite3").Row
    try:
        phase_of = {rid: name for name, ph in phases.items() for s in ph["stages"] for rid in s["run_ids"]}
        recs = conn.execute("""SELECT s.review_id, s.csv_row, r.status, r.reason, r.topic, r.intent, r.sentiment,
                                      r.severity, r.entities_json, r.evidence_quote, r.needs_review, r.label_config,
                                      r.cache_source_id, s.source_sha256
                               FROM source_reviews s LEFT JOIN records r ON r.review_id = s.review_id
                               ORDER BY s.csv_row""").fetchall()
        with open(out / "pilot_records.jsonl", "w", encoding="utf-8", newline="\n") as f:
            for r in recs:
                d = dict(r)
                d["entities"] = json.loads(d.pop("entities_json")) if d["entities_json"] else None
                d["needs_review"] = None if d["needs_review"] is None else bool(d["needs_review"])
                f.write(json.dumps(d, ensure_ascii=False, sort_keys=True) + "\n")
        calls = [dict(c) for c in conn.execute("SELECT * FROM calls ORDER BY started_at, request_id")]
        with open(out / "pilot_calls.jsonl", "w", encoding="utf-8", newline="\n") as f:
            for c in calls:
                c["review_ids"] = json.loads(c.pop("review_ids_json"))
                c["provider"] = provider_of(c["model"])
                c["pilot_phase"] = phase_of.get(c["run_id"], "other")
                f.write(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n")
        with open(out / "usage.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, USAGE_FIELDS, lineterminator="\n")
            w.writeheader()
            for c in calls:
                provider = c["provider"]
                for item, units, unit in usage_items(c):
                    w.writerow({"request_id": c["request_id"], "run_id": c["run_id"], "pilot_phase": c["pilot_phase"],
                                "stage": c["role"], "agent": c["agent"], "provider": provider, "model": c["model"],
                                "tier": rate_tier(provider, c["tier"]), "item": item, "units": units, "unit": unit,
                                "usage_available": c["usage_available"], "outcome": c["outcome"]})
        routes = dict(conn.execute("""SELECT json_extract(output_json, '$.route'), COUNT(*) FROM agent_outputs
                                      WHERE agent='A4' AND label_config=? GROUP BY 1""", (opts.label_config,)).fetchall())
        status = dict(conn.execute("SELECT status, COUNT(*) FROM records GROUP BY 1").fetchall())
        reused = conn.execute("SELECT COUNT(*) FROM records WHERE cache_source_id IS NOT NULL").fetchone()[0]
        sample = conn.execute("""SELECT COUNT(*) FROM work_items WHERE stage='verify'""").fetchone()[0]
        verified = conn.execute("SELECT COUNT(*) FROM verifier_outputs WHERE label_config=?",
                                (opts.label_config,)).fetchone()[0]
    finally:
        conn.close()
    esc_model = None if opts.escalation == "none" else ("claude-haiku-5-5" if opts.escalation == "anthropic"
                                                         else "fake-escalation")
    down_model = "claude-haiku-5-5" if opts.downstream == "anthropic" else "fake-analysis"
    measured = {
        "generated_at": now_iso(),
        "input": opts.input_check,
        "ingest": opts.ingest_result,
        "records": {"completed": status.get("completed", 0), "quarantined": status.get("quarantined", 0),
                    "rows": len(recs), "completed_via_exact_text_reuse": reused},
        "unique_texts": opts.ingest_result.get("distinct_texts"),
        "routes": routes,
        "verification": {"declared_sample_rate": opts.sample_rate, "seed": verifier.DEFAULT_SEED,
                         "sampled_texts": sample, "verified_texts": verified, "tier": "standard (real time)"},
        "label_config": opts.label_config,
        "stages": {
            "enrich_decision": {"agent": "A3", "provider": provider_of(opts.decision_model) if opts.provider != "fake"
                                else "fake", "model": opts.decision_model, "effort": "n/a (decision model)",
                                "prompt_version": decision.PROMPT_VERSION, "schema_version": SCHEMA_VERSION,
                                "shape": "A", "batch_size": opts.batch_size, "workers": 1},
            "enrich_escalation": {"agent": "A5", "model": esc_model, "effort": escalation.EFFORT,
                                  "prompt_version": "escalation-prompt-v1", "gate": 0.60, "esc_fraction_cap": 0.15,
                                  "batch_size": f"≤ {opts.batch_size} (the batch's escalated texts)", "workers": 1},
            "extract_validate": {"agent": "A6+A7", "model": "code", "version": f"{extractor.EXTRACT_VERSION}+{SCHEMA_VERSION}"},
            "verify": {"agent": "A8", "model": down_model, "effort": verifier.EFFORT,
                       "prompt_version": verifier.PROMPT_VERSION, "batch_size": 50, "workers": 1, "tier": "standard"},
            "group": {"agent": "A9", "model": down_model, "effort": "low", "prompt_version": grouper.PROMPT_VERSION},
            "rank": {"agent": "A10", "model": "code"},
            "memo": {"agent": "A11", "model": down_model, "effort": "low", "prompt_version": memo.PROMPT_VERSION},
        },
        "phases": phases,
        "local_compute": {"usd": "unknown (local laptop CPU and SQLite; not billed, not metered)",
                          "note": "reported apart from API spend; times above include local work"},
    }
    (out / "pilot_measured.json").write_text(json.dumps(measured, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return measured


def run_pilot(opts, cli_main):
    opts.input_check = verify_input(opts.input, opts.raw_dir)
    if opts.input_check["rows"] != 100:
        raise PilotError(f"the pilot input must have 100 rows, found {opts.input_check['rows']}")
    if Path(opts.db).exists():
        raise PilotError(f"{opts.db} exists: the cold run needs an empty result cache (pass a new --db)")
    opts.ingest_result = {}
    phases = {"cold": run_phase(opts, "cold", cli_main)}
    phases["warm"] = run_phase(opts, "warm", cli_main)
    return export(opts, phases)
