"""M9: investigate the A8 verifier disagreements (code only, zero model calls). Reads evals/verify/ + state/pipeline.db
(read-only) and writes evals/m9/verify_investigation.json: confusion pairs, agreement by scope / text length, and the
area roll-up of the verified sample under the first labels vs the verifier's labels (does the conclusion move?)."""
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.agents.grouper import areas  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ver = {}
for line in (ROOT / "evals/verify/verifier_predictions.jsonl").open(encoding="utf-8"):
    v = json.loads(line)
    ver[v["text_sha256"]] = v
cfg = json.loads((ROOT / "evals/verify/verify_report.json").read_text(encoding="utf-8"))["label_config"]
conn = sqlite3.connect(f"file:{ROOT / 'state/pipeline.db'}?mode=ro", uri=True)
first, meta = {}, {}
for sha, topic, intent, sev, length, rank in conn.execute(
        """SELECT r.text_sha256, r.topic, r.intent, r.severity, LENGTH(t.review_text), t.scope_rank FROM results r
           JOIN texts t ON t.text_sha256 = r.text_sha256 WHERE r.label_config = ?""", (cfg,)):
    if sha in ver:
        first[sha] = {"topic": topic, "intent": intent, "severity": sev}
        meta[sha] = {"scope": "random_100k" if rank <= 100000 else "frequency_addon",
                     "length": "<40" if length < 40 else "40-149" if length < 150 else "150+"}
topic_area = areas()[1]
pairs = {f: Counter() for f in ("topic", "intent")}
sev_diff = Counter()
by = defaultdict(lambda: defaultdict(lambda: [0, 0]))
roll = {"first": defaultdict(lambda: [0, 0]), "verifier": defaultdict(lambda: [0, 0])}
for sha, a in first.items():
    v = ver[sha]
    for f in ("topic", "intent"):
        if a[f] != v[f]:
            pairs[f][f"{a[f]} -> {v[f]}"] += 1
    sev_diff[int(v["severity"]) - int(a["severity"])] += 1
    for dim in ("scope", "length"):
        cell = by[dim][meta[sha][dim]]
        cell[0] += 1
        cell[1] += all(a[f] == (int(v[f]) if f == "severity" else v[f]) for f in ("topic", "intent", "severity"))
    for who, lab in (("first", a), ("verifier", v)):
        if lab["intent"] in ("complaint", "cancellation"):
            area = topic_area[lab["topic"]]
            roll[who][area][0] += 1
            roll[who][area][1] += int(lab["severity"])
four = ["access", "usability", "playback", "billing/support"]
order = {w: sorted(four, key=lambda x: -roll[w][x][1]) for w in roll}
out = {
    "verified_texts": len(first), "label_config": cfg,
    "top_topic_confusions": pairs["topic"].most_common(10), "top_intent_confusions": pairs["intent"].most_common(10),
    "severity_verifier_minus_first": dict(sorted(sev_diff.items())),
    "all_fields_agree_by": {d: {k: {"n": n, "agree": a, "rate": round(a / n, 4)} for k, (n, a) in sorted(c.items())}
                            for d, c in by.items()},
    "area_rollup_on_sample (distinct texts; complaint+cancellation)": {
        w: {a: {"complaints": roll[w][a][0], "severity_sum": roll[w][a][1]} for a in four + ["outside"]} for w in roll},
    "area_order_by_severity_sum": order, "area_order_agrees": order["first"] == order["verifier"],
}
(ROOT / "evals/m9").mkdir(exist_ok=True)
(ROOT / "evals/m9/verify_investigation.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
print(json.dumps(out, indent=1))
