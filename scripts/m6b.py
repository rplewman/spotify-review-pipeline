"""M6b: fidelity upgrade + re-freeze (PLAN v4.2 §5 M6b).

Paid steps need --paid AND --max-spend (PLAN §4). Everything else is offline and reads saved DBs / files only.

    python scripts/m6b.py sentiment-check           offline: mean vs mode sentiment from the saved Celeris probabilities on
                                                    golden 50 and dev_100 (golden-driven → disclosed) → evals/m6b/
    python scripts/m6b.py heldout-prepare           offline: seed ranks 10,051–10,080 (inside the 100k, after
                                                    analysis_10000, never used by any tuning step) → evals/heldout_30/
                                                    heldout_30_to_label.csv; 👤 Rory labels them blind with
                                                    `scripts/label_tool.py heldout`
    python scripts/m6b.py heldout-enrich --paid --max-spend USD
                                                    Celeris (shape A, = the pipeline's A3 request) + Haiku severity (= A5,
                                                    A3's top-2) on the 30 → evals/heldout_30/model_answers.json
                                                    (state/m6b_heldout.db holds the calls and the ledger)
    python scripts/m6b.py heldout-score             offline: old (mean) vs new (mode) sentiment, and Celeris vs Haiku
                                                    severity, against Rory's labels → evals/heldout_30/heldout_eval.*
    python scripts/m6b.py topic-test --model claude-sonnet-5-5 --paid --max-spend USD
                                                    the stronger model answers topic on all of dev_100 with EVERY topic
                                                    option (Celeris's probabilities shown) → evals/topic_test/answers.json
    python scripts/m6b.py topic-score               offline: vs Celeris top-1 on the adjudicated dev rows + the PLAN rule
                                                    (≥ 2 reviews better AND the projected cost fits) → evals/topic_test/
    python scripts/m6b.py golden-compare            offline: M6 vs M6b golden numbers side by side → evals/golden/

The held-out labels are only ever read by heldout-score; nothing tunes on them.
"""
import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import m6  # noqa: E402
from pipeline.agents import decision, escalation, intake, router  # noqa: E402
from pipeline.db.store import connect, migrate  # noqa: E402
from pipeline.schema import TOPICS  # noqa: E402

HELDOUT_RANKS = (10_051, 10_080)
HELDOUT_DIR = ROOT / "evals/heldout_30"
HELDOUT_TO_LABEL = HELDOUT_DIR / "heldout_30_to_label.csv"
HELDOUT_LABELED = HELDOUT_DIR / "heldout_30_labeled.csv"
HELDOUT_ANSWERS = HELDOUT_DIR / "model_answers.json"
HELDOUT_DB = ROOT / "state/m6b_heldout.db"
TOPIC_DIR = ROOT / "evals/topic_test"
M6B_DIR = ROOT / "evals/m6b"
SOURCE_FIELDS = ["review_id", "review_text", "review_rating", "review_likes", "app_version", "review_timestamp"]
TOL = m6.TOLERANCE
# PLAN §4 v4.2 (conservative, list): what the topic model must fit into, with ≥ $1.00 left over.
ESCALATION_CAP, RESERVE_CAP, FLOOR = 5.00, 1.25, 1.00
SEVERITY_ALL_CONSERVATIVE = 3.43 + 1.10  # Haiku severity on every review: 100k + add-on (§4 v4.2)
TEXTS_100K_PLUS_ADDON = 78_099 + 25_000


def sentiments(d):
    """(mean, mode) sentiment of one A3 decision parsed under `mean` (M3b–M6): the stored value, and the most probable
    level recomputed from the saved probabilities (= what `+sentiment-mode-v1` produces from the same answer)."""
    p = {str(i): d["probs"]["sentiment"][lvl] for i, lvl in enumerate(decision.SENTIMENT_LEVELS)}
    return d["sentiment"], decision.sentiment_value(None, p, "mode")


def sent_stats(pairs):
    """pairs = [(gold, pred)] → {n, mae, within}."""
    if not pairs:
        return {"n": 0, "mae": None, "within": None}
    errs = [abs(g - p) for g, p in pairs]
    return {"n": len(pairs), "mae": round(sum(errs) / len(errs), 3),
            "within": round(sum(e <= TOL + 1e-9 for e in errs) / len(errs), 3)}


def fmt(s):
    return "n/a" if s["mae"] is None else f"MAE {s['mae']:.3f}, within ±{TOL} {s['within']:.0%} (n={s['n']})"


# ---------- sentiment check on saved probabilities (offline) ----------
def cmd_sentiment_check(a):
    out = {"note": "golden-driven: the golden 50 showed the pull toward 0 (M6), so this is disclosed and re-checked on "
                   "the fresh held-out 30 (heldout-score)", "sets": {}}
    for name, labeled, db in (("golden_50", m6.GOLDEN_LABELED, a.db), ("dev_100", m6.DEV_LABELED, a.dev_db)):
        rows = m6.read_csv(labeled)
        conn = m6.ro(db)
        try:
            got = m6.a3_by_review(conn, m6.main_config(conn), [r["review_id"] for r in rows])
        finally:
            conn.close()
        old, new = [], []
        for r in rows:
            d = got.get(r["review_id"], (None, None, None))[2]
            if d is None or r["sentiment"] == "":
                continue
            stored, mode = sentiments(d)
            old.append((float(r["sentiment"]), stored))
            new.append((float(r["sentiment"]), mode))
        out["sets"][name] = {"mean": sent_stats(old), "mode": sent_stats(new),
                             "reference": "hand labels" if name == "golden_50" else
                                          "dev_100 sentiment is a model-agreement pre-fill for 77 rows (not independent)"}
    m6.write_json(M6B_DIR / "sentiment_check.json", out)
    L = ["# Sentiment: probability-weighted (mean) vs most probable level (mode)", "",
         "From the saved Celeris probabilities (no new calls). " + out["note"] + ".", "",
         "| Set | mean (M6) | mode (M6b) |", "|---|---|---|"]
    for name, s in out["sets"].items():
        L.append(f"| {name} | {fmt(s['mean'])} | {fmt(s['mode'])} |")
    L += ["", "dev_100's sentiment reference is mostly the old model pre-fill, so it favours `mean`; read golden and the "
          "held-out 30 instead."]
    m6.write_text(M6B_DIR / "sentiment_check.md", "\n".join(L) + "\n")
    print("\n".join(L))
    return 0


# ---------- held-out 30 ----------
def cmd_heldout_prepare(a):
    conn = m6.ro(a.db)
    try:
        lo, hi = HELDOUT_RANKS
        rows = [dict(r) for r in conn.execute(
            f"SELECT {', '.join(SOURCE_FIELDS)}, scope FROM source_reviews WHERE scope_rank BETWEEN ? AND ? "
            "ORDER BY scope_rank", (lo, hi))]
    finally:
        conn.close()
    if len(rows) != hi - lo + 1 or any(r["scope"] != "random_100k" for r in rows):
        raise SystemExit("held-out ranks are not 30 in-scope rows")
    used = {r["review_id"] for p in (m6.GOLDEN_LABELED, m6.DEV_TO_LABEL) for r in m6.read_csv(p)}
    if used & {r["review_id"] for r in rows}:
        raise SystemExit("held-out rows overlap golden/dev")
    HELDOUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(HELDOUT_TO_LABEL, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, SOURCE_FIELDS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {HELDOUT_TO_LABEL.relative_to(ROOT)} (seed ranks {lo}–{hi}, {len(rows)} reviews)")
    print("next (Rory): .venv/Scripts/python scripts/label_tool.py heldout")
    return 0


def _paid_provider(a, kind):
    from dotenv import load_dotenv
    if not a.paid or a.max_spend is None:
        raise SystemExit("refused: paid step; pass --paid and --max-spend (PLAN §4)")
    load_dotenv(ROOT / ".env")
    if kind == "celeris":
        from pipeline.providers.celeris import CelerisProvider
        return CelerisProvider()
    from pipeline.providers.claude import ClaudeProvider
    return ClaudeProvider(model=kind)


def cmd_heldout_enrich(a):
    from pipeline.oneshot import call_validated, end_run, start_run
    rows = m6.read_csv(HELDOUT_TO_LABEL)
    celeris, haiku = _paid_provider(a, "celeris"), _paid_provider(a, "claude-haiku-5-5")
    texts = [r["review_text"] for r in rows]
    conn = connect(HELDOUT_DB)
    migrate(conn)
    cfg = "m6b-heldout+celeris-1-decision+shapeA+prompt-v1"
    run_id = start_run(conn, "m6b-heldout", {"max_spend": a.max_spend})
    reason = "failed"
    try:
        def check_a3(body):
            ok, bad, _ = decision.parse(body, len(texts))
            return ok, ([f"{len(bad)} invalid"] if bad else [])
        a3, rid3 = call_validated(conn, celeris, role="compare", agent="A3-heldout", label_config=cfg,
                                  payload=decision.build_payload(texts), validate=check_a3, run_id=run_id,
                                  bucket="pilots", max_spend=a.max_spend, attempts=2,
                                  review_ids=[r["review_id"] for r in rows])
        entries = [(f"R{n}", texts[n - 1], [("severity", router.top2(a3[n], "severity"))]) for n in a3]
        expected = {ref: ["severity"] for ref, _, _ in entries}

        def check_a5(body):
            got, _ = escalation.parse(body, expected)
            return got, []
        a5, rid5 = call_validated(conn, haiku, role="compare", agent="A5-heldout", label_config=cfg,
                                  payload=escalation.build_payload(entries), validate=check_a5, run_id=run_id,
                                  bucket="pilots", max_spend=a.max_spend, attempts=2,
                                  review_ids=[r["review_id"] for r in rows])
        reason = "done"
    finally:
        end_run(conn, run_id, reason)
        spent = conn.execute("SELECT COALESCE(SUM(usd),0) FROM ledger WHERE kind='settle' AND run_id=?",
                             (run_id,)).fetchone()[0]
        conn.close()
    out = {"ranks": HELDOUT_RANKS, "run_id": run_id, "requests": [rid3, rid5], "usd_list": spent, "reviews": []}
    for n, r in enumerate(rows, 1):
        d = a3[n]
        out["reviews"].append({
            "review_id": r["review_id"], "topic": d["topic"], "intent": d["intent"],
            "severity_celeris": d["severity"], "severity_haiku": a5.get(f"R{n}", {}).get("fields", {}).get("severity"),
            "sentiment_mean": sentiments(d)[0], "sentiment_mode": sentiments(d)[1],
            "probs": d["probs"]})
    m6.write_json(HELDOUT_ANSWERS, out)
    print(f"wrote {HELDOUT_ANSWERS.relative_to(ROOT)}; spent ${spent:.4f} (list)")
    return 0


def cmd_heldout_score(a):
    labels = {r["review_id"]: r for r in m6.read_csv(HELDOUT_LABELED)}
    data = json.loads(HELDOUT_ANSWERS.read_text(encoding="utf-8"))
    revs = [r for r in data["reviews"] if r["review_id"] in labels and labels[r["review_id"]]["sentiment"] != ""]
    if len(revs) < len(data["reviews"]):
        print(f"note: {len(data['reviews']) - len(revs)} held-out reviews not labeled yet")
    gold = {r["review_id"]: m6.reference({**labels[r["review_id"]], "notes": ""}) for r in revs}
    sent = {k: sent_stats([(float(labels[r["review_id"]]["sentiment"]), r[f"sentiment_{k}"]) for r in revs])
            for k in ("mean", "mode")}

    def acc(field, key):
        ok = [str(r[key]) in gold[r["review_id"]]["accepted"][field] for r in revs if r[key] is not None]
        return {"n": len(ok), "accepted": round(sum(ok) / len(ok), 3) if ok else None, "correct": sum(ok)}
    sev = {"celeris": acc("severity", "severity_celeris"), "haiku": acc("severity", "severity_haiku")}
    other = {"topic": acc("topic", "topic"), "intent": acc("intent", "intent")}
    out = {"ranks": HELDOUT_RANKS, "labeled": len(revs), "sentiment": sent, "severity": sev, "celeris_only": other,
           "note": "fresh held-out reviews (seed ranks 10,051–10,080), labeled by Rory without model answers; "
                   "never used for tuning"}
    m6.write_json(HELDOUT_DIR / "heldout_eval.json", out)
    L = ["# Held-out 30 (fresh, hand-labeled by Rory)", "", out["note"] + ".", "",
         "| Check | M6 config | M6b config |", "|---|---|---|",
         f"| sentiment | mean: {fmt(sent['mean'])} | mode: {fmt(sent['mode'])} |",
         f"| severity accepted | Celeris top-1 {m6.pct(sev['celeris']['accepted'])} ({sev['celeris']['correct']}/"
         f"{sev['celeris']['n']}) | Haiku {m6.pct(sev['haiku']['accepted'])} ({sev['haiku']['correct']}/{sev['haiku']['n']}) |",
         f"| topic / intent accepted (Celeris, unchanged) | {m6.pct(other['topic']['accepted'])} / "
         f"{m6.pct(other['intent']['accepted'])} | same |", "",
         "M6 severity was Haiku only below gate 0.70; the column shows Celeris everywhere as the no-escalation baseline."]
    m6.write_text(HELDOUT_DIR / "heldout_eval.md", "\n".join(L) + "\n")
    print("\n".join(L))
    return 0


# ---------- stronger-model topic test on dev_100 ----------
def topic_entries(db):
    cfg, entries = m6.dev_entries(db)
    qs = []
    for rid, sha, text, d in entries:
        cands = sorted(d["probs"]["topic"].items(), key=lambda kv: (-kv[1], kv[0]))
        assert sorted(c for c, _ in cands) == sorted(TOPICS)
        qs.append((rid, sha, text, d, cands))
    return cfg, qs


def cmd_topic_test(a):
    from pipeline.oneshot import CallRejected, call_validated, end_run, start_run
    provider = _paid_provider(a, a.model)
    cfg, qs = topic_entries(a.db)
    conn = connect(a.db)
    migrate(conn)
    run_id = start_run(conn, "m6b-topic-test", {"model": provider.model, "max_spend": a.max_spend})
    answers, requests, reason = {}, [], "failed"
    try:
        for start in range(0, len(qs), 50):
            chunk = qs[start:start + 50]
            refs = {f"R{n}": q for n, q in enumerate(chunk, 1)}
            payload = escalation.build_payload([(ref, q[2], [("topic", q[4])]) for ref, q in refs.items()])
            expected = {ref: ["topic"] for ref in refs}

            def validate(body, expected=expected):
                got, _ = escalation.parse(body, expected)
                missing = sum(len(g["missing"]) for g in got.values())
                return got, ([f"missing {missing} answers"] if missing == len(expected) else [])
            try:
                got, rid = call_validated(conn, provider, role="compare", agent="A5-topic-test", label_config=cfg,
                                          payload=payload, validate=validate, run_id=run_id, bucket="pilots",
                                          max_spend=a.max_spend, attempts=2, review_ids=[q[0] for q in chunk])
            except CallRejected as e:
                print(f"chunk {start}: {e}")
                continue
            requests.append(rid)
            for ref, g in got.items():
                answers[refs[ref][0]] = g
        reason = "done"
    finally:
        end_run(conn, run_id, reason)
        usage = conn.execute("""SELECT COALESCE(SUM(l.usd), 0), COUNT(DISTINCT c.request_id),
                                       COALESCE(SUM(c.input_tokens), 0), COALESCE(SUM(c.output_tokens), 0)
                                FROM calls c JOIN ledger l ON l.request_id = c.request_id AND l.kind = 'settle'
                                WHERE c.run_id = ?""", (run_id,)).fetchone()
        conn.close()
    out = {"model": provider.model, "effort": escalation.EFFORT, "label_config_of_a3": cfg, "run_id": run_id,
           "requests": requests, "usd_list": usage[0], "calls": usage[1], "input_tokens": usage[2],
           "output_tokens": usage[3],
           "request": "the pipeline's A5 request (prompts/escalation_system_v1.md) with all 8 topics as candidates, "
                      "sorted by Celeris's probability (shown)",
           "reviews": [{"review_id": rid, "celeris": c[0][0], "p_top": c[0][1],
                        "model": answers.get(rid, {}).get("fields", {}).get("topic"),
                        "needs_review": answers.get(rid, {}).get("needs_review")}
                       for rid, _, _, _, c in qs]}
    m6.write_json(TOPIC_DIR / "answers.json", out)
    print(f"wrote {(TOPIC_DIR / 'answers.json').relative_to(ROOT)}: {len(answers)}/{len(qs)} answered, "
          f"${usage[0]:.4f} list")
    return 0


def topic_rows(revs, refs, gate):
    """Topic accepted counts when reviews with Celeris p_top < gate take the model's answer."""
    res = {"all": [0, 0], "adjudicated": [0, 0], "escalated": 0}
    for r in revs:
        esc = r["p_top"] < gate and r["model"] is not None
        pred = r["model"] if esc else r["celeris"]
        ref = refs[r["review_id"]]
        ok = pred in ref["accepted"]["topic"]
        res["escalated"] += esc
        for k in ("all",) + (("adjudicated",) if ref["adjudicated"] else ()):
            res[k][0] += ok
            res[k][1] += 1
    return res


def topic_decision(revs, refs, usd_per_review):
    """PLAN M6b rule over a gate sweep → (rows, decision)."""
    limit = ESCALATION_CAP + RESERVE_CAP - SEVERITY_ALL_CONSERVATIVE - FLOOR
    base = topic_rows(revs, refs, 0.0)
    rows = []
    for gate in (0.50, 0.60, 0.70, 0.80, 0.90, 1.01):
        r = topic_rows(revs, refs, gate)
        share = r["escalated"] / len(revs)
        cost = usd_per_review * 1.5 * share * TEXTS_100K_PLUS_ADDON  # conservative = measured × 1.5
        gain = r["adjudicated"][0] - base["adjudicated"][0]
        rows.append({"gate": gate, "escalated_share": round(share, 3), "adjudicated": r["adjudicated"], "all": r["all"],
                     "gain_adjudicated": gain, "projected_usd_conservative": round(cost, 2),
                     "passes": gain >= 2 and cost <= limit})
    ok = [r for r in rows if r["passes"]]
    best = max(ok, key=lambda r: (r["gain_adjudicated"], -r["gate"])) if ok else None
    return base, rows, {
        "adopt": best is not None, "gate": best["gate"] if best else None,
        "rule": f"adopt only if it beats Celeris top-1 on the adjudicated dev rows by ≥ 2 reviews AND the projected "
                f"conservative cost ≤ ${limit:.2f} (= escalation ${ESCALATION_CAP:.2f} + reserve ${RESERVE_CAP:.2f} − "
                f"Haiku severity on all ${SEVERITY_ALL_CONSERVATIVE:.2f} − ${FLOOR:.2f} floor)"}


def cmd_topic_score(a):
    data = json.loads((TOPIC_DIR / "answers.json").read_text(encoding="utf-8"))
    refs = {r["review_id"]: m6.reference(r) for r in m6.read_csv(m6.DEV_LABELED)}
    revs = data["reviews"]
    per_review = data["usd_list"] / max(1, sum(r["model"] is not None for r in revs))
    base, rows, dec = topic_decision(revs, refs, per_review)
    m6.write_json(TOPIC_DIR / "topic_test.json", {"model": data["model"], "usd_list_test": data["usd_list"],
                                                   "usd_per_review": per_review, "celeris_top1": base, "gates": rows,
                                                   "decision": dec})
    n_adj = base["adjudicated"][1]
    L = [f"# Stronger-model topic test ({data['model']}, effort {data['effort']}) on dev_100", "",
         f"{data['request']}. Test cost ${data['usd_list']:.4f} list ({data['calls']} calls; "
         f"${per_review:.5f}/review). Reference: dev_100 ({n_adj} adjudicated by Rory; the rest are model-agreement "
         "references between Celeris and Haiku, which favour Celeris).", "",
         f"Celeris top-1: adjudicated {base['adjudicated'][0]}/{n_adj}, all {base['all'][0]}/{base['all'][1]}.", "",
         "| Gate (model below) | Escalated | Adjudicated ok | Gain | All 100 ok | Projected 100k+add-on (cons.) | Passes |",
         "|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['gate']:.2f} | {r['escalated_share']:.0%} | {r['adjudicated'][0]}/{r['adjudicated'][1]} | "
                 f"{r['gain_adjudicated']:+d} | {r['all'][0]}/{r['all'][1]} | ${r['projected_usd_conservative']:.2f} | "
                 f"{'yes' if r['passes'] else 'no'} |")
    verdict = f"ADOPT at gate {dec['gate']:.2f}" if dec["adopt"] else "NOT adopted — topic stays with Celeris"
    L += ["", f"Rule: {dec['rule']}.", "", f"**Decision: {verdict}.**"]
    m6.write_text(TOPIC_DIR / "topic_test.md", "\n".join(L) + "\n")
    print("\n".join(L))
    return 0


# ---------- golden: M6 vs M6b ----------
def cmd_golden_compare(a):
    gdir = ROOT / "evals/golden"
    old = json.loads((gdir / "golden_eval_m6.json").read_text(encoding="utf-8"))["summary"]
    new = json.loads((gdir / "golden_eval.json").read_text(encoding="utf-8"))["summary"]
    L = ["# Golden 50: M6 vs M6b", "", f"- M6: `{old['label_config']}`", f"- M6b: `{new['label_config']}`", "",
         "| Metric | M6 | M6b |", "|---|---|---|"]
    for f in ("topic", "intent", "severity"):
        L.append(f"| {f} accepted / exact | {m6.pct(old[f]['accepted'])} / {m6.pct(old[f]['exact'])} | "
                 f"{m6.pct(new[f]['accepted'])} / {m6.pct(new[f]['exact'])} |")
    nr_o, nr_n = old["needs_review_as_error_predictor"], new["needs_review_as_error_predictor"]
    L += [f"| severity MAE | {old['severity']['mae']:.2f} | {new['severity']['mae']:.2f} |",
          f"| sentiment MAE / within ±{TOL} | {old['sentiment']['mae']:.3f} / "
          f"{m6.pct(old['sentiment']['within_tolerance'])} | {new['sentiment']['mae']:.3f} / "
          f"{m6.pct(new['sentiment']['within_tolerance'])} |",
          f"| needs_review precision / recall | {m6.pct(nr_o['precision'])} / {m6.pct(nr_o['recall'])} | "
          f"{m6.pct(nr_n['precision'])} / {m6.pct(nr_n['recall'])} |", "",
          "The sentiment change was motivated by the golden 50 (M6), so its golden gain is not independent evidence; "
          "the held-out 30 (`evals/heldout_30/heldout_eval.md`) is."]
    m6.write_text(gdir / "golden_m6_vs_m6b.md", "\n".join(L) + "\n")
    print("\n".join(L))
    return 0


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")  # the reports print ≥ and ±
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sentiment-check")
    s.add_argument("--db", type=Path, default=ROOT / "state/pipeline.db")
    s.add_argument("--dev-db", type=Path, default=m6.DEV_DB)
    s = sub.add_parser("heldout-prepare")
    s.add_argument("--db", type=Path, default=ROOT / "state/pipeline.db")
    for name in ("heldout-enrich", "topic-test"):
        s = sub.add_parser(name)
        s.add_argument("--paid", action="store_true")
        s.add_argument("--max-spend", type=float, default=None)
        if name == "topic-test":
            s.add_argument("--model", default="claude-sonnet-5-5")
            s.add_argument("--db", type=Path, default=m6.DEV_DB)
    for name in ("heldout-score", "topic-score", "golden-compare"):
        sub.add_parser(name)
    a = p.parse_args(argv)
    return {"sentiment-check": cmd_sentiment_check, "heldout-prepare": cmd_heldout_prepare,
            "heldout-enrich": cmd_heldout_enrich, "heldout-score": cmd_heldout_score, "topic-test": cmd_topic_test,
            "topic-score": cmd_topic_score, "golden-compare": cmd_golden_compare}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
