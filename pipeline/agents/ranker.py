"""A10 Ranker (PLAN §3.3, §9.5, brief step 5), code only — zero model calls.

Inputs are saved files only: outputs/enriched.jsonl.gz (records), outputs/membership.csv, outputs/issues.json and
labels/areas.yaml. Outputs (byte-identical on every rerun of the same inputs):
- outputs/ranking.csv           rank,issue_id,complaint_count,severity_sum,mean_severity,priority_score  (the contract)
- outputs/aggregates.csv        the same per issue + name, topic, subtopic, area, cancellations, needs_review, share
- outputs/area_aggregates.csv   the declared roll-up of the 8 topics into the brief's 4 effort areas (+ outside)
- outputs/overall_metrics.json  record counts and label distributions for the dashboard
- outputs/comparison_random_100k_{ranking,areas}.csv  the same computation on the members listed in
  outputs/comparison_random_100k.txt (the unbiased random 100k; PLAN §1.1), for the memo's robustness check
priority_score = severity_sum = complaint_count × mean_severity; sort by score desc, then issue_id asc; ranks from 1.
mean_severity and shares: 6 decimals, half-up (the checker's `mean_string`). Membership is validated first.
"""
import csv
import gzip
import json
from collections import Counter, defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from pipeline.agents.grouper import areas
from pipeline.check_submission import mean_string
from pipeline.db.store import immediate

RANK_FIELDS = ["rank", "issue_id", "complaint_count", "severity_sum", "mean_severity", "priority_score"]
AGG_FIELDS = ["rank", "issue_id", "name", "topic", "subtopic", "area", "complaint_count", "cancellation_count",
              "severity_sum", "mean_severity", "priority_score", "needs_review_count", "share_of_members"]
AREA_FIELDS = ["area", "topics", "issue_count", "complaint_count", "cancellation_count", "severity_sum",
               "mean_severity", "share_of_members", "share_of_severity", "top_issue_id"]
MEMBER_INTENTS = {"complaint", "cancellation"}


class RankError(ValueError):
    pass


def share(a, b):
    return str((Decimal(a) / Decimal(b)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)) if b else "0.000000"


def load(root):
    out = Path(root) / "outputs"
    records = {}
    with gzip.open(out / "enriched.jsonl.gz", "rt", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            records[r["review_id"]] = r
    with (out / "membership.csv").open(encoding="utf-8", newline="") as f:
        membership = [(row["issue_id"], row["review_id"]) for row in csv.DictReader(f)]
    issues = json.loads((out / "issues.json").read_text(encoding="utf-8"))
    comp = out / "comparison_random_100k.txt"
    sample = frozenset(comp.read_text(encoding="utf-8").split()) if comp.exists() else None
    return records, membership, issues, sample


def validate(records, membership, issue_ids):
    """The contract's membership rules (allow_multi_issue: false). Raises RankError listing the first problems."""
    errors, seen, per_review = [], set(), Counter()
    for iid, rid in membership:
        r = records.get(rid)
        if iid not in issue_ids:
            errors.append(f"unknown issue {iid}")
        if r is None or r["status"] != "completed" or r["intent"] not in MEMBER_INTENTS:
            errors.append(f"{rid} is not a completed complaint/cancellation record")
        if (iid, rid) in seen:
            errors.append(f"duplicate pair {iid},{rid}")
        seen.add((iid, rid))
        per_review[rid] += 1
    errors += [f"{rid} in {n} issues" for rid, n in per_review.items() if n > 1]
    ungrouped = [rid for rid, r in records.items() if r["status"] == "completed" and r["intent"] in MEMBER_INTENTS
                 and rid not in per_review]
    if ungrouped:
        errors.append(f"{len(ungrouped)} complaint/cancellation records not grouped (e.g. {ungrouped[0]})")
    if errors:
        raise RankError(f"{len(errors)} membership problem(s): " + "; ".join(errors[:5]))


def compute(records, membership, issues_doc):
    issues = {i["issue_id"]: i for i in issues_doc["issues"]}
    validate(records, membership, set(issues))
    rows, area_rows = roll_up(records, membership, issues)
    completed = [r for r in records.values() if r["status"] == "completed"]
    overall = {"label_config": issues_doc["label_config"], "records": len(records),
               "completed": len(completed), "quarantined": len(records) - len(completed),
               "cache_reused": sum(1 for r in completed if r.get("cache_source_id")),
               "needs_review": sum(1 for r in completed if r["needs_review"] is True),
               "members": len(membership), "issues": len(rows),
               "by_intent": dict(sorted(Counter(r["intent"] for r in completed).items())),
               "by_topic": dict(sorted(Counter(r["topic"] for r in completed).items())),
               "by_severity": {str(k): v for k, v in sorted(Counter(r["severity"] for r in completed).items())},
               "quarantine_reasons": dict(sorted(Counter(r["reason"] for r in records.values()
                                                         if r["status"] == "quarantined").items()))}
    return rows, area_rows, overall


def roll_up(records, membership, issues):
    """Issue rows (ranked) + area rows for a validated membership (or a subset of it). Code only."""
    sev, canc, nr = defaultdict(list), Counter(), Counter()
    for iid, rid in membership:
        r = records[rid]
        sev[iid].append(r["severity"])
        canc[iid] += r["intent"] == "cancellation"
        nr[iid] += r["needs_review"] is True
    total = len(membership)
    rows = [{"issue_id": iid, "complaint_count": len(v), "severity_sum": sum(v),
             "mean_severity": mean_string(sum(v), len(v)), "priority_score": sum(v)} for iid, v in sev.items()]
    rows.sort(key=lambda x: (-x["priority_score"], x["issue_id"]))
    for n, row in enumerate(rows, 1):
        it = issues[row["issue_id"]]
        row.update(rank=n, name=it["name"], topic=it["topic"], subtopic=it["subtopic"], area=it["area"],
                   cancellation_count=canc[row["issue_id"]], needs_review_count=nr[row["issue_id"]],
                   share_of_members=share(row["complaint_count"], total))
    _, _, order, area_topics = areas()
    total_sev = sum(r["severity_sum"] for r in rows)
    area_rows = []
    for area in order:
        mine = [r for r in rows if r["area"] == area]
        cnt, ssum = sum(r["complaint_count"] for r in mine), sum(r["severity_sum"] for r in mine)
        area_rows.append({"area": area, "topics": "|".join(area_topics[area]), "issue_count": len(mine),
                          "complaint_count": cnt, "cancellation_count": sum(r["cancellation_count"] for r in mine),
                          "severity_sum": ssum, "mean_severity": mean_string(ssum, cnt) if cnt else "",
                          "share_of_members": share(cnt, total), "share_of_severity": share(ssum, total_sev),
                          "top_issue_id": mine[0]["issue_id"] if mine else ""})
    return rows, area_rows


def _write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fields, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def rank(root, conn=None):
    """Recompute the ranking and aggregates from saved files; optionally refresh the DB tables. Zero model calls."""
    records, membership, issues_doc, sample = load(root)
    rows, area_rows, overall = compute(records, membership, issues_doc)
    out = Path(root) / "outputs"
    _write_csv(out / "ranking.csv", RANK_FIELDS, rows)
    _write_csv(out / "aggregates.csv", AGG_FIELDS, rows)
    _write_csv(out / "area_aggregates.csv", AREA_FIELDS, area_rows)
    if sample is not None:
        sub = [(iid, rid) for iid, rid in membership if rid in sample]
        if len({rid for _, rid in sub}) != len(sample):
            raise RankError("comparison_random_100k.txt lists review IDs that are not members")
        c_rows, c_areas = roll_up(records, sub, {i["issue_id"]: i for i in issues_doc["issues"]})
        _write_csv(out / "comparison_random_100k_ranking.csv", AGG_FIELDS, c_rows)
        _write_csv(out / "comparison_random_100k_areas.csv", AREA_FIELDS, c_areas)
        overall["comparison_random_100k"] = {"members": len(sub), "issues": len(c_rows)}
    (out / "overall_metrics.json").write_text(json.dumps(overall, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if conn is not None:
        cfg = issues_doc["label_config"]
        with immediate(conn):
            for t in ("ranking", "aggregates", "area_aggregates"):
                conn.execute(f"DELETE FROM {t} WHERE label_config=?", (cfg,))
            conn.executemany("INSERT INTO ranking VALUES (?,?,?,?,?,?,?)",
                             [(cfg, *(r[k] for k in RANK_FIELDS)) for r in rows])
            conn.executemany("INSERT INTO aggregates VALUES (?,?,?)",
                             [(cfg, r["issue_id"], json.dumps({k: r[k] for k in AGG_FIELDS}, sort_keys=True))
                              for r in rows])
            conn.executemany("INSERT INTO area_aggregates VALUES (?,?,?)",
                             [(cfg, a["area"], json.dumps(a, sort_keys=True)) for a in area_rows])
    return {"issues": len(rows), "members": overall["members"], "top": rows[0]["issue_id"] if rows else None,
            "files": [f"outputs/{n}" for n in ("ranking.csv", "aggregates.csv", "area_aggregates.csv",
                                               "overall_metrics.json")]
                     + ([f"outputs/comparison_random_100k_{n}.csv" for n in ("ranking", "areas")] if sample else [])}
