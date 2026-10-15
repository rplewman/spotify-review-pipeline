"""M5 comparisons on the pilot's 100 reviews (PLAN §5 M5, §3.8). Paid parts need --paid and --max-spend.

    python scripts/pilot_compare.py shape-b    [--provider fake|celeris] [--paid --max-spend USD]
        Celeris request shape B (one review per request, definitions in option descriptions, no escalation) on a NEW
        database built from cost_100.csv, so the pilot's records are never touched.
    python scripts/pilot_compare.py escalation [--provider fake|anthropic] [--paid --max-spend USD]
        The pilot's low-confidence reviews (A4 route escalate / capped) sent to three candidates with the same
        questions (A3's top-2 per uncertain field): Haiku 5.5 effort low (thinking adaptive, = the pipeline's A5),
        Haiku 5.5 thinking disabled, and celeris-1-magnus (reasoning_effort low). Logged in its own DB/ledger.
    python scripts/pilot_compare.py report     offline: cost/comparisons.{json,md} from the saved DBs

No ground truth exists for cost_100 (golden/dev labels are M6); quality here = agreement between variants.
"""
import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from pipeline import cli, pilot  # noqa: E402
from pipeline.agents import escalation, router  # noqa: E402
from pipeline.db.store import connect, migrate  # noqa: E402
from pipeline.oneshot import CallRejected, call_validated, end_run, start_run  # noqa: E402
from pipeline.providers.fake import FakeProvider  # noqa: E402

FIELDS = ("topic", "intent", "severity")
VARIANTS = ("haiku-low", "haiku-off", "magnus-low")


def ro(path):
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def pilot_config(conn):
    return conn.execute("SELECT label_config FROM agent_outputs WHERE agent='A3' GROUP BY 1 ORDER BY COUNT(*) DESC "
                        "LIMIT 1").fetchone()[0]


def a3_outputs(conn, label_config):
    return {r["text_sha256"]: json.loads(r["output_json"]) for r in conn.execute(
        "SELECT text_sha256, output_json FROM agent_outputs WHERE agent='A3' AND label_config=?", (label_config,))}


def a3_calls(conn, label_config):
    return [dict(r) for r in conn.execute("SELECT * FROM calls WHERE agent='A3' AND label_config=?", (label_config,))]


# ---------- shape B ----------
def cmd_shape_b(a):
    if Path(a.db).exists():
        raise SystemExit(f"{a.db} exists; shape B needs a new DB")
    pilot.verify_input(a.input, a.raw_dir)
    pilot.ingest_pilot(a.db, a.input)
    argv = ["enrich", "--db", str(a.db), "--root", str(a.db.parent / "shapeB_run"), "--provider", a.provider,
            "--escalation", "none", "--shape", "B", "--batch-size", "1", "--workers", "1", "--bucket", "pilots",
            "--label-config", "pilot-shapeB"]
    if a.paid:
        argv += ["--paid", "--max-spend", str(a.max_spend)]
    return cli.main(argv)


# ---------- escalation candidates ----------
def low_conf_entries(pilot_db):
    conn = ro(pilot_db)
    try:
        cfg = pilot_config(conn)
        a3 = a3_outputs(conn, cfg)
        rows = conn.execute("""SELECT o.text_sha256, o.output_json, t.review_text FROM agent_outputs o
                               JOIN texts t ON t.text_sha256 = o.text_sha256
                               WHERE o.agent='A4' AND o.label_config=? AND json_extract(o.output_json, '$.route') != 'accept'
                               ORDER BY t.scope_rank""", (cfg,)).fetchall()
    finally:
        conn.close()
    return [(r["text_sha256"], r["review_text"], [(f, router.top2(a3[r["text_sha256"]], f))
                                                  for f in json.loads(r["output_json"])["fields"]]) for r in rows]


def magnus_payload(payload):
    """The same system prompt + questions as A5, in chat-completions form with JSON mode."""
    return {"messages": [{"role": "system", "content": payload["system"][0]["text"]}, *payload["messages"]],
            "max_tokens": payload["max_tokens"], "reasoning_effort": "low",
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "answers", "schema": escalation.SCHEMA, "strict": True}}}


def variant_provider(name, provider):
    if provider == "fake":
        return FakeProvider(model=f"fake-{name}")
    from pipeline.providers.celeris import MagnusProvider
    from pipeline.providers.claude import ClaudeProvider
    return MagnusProvider() if name == "magnus-low" else ClaudeProvider()


def variant_payload(name, payload):
    if name == "haiku-off":
        return {**payload, "thinking": {"type": "disabled"}}  # accepted at effort ≤ high (PLAN §9.10)
    if name == "magnus-low":
        return magnus_payload(payload)
    return payload


def cmd_escalation(a):
    entries = low_conf_entries(a.pilot_db)
    if not entries:
        print("no low-confidence reviews in the pilot: nothing to compare")
        return 0
    if a.provider != "fake":
        load_dotenv(".env")
    conn = connect(a.db)
    migrate(conn)
    out = {"entries": [{"text_sha256": s, "questions": [[f, c] for f, c in qs]} for s, _, qs in entries], "variants": {}}
    for name in a.variants.split(","):
        provider = variant_provider(name, a.provider)
        run_id = start_run(conn, f"compare-{name}", {"model": provider.model, "max_spend": a.max_spend})
        answers, reason = {}, "failed"
        try:
            for start in range(0, len(entries), 50):
                chunk = entries[start:start + 50]
                refs = {f"R{n}": e for n, e in enumerate(chunk, 1)}
                payload = escalation.build_payload([(ref, text, qs) for ref, (_, text, qs) in refs.items()])
                expected = {ref: [f for f, _ in qs] for ref, (_, _, qs) in refs.items()}
                questions = [(ref, f, [v for v, _ in c]) for ref, (_, _, qs) in refs.items() for f, c in qs]

                def validate(body, expected=expected):
                    got, notes = escalation.parse(body, expected)
                    missing = [f"{r}.{f}" for r, g in got.items() for f in g["missing"]]
                    return got, ([f"missing {', '.join(missing)}"] if len(missing) == sum(map(len, expected.values()))
                                 else [])
                try:
                    got, rid = call_validated(conn, provider, role="compare", agent=f"A5-{name}", label_config=None,
                                              payload=variant_payload(name, payload), validate=validate, run_id=run_id,
                                              bucket="pilots", max_spend=a.max_spend, attempts=2,
                                              meta={"shape": "escalation", "questions": questions})
                except CallRejected as e:
                    print(f"{name}: {e}")
                    continue
                for ref, g in got.items():
                    answers[refs[ref][0]] = g["fields"]
            reason = "done"
        finally:
            end_run(conn, run_id, reason)
        out["variants"][name] = {"model": provider.model, "run_id": run_id, "answers": answers}
    conn.close()
    path = Path(a.out) / "comparison_escalation_answers.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    return 0


# ---------- report (offline) ----------
def top(probs):
    return max(sorted(probs), key=lambda k: probs[k])


def shape_report(pilot_db, shape_db):
    p, b = ro(pilot_db), ro(shape_db)
    try:
        cfg_a, cfg_b = pilot_config(p), pilot_config(b)
        A, B = a3_outputs(p, cfg_a), a3_outputs(b, cfg_b)
        calls_a, calls_b = a3_calls(p, cfg_a), a3_calls(b, cfg_b)
    finally:
        p.close()
        b.close()
    common = sorted(set(A) & set(B))
    agree = {f: sum(top(A[s]["probs"][f]) == top(B[s]["probs"][f]) for s in common) / max(len(common), 1)
             for f in FIELDS}
    sent = [abs(A[s]["sentiment"] - B[s]["sentiment"]) for s in common if "sentiment" in A[s] and "sentiment" in B[s]]

    def per_review(calls, n):
        ok = [c for c in calls if c["usage_available"]]
        return {"requests": len(calls), "input_tokens": sum(c["input_tokens"] or 0 for c in ok),
                "input_tokens_per_review": sum(c["input_tokens"] or 0 for c in ok) / max(n, 1),
                "usd": sum(c["cost_usd"] or 0 for c in calls),
                "latency_ms_per_review": sum(c["latency_ms"] or 0 for c in calls) / max(n, 1)}
    return {"reviews_compared": len(common), "label_config_A": cfg_a, "label_config_B": cfg_b,
            "agreement": agree, "sentiment_mean_abs_diff": sum(sent) / len(sent) if sent else None,
            "A": per_review(calls_a, len(A)), "B": per_review(calls_b, len(B))}


def escalation_report(answers_path, compare_db, pilot_db):
    data = json.loads(Path(answers_path).read_text(encoding="utf-8"))
    conn = ro(compare_db)
    try:
        calls = [dict(r) for r in conn.execute("SELECT * FROM calls WHERE role='compare'")]
    finally:
        conn.close()
    qs = [(e["text_sha256"], f, c) for e in data["entries"] for f, c in e["questions"]]
    out = {"reviews": len(data["entries"]), "questions": len(qs), "variants": {}, "pairwise_agreement": {}}
    for name, v in data["variants"].items():
        mine = [c for c in calls if c["agent"] == f"A5-{name}"]
        ans = v["answers"]
        answered = [(s, f) for s, f, _ in qs if f in ans.get(s, {})]
        out["variants"][name] = {
            "model": v["model"], "answered": len(answered),
            "agrees_with_a3_top1": sum(ans[s][f] == c[0][0] for s, f, c in qs if f in ans.get(s, {})) / max(len(answered), 1),
            "attempts": len(mine), "succeeded": sum(c["outcome"] == "succeeded" for c in mine),
            "input_tokens": sum(c["input_tokens"] or 0 for c in mine),
            "output_tokens": sum(c["output_tokens"] or 0 for c in mine),
            "usd": sum(c["cost_usd"] or 0 for c in mine),
            "usd_per_review": sum(c["cost_usd"] or 0 for c in mine) / max(len(data["entries"]), 1),
            "latency_ms": sum(c["latency_ms"] or 0 for c in mine)}
    names = list(data["variants"])
    for i, x in enumerate(names):
        for y in names[i + 1:]:
            ax, ay = data["variants"][x]["answers"], data["variants"][y]["answers"]
            both = [(s, f) for s, f, _ in qs if f in ax.get(s, {}) and f in ay.get(s, {})]
            out["pairwise_agreement"][f"{x} vs {y}"] = (sum(ax[s][f] == ay[s][f] for s, f in both) / len(both)
                                                         if both else None)
    return out


def cmd_report(a):
    rep = {}
    if Path(a.shape_db).exists():
        rep["shape_A_vs_B"] = shape_report(a.pilot_db, a.shape_db)
    answers = Path(a.out) / "comparison_escalation_answers.json"
    if answers.exists() and Path(a.db).exists():
        rep["escalation_candidates"] = escalation_report(answers, a.db, a.pilot_db)
    rep["not_run"] = {
        "sonnet_escalation": "not run; modeled only (higher per-token price than Haiku, PLAN §4)",
        "haiku_only_enrichment": "not run; modeled: every review through Haiku ≈ the contract's GPT-6 Luna illustration "
                                 "order of magnitude (PLAN §4 reference point)",
        "batch_vs_standard_other_stages": "not run; Batch API = 50% of standard token prices (rates.csv), only A8 uses it",
    }
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / "comparisons.json").write_text(json.dumps(rep, indent=1) + "\n", encoding="utf-8")
    L = ["# Pilot comparisons (cost_100)", ""]
    if "shape_A_vs_B" in rep:
        s = rep["shape_A_vs_B"]
        L += ["## Celeris request shape A vs B (A3 only)", "",
              f"{s['reviews_compared']} reviews. Agreement of the top label: " +
              ", ".join(f"{f} {v:.0%}" for f, v in s["agreement"].items()) +
              (f"; sentiment mean |Δ| {s['sentiment_mean_abs_diff']:.3f}." if s["sentiment_mean_abs_diff"] is not None
               else "."), "",
              "| Shape | Requests | Input tokens | Tokens / review | Cost (list) | Latency / review |", "|---|---|---|---|---|---|"]
        for k in ("A", "B"):
            x = s[k]
            L.append(f"| {k} | {x['requests']} | {x['input_tokens']:,} | {x['input_tokens_per_review']:.0f} | "
                     f"${x['usd']:.6f} | {x['latency_ms_per_review']:.0f} ms |")
        L.append("")
    if "escalation_candidates" in rep:
        e = rep["escalation_candidates"]
        L += ["## Escalation candidates on the low-confidence reviews", "",
              f"{e['reviews']} reviews, {e['questions']} questions (A3's top-2 per uncertain field).", "",
              "| Variant | Model | Answered | = A3 top-1 | Attempts (ok) | In / out tokens | Cost (list) | $ / review | "
              "Latency |", "|---|---|---|---|---|---|---|---|---|"]
        for name, v in e["variants"].items():
            L.append(f"| {name} | {v['model']} | {v['answered']}/{e['questions']} | {v['agrees_with_a3_top1']:.0%} | "
                     f"{v['attempts']} ({v['succeeded']}) | {v['input_tokens']:,} / {v['output_tokens']:,} | "
                     f"${v['usd']:.6f} | ${v['usd_per_review']:.6f} | {v['latency_ms'] / 1000:.1f} s |")
        L += ["", "Pairwise agreement: " + ", ".join(f"{k} {v:.0%}" if v is not None else f"{k} n/a"
                                                   for k, v in e["pairwise_agreement"].items()), ""]
    L += ["## Not run (disclosed, modeled only)", ""] + [f"- {k}: {v}" for k, v in rep["not_run"].items()]
    (Path(a.out) / "comparisons.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {Path(a.out) / 'comparisons.md'} and comparisons.json")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("shape-b", "escalation", "report"))
    ap.add_argument("--pilot-db", type=Path, default=Path("state/pilot.db"))
    ap.add_argument("--shape-db", type=Path, default=Path("state/pilot_shapeB.db"))
    ap.add_argument("--db", type=Path, default=None, help="this command's DB (shape-b: --shape-db; escalation: "
                                                          "state/pilot_compare.db)")
    ap.add_argument("--input", type=Path, default=Path("data/raw/cost_100.csv"))
    ap.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    ap.add_argument("--out", type=Path, default=Path("cost"))
    ap.add_argument("--provider", default="fake", choices=("fake", "celeris", "anthropic"))
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--paid", action="store_true")
    ap.add_argument("--max-spend", type=float, default=None)
    a = ap.parse_args(argv)
    if a.provider != "fake" and (not a.paid or a.max_spend is None):
        raise SystemExit("refused: paid comparison; pass --paid and --max-spend (PLAN §4)")
    if a.command == "shape-b":
        a.db = a.db or a.shape_db
        if a.provider not in ("fake", "celeris"):
            raise SystemExit("shape-b uses --provider celeris (or fake)")
        return cmd_shape_b(a)
    a.db = a.db or Path("state/pilot_compare.db")
    if a.command == "escalation":
        return cmd_escalation(a)
    return cmd_report(a)


if __name__ == "__main__":
    sys.exit(main())
