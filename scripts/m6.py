"""M6: scale checkpoints, dev_100 adjudication, threshold sweep, golden evaluation, 10k measurement, full-corpus gate.

Paid steps need --paid AND --max-spend (PLAN §4). Everything else is offline and reads saved DBs / files only.

    python scripts/m6.py dev-db                     offline: state/m6_dev.db = a copy of state/pilot.db + checkpoint_500
                                                    (the pilot's 100 texts stay done = cached; 400 new are queued)
    python scripts/m6.py dev-enrich --paid --max-spend USD
                                                    checkpoint_500 through the normal chain under the pilot's config
    python scripts/m6.py dev-escalate --paid --max-spend USD
                                                    A5 (Haiku, = the pipeline's escalation request) on ALL of dev_100,
                                                    every gated field, A3's top-2 → evals/dev_100/model_answers.json
    python scripts/m6.py dev-prepare                offline: Celeris top-1 vs Haiku per field → disagreements.json; the
                                                    agreeing reviews are written to dev_100_labeled.csv as model-agreement
                                                    references (marked in notes); Rory adjudicates the rest with
                                                    `scripts/label_tool.py dev --disagreements`
    python scripts/m6.py sweep                      offline: gates 0.50 / 0.60 / 0.70 → evals/threshold_sweep/
    python scripts/m6.py golden [--db state/pipeline.db --label-config CFG]
                                                    offline: golden 50 vs the records → evals/golden/golden_eval.*
    python scripts/m6.py measure --label-config CFG [--max-rank 10050]
                                                    offline: the 10k runs in state/pipeline.db → cost/m6/ (calculator
                                                    inputs, merged with the pilot's A8/A9/A11 rows) + scale_runs.md
    python scripts/m6.py decide                     offline: PLAN §1.2 gate → cost/full_corpus_decision.{json,md}
    python scripts/m6.py compare-b --db state/m6_dev_B.db
                                                    offline: shape A vs B on dev_100 → evals/threshold_sweep/shapeB_dev.*

Reference labels: dev_100 is NOT fully hand-labeled (PLAN v4.1). Where Celeris top-1 and Haiku agree on topic, intent
and severity, the agreed labels are the reference (not hand-checked; disclosed). Where they disagree, Rory decides.
So the sweep measures who is right where the two models differ; the golden 50 (all hand-labeled) is the unbiased check.
"""
import argparse
import csv
import json
import math
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pipeline import pilot  # noqa: E402
from pipeline.agents import escalation, extractor, intake, router  # noqa: E402
from pipeline.db.store import connect, migrate  # noqa: E402

FIELDS = router.GATED  # topic, intent, severity
GATES = (0.50, 0.60, 0.70)
EXTRA_GATES = (0.0, 0.40, 0.80, 1.01)  # context rows: A3 only … Haiku on every field
TOLERANCE = 0.25  # predeclared sentiment tolerance (labels/taxonomy.md)
DEV_DB, PILOT_DB = ROOT / "state/m6_dev.db", ROOT / "state/pilot.db"
DEV_TO_LABEL = ROOT / "evals/dev_100/dev_100_to_label.csv"
DEV_LABELED = ROOT / "evals/dev_100/dev_100_labeled.csv"
GOLDEN_LABELED = ROOT / "evals/golden/golden_50_labeled.csv"
ANSWERS = ROOT / "evals/dev_100/model_answers.json"
DISAGREEMENTS = ROOT / "evals/dev_100/disagreements.json"
AGREEMENT_NOTE = "[model-agreement] reference = Celeris top-1 and Haiku agree on topic/intent/severity; not hand-checked"
FULL_DISTINCT, SCOPE_DISTINCT = 484_189, 78_099
DEADLINE = datetime(2026, 10, 20, tzinfo=timezone.utc)


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def ro(path):
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def main_config(conn, agent="A3"):
    row = conn.execute("SELECT label_config FROM agent_outputs WHERE agent=? GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1",
                       (agent,)).fetchone()
    if row is None:
        raise SystemExit("no A3 decisions in this DB")
    return row[0]


def top1(d, f):
    return router.top2(d, f)[0][0]


def a3_by_review(conn, cfg, review_ids):
    """{review_id: (text_sha, text, A3 decision)} for the given IDs (texts decided under cfg)."""
    marks = ",".join("?" * len(review_ids))
    rows = conn.execute(f"""SELECT s.review_id, s.text_sha256, t.review_text, o.output_json FROM source_reviews s
                            JOIN texts t ON t.text_sha256 = s.text_sha256
                            LEFT JOIN agent_outputs o ON o.text_sha256 = s.text_sha256 AND o.agent='A3' AND o.label_config=?
                            WHERE s.review_id IN ({marks})""", [cfg, *review_ids]).fetchall()
    return {r["review_id"]: (r["text_sha256"], r["review_text"], json.loads(r["output_json"]) if r["output_json"] else None)
            for r in rows}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8", newline="\n")


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def pct(x):
    return "n/a" if x is None else f"{x:.0%}"


# ---------- dev DB (checkpoint_500) ----------
def cmd_dev_db(a):
    if Path(a.db).exists():
        raise SystemExit(f"{a.db} exists (delete it yourself to rebuild)")
    check = pilot.verify_input(a.input, a.raw_dir)
    if check["rows"] != 500:
        raise SystemExit(f"checkpoint_500 must have 500 rows, found {check['rows']}")
    shutil.copyfile(a.pilot_db, a.db)
    conn = intake.connect(a.db)
    try:
        intake.apply_schema(conn)
        stats = intake.load_source(conn, a.input)  # upsert: the pilot's 100 rows are the file's first 100 (same ranks)
        intake.assign_scope(conn, size=stats["records"])
        intake.build_texts(conn)
        intake.quarantine_empty(conn)
        n_texts = conn.execute("SELECT COUNT(*) FROM texts").fetchone()[0]
        n_rows = conn.execute("SELECT COUNT(*) FROM source_reviews").fetchone()[0]
    finally:
        conn.close()
    conn = connect(a.db)
    migrate(conn)
    cfg = main_config(conn)
    added = intake.enqueue(conn, "enrich", cfg)
    done = conn.execute("SELECT COUNT(*) FROM work_items WHERE stage='enrich' AND label_config=? AND status='done'",
                        (cfg,)).fetchone()[0]
    conn.close()
    print(json.dumps({"db": str(a.db), "input": check, "rows": n_rows, "distinct_texts": n_texts, "label_config": cfg,
                      "newly_enqueued": added, "already_done_from_pilot": done}, indent=1))
    return 0


def cmd_dev_enrich(a):
    if not a.paid or a.max_spend is None:
        raise SystemExit("refused: paid (Celeris + Haiku); pass --paid and --max-spend (PLAN §4)")
    from pipeline import cli
    conn = ro(a.db)
    cfg = main_config(conn)
    conn.close()
    return cli.main(["enrich", "--db", str(a.db), "--root", str(ROOT / "state/m6_dev_run"), "--provider", "celeris",
                     "--escalation", "anthropic", "--workers", "1", "--bucket", "pilots", "--label-config", cfg,
                     "--gate", "0.60", "--esc-fraction", "0.15", "--esc-fields", "topic,intent,severity",
                     "--paid", "--max-spend", str(a.max_spend)])


# ---------- A5 on all of dev_100 ----------
def dev_entries(db):
    ids = [r["review_id"] for r in read_csv(DEV_TO_LABEL)]
    conn = ro(db)
    try:
        cfg = main_config(conn)
        got = a3_by_review(conn, cfg, ids)
    finally:
        conn.close()
    missing = [i for i in ids if got.get(i, (None, None, None))[2] is None]
    if missing:
        raise SystemExit(f"{len(missing)} dev_100 reviews have no A3 decision yet (run dev-enrich first)")
    return cfg, [(rid, *got[rid]) for rid in ids]


def cmd_dev_escalate(a):
    from dotenv import load_dotenv
    from pipeline.oneshot import CallRejected, call_validated, end_run, start_run
    from pipeline.providers.fake import FakeProvider
    if a.provider != "fake" and (not a.paid or a.max_spend is None):
        raise SystemExit("refused: Haiku is paid; pass --paid and --max-spend (PLAN §4)")
    cfg, entries = dev_entries(a.db)
    if a.provider == "fake":
        provider = FakeProvider(model="fake-escalation")
    else:
        load_dotenv(ROOT / ".env")
        from pipeline.providers.claude import ClaudeProvider
        provider = ClaudeProvider()
    conn = connect(a.db)
    migrate(conn)
    run_id = start_run(conn, "dev100-escalation", {"model": provider.model, "max_spend": a.max_spend})
    answers, reason = {}, "failed"
    try:
        for start in range(0, len(entries), 50):
            chunk = entries[start:start + 50]
            refs = {f"R{n}": e for n, e in enumerate(chunk, 1)}
            qs = {ref: [(f, router.top2(e[3], f)) for f in FIELDS] for ref, e in refs.items()}
            payload = escalation.build_payload([(ref, e[2], qs[ref]) for ref, e in refs.items()])
            expected = {ref: list(FIELDS) for ref in refs}
            meta = {"shape": "escalation", "questions": [(ref, f, [v for v, _ in c]) for ref, q in qs.items() for f, c in q]}

            def validate(body, expected=expected):
                got, _ = escalation.parse(body, expected)
                missing = sum(len(g["missing"]) for g in got.values())
                return got, ([f"missing {missing} answers"] if missing == 3 * len(expected) else [])
            try:
                got, rid = call_validated(conn, provider, role="compare", agent="A5-dev100", label_config=cfg,
                                          payload=payload, validate=validate, run_id=run_id, bucket="pilots",
                                          max_spend=a.max_spend, attempts=2, meta=meta,
                                          review_ids=[e[0] for e in chunk])
            except CallRejected as e:
                print(f"chunk {start}: {e}")
                continue
            for ref, g in got.items():
                answers[refs[ref][0]] = {**g, "request_id": rid}
        reason = "done"
    finally:
        end_run(conn, run_id, reason)
        conn.close()
    out = {"label_config": cfg, "escalation_model": provider.model, "effort": escalation.EFFORT, "run_id": run_id,
           "note": "every gated field of every dev_100 review, A3's top-2 as candidates (same request as the pipeline's A5)",
           "reviews": []}
    for rid, sha, text, d in entries:
        out["reviews"].append({
            "review_id": rid, "text_sha256": sha,
            "a3": {f: {"top2": router.top2(d, f), "p_top": router.top_prob(d, f)} for f in FIELDS},
            "a3_sentiment": d["sentiment"], "a3_confidence": d.get("confidence"),
            "haiku": answers.get(rid, {}).get("fields", {}), "haiku_needs_review": answers.get(rid, {}).get("needs_review"),
        })
    write_json(ANSWERS, out)
    print(f"wrote {ANSWERS.relative_to(ROOT)}: {len(answers)}/{len(entries)} reviews answered")
    return 0


# ---------- disagreements → dev_100_labeled.csv ----------
def disputed(rev):
    return [f for f in FIELDS if rev["haiku"].get(f) != rev["a3"][f]["top2"][0][0]]


def model_labels(rev, text, fields):
    """Pre-filled labels for the agreed fields (+ sentiment, entities, quote from code)."""
    out = {}
    for f in fields:
        v = rev["a3"][f]["top2"][0][0]
        out[f] = int(v) if f == "severity" else v
    out["sentiment"] = max(-1.0, min(1.0, round(rev["a3_sentiment"] * 4) / 4))
    out["entities"] = extractor.entities(text)
    out["evidence_quote"] = extractor.evidence_quote(text, out.get("topic") or rev["a3"]["topic"]["top2"][0][0])
    out["needs_review"] = False
    return out


def cmd_dev_prepare(a):
    import label_tool
    data = json.loads(ANSWERS.read_text(encoding="utf-8"))
    texts = {r["review_id"]: r["review_text"] for r in read_csv(DEV_TO_LABEL)}
    store = label_tool.Store(DEV_TO_LABEL, DEV_LABELED)
    have = {i["review_id"] for i in store.items() if i["labels"]}
    dis, written = [], 0
    for rev in data["reviews"]:
        rid, text = rev["review_id"], texts[rev["review_id"]]
        bad = disputed(rev)
        if bad:
            dis.append({"review_id": rid, "fields": bad,
                        "hint": {f: {"celeris": rev["a3"][f]["top2"][0][0], "p": rev["a3"][f]["p_top"],
                                     "haiku": rev["haiku"].get(f)} for f in bad},
                        "prefill": model_labels(rev, text, [f for f in FIELDS if f not in bad])})
        elif rid not in have:
            labels = {**model_labels(rev, text, FIELDS), "notes": AGREEMENT_NOTE,
                      "needs_review": bool(rev.get("haiku_needs_review"))}
            errors = store.save(rid, labels)
            if errors:
                raise SystemExit(f"{rid}: {errors}")
            written += 1
    write_json(DISAGREEMENTS, {"source": ANSWERS.name, "reviews": dis,
                               "by_field": dict(Counter(f for d in dis for f in d["fields"]))})
    print(f"{len(dis)} reviews disagree ({dict(Counter(f for d in dis for f in d['fields']))}); "
          f"{written} agreeing reviews written as model-agreement references")
    print("next (Rory): .venv/Scripts/python scripts/label_tool.py dev --disagreements")
    return 0


# ---------- threshold sweep (offline) ----------
def reference(row):
    acc = {"topic": row["accepted_topics"].split("|"), "intent": row["accepted_intents"].split("|"),
           "severity": row["accepted_severities"].split("|")}
    return {"primary": {"topic": row["topic"], "intent": row["intent"], "severity": row["severity"]},
            "accepted": acc, "adjudicated": not row["notes"].startswith("[model-agreement]")}


def sweep_rows(revs, refs, gate, signal="p_top"):
    """Accuracy per field when fields below `gate` take Haiku's answer (uncapped)."""
    res = {f: {"n": 0, "primary": 0, "accepted": 0, "escalated": 0} for f in FIELDS}
    adj = {f: {"n": 0, "accepted": 0} for f in FIELDS}
    for rev in revs:
        ref = refs[rev["review_id"]]
        conf = rev.get("a3_confidence") or {}
        for f in FIELDS:
            s = rev["a3"][f]["p_top"] if signal == "p_top" else (conf.get(f) if conf.get(f) is not None else 1.0)
            esc = s < gate and f in rev["haiku"]
            pred = rev["haiku"][f] if esc else rev["a3"][f]["top2"][0][0]
            r = res[f]
            r["n"] += 1
            r["escalated"] += esc
            r["primary"] += pred == ref["primary"][f]
            r["accepted"] += pred in ref["accepted"][f]
            if ref["adjudicated"]:
                adj[f]["n"] += 1
                adj[f]["accepted"] += pred in ref["accepted"][f]
    return {f: {**res[f], "acc_primary": res[f]["primary"] / res[f]["n"], "acc_accepted": res[f]["accepted"] / res[f]["n"],
                "adjudicated_n": adj[f]["n"],
                "adjudicated_acc": adj[f]["accepted"] / adj[f]["n"] if adj[f]["n"] else None} for f in FIELDS}


def escalation_rates(db, gate):
    conn = ro(db)
    try:
        cfg = main_config(conn)
        ds = [json.loads(r[0]) for r in conn.execute("SELECT output_json FROM agent_outputs WHERE agent='A3' AND "
                                                       "label_config=?", (cfg,))]
    finally:
        conn.close()
    below = [router.uncertain_fields(d, gate) for d in ds]
    return {"texts": len(ds), "review_rate": sum(bool(b) for b in below) / len(ds),
            "field_rates": {f: sum(f in b for b in below) / len(ds) for f in FIELDS},
            "questions_per_escalated": (sum(map(len, below)) / max(sum(bool(b) for b in below), 1))}


def a5_usd_per_review():
    rep = json.loads((ROOT / "cost/report.json").read_text(encoding="utf-8"))
    st = rep["measured"]["stages"]["A5"]
    return st["cost"] / st["units"] if st["units"] else None


def cmd_sweep(a):
    data = json.loads(ANSWERS.read_text(encoding="utf-8"))
    rows = {r["review_id"]: r for r in read_csv(DEV_LABELED)}
    revs = [r for r in data["reviews"] if rows.get(r["review_id"], {}).get("topic")]
    if len(revs) < len(data["reviews"]):
        raise SystemExit(f"{len(data['reviews']) - len(revs)} dev_100 reviews are not labeled yet "
                         f"(scripts/label_tool.py dev --disagreements)")
    refs = {rid: reference(r) for rid, r in rows.items() if r.get("topic")}
    per_esc = a5_usd_per_review()
    out = {"reference": {"reviews": len(revs), "adjudicated": sum(refs[r["review_id"]]["adjudicated"] for r in revs),
                         "note": "model-agreement rows are not hand-checked; on them every gate scores the same"},
           "a5_usd_per_escalated_review": per_esc, "gates": {}, "confidence_signal": {}}
    for g in sorted(GATES + EXTRA_GATES):
        esc = escalation_rates(a.db, g)
        out["gates"][f"{g:.2f}"] = {"fields": sweep_rows(revs, refs, g), "escalation_500": esc,
                                    "a5_usd_100k": esc["review_rate"] * SCOPE_DISTINCT * per_esc if per_esc else None,
                                    "main": g in GATES}
    for g in GATES:
        out["confidence_signal"][f"{g:.2f}"] = sweep_rows(revs, refs, g, signal="confidence")
    write_json(ROOT / "evals/threshold_sweep/sweep.json", out)
    L = ["# Threshold sweep (dev_100 reference, escalation rate on checkpoint_500)", "",
         f"Reference: {out['reference']['reviews']} reviews, {out['reference']['adjudicated']} adjudicated by Rory "
         "(the model disagreements); the rest = Celeris and Haiku agree (not hand-checked, scored equal at every gate).",
         "Prediction per field: Haiku's answer when A3's top probability < gate, else A3's top-1 (no cap applied). "
         "Accuracy counts the accepted alternatives.", "",
         "| Gate | topic | intent | severity | adjudicated: topic / intent / severity | escalated reviews (500) | "
         "A5 $ at 100k (uncapped) |", "|---|---|---|---|---|---|---|"]
    for g, v in out["gates"].items():
        f = v["fields"]
        label = f"**{g}**" if v["main"] else {"0.00": "0.00 (A3 only)", "1.01": "1.01 (Haiku on all)"}.get(g, g)
        L.append(f"| {label} | {pct(f['topic']['acc_accepted'])} | {pct(f['intent']['acc_accepted'])} | "
                 f"{pct(f['severity']['acc_accepted'])} | {pct(f['topic']['adjudicated_acc'])} / "
                 f"{pct(f['intent']['adjudicated_acc'])} / {pct(f['severity']['adjudicated_acc'])} | "
                 f"{v['escalation_500']['review_rate']:.1%} | "
                 + (f"${v['a5_usd_100k']:.2f}" if v["a5_usd_100k"] is not None else "n/a") + " |")
    L += ["", "Signal check (Q11): gating on Celeris `confidence` instead of the raw top probability:", "",
          "| Gate | topic | intent | severity |", "|---|---|---|---|"]
    for g, f in out["confidence_signal"].items():
        L.append(f"| {g} | {pct(f['topic']['acc_accepted'])} | {pct(f['intent']['acc_accepted'])} | "
                 f"{pct(f['severity']['acc_accepted'])} |")
    write_text(ROOT / "evals/threshold_sweep/sweep.md", "\n".join(L) + "\n")
    print("\n".join(L))
    return 0


# ---------- golden evaluation (offline) ----------
def span(text, quote):
    i = text.find(quote) if quote else -1
    return (i, i + len(quote)) if i >= 0 else None


def golden_eval(golden_rows, recs, routes, variant=None):
    per, dis = [], []
    conf = {f: Counter() for f in ("topic", "intent")}
    for g in golden_rows:
        rid, text = g["review_id"], g["review_text"]
        rec = recs.get(rid)
        if rec is None or rec["status"] != "completed":
            per.append({"review_id": rid, "missing": True})
            continue
        ref = reference({**g, "notes": ""})
        row = {"review_id": rid, "route": routes.get(rid)}
        for f in FIELDS:
            pred = str(rec[f])
            row[f] = {"pred": pred, "gold": ref["primary"][f], "exact": pred == ref["primary"][f],
                      "accepted": pred in ref["accepted"][f]}
            if not row[f]["accepted"]:
                dis.append({"review_id": rid, "field": f, "gold": ref["primary"][f],
                            "accepted": "|".join(ref["accepted"][f]), "pred": pred, "route": routes.get(rid),
                            "review_text": text})
        for f in ("topic", "intent"):
            conf[f][(ref["primary"][f], str(rec[f]))] += 1
        row["severity_abs_err"] = abs(int(rec["severity"]) - int(g["severity"]))
        row["sentiment_abs_err"] = abs(float(rec["sentiment"]) - float(g["sentiment"]))
        if row["sentiment_abs_err"] > TOLERANCE:
            dis.append({"review_id": rid, "field": "sentiment", "gold": g["sentiment"], "accepted": f"±{TOLERANCE}",
                        "pred": rec["sentiment"], "route": routes.get(rid), "review_text": text})
        q, gq = rec["evidence_quote"], g["evidence_quote"]
        ps, gs = span(text, q), span(text, gq)
        row["quote_member"] = ps is not None
        row["quote_support"] = bool(ps and gs and ps[0] < gs[1] and gs[0] < ps[1])  # overlaps the gold quote
        pe = {e.lower() for e in json.loads(rec["entities_json"] or "[]")}
        ge = {e.lower() for e in json.loads(g["entities"] or "[]")}
        row["entities_jaccard"] = len(pe & ge) / len(pe | ge) if pe | ge else 1.0
        row["pred_needs_review"] = bool(rec["needs_review"])
        row["gold_needs_review"] = g["needs_review"] == "true"
        row["any_wrong"] = not all(row[f]["accepted"] for f in FIELDS)
        row["ambiguous"] = row["gold_needs_review"] or any(len(ref["accepted"][f]) > 1 for f in FIELDS)
        if variant is not None and rid in variant:
            row["variant"] = {f: variant[rid][f] in ref["accepted"][f] for f in FIELDS}
        per.append(row)
    ok = [r for r in per if not r.get("missing")]
    n = len(ok)

    def share(key, sub=None):
        return sum(bool(r[key][sub] if sub else r[key]) for r in ok) / n if n else None
    nr_tp = sum(r["pred_needs_review"] and r["any_wrong"] for r in ok)
    nr_pred, wrong = sum(r["pred_needs_review"] for r in ok), sum(r["any_wrong"] for r in ok)
    by_route = defaultdict(lambda: {"n": 0, "all_fields_ok": 0})
    for r in ok:
        by_route[r["route"]]["n"] += 1
        by_route[r["route"]]["all_fields_ok"] += not r["any_wrong"]
    summary = {
        "evaluated": n, "missing": len(per) - n,
        "topic": {"exact": share("topic", "exact"), "accepted": share("topic", "accepted")},
        "intent": {"exact": share("intent", "exact"), "accepted": share("intent", "accepted")},
        "severity": {"exact": share("severity", "exact"), "accepted": share("severity", "accepted"),
                     "mae": sum(r["severity_abs_err"] for r in ok) / n if n else None},
        "sentiment": {"mae": sum(r["sentiment_abs_err"] for r in ok) / n if n else None,
                      "within_tolerance": sum(r["sentiment_abs_err"] <= TOLERANCE for r in ok) / n if n else None,
                      "tolerance": TOLERANCE},
        "quote": {"membership": share("quote_member"), "support_overlaps_gold": share("quote_support")},
        "entities_mean_jaccard": sum(r["entities_jaccard"] for r in ok) / n if n else None,
        "needs_review_as_error_predictor": {
            "flagged": nr_pred, "reviews_with_an_error": wrong, "flagged_and_wrong": nr_tp,
            "precision": nr_tp / nr_pred if nr_pred else None, "recall": nr_tp / wrong if wrong else None,
            "agrees_with_gold_needs_review": sum(r["pred_needs_review"] == r["gold_needs_review"] for r in ok) / n if n else None},
        "ambiguous_cases": sum(r["ambiguous"] for r in ok),
        "by_route": dict(by_route),
        "confusion": {f: [[g, p, c] for (g, p), c in sorted(conf[f].items())] for f in conf},
    }
    if variant is not None:
        vs = [r for r in ok if "variant" in r]
        summary["variant_accepted"] = {f: sum(r["variant"][f] for r in vs) / len(vs) if vs else None for f in FIELDS}
        summary["variant_n"] = len(vs)
    return summary, per, dis


def cmd_golden(a):
    golden = read_csv(GOLDEN_LABELED)
    ids = [g["review_id"] for g in golden]
    conn = ro(a.db)
    try:
        cfg = a.label_config or main_config(conn)
        marks = ",".join("?" * len(ids))
        recs = {r["review_id"]: dict(r) for r in conn.execute(
            f"SELECT * FROM records WHERE review_id IN ({marks}) AND label_config=?", [*ids, cfg])}
        routes = {r[0]: r[1] for r in conn.execute(
            f"""SELECT s.review_id, json_extract(o.output_json, '$.route') FROM source_reviews s
                JOIN agent_outputs o ON o.text_sha256 = s.text_sha256 AND o.agent='A4' AND o.label_config=?
                WHERE s.review_id IN ({marks})""", [cfg, *ids])}
    finally:
        conn.close()
    variant = None
    if a.variant_db:
        vc = ro(a.variant_db)
        try:
            vcfg = main_config(vc)
            got = a3_by_review(vc, vcfg, ids)
        finally:
            vc.close()
        variant = {rid: {f: top1(d, f) for f in FIELDS} for rid, (_, _, d) in got.items() if d}
    summary, per, dis = golden_eval(golden, recs, routes, variant)
    summary.update({"label_config": cfg, "db": str(a.db), "golden_file": str(GOLDEN_LABELED.relative_to(ROOT)),
                    "variant_db": str(a.variant_db) if a.variant_db else None,
                    "tuning_note": "golden labels were not used for tuning (gate and cap chosen on dev_100 only)"})
    out = ROOT / "evals/golden"
    write_json(out / "golden_eval.json", {"summary": summary, "per_review": per})
    with open(out / "golden_disagreements.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, ["review_id", "field", "gold", "accepted", "pred", "route", "review_text"],
                           lineterminator="\n")
        w.writeheader()
        w.writerows(dis)
    s = summary
    L = ["# Golden evaluation (50 hand-labeled reviews)", "", f"label_config `{cfg}`; {s['evaluated']} evaluated, "
         f"{s['missing']} missing. Accepted = the primary label or a recorded accepted alternative.", "",
         "| Metric | Value |", "|---|---|",
         f"| topic (exact / accepted) | {pct(s['topic']['exact'])} / {pct(s['topic']['accepted'])} |",
         f"| intent (exact / accepted) | {pct(s['intent']['exact'])} / {pct(s['intent']['accepted'])} |",
         f"| severity (exact / accepted / MAE) | {pct(s['severity']['exact'])} / {pct(s['severity']['accepted'])} / "
         f"{s['severity']['mae']:.2f} |",
         f"| sentiment (MAE / within ±{TOLERANCE}) | {s['sentiment']['mae']:.3f} / {pct(s['sentiment']['within_tolerance'])} |",
         f"| evidence quote (exact substring / overlaps gold quote) | {pct(s['quote']['membership'])} / "
         f"{pct(s['quote']['support_overlaps_gold'])} |",
         f"| entities (mean Jaccard, informational) | {s['entities_mean_jaccard']:.2f} |",
         f"| needs_review as an error flag (precision / recall) | "
         f"{pct(s['needs_review_as_error_predictor']['precision'])} / {pct(s['needs_review_as_error_predictor']['recall'])} "
         f"({s['needs_review_as_error_predictor']['flagged']} flagged, "
         f"{s['needs_review_as_error_predictor']['reviews_with_an_error']} with an error) |",
         f"| needs_review = gold needs_review | {pct(s['needs_review_as_error_predictor']['agrees_with_gold_needs_review'])} |",
         f"| ambiguous cases (gold needs_review or accepted alternatives) | {s['ambiguous_cases']} |", ""]
    L += ["By route (all three fields accepted):", ""] + [
        f"- {k}: {v['all_fields_ok']}/{v['n']}" for k, v in sorted(s["by_route"].items(), key=lambda kv: str(kv[0]))] + [""]
    if variant is not None:
        L += [f"Variant `{a.variant_db}` (A3 top-1 only, {s['variant_n']} reviews), accepted: " +
              ", ".join(f"{f} {pct(v)}" for f, v in s["variant_accepted"].items()), ""]
    L += ["Topic confusion (gold → predicted, off-diagonal only):", ""]
    L += [f"- {g} → {p}: {c}" for g, p, c in s["confusion"]["topic"] if g != p] or ["- none"]
    L += ["", f"Every disagreement: `evals/golden/golden_disagreements.csv` ({len(dis)} rows, sentiment beyond ±{TOLERANCE} "
          "included). Golden labels were not used for tuning."]
    write_text(out / "golden_eval.md", "\n".join(L) + "\n")
    print("\n".join(L))
    return 0


# ---------- 10k measurement → calculator inputs (offline) ----------
def cmd_measure(a):
    m_pilot = json.loads((ROOT / "cost/pilot_measured.json").read_text(encoding="utf-8"))
    conn = ro(a.db)
    try:
        cfg = a.label_config
        runs = [dict(r) for r in conn.execute("SELECT * FROM runs ORDER BY started_at")]
        runs = [r for r in runs if json.loads(r["config_json"]).get("label_config") == cfg and
                json.loads(r["config_json"]).get("stage", "enrich") == "enrich"]
        calls = [dict(r) for r in conn.execute("SELECT * FROM calls WHERE label_config=? AND role='enrich' "
                                               "ORDER BY started_at", (cfg,))]
        texts = conn.execute("SELECT COUNT(*) FROM texts WHERE scope_rank <= ?", (a.max_rank,)).fetchone()[0]
        rows = conn.execute("SELECT COUNT(*) FROM source_reviews WHERE scope_rank <= ?", (a.max_rank,)).fetchone()[0]
        status = dict(conn.execute("""SELECT r.status, COUNT(*) FROM records r JOIN source_reviews s USING (review_id)
                                      WHERE s.scope_rank <= ? AND r.label_config=? GROUP BY 1""", (a.max_rank, cfg)))
        reused = conn.execute("""SELECT COUNT(*) FROM records r JOIN source_reviews s USING (review_id)
                                 WHERE s.scope_rank <= ? AND r.label_config=? AND r.cache_source_id IS NOT NULL""",
                              (a.max_rank, cfg)).fetchone()[0]
        routes = dict(conn.execute("""SELECT json_extract(o.output_json, '$.route'), COUNT(*) FROM agent_outputs o
                                      JOIN texts t ON t.text_sha256 = o.text_sha256
                                      WHERE o.agent='A4' AND o.label_config=? AND t.scope_rank <= ? GROUP BY 1""",
                                   (cfg, a.max_rank)))
        ids = [r[0] for r in conn.execute("SELECT review_id FROM source_reviews WHERE scope_rank <= ? ORDER BY scope_rank",
                                          (a.max_rank,))]
    finally:
        conn.close()
    if not runs:
        raise SystemExit(f"no enrich runs under {cfg}")
    # per-run scale table
    table = []
    for r in runs:
        c = json.loads(r["config_json"])
        mine = [x for x in calls if x["run_id"] == r["run_id"]]
        t0, t1 = r["started_at"], r["ended_at"]
        secs = (datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds() if t0 and t1 else None
        a3 = [x for x in mine if x["agent"] == "A3"]
        n_texts = sum(len(json.loads(x["review_ids_json"])) for x in a3 if x["outcome"] == "succeeded")
        table.append({"run_id": r["run_id"], "workers": c.get("workers"), "max_rank": c.get("max_rank"),
                      "stop_reason": r["stop_reason"], "seconds": secs, "calls": len(mine), "a3_calls": len(a3),
                      "a5_calls": len(mine) - len(a3), "texts_decided": n_texts,
                      "http_429": sum("429" in (x["error"] or "") for x in mine),
                      "failed": sum(x["outcome"] != "succeeded" for x in mine),
                      "texts_per_s": n_texts / secs if secs else None,
                      "requests_per_min": len(mine) / secs * 60 if secs else None})
    one = [t for t in table if t["workers"] == 1 and t["texts_decided"]]
    s_per_text = (sum(t["seconds"] for t in one) / sum(t["texts_decided"] for t in one)) if one else \
        sum(t["seconds"] or 0 for t in table) / max(sum(t["texts_decided"] for t in table), 1)
    warm = [t for t in table if t["calls"] == 0 and t["stop_reason"] == "drained"]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    # calls + usage: the 10k enrich calls, plus the pilot's cold A8/A9/A11 (fixed / per-verified-text stages)
    pilot_calls = [json.loads(line) for line in (ROOT / "cost/pilot_calls.jsonl").read_text(encoding="utf-8").splitlines()
                   if line.strip()]
    keep = lambda c: c["role"] in ("verify", "group", "memo") and c.get("pilot_phase") == "cold"  # noqa: E731
    with open(out / "calls.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for c in calls:
            c = {**c, "review_ids": json.loads(c.pop("review_ids_json")), "provider": pilot.provider_of(c["model"]),
                 "pilot_phase": "cold"}
            f.write(json.dumps(c, sort_keys=True) + "\n")
        for c in pilot_calls:
            if keep(c):
                f.write(json.dumps(c, sort_keys=True) + "\n")
    with open(ROOT / "cost/usage.csv", encoding="utf-8", newline="") as f:
        pilot_usage = [u for u in csv.DictReader(f) if u["stage"] in ("verify", "group", "memo") and u["pilot_phase"] == "cold"]
    with open(out / "usage.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, pilot.USAGE_FIELDS, lineterminator="\n")
        w.writeheader()
        for c in calls:
            provider = pilot.provider_of(c["model"])
            for item, units, unit in pilot.usage_items(c):
                w.writerow({"request_id": c["request_id"], "run_id": c["run_id"], "pilot_phase": "cold", "stage": c["role"],
                            "agent": c["agent"], "provider": provider, "model": c["model"],
                            "tier": pilot.rate_tier(provider, c["tier"]), "item": item, "units": units, "unit": unit,
                            "usage_available": c["usage_available"], "outcome": c["outcome"]})
        w.writerows(pilot_usage)
    analysis = pilot.verify_input(ROOT / "data/raw/analysis_10000.csv", ROOT / "data/raw")
    pilot_stage = {s["stage"]: s for s in m_pilot["phases"]["cold"]["stages"]}
    enrich_s = s_per_text * texts
    stages = [{"stage": "ingest", "seconds": pilot_stage["ingest"]["seconds"] / m_pilot["records"]["rows"] * rows,
               "new_calls": 0, "note": "pilot ingest seconds per row × rows"},
              {"stage": "enrich", "seconds": enrich_s, "new_calls": len(calls),
               "note": "1-worker seconds per decided text × texts (worker-count runs below)"}]
    stages += [pilot_stage[k] for k in ("verify", "group", "rank", "memo")]
    measured = {
        "label": f"seed ranks 1–{a.max_rank:,} (golden 50 + analysis_10000) under the frozen config",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "input": {"file": f"analysis_10000.csv (+ golden ranks 1–50) = seed ranks 1–{a.max_rank}",
                  "sha256": analysis["sha256"], "match": analysis["match"], "rows": rows,
                  "ids_sha256": __import__("hashlib").sha256("\n".join(ids).encode()).hexdigest()},
        "records": {"completed": status.get("completed", 0), "quarantined": status.get("quarantined", 0), "rows": rows,
                    "completed_via_exact_text_reuse": reused},
        "unique_texts": texts, "routes": routes, "verification": m_pilot["verification"], "label_config": cfg,
        "stages": {**m_pilot["stages"], "enrich_decision": {**m_pilot["stages"]["enrich_decision"],
                                                            "workers": sorted({t["workers"] for t in table})}},
        "phases": {"cold": {"stages": stages, "total_seconds": sum(s["seconds"] for s in stages), "new_calls": len(calls),
                            "new_enrich_calls": len(calls)},
                   "warm": {"stages": [{"stage": "enrich", "seconds": w["seconds"], "new_calls": 0} for w in warm[-1:]],
                            "total_seconds": warm[-1]["seconds"] if warm else 0.0, "new_calls": 0 if warm else None,
                            "new_enrich_calls": 0 if warm else None}},
        "local_compute": m_pilot["local_compute"],
        "downstream_from": "pilot (cost_100, cold): A8 per verified text, A9 and A11 per run",
        "scale_runs": table,
    }
    write_json(out / "measured.json", measured)
    L = ["# Scale runs (state/pipeline.db, frozen config)", "", f"`{cfg}`", "",
         "| Run | Workers | max_rank | Stop | Seconds | Calls (A3/A5) | Texts decided | Texts/s | Requests/min | 429s | Failed |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for t in table:
        L.append(f"| {t["run_id"][-6:]} | {t['workers']} | {t['max_rank']} | {t['stop_reason']} | "
                 f"{(t['seconds'] or 0):.0f} | {t['calls']} ({t['a3_calls']}/{t['a5_calls']}) | {t['texts_decided']} | "
                 f"{(t['texts_per_s'] or 0):.1f} | {(t['requests_per_min'] or 0):.1f} | {t['http_429']} | {t['failed']} |")
    L += ["", f"1-worker seconds per decided text: {s_per_text:.3f}. Routes: {json.dumps(routes)}. "
          f"Records: {json.dumps(measured['records'])}."]
    write_text(out / "scale_runs.md", "\n".join(L) + "\n")
    print("\n".join(L))
    print(f"next (offline): python cost/calculator.py --usage {out}/usage.csv --calls {out}/calls.jsonl "
          f"--measured {out}/measured.json --max-fallback-fraction <cap> --max-concurrency <workers>")
    return 0


# ---------- full-corpus gate, PLAN §1.2 (offline) ----------
def spend_to_date(dbs):
    """Settled + outstanding list USD over the given DBs, each request counted once (m6_dev.db is a copy of pilot.db)."""
    seen = {}
    for db in dbs:
        if not Path(db).exists():
            continue
        conn = ro(db)
        try:
            for rid, kind, usd, provider in conn.execute("SELECT request_id, kind, usd, provider FROM ledger"):
                d = seen.setdefault(rid, {"provider": provider})
                d[kind] = usd
        finally:
            conn.close()
    total, by_provider = 0.0, defaultdict(float)
    for d in seen.values():
        usd = d.get("settle", 0.0 if "release" in d else d.get("reserve", 0.0))
        total += usd
        by_provider[d["provider"]] += usd
    return total, dict(by_provider)


def cmd_decide(a):
    rep = json.loads(Path(a.report).read_text(encoding="utf-8"))
    measured = json.loads(Path(a.measured).read_text(encoding="utf-8"))
    cap = rep["controls"]["max_fallback_fraction"]
    full = rep["projections"]["full_with_reuse"]["conservative"]
    scope = rep["projections"]["scope_100k"]["conservative"]
    per_text = (full["stages"]["A3"]["usd"] + full["stages"]["A5"]["usd"]) / FULL_DISTINCT
    downstream = sum(full["stages"][k]["usd"] for k in ("A8", "A9", "A11"))
    spent, by_provider = spend_to_date([ROOT / p for p in a.dbs])
    done = measured["unique_texts"]
    remaining = FULL_DISTINCT - done
    projected = spent + remaining * per_text + downstream
    routes = measured["routes"]
    routed = sum(routes.values())
    below = (routed - routes.get("accept", 0)) / routed if routed else None
    enrich_full_s = full["time"]["stages_s"]["enrich"]
    hours = enrich_full_s * remaining / FULL_DISTINCT / 3600
    now = datetime.now(timezone.utc)
    latest_end = DEADLINE.timestamp() - 2 * 86400
    finish = now.timestamp() + hours * 3600 + a.lead_hours * 3600
    conds = {
        "1_shape_A": {"ok": "shapeA" in measured["label_config"], "value": measured["label_config"]},
        "2_projection_le_9": {"ok": projected <= 9.00, "value": round(projected, 4),
                              "parts": {"spend_to_date": round(spent, 6), "remaining_texts": remaining,
                                        "usd_per_text_A3_A5_conservative": per_text,
                                        "downstream_A8_A9_A11_conservative": round(downstream, 4)}},
        "3_escalation_rate": {"ok": (below is not None and below <= 0.15) or projected <= 9.00,
                              "value": below, "cap": cap,
                              "note": "measured share below the gate; the conservative projection prices the cap"},
        "4_time": {"ok": finish <= latest_end, "enrich_hours_conservative": round(hours, 2),
                   "lead_hours_before_start": a.lead_hours,
                   "projected_finish": datetime.fromtimestamp(finish, timezone.utc).isoformat(timespec="minutes"),
                   "latest_finish": datetime.fromtimestamp(latest_end, timezone.utc).isoformat(timespec="minutes")},
    }
    go = all(c["ok"] for c in conds.values())
    curve = json.loads((ROOT / "outputs/ingestion_report.json").read_text(encoding="utf-8"))["frequency_addon_curve"]
    scope_total = spent + (SCOPE_DISTINCT - done) * per_text + sum(scope["stages"][k]["usd"] for k in ("A8", "A9", "A11"))
    headroom = 9.00 - scope_total
    addon = [{"top": s["top_distinct_texts"], "rows": s["rows_completed"], "coverage": s["coverage_point_with_100k_and_fill"],
              "usd_upper": round(s["top_distinct_texts"] * per_text, 4)} for s in curve]
    fit = [s for s in addon if s["usd_upper"] <= headroom / 2 and s.get("top") != curve[-1]["top_distinct_texts"]]
    out = {"decision": "GO" if go else "NO-GO", "conditions": conds, "spend_to_date_by_provider": by_provider,
           "scope_100k_projected_total_conservative": round(scope_total, 4), "headroom_under_9": round(headroom, 4),
           "frequency_addon": {"steps": addon, "recommended_top": fit[-1]["top"] if fit else 0,
                               "rule": "largest step whose upper-end cost (per-text cost, no length discount) ≤ half "
                                       "the headroom under $9.00"},
           "inputs": {"report": str(a.report), "measured": str(a.measured), "dbs": a.dbs},
           "confirm": "Rory confirms before M8"}
    write_json(ROOT / "cost/full_corpus_decision.json", out)
    L = ["## Full-corpus decision point (PLAN §1.2)", "",
         f"**{out['decision']}** (computed {now.date()}; Rory confirms before M8).", "",
         "| Condition | Value | OK |", "|---|---|---|",
         f"| 1. shape A adopted | `{measured['label_config']}` | {conds['1_shape_A']['ok']} |",
         f"| 2. spend to date ${spent:.4f} + {remaining:,} texts × ${per_text:.7f} + downstream ${downstream:.3f} ≤ $9.00 "
         f"| ${projected:.2f} | {conds['2_projection_le_9']['ok']} |",
         f"| 3. escalation rate ≤ 15% (or the cap makes it fit) | {pct(below)} below the gate; cap {cap:.2f} | "
         f"{conds['3_escalation_rate']['ok']} |",
         f"| 4. finish ≥ 2 days before 2026-10-20 | {hours:.1f} h enrichment (conservative) + {a.lead_hours} h lead → "
         f"{conds['4_time']['projected_finish']} (latest {conds['4_time']['latest_finish']}) | {conds['4_time']['ok']} |",
         "", f"100k scope projected total (conservative, incl. spend to date): ${scope_total:.2f}; headroom under $9.00: "
         f"${headroom:.2f}.", ""]
    if not go:
        L += ["Frequency add-on (NO-GO path, §1.1), upper-end cost at the measured per-text cost:", "",
              "| Top distinct texts | + rows | coverage point | ≤ cost |", "|---|---|---|---|"]
        L += [f"| {s['top']:,} | {s['rows']:,} | {s['coverage']} | ${s['usd_upper']:.2f} |" for s in addon]
        L += ["", f"Recommended size: **{out['frequency_addon']['recommended_top']:,}** ({out['frequency_addon']['rule']})."]
    md = "\n".join(L) + "\n"
    write_text(ROOT / "cost/full_corpus_decision.md", md)
    report_md = Path(a.report).with_suffix(".md")
    if report_md.exists():
        text = report_md.read_text(encoding="utf-8")
        marker = "## Full-corpus decision point (PLAN §1.2)"
        text = text.split(marker)[0].rstrip() + "\n\n" + md
        report_md.write_text(text, encoding="utf-8", newline="\n")
    print(md)
    return 0


# ---------- shape A vs B on dev_100 (offline; B decided by `enrich --shape B` in state/m6_dev_B.db) ----------
def cmd_compare_b(a):
    rows = {r["review_id"]: r for r in read_csv(DEV_LABELED)}
    refs = {k: reference(r) for k, r in rows.items()}
    data = json.loads(ANSWERS.read_text(encoding="utf-8"))
    conn = ro(a.db)
    try:
        B = a3_by_review(conn, main_config(conn), list(rows))
    finally:
        conn.close()

    def frozen(r, f):
        return r["haiku"][f] if f == "severity" and r["a3"][f]["p_top"] < 0.70 else r["a3"][f]["top2"][0][0]
    variants = {"A top-1": lambda r, f: r["a3"][f]["top2"][0][0], "frozen (A + Haiku severity < 0.70)": frozen,
                "B top-1": lambda r, f: top1(B[r["review_id"]][2], f),
                "B + Haiku severity (A-gated, approx.)": lambda r, f: frozen(r, f) if f == "severity"
                else top1(B[r["review_id"]][2], f)}
    out, L = {}, ["# Shape A vs B on dev_100", "", "Accepted-label matches. Only the adjudicated rows are a fair test: "
                  "the 77 model-agreement references come from shape A + Haiku, so 'all' is biased toward A.", "",
                  "| Variant | Rows | topic | intent | severity |", "|---|---|---|---|---|"]
    for name, fn in variants.items():
        for sub in ("adjudicated", "all"):
            revs = [r for r in data["reviews"] if sub == "all" or refs[r["review_id"]]["adjudicated"]]
            acc = {f: sum(fn(r, f) in refs[r["review_id"]]["accepted"][f] for r in revs) for f in FIELDS}
            out[f"{name} / {sub}"] = {"n": len(revs), **acc}
            L.append(f"| {name} | {sub} ({len(revs)}) | {acc['topic']} | {acc['intent']} | {acc['severity']} |")
    write_json(ROOT / "evals/threshold_sweep/shapeB_dev.json", out)
    write_text(ROOT / "evals/threshold_sweep/shapeB_dev.md", "\n".join(L) + "\n")
    print("\n".join(L))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("dev-db", "dev-enrich", "dev-escalate", "dev-prepare", "sweep", "golden",
                                        "measure", "decide", "compare-b"))
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--pilot-db", type=Path, default=PILOT_DB)
    ap.add_argument("--input", type=Path, default=ROOT / "data/raw/checkpoint_500.csv")
    ap.add_argument("--raw-dir", type=Path, default=ROOT / "data/raw")
    ap.add_argument("--provider", default="anthropic", choices=("fake", "anthropic"))
    ap.add_argument("--label-config", default=None)
    ap.add_argument("--variant-db", type=Path, default=None, help="golden: also score this DB's A3 top-1 (e.g. shape B)")
    ap.add_argument("--max-rank", type=int, default=10_050)
    ap.add_argument("--out", type=Path, default=ROOT / "cost/m6")
    ap.add_argument("--report", type=Path, default=ROOT / "cost/report.json")
    ap.add_argument("--measured", type=Path, default=ROOT / "cost/m6/measured.json")
    ap.add_argument("--dbs", nargs="*", default=["state/pipeline.db", "state/pilot.db", "state/pilot_attempt1.db",
                                                  "state/pilot_attempt2.db", "state/pilot_shapeB.db",
                                                  "state/pilot_compare.db", "state/m6_dev.db", "state/m6_golden_B.db",
                                                  "state/m6_dev_B.db"])
    ap.add_argument("--lead-hours", type=float, default=24.0, help="decide: hours before the full run could start (M7)")
    ap.add_argument("--paid", action="store_true")
    ap.add_argument("--max-spend", type=float, default=None)
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if a.db is None:
        a.db = ROOT / "state/pipeline.db" if a.command in ("golden", "measure") else DEV_DB
    return {"dev-db": cmd_dev_db, "dev-enrich": cmd_dev_enrich, "dev-escalate": cmd_dev_escalate,
            "dev-prepare": cmd_dev_prepare, "sweep": cmd_sweep, "golden": cmd_golden, "measure": cmd_measure,
            "decide": cmd_decide, "compare-b": cmd_compare_b}[a.command](a)


if __name__ == "__main__":
    sys.exit(main())
