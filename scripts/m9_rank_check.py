"""M9 gate: recompute the ranking exactly as pipeline/check_submission.py does (membership → per-issue severities →
sort by -priority_score, issue_id) from the saved files, and compare it and claims.csv string-by-string. Zero calls."""
import csv
import gzip
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.check_submission import mean_string  # noqa: E402

out = Path(sys.argv[1] if len(sys.argv) > 1 else "outputs")
valid = {}
with gzip.open(out / "enriched.jsonl.gz", "rt", encoding="utf-8") as f:
    for line in f:
        r = json.loads(line)
        if r["status"] == "completed":
            valid[r["review_id"]] = r
read = lambda n: list(csv.DictReader((out / n).open(encoding="utf-8", newline="")))
members, member_reviews, problems = defaultdict(list), set(), []
for row in read("membership.csv"):
    rid, iid = row["review_id"], row["issue_id"]
    if rid not in valid or valid[rid]["intent"] not in {"complaint", "cancellation"} or rid in member_reviews:
        problems.append(f"bad member {iid},{rid}")
        continue
    member_reviews.add(rid)
    members[iid].append(valid[rid]["severity"])
ungrouped = {r for r, v in valid.items() if v["intent"] in {"complaint", "cancellation"}} - member_reviews
calc = sorted(({"issue_id": i, "complaint_count": len(v), "severity_sum": sum(v), "mean_severity": mean_string(sum(v), len(v)),
                "priority_score": sum(v)} for i, v in members.items()), key=lambda x: (-x["priority_score"], x["issue_id"]))
for n, row in enumerate(calc, 1):
    row["rank"] = n
ranking = read("ranking.csv")
mismatch = [i for i, (a, b) in enumerate(zip(calc, ranking), 1) if any(str(a[k]) != b[k] for k in b)]
by_id = {x["issue_id"]: x for x in calc}
claims = read("claims.csv")
bad_claims = [c["claim_id"] for c in claims if str(by_id.get(c["issue_id"], {}).get(c["metric"])) != c["value"]]
result = {"members": len(member_reviews), "issues": len(calc), "ungrouped_complaints": len(ungrouped),
          "membership_problems": len(problems), "ranking_rows": len(ranking), "ranking_mismatches": mismatch,
          "claims": len(claims), "claim_mismatches": bad_claims,
          "pass": not (ungrouped or problems or mismatch or bad_claims or len(ranking) != len(calc))}
print(json.dumps(result, indent=1))
sys.exit(0 if result["pass"] else 1)
