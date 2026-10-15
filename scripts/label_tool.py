"""Local hand-labeling helper for the golden 50 and dev_100 (M2). No API calls; binds to 127.0.0.1 only.

Usage: python scripts/label_tool.py golden   (or: dev, heldout)  [--port 8765] [--no-browser]
       python scripts/label_tool.py dev --disagreements   M6: only the reviews where Celeris and Haiku disagree
           (evals/dev_100/disagreements.json from `scripts/m6.py dev-prepare`); the agreed fields are pre-filled and
           both models' answers are shown for the disputed ones.

Shows one review at a time (text only: stars, likes, version and date are hidden so they can't steer the label), the
contract definitions, and the label form. Every save validates the labels and rewrites the output CSV atomically, so you can
stop at any point and resume later. Output = the template's 13 columns filled in, plus accepted alternatives and notes:
  golden -> evals/golden/golden_50_labeled.csv     dev -> evals/dev_100/dev_100_labeled.csv
  heldout -> evals/heldout_30/heldout_30_labeled.csv (M6b; `scripts/m6b.py heldout-prepare` writes the input)
This script and the M6 sweep/evaluation scripts are the only code allowed to touch these files (tests/test_m2_labels.py).
"""
import argparse
import csv
import json
import os
import sys
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline.schema import INTENTS, SEVERITIES, TOPICS, validate_labels  # noqa: E402

SETS = {
    "golden": (ROOT / "data/raw/golden_50_to_label.csv", ROOT / "evals/golden/golden_50_labeled.csv"),
    "dev": (ROOT / "evals/dev_100/dev_100_to_label.csv", ROOT / "evals/dev_100/dev_100_labeled.csv"),
    # M6b: 30 fresh held-out reviews (seed ranks 10,051–10,080), labeled blind (no model answers shown)
    "heldout": (ROOT / "evals/heldout_30/heldout_30_to_label.csv", ROOT / "evals/heldout_30/heldout_30_labeled.csv"),
}
SOURCE_FIELDS = ["review_id", "review_text", "review_rating", "review_likes", "app_version", "review_timestamp"]
LABEL_COLUMNS = ["topic", "intent", "sentiment", "severity", "entities", "evidence_quote", "needs_review"]
EXTRA_COLUMNS = ["accepted_topics", "accepted_intents", "accepted_severities", "notes", "labeled_at"]
HTML = Path(__file__).with_name("label_tool.html")
DISAGREEMENTS = ROOT / "evals/dev_100/disagreements.json"


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, strict=True))


def to_row(labels):
    """Hand-label dict -> CSV cells. The primary label is always included in the accepted lists."""
    def accepted(key, primary):
        vals = [primary] + [v for v in labels.get(f"accepted_{key}", []) if v != primary]
        return "|".join(str(v) for v in vals)
    return {
        "topic": labels["topic"], "intent": labels["intent"], "sentiment": f"{labels['sentiment']:g}",
        "severity": str(labels["severity"]), "entities": json.dumps(labels["entities"], ensure_ascii=False),
        "evidence_quote": labels["evidence_quote"], "needs_review": "true" if labels["needs_review"] else "false",
        "accepted_topics": accepted("topics", labels["topic"]), "accepted_intents": accepted("intents", labels["intent"]),
        "accepted_severities": accepted("severities", labels["severity"]), "notes": labels.get("notes", "").strip(),
        "labeled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def from_row(row):
    """CSV cells -> hand-label dict (None if the row is unlabeled)."""
    if not row.get("topic"):
        return None
    return {
        "topic": row["topic"], "intent": row["intent"], "sentiment": float(row["sentiment"]), "severity": int(row["severity"]),
        "entities": json.loads(row["entities"] or "[]"), "evidence_quote": row["evidence_quote"],
        "needs_review": row["needs_review"] == "true",
        "accepted_topics": [v for v in row.get("accepted_topics", "").split("|") if v and v != row["topic"]],
        "accepted_intents": [v for v in row.get("accepted_intents", "").split("|") if v and v != row["intent"]],
        "accepted_severities": [int(v) for v in row.get("accepted_severities", "").split("|") if v and v != row["severity"]],
        "notes": row.get("notes", ""),
    }


class Store:
    def __init__(self, src, out, only=None):
        self.out, self.only = out, only or {}
        self.rows = read_csv(src)
        saved = {r["review_id"]: r for r in read_csv(out)} if out.exists() else {}
        for r in self.rows:
            for col in LABEL_COLUMNS + EXTRA_COLUMNS:
                r[col] = saved.get(r["review_id"], {}).get(col, "")

    def items(self):
        items = [{"review_id": r["review_id"], "review_text": r["review_text"], "labels": from_row(r)} for r in self.rows]
        if self.only:  # disagreements mode: just those reviews, with the hint and the pre-filled agreed fields
            items = [{**i, "hint": self.only[i["review_id"]]["hint"], "prefill": self.only[i["review_id"]]["prefill"]}
                     for i in items if i["review_id"] in self.only]
        return items

    def save(self, review_id, labels):
        row = next((r for r in self.rows if r["review_id"] == review_id), None)
        if row is None:
            return [f"unknown review_id {review_id}"]
        labels["entities"] = [e.strip() for e in labels.get("entities", []) if e.strip()]
        errors = validate_labels(labels, row["review_text"])
        errors += [f"accepted topic {v!r} invalid" for v in labels.get("accepted_topics", []) if v not in TOPICS]
        errors += [f"accepted intent {v!r} invalid" for v in labels.get("accepted_intents", []) if v not in INTENTS]
        errors += [f"accepted severity {v!r} invalid" for v in labels.get("accepted_severities", []) if v not in SEVERITIES]
        if errors:
            return errors
        row.update(to_row(labels))
        self.out.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.out.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=SOURCE_FIELDS + LABEL_COLUMNS + EXTRA_COLUMNS, lineterminator="\n")
            w.writeheader()
            w.writerows(self.rows)
        os.replace(tmp, self.out)
        return []


def make_handler(store, set_name):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/":
                self._send(200, HTML.read_bytes(), "text/html")
            elif self.path == "/api/items":
                self._send(200, {"set": set_name, "output": str(store.out.relative_to(ROOT)), "items": store.items(),
                                 "topics": TOPICS, "intents": INTENTS, "severities": SEVERITIES})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/api/save":
                return self._send(404, {"error": "not found"})
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            errors = store.save(body.get("review_id"), body.get("labels", {}))
            self._send(400 if errors else 200, {"ok": not errors, "errors": errors})

        def log_message(self, *args):
            pass
    return Handler


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("set", choices=sorted(SETS))
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--disagreements", action="store_true", help="dev only: label just the model disagreements (M6)")
    args = p.parse_args(argv)
    src, out = SETS[args.set]
    only = None
    if args.disagreements:
        if args.set != "dev":
            raise SystemExit("--disagreements is for the dev set")
        only = {d["review_id"]: d for d in json.loads(DISAGREEMENTS.read_text(encoding="utf-8"))["reviews"]}
    store = Store(src, out, only)
    done = sum(1 for i in store.items() if i["labels"])
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(store, args.set))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Labeling {args.set}: {done}/{len(store.items())} done. Saving to {out.relative_to(ROOT)}. Open {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
