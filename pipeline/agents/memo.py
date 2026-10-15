"""A11 Memo writer + recommender (PLAN §3.3, brief step 6): ONE Haiku call (role `memo`) over saved aggregates only.

Input (built by code, saved as outputs/memo_input.json): the area roll-up, overall counts, the top-ranked issues with
claim IDs for their four metrics, and a bounded evidence pack (≤ 3 quotes with review IDs per issue). Never raw data.
Output (structured): priority_area, memo_markdown, 2–4 recommendations with issue / claim / review IDs.

Code checks before anything is saved — an answer that fails is rejected (logged as a failed call), retried once with
the errors, and otherwise the stage fails:
  priority_area ∈ the 4 areas · issue IDs ∈ the ranking · review IDs ∈ the evidence pack · claim IDs ∈ the offered
  claims · every number in the prose appears in the input (after IDs and claim tags are removed) · ≥ 1 claim cited.
Saved: outputs/memo.md (assembled by code: provenance + the memo + the cited claims table), outputs/claims.csv (the
cited claims, values from the ranking), outputs/recommendations.json, DB `claims` + `recommendations`.
Cached by input hash (analysis_cache): a warm rerun makes zero calls.
"""
import csv
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pipeline.agents.grouper import input_sha
from pipeline.db.store import immediate, now_iso
from pipeline.oneshot import call_validated

ROOT = Path(__file__).resolve().parents[2]
SYSTEM = (ROOT / "prompts" / "memo_system_v3.md").read_text(encoding="utf-8").strip()
# M9: v2 = scope disclosure, wording rules, random-100k comparison; v3 = no "every measure" claims (Rory)
PROMPT_VERSION = "memo-prompt-v3"
TOP_ISSUES = 10
EVIDENCE_PER_ISSUE = 3
METRICS = {"a": "complaint_count", "b": "severity_sum", "c": "mean_severity", "d": "priority_score"}
FOUR = ["access", "usability", "playback", "billing/support"]
QUESTION = "Where should Spotify put the next quarter of product effort: access, usability, playback, or billing/support?"
SCHEMA = {
    "type": "object",
    "properties": {
        "priority_area": {"type": "string", "enum": FOUR},
        "memo_markdown": {"type": "string"},
        "recommendations": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "area": {"type": "string", "enum": FOUR},
                           "rationale": {"type": "string"},
                           "issue_ids": {"type": "array", "items": {"type": "string"}},
                           "claim_ids": {"type": "array", "items": {"type": "string"}},
                           "review_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["title", "area", "rationale", "issue_ids", "claim_ids", "review_ids"],
            "additionalProperties": False}},
    },
    "required": ["priority_area", "memo_markdown", "recommendations"], "additionalProperties": False,
}
CLAIM_TAG = re.compile(r"\bC\d{2}[a-d]\b")
UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
ISSUE_REF = re.compile(r"`([a-z]+-[a-z_]+)`")  # issues are cited as `topic-subtopic`


def _read_csv(path):
    with Path(path).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def pct(s):
    return f"{Decimal(s) * 100:.1f}%"


def build_pack(root):
    """The memo's whole input, from saved analysis files only."""
    out = Path(root) / "outputs"
    ranking = _read_csv(out / "aggregates.csv")
    area_rows = _read_csv(out / "area_aggregates.csv")
    overall = json.loads((out / "overall_metrics.json").read_text(encoding="utf-8"))
    issues = {i["issue_id"]: i for i in json.loads((out / "issues.json").read_text(encoding="utf-8"))["issues"]}
    top = []
    for row in ranking[:TOP_ISSUES]:
        r = int(row["rank"])
        top.append({"rank": r, "issue_id": row["issue_id"], "name": row["name"], "area": row["area"],
                    "topic": row["topic"], **{m: row[m] for m in ("complaint_count", "cancellation_count",
                                                                   "severity_sum", "mean_severity", "priority_score")},
                    "share_of_members": pct(row["share_of_members"]),
                    "claims": {m: f"C{r:02d}{k}" for k, m in METRICS.items()},
                    "evidence": issues[row["issue_id"]]["examples"][:EVIDENCE_PER_ISSUE]})
    pack = {
        "question": QUESTION, "label_config": overall["label_config"],
        "totals": {k: overall[k] for k in ("records", "completed", "quarantined", "needs_review", "members", "issues")},
        "quarantine_reasons": overall["quarantine_reasons"],
        "areas": _areas(area_rows),
        "top_issues": top, "issues_not_shown": max(0, len(ranking) - TOP_ISSUES),
    }
    comp = out / "comparison_random_100k_areas.csv"
    if comp.exists():  # M9: the same roll-up on the unbiased random 100k alone (PLAN §1.1)
        pack["comparison_random_100k"] = {"members": overall["comparison_random_100k"]["members"],
                                          "areas": _areas(_read_csv(comp))}
    return pack


def _areas(area_rows):
    return [{**{k: a[k] for k in ("area", "topics", "top_issue_id")},
             **{k: int(a[k]) for k in ("issue_count", "complaint_count", "cancellation_count", "severity_sum")},
             "mean_severity": a["mean_severity"], "share_of_members": pct(a["share_of_members"]),
             "share_of_severity": pct(a["share_of_severity"])} for a in area_rows]


def payload(pack, errors=None):
    user = "Write the memo from this input.\n\n" + json.dumps(pack, ensure_ascii=False, indent=1)
    if errors:
        user += ("\n\nYour previous answer was rejected by the code checks:\n- " + "\n- ".join(errors[:20])
                 + "\nAnswer again, fixing every point.")
    return {"max_tokens": 6000,
            "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
            "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}}}


def _num(token):
    try:
        return Decimal(token.replace(",", "")).normalize()
    except InvalidOperation:
        return None


def allowed_numbers(pack):
    text = json.dumps(pack, ensure_ascii=False)
    text = CLAIM_TAG.sub(" ", UUID.sub(" ", text))
    nums = {_num(t) for t in NUMBER.findall(text)}
    return {n for n in nums if n is not None} | {Decimal(k) for k in range(1, 11)}


def check(body, pack, known_issues):
    """→ errors (empty = accepted). Pure code; the model's text is data."""
    if not isinstance(body, dict):
        return ["unparseable memo output"]
    errors = []
    claims = {cid: (t["issue_id"], m) for t in pack["top_issues"] for m, cid in t["claims"].items()}
    evidence = {e["review_id"] for t in pack["top_issues"] for e in t["evidence"]}
    allowed = allowed_numbers(pack)
    if body.get("priority_area") not in FOUR:
        errors.append(f"priority_area {body.get('priority_area')!r} is not one of {FOUR}")
    memo = body.get("memo_markdown")
    recs = body.get("recommendations")
    if not isinstance(memo, str) or not memo.strip():
        return errors + ["memo_markdown is empty"]
    if not isinstance(recs, list) or not 1 <= len(recs) <= 5:
        errors.append("recommendations must be a list of 1-5 items")
        recs = []
    texts = [("memo", memo)] + [(f"recommendation {n}", f"{r.get('title', '')} {r.get('rationale', '')}")
                                for n, r in enumerate(recs, 1) if isinstance(r, dict)]
    cited = set()
    for where, text in texts:
        for cid in CLAIM_TAG.findall(text):
            (cited.add(cid) if cid in claims else errors.append(f"{where}: unknown claim ID {cid}"))
        for rid in UUID.findall(text):
            if rid not in evidence:
                errors.append(f"{where}: review ID {rid} is not in the evidence pack")
        errors += [f"{where}: unknown issue ID {i}" for i in ISSUE_REF.findall(text) if i not in known_issues]
        stripped = CLAIM_TAG.sub(" ", UUID.sub(" ", ISSUE_REF.sub(" ", text)))
        for tok in NUMBER.findall(stripped):
            n = _num(tok)
            if n is not None and n not in allowed:
                errors.append(f"{where}: number {tok} is not in the input")
    for n, r in enumerate(recs, 1):
        if not isinstance(r, dict):
            errors.append(f"recommendation {n} is not an object")
            continue
        if r.get("area") not in FOUR:
            errors.append(f"recommendation {n}: area {r.get('area')!r} invalid")
        if not r.get("issue_ids"):
            errors.append(f"recommendation {n}: no issue IDs")
        errors += [f"recommendation {n}: unknown issue ID {i}" for i in r.get("issue_ids", []) if i not in known_issues]
        errors += [f"recommendation {n}: unknown claim ID {c}" for c in r.get("claim_ids", []) if c not in claims]
        errors += [f"recommendation {n}: review ID {v} is not in the evidence pack"
                   for v in r.get("review_ids", []) if v not in evidence]
        cited |= {c for c in r.get("claim_ids", []) if c in claims}
    if not cited:
        errors.append("no claim IDs cited")
    return errors


def _sources(pack):
    """{number: [paths in memo_input.json]}; list items are labelled by their area / issue ID."""
    found = {}

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                label = (v.get("issue_id") or v.get("area") or v.get("review_id")) if isinstance(v, dict) else None
                walk(v, f"{path}[{label or i}]")
        elif isinstance(node, (int, float, str)) and not isinstance(node, bool):
            for tok in NUMBER.findall(CLAIM_TAG.sub(" ", UUID.sub(" ", str(node)))):
                n = _num(tok)
                if n is not None:
                    found.setdefault(n, []).append(path)
    walk(pack, "")
    return found


def trace_numbers(body, pack):
    """M9 gate: every number in the memo maps to a claim ID or to a field of memo_input.json (code, no model)."""
    claims = {cid: t[m] for t in pack["top_issues"] for m, cid in t["claims"].items()}
    sources = _sources(pack)
    texts = [body["memo_markdown"]] + [f"{r['title']} {r['rationale']}" for r in body["recommendations"]]
    rows, seen = [], set()
    for text in texts:
        cited = {_num(str(claims[c])): c for c in CLAIM_TAG.findall(text) if c in claims}
        for tok in NUMBER.findall(CLAIM_TAG.sub(" ", UUID.sub(" ", ISSUE_REF.sub(" ", text)))):
            tok = tok.rstrip(",")
            n = _num(tok)
            if n is None or n in seen:
                continue
            seen.add(n)
            if n in cited:
                rows.append((tok, f"claim {cited[n]}"))
            elif n in sources:
                rows.append((tok, "; ".join(sources[n][:3]) + (" …" if len(sources[n]) > 3 else "")))
            else:
                rows.append((tok, "small count/ordinal (1–10, allowed by the check)"))
    return rows


def write_outputs(conn, root, pack, body, prov, label_config, ranking):
    out = Path(root) / "outputs"
    claims = {cid: (t["issue_id"], m) for t in pack["top_issues"] for m, cid in t["claims"].items()}
    value = {r["issue_id"]: r for r in ranking}
    cited = set(CLAIM_TAG.findall(body["memo_markdown"]))
    for r in body["recommendations"]:
        cited |= set(r["claim_ids"]) | set(CLAIM_TAG.findall(r["rationale"]))
    rows = [(cid, claims[cid][0], claims[cid][1], value[claims[cid][0]][claims[cid][1]])
            for cid in sorted(cited & set(claims))]
    with (out / "claims.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["claim_id", "issue_id", "metric", "value"])
        w.writerows(rows)
    by_claim = {r[0]: r for r in rows}
    quotes = {e["review_id"]: (t["issue_id"], e["quote"]) for t in pack["top_issues"] for e in t["evidence"]}
    recs = [{"position": n, "title": r["title"], "area": r["area"], "rationale": r["rationale"],
             "issue_ids": r["issue_ids"], "claim_ids": r["claim_ids"],
             "supporting": [dict(zip(("claim_id", "issue_id", "metric", "value"), by_claim[c]))
                            for c in r["claim_ids"] if c in by_claim],
             "evidence": [{"review_id": v, "issue_id": quotes[v][0], "quote": quotes[v][1]} for v in r["review_ids"]]}
            for n, r in enumerate(body["recommendations"], 1)]
    doc = {"label_config": label_config, "priority_area": body["priority_area"], "question": QUESTION,
           "memo_prompt": PROMPT_VERSION, **prov, "recommendations": recs}
    (out / "recommendations.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                              encoding="utf-8")
    table = "\n".join(f"| {c} | `{i}` | {m} | {v} |" for c, i, m, v in rows)
    rec_md = "\n".join(f"{r['position']}. **{r['title']}** ({r['area']}): {r['rationale']} — issues "
                       f"{', '.join(f'`{i}`' for i in r['issue_ids'])}; claims {', '.join(r['claim_ids'])}"
                       + (f"; reviews {', '.join(e['review_id'] for e in r['evidence'])}" if r["evidence"] else "")
                       for r in recs)
    memo = (f"# Decision memo: next quarter's product priority\n\n"
            f"> {QUESTION}\n>\n> **Priority: {body['priority_area']}.** Generated once by `{prov['model']}` "
            f"({PROMPT_VERSION}, request `{prov['request_id']}`) from `outputs/memo_input.json` "
            f"(sha256 `{prov['input_sha256'][:16]}…`); label_config `{label_config}`. Every number below was checked by "
            f"code against the saved aggregates; claim IDs resolve in `outputs/claims.csv`.\n\n"
            f"{body['memo_markdown'].strip()}\n\n## Recommendations\n\n{rec_md}\n\n"
            f"## Claims cited\n\n| claim_id | issue_id | metric | value |\n|---|---|---|---|\n{table}\n\n"
            f"## Number trace\n\nEvery number in the memo and recommendations, mapped by code to a claim ID or to its "
            f"field in `outputs/memo_input.json` (area-level numbers and totals have no contract claim metric).\n\n"
            f"| number | source |\n|---|---|\n" + "\n".join(f"| {n} | {src} |" for n, src in trace_numbers(body, pack))
            + "\n")
    (out / "memo.md").write_text(memo, encoding="utf-8", newline="\n")
    with immediate(conn):
        conn.execute("DELETE FROM claims WHERE label_config=?", (label_config,))
        conn.execute("DELETE FROM recommendations WHERE label_config=?", (label_config,))
        conn.executemany("INSERT INTO claims VALUES (?,?,?,?,?)", [(label_config, *r) for r in rows])
        conn.executemany("INSERT INTO recommendations VALUES (?,?,?,?)",
                         [(label_config, r["position"], json.dumps(r, ensure_ascii=False, sort_keys=True),
                           prov["request_id"]) for r in recs])
    return rows, recs


def write_memo(conn, root, provider, run_id, max_spend=None):
    out = Path(root) / "outputs"
    pack = build_pack(root)
    label_config = pack["label_config"]
    ranking = _read_csv(out / "aggregates.csv")
    known = {r["issue_id"] for r in ranking}
    base = payload(pack)
    sha = input_sha(base, provider.model)
    (out / "memo_input.json").write_text(json.dumps({"input_sha256": sha, "pack": pack}, ensure_ascii=False, indent=1,
                                                    sort_keys=True) + "\n", encoding="utf-8")
    hit = conn.execute("SELECT output_json, request_id FROM analysis_cache WHERE agent='A11' AND input_sha256=? "
                       "AND model=?", (sha, provider.model)).fetchone()
    if hit:
        body, rid, cache = json.loads(hit[0]), hit[1], "hit"
    else:
        meta = {"shape": "memo", "pack": pack}
        evidence = sorted({e["review_id"] for t in pack["top_issues"] for e in t["evidence"]})
        body, rid = call_validated(
            conn, provider, role="memo", agent="A11", label_config=label_config, payload=base, run_id=run_id,
            bucket="analysis", max_spend=max_spend, review_ids=evidence, meta=meta,
            validate=lambda b: (b, check(b, pack, known)), retry_payload=lambda errors: payload(pack, errors))
        cache = "miss"
        with immediate(conn):
            conn.execute("INSERT OR REPLACE INTO analysis_cache (agent, input_sha256, model, output_json, request_id, "
                         "created_at) VALUES ('A11',?,?,?,?,?)",
                         (sha, provider.model, json.dumps(body, ensure_ascii=False, sort_keys=True), rid, now_iso()))
    prov = {"model": provider.model, "request_id": rid, "input_sha256": sha, "cache": cache}
    rows, recs = write_outputs(conn, root, pack, body, prov, label_config, ranking)
    return {"priority_area": body["priority_area"], "claims": len(rows), "recommendations": len(recs), **prov}
