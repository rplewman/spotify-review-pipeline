"""Pipeline command line. Runs without Claude (PLAN §3.1).

    python -m pipeline.cli ingest  [--csv data/raw/spotify_reviews_18months.csv] [--db state/pipeline.db]
    python -m pipeline.cli enrich  [--provider fake|celeris] [--escalation fake|anthropic|none] [--shape A|B] [--gate 0.60]
                                   [--esc-fraction 0.15] [--paid --max-spend USD] [--workers N] [--limit N] ...
    python -m pipeline.cli resume  [same provider flags]   continue the last stopped/crashed run (phase "resume")
    python -m pipeline.cli status
    python -m pipeline.cli verify  [--provider fake|anthropic] [--sample-rate 0.05] [--seed verify-v1] [--standard]
                                   A8 on a declared seeded sample: Batch API (default; resumes an open job by ID) or
                                   real time with --standard; then the disagreement report + the planted-error test
    python -m pipeline.cli group   [--provider fake|anthropic]   A9: membership (code) + issue names (one call)
    python -m pipeline.cli rank    [--root .]                    A10: ranking + aggregates from saved files, zero calls
    python -m pipeline.cli memo    [--provider fake|anthropic]   A11: memo, claims, recommendations (one call)
    python -m pipeline.cli analyze [--provider fake|anthropic]   group → rank → memo
    python -m pipeline.cli run --input <csv>              ingest → enrich → verify → group → rank → memo
    python -m pipeline.cli export | publish               arrive in M10 / M11

Money rule: a paid provider runs only with --paid AND --max-spend. The fake provider is offline and free.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from pipeline.agents import grouper, intake, memo, ranker, verifier
from pipeline.batch_runner import BatchConfig, run_verify_batch
from pipeline.db import ledger, queue
from pipeline.db.store import connect, migrate
from pipeline.oneshot import end_run, start_run
from pipeline.orchestrator import Orchestrator, RunConfig, recover
from pipeline.providers.celeris import CelerisProvider
from pipeline.providers.claude import ClaudeBatchProvider, ClaudeProvider
from pipeline.providers.fake import FakeBatchProvider, FakeProvider, parse_script
from pipeline.stages import EnrichStage, PassthroughStage, make_label_config

PAID_PROVIDERS = ("celeris", "anthropic")
STAGE_DEFAULTS = {"stage": "chain", "shape": "A", "gate": 0.60, "esc_fraction": 0.15, "esc_max_spend": None}


def add_enrich_flags(p, resume=False):
    d = None if resume else argparse.SUPPRESS  # resume: unset flags inherit the stopped run's config
    p.add_argument("--db", type=Path, default=Path("state/pipeline.db"))
    p.add_argument("--root", type=Path, default=Path("."), help="where outputs/run_log.jsonl and quarantine.jsonl go")
    p.add_argument("--provider", choices=("fake", "celeris"), default=None if resume else "fake",
                   help="A3 decision provider")
    p.add_argument("--escalation", choices=("fake", "anthropic", "none"), default=None,
                   help="A5 provider (default: fake with --provider fake, else anthropic = Claude Haiku 5.5)")
    p.add_argument("--stage", choices=("chain", "passthrough"), default=None,
                   help="chain = A3-A7 (default); passthrough = the M3a single-call stage (fake only)")
    p.add_argument("--shape", choices=("A", "B"), default=None, help="Celeris request shape (PLAN §3.8); B needs --batch-size 1")
    p.add_argument("--gate", type=float, default=None, help="A4 escalation gate on the top probability (default 0.60)")
    p.add_argument("--esc-fraction", type=float, default=None, help="A4 cap: max escalated share of the queue (default 0.15)")
    p.add_argument("--esc-max-spend", type=float, default=None, help="A5 $ sub-cap (default: PLAN §4 escalation bucket)")
    p.add_argument("--label-config", default=d)
    p.add_argument("--workers", type=int, default=d)
    p.add_argument("--batch-size", type=int, default=d)
    p.add_argument("--limit", type=int, default=d, help="max items to claim in this run")
    p.add_argument("--max-minutes", type=float, default=d)
    p.add_argument("--lease-seconds", type=float, default=d)
    p.add_argument("--bucket", default=d, help=f"ledger bucket ({', '.join(ledger.BUCKET_CAPS)})")
    p.add_argument("--paid", action="store_true", help="allow a paid provider (needs --max-spend)")
    p.add_argument("--max-spend", type=float, default=None, help="this run's spend cap in USD (list price)")
    p.add_argument("--fake-script", default="", help="fake provider behaviours in call order, e.g. valid,429,valid")
    p.add_argument("--fake-latency", type=float, default=0.0)


def resolve_settings(args, spec=None):
    """Stage settings from the flags, else the resumed run's provider spec, else the defaults."""
    spec = spec or {}
    for key, default in STAGE_DEFAULTS.items():
        if getattr(args, key, None) is None:
            setattr(args, key, spec.get(key, default))
    if getattr(args, "escalation", None) is None:
        args.escalation = spec.get("escalation") or ("fake" if args.provider == "fake" else "anthropic")


def paid_parts(args):
    return [p for p in (args.provider, getattr(args, "escalation", None)) if p in PAID_PROVIDERS]


def check_paid(args):
    paid = paid_parts(args)
    if paid and (not args.paid or args.max_spend is None):
        raise SystemExit(f"refused: {', '.join(paid)} is paid; pass --paid and --max-spend (PLAN §4)")


def default_label_config(args):
    if args.provider == "fake":
        return "fake-v0"
    esc = None if args.escalation == "none" else "claude-haiku-5-5"
    return make_label_config("celeris-1-decision", args.shape, args.gate, esc, args.esc_fraction)


def make_stage(args, label_config):
    check_paid(args)
    if paid_parts(args):
        load_dotenv(".env")
    spec = {"name": args.provider, "escalation": args.escalation, **{k: getattr(args, k) for k in STAGE_DEFAULTS},
            "script": args.fake_script, "latency": args.fake_latency}
    if args.provider == "celeris":
        provider = CelerisProvider()
    else:
        provider = FakeProvider(script=parse_script(args.fake_script), latency=args.fake_latency)
    if args.stage == "passthrough":
        if args.provider != "fake":
            raise SystemExit("refused: the passthrough stage only works with the fake provider")
        return PassthroughStage(provider, label_config), spec
    esc = {"fake": lambda: FakeProvider(latency=args.fake_latency, model="fake-escalation"),
           "anthropic": lambda: ClaudeProvider(), "none": lambda: None}[args.escalation]()
    return EnrichStage(provider, label_config, esc, shape=args.shape, gate=args.gate, esc_fraction=args.esc_fraction,
                       esc_bucket_cap=args.esc_max_spend), spec


def run_enrich(args, base=None, phase="initial"):
    base = dict(base or {})
    overrides = {"label_config": "label_config", "workers": "workers", "batch_size": "batch_size", "limit": "limit",
                 "lease_seconds": "lease_seconds", "bucket": "bucket"}
    for arg, key in overrides.items():
        if getattr(args, arg, None) is not None:
            base[key] = getattr(args, arg)
    if getattr(args, "max_minutes", None) is not None:
        base["max_seconds"] = args.max_minutes * 60
    base["max_spend"] = args.max_spend
    resolve_settings(args, base.get("provider"))
    check_paid(args)
    base.setdefault("label_config", default_label_config(args))
    if args.shape == "B" and base.get("batch_size", 50) != 1:
        raise SystemExit("shape B sends one review per request: pass --batch-size 1")
    base["phase"] = phase
    fields = RunConfig.__dataclass_fields__
    cfg = RunConfig(**{k: v for k, v in base.items() if k in fields})
    stage, spec = make_stage(args, cfg.label_config)
    if args.provider == "fake" and Path(args.db).resolve() == Path("state/pipeline.db").resolve():
        raise SystemExit("refused: fake runs must use their own --db, not the operational state/pipeline.db")
    conn = connect(args.db)
    migrate(conn)
    added = intake.enqueue(conn, cfg.stage, cfg.label_config) if conn.execute("SELECT 1 FROM texts LIMIT 1").fetchone() else 0
    conn.close()
    if added:
        print(f"enqueued {added} items for {cfg.stage} / {cfg.label_config}")
    orch = Orchestrator(args.db, stage, cfg, root=args.root, provider_spec=spec)
    try:
        summary = asyncio.run(orch.run())
    except KeyboardInterrupt:
        print("interrupted: leases released, state saved; continue with `python -m pipeline.cli resume`")
        print(json.dumps(orch.summary, indent=1))
        return 130
    print(json.dumps(summary, indent=1))
    if summary["stop_reason"] == "budget":
        print(f"stopped by the ledger: {getattr(orch, 'budget_message', '')}")
    return 0 if summary["stop_reason"] in ("drained", "limit", "time", "budget") else 1


def cmd_resume(args):
    conn = connect(args.db)
    migrate(conn)
    lease = args.lease_seconds if args.lease_seconds is not None else 300.0
    dead = recover(conn, lease)
    if dead:
        print(f"recovered crashed runs: {dead}")
    row = conn.execute("SELECT run_id, config_json, ended_at FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 1"
                       ).fetchone()
    conn.close()
    if row is None:
        raise SystemExit("nothing to resume: no runs yet")
    if row[2] is None:
        raise SystemExit(f"run {row[0]} looks alive (heartbeat < {lease:.0f}s old); wait for its lease to expire")
    base = json.loads(row[1])
    args.provider = args.provider or base.get("provider", {}).get("name", "fake")
    print(f"resuming from {row[0]} ({base['label_config']})")
    return run_enrich(args, base, phase="resume")


def cmd_status(args):
    conn = connect(args.db)
    migrate(conn)
    print("queue (stage, label_config, status, items):")
    for r in conn.execute("SELECT stage, label_config, status, COUNT(*) FROM work_items GROUP BY 1,2,3 ORDER BY 1,2,3"):
        print("  ", *r)
    print("records:", dict(conn.execute("SELECT status, COUNT(*) FROM records GROUP BY 1").fetchall()))
    print("ledger by bucket (list price USD):")
    for (b,) in conn.execute("SELECT DISTINCT bucket FROM ledger ORDER BY 1").fetchall():
        print(f"   {b}: {ledger.totals(conn, bucket=b)}")
    print("   total:", ledger.totals(conn))
    print("calls:", dict(conn.execute("SELECT outcome, COUNT(*) FROM calls GROUP BY 1").fetchall()))
    print("last runs:")
    for r in conn.execute("SELECT run_id, phase, stop_reason, started_at, ended_at FROM runs ORDER BY started_at DESC "
                          "LIMIT 5"):
        print("  ", *r)
    return 0


# ---------- downstream stages (M4) ----------
def add_downstream_flags(p, verify=False):
    p.add_argument("--db", type=Path, default=Path("state/pipeline.db"))
    p.add_argument("--root", type=Path, default=Path("."), help="where outputs/ and evals/ are written")
    p.add_argument("--provider", choices=("fake", "anthropic"), default="fake",
                   help="fake (offline) or anthropic = Claude Haiku 5.5")
    p.add_argument("--label-config", default=None, help="enrichment label_config (default: the one with most records)")
    p.add_argument("--paid", action="store_true", help="allow a paid provider (needs --max-spend)")
    p.add_argument("--max-spend", type=float, default=None, help="this command's spend cap in USD (list price)")
    p.add_argument("--fake-script", default="", help="fake behaviours in call order, e.g. invented,valid")
    if verify:
        p.add_argument("--sample-rate", type=float, default=None, help="declared sample rate (default 0.05)")
        p.add_argument("--seed", default=None, help="sample seed (default verify-v1)")
        p.add_argument("--standard", action="store_true", help="real-time Messages API instead of the Batch API")
        p.add_argument("--batch-size", type=int, default=50, help="reviews per request (≤ 50)")
        p.add_argument("--poll-seconds", type=float, default=None, help="Batch API poll interval (default 30)")
        p.add_argument("--max-wait-minutes", type=float, default=360.0, help="stop polling; rerun to resume the job")
        p.add_argument("--plant", type=int, default=3, help="planted errors in the synthetic test copy")
        p.add_argument("--fake-batch-outcomes", default="", help="fake batch results per request, e.g. errored,expired")


def downstream_conn(args):
    if args.provider == "fake" and Path(args.db).resolve() == Path("state/pipeline.db").resolve():
        raise SystemExit("refused: fake runs must use their own --db, not the operational state/pipeline.db")
    if args.provider == "anthropic":
        if not args.paid or args.max_spend is None:
            raise SystemExit("refused: anthropic is paid; pass --paid and --max-spend (PLAN §4)")
        load_dotenv(".env")
    conn = connect(args.db)
    migrate(conn)
    if args.label_config is None:
        row = conn.execute("SELECT label_config FROM records WHERE status='completed' GROUP BY 1 ORDER BY COUNT(*) DESC "
                           "LIMIT 1").fetchone()
        if row is None:
            raise SystemExit("nothing to analyse: no completed records")
        args.label_config = row[0]
    return conn


def analysis_provider(args):
    if args.provider == "anthropic":
        return ClaudeProvider()
    return FakeProvider(script=parse_script(args.fake_script), model="fake-analysis")


def cmd_verify(args):
    conn = downstream_conn(args)
    rate = args.sample_rate if args.sample_rate is not None else verifier.DEFAULT_RATE
    seed = args.seed or verifier.DEFAULT_SEED
    if args.batch_size > 50:
        raise SystemExit("requests are limited to 50 reviews")
    if args.standard:
        provider = ClaudeProvider() if args.provider == "anthropic" else FakeProvider(
            script=parse_script(args.fake_script), model="fake-verifier")
    else:
        provider = ClaudeBatchProvider() if args.provider == "anthropic" else FakeBatchProvider(
            outcomes=parse_script(args.fake_batch_outcomes), polls_to_end=1)
    stage = verifier.VerifyStage(provider, args.label_config)
    added, size = verifier.enqueue_sample(conn, args.label_config, stage.label_config, rate, seed)
    print(f"verify sample: {size} texts at rate {rate} (seed {seed!r}); {added} newly enqueued")
    if args.standard:
        conn.close()
        cfg = RunConfig(label_config=stage.label_config, stage="verify", batch_size=args.batch_size,
                        max_spend=args.max_spend, bucket="verification")
        summary = asyncio.run(Orchestrator(args.db, stage, cfg, root=args.root,
                                           provider_spec={"name": args.provider, "verify": True}).run())
        conn = connect(args.db)
    else:
        poll = args.poll_seconds if args.poll_seconds is not None else (30.0 if args.provider == "anthropic" else 0.01)
        summary = run_verify_batch(conn, stage, BatchConfig(batch_size=args.batch_size, max_spend=args.max_spend,
                                                            poll_seconds=poll,
                                                            max_wait_seconds=args.max_wait_minutes * 60))
    rep = verifier.report(conn, args.label_config, stage.vcfg, rate, seed, args.root)
    planted = verifier.planted_error_test(conn, args.label_config, stage.vcfg, args.root, args.plant)
    conn.close()
    print(json.dumps({"run": summary, "verified": rep["verified"], "unverified": rep["unverified"],
                      "agreement": rep["agreement"], "disagreements": rep["disagreements"],
                      "planted_detected": f"{planted['detected']}/{len(planted['planted'])}"}, indent=1, default=str))
    return 0 if summary.get("stop_reason") in ("drained", "waiting", "limit", "max_rounds") else 1


def _one_call_stage(args, name, fn):
    conn = downstream_conn(args)
    provider = analysis_provider(args)
    run_id = start_run(conn, name, {"label_config": args.label_config, "model": provider.model,
                                    "max_spend": args.max_spend})
    reason = "failed"
    try:
        out = fn(conn, provider, run_id)
        reason = "done"
    finally:
        end_run(conn, run_id, reason)
        conn.close()
    print(json.dumps({name: out, "run_id": run_id}, indent=1, default=str))
    return 0


def cmd_group(args):
    return _one_call_stage(args, "group", lambda conn, p, run_id: grouper.group(
        conn, args.label_config, p, args.root, run_id, args.max_spend))


def cmd_rank(args):
    conn = None
    if args.db is not None and Path(args.db).exists():
        conn = connect(args.db)
        migrate(conn)
    try:
        out = ranker.rank(args.root, conn)
    except (ranker.RankError, FileNotFoundError) as e:
        print(f"RANK FAILED: {e}", file=sys.stderr)
        return 1
    print(json.dumps({"rank": out, "model_calls": 0}, indent=1))
    return 0


def cmd_memo(args):
    return _one_call_stage(args, "memo", lambda conn, p, run_id: memo.write_memo(conn, args.root, p, run_id,
                                                                                  args.max_spend))


def cmd_analyze(args):
    def chain(conn, provider, run_id):
        g = grouper.group(conn, args.label_config, provider, args.root, run_id, args.max_spend)
        r = ranker.rank(args.root, conn)
        m = memo.write_memo(conn, args.root, provider, run_id, args.max_spend)
        return {"group": g, "rank": r, "memo": m}
    return _one_call_stage(args, "analyze", chain)


def cmd_run(args):
    """Every stage from one CSV path, no manual steps: ingest → enrich → verify → group → rank → memo."""
    resolve_settings(args)
    check_paid(args)
    enrich_cfg = getattr(args, "label_config", None) or default_label_config(args)
    args.csv, args.label_config = args.input, intake.PROVISIONAL_LABEL_CONFIG
    if cmd_ingest(args):
        return 1
    args.label_config = enrich_cfg
    if run_enrich(args):
        return 1
    down = argparse.Namespace(db=args.db, root=args.root, label_config=enrich_cfg, paid=args.paid,
                              max_spend=args.max_spend, fake_script="",
                              provider="fake" if args.provider == "fake" else "anthropic")
    verify_args = argparse.Namespace(**vars(down), sample_rate=args.sample_rate, seed=None,
                                     standard=args.verify_standard, batch_size=50, poll_seconds=None,
                                     max_wait_minutes=360.0, plant=3, fake_batch_outcomes="")
    if cmd_verify(verify_args):
        return 1
    return cmd_analyze(down)


def cmd_ingest(args):
    try:
        r = intake.ingest(args.csv, args.raw_dir, args.db, args.root, label_config=args.label_config)
    except intake.IntakeError as e:
        print(f"INGEST FAILED: {e}", file=sys.stderr)
        return 1
    s = r["random_100k"]
    print(f"ingest ok: {r['profile']['records']} rows, {sum(c['ok'] for c in r['checks'])}/{len(r['checks'])} checks passed")
    print(f"scope: {r['scope_counts']}")
    print(f"random_100k: {s['distinct_texts']} distinct texts ({s['duplicate_rows']} duplicate rows); "
          f"cache fill completes {r['cache_fill']['out_of_scope_rows_completed']} out-of-scope rows")
    for step in r["frequency_addon_curve"]:
        print(f"  add-on top {step['top_distinct_texts']}: +{step['rows_completed']} rows, "
              f"{step['total_chars']} chars, coverage point {step['coverage_point_with_100k_and_fill']}")
    print(f"queue: {r['queue']}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    p = commands.add_parser("ingest", help="A1: load, verify and scope the full CSV (no API calls)")
    p.add_argument("--csv", type=Path, default=Path("data/raw/spotify_reviews_18months.csv"))
    p.add_argument("--raw-dir", type=Path, default=Path("data/raw"), help="folder with manifest.json and the dev files")
    p.add_argument("--db", type=Path, default=Path("state/pipeline.db"))
    p.add_argument("--root", type=Path, default=Path("."), help="where outputs/, grading/ and evals/ are written")
    p.add_argument("--label-config", default=intake.PROVISIONAL_LABEL_CONFIG)

    add_enrich_flags(commands.add_parser("enrich", help="A2-A7 over the queue (fake provider unless --paid)"))
    add_enrich_flags(commands.add_parser("resume", help="recover a crashed run and continue (phase 'resume')"), True)
    p = commands.add_parser("status", help="queue, ledger, calls and runs")
    p.add_argument("--db", type=Path, default=Path("state/pipeline.db"))
    p = commands.add_parser("run", help="all stages: ingest, then enrich")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    p.add_argument("--sample-rate", type=float, default=None, help="A8 declared sample rate (default 0.05)")
    p.add_argument("--verify-standard", action="store_true", help="A8 in real time instead of the Batch API")
    add_enrich_flags(p)
    add_downstream_flags(commands.add_parser("verify", help="A8: verify a declared random sample (Batch API)"), True)
    add_downstream_flags(commands.add_parser("group", help="A9: issue membership (code) + issue names (one call)"))
    p = commands.add_parser("rank", help="A10: ranking + aggregates from saved files (zero model calls)")
    p.add_argument("--root", type=Path, default=Path("."))
    p.add_argument("--db", type=Path, default=None, help="also refresh the DB's ranking tables")
    add_downstream_flags(commands.add_parser("memo", help="A11: memo + claims + recommendations (one call)"))
    add_downstream_flags(commands.add_parser("analyze", help="group → rank → memo"))
    for name in ("export", "publish"):
        commands.add_parser(name, help=f"arrives in {'M10' if name == 'export' else 'M11'}")
    args = parser.parse_args(argv)

    if args.command == "ingest":
        return cmd_ingest(args)
    if args.command == "enrich":
        return run_enrich(args, {k: getattr(args, k) for k in ("label_config", "workers", "batch_size", "limit",
                                                                "lease_seconds", "bucket") if hasattr(args, k)})
    if args.command == "resume":
        return cmd_resume(args)
    if args.command == "status":
        return cmd_status(args)
    simple = {"verify": cmd_verify, "group": cmd_group, "rank": cmd_rank, "memo": cmd_memo, "analyze": cmd_analyze}
    if args.command in simple:
        return simple[args.command](args)
    if args.command == "run":
        return cmd_run(args)
    print(f"`{args.command}` arrives in {'M10' if args.command == 'export' else 'M11'}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
