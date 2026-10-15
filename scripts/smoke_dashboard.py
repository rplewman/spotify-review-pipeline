"""M11 smoke test: the dashboard API's numbers equal the saved calculations in outputs/ (no DB access, no API keys).

    python scripts/smoke_dashboard.py --base-url https://spotify-review-pipeline.onrender.com [--out evals/m11/smoke_live.json]

Compares /api/overview, /api/areas, /api/ranking, /api/claims and /api/recommendations with outputs/overall_metrics.json,
area_aggregates.csv, ranking.csv, claims.csv and recommendations.json. Exit 0 = every check equal.
"""
import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

INT_COLS = {"rank", "issue_count", "complaint_count", "cancellation_count", "severity_sum", "priority_score"}


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return [{k: int(v) if k in INT_COLS else v for k, v in row.items()} for row in csv.DictReader(f)]


def run(base, root):
    client = httpx.Client(base_url=base, timeout=120)
    get = lambda p: client.get(p).raise_for_status().json()  # noqa: E731
    out = root / "outputs"
    m = json.loads((out / "overall_metrics.json").read_text(encoding="utf-8"))
    ov = get("/api/overview")
    o = ov["overall"]
    checks = {
        "counts": ({k: o[k] for k in ("records", "completed", "quarantined", "cache_reused", "needs_review", "issues", "members")},
                   {k: m[k] for k in ("records", "completed", "quarantined", "cache_reused", "needs_review", "issues", "members")}),
        "quarantine_reasons": ({r["reason"]: r["n"] for r in ov["status_reasons"] if r["status"] == "quarantined"},
                               m["quarantine_reasons"]),
        "topic": ({r["label"]: r["n"] for r in ov["topic"]}, m["by_topic"]),
        "intent": ({r["label"]: r["n"] for r in ov["intent"]}, m["by_intent"]),
        "severity": ({r["label"]: r["n"] for r in ov["severity"]}, m["by_severity"]),
        "label_config": (ov["summary"]["label_config"], m["label_config"]),
    }
    ranking_cols = ["rank", "issue_id", "complaint_count", "severity_sum", "mean_severity", "priority_score"]
    checks["ranking"] = ([{k: r[k] for k in ranking_cols} for r in get("/api/ranking")], read_csv(out / "ranking.csv"))
    area_file = read_csv(out / "area_aggregates.csv")
    checks["areas"] = (sorted(({k: a[k] for k in area_file[0]} for a in get("/api/areas")), key=lambda a: a["area"]),
                       sorted(area_file, key=lambda a: a["area"]))
    claims = get("/api/claims")
    checks["claims"] = ([{k: c[k] for k in ("claim_id", "issue_id", "metric", "value")} for c in claims],
                        read_csv(out / "claims.csv"))
    checks["claims_recomputed_equal"] = (all(c["matches"] for c in claims), True)
    recs = json.loads((out / "recommendations.json").read_text(encoding="utf-8"))["recommendations"]
    api_recs = get("/api/recommendations")
    checks["recommendations"] = ([(r["position"], r["title"], [c["claim_id"] for c in r["claims"]]) for r in api_recs],
                                 [(r["position"], r["title"], r["claim_ids"]) for r in recs])
    checks["evidence_reviews_found"] = (all(e["review"] is not None for r in api_recs for e in r["evidence"]), True)
    results = {name: {"ok": api == saved} | ({} if api == saved else {"api": api, "saved": saved})
               for name, (api, saved) in checks.items()}
    return {"base_url": base, "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "published_at": ov["summary"]["published_at"], "ok": all(r["ok"] for r in results.values()), "checks": results}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", required=True)
    p.add_argument("--root", type=Path, default=Path("."))
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args(argv)
    r = run(args.base_url.rstrip("/"), args.root)
    text = json.dumps(r, indent=1)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({k: v["ok"] for k, v in r["checks"].items()} | {"ok": r["ok"]}, indent=1))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
