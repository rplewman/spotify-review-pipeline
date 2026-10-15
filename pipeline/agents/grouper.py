"""A9 Grouper (PLAN §3.3, brief step 4): code assigns issue membership; Haiku only names the issues.

- Members: every completed record under the label_config whose intent is complaint or cancellation (originals, cache
  reuses, out-of-scope fills and the frequency add-on alike: each review ID counts separately). Exactly one issue per
  review. The contract requires it (the checker flags `ungrouped_complaints` otherwise), so nothing is left out (M9).
- Comparison (M9): the members in the unbiased random 100k scope are listed in outputs/comparison_random_100k.txt;
  the ranker computes the same roll-up on them alone (PLAN §1.1: reported on both).
- issue_id = "<topic>-<subtopic>" from labels/subtopics.yaml (first subtopic with a term in the review text, else
  "<topic>-general"). Deterministic: the same records always give the same membership.
- Naming (one model call, role `group`): per issue its ID, topic, subtopic, size and ≤ 5 example quotes with review
  IDs. Unknown issue IDs in the answer are rejected; a missing or unusable name falls back to a code name. The answer
  is cached by an input hash (analysis_cache), so a warm rerun makes zero calls.
- Saved (the accepted mapping): DB `issues` + `membership`; files outputs/issues.json, outputs/membership.csv and
  outputs/enriched.jsonl.gz (the record snapshot the ranking is computed from).
"""
import csv
import gzip
import hashlib
import io
import json
import re
from functools import lru_cache
from pathlib import Path

import yaml

from pipeline.agents.extractor import _pattern
from pipeline.check_submission import canonical
from pipeline.db.store import immediate, now_iso
from pipeline.oneshot import call_validated

ROOT = Path(__file__).resolve().parents[2]
SUBTOPICS = ROOT / "labels" / "subtopics.yaml"
AREAS = ROOT / "labels" / "areas.yaml"
SYSTEM = (ROOT / "prompts" / "grouper_system_v1.md").read_text(encoding="utf-8").strip()
PROMPT_VERSION = "grouper-prompt-v1"
EXAMPLES_PER_ISSUE = 5
QUOTE_CHARS = 160
SCHEMA = {
    "type": "object",
    "properties": {"issues": {"type": "array", "items": {
        "type": "object",
        "properties": {"issue_id": {"type": "string"}, "name": {"type": "string"}, "description": {"type": "string"}},
        "required": ["issue_id", "name", "description"], "additionalProperties": False}}},
    "required": ["issues"], "additionalProperties": False,
}


@lru_cache(maxsize=1)
def subtopics():
    data = yaml.safe_load(SUBTOPICS.read_text(encoding="utf-8"))
    rules = {topic: [(sub, _pattern(terms)) for sub, terms in subs.items() if terms]
             for topic, subs in data["subtopics"].items()}
    return data["version"], rules


@lru_cache(maxsize=1)
def areas():
    """topic → area (the four effort areas; catalog/other → 'outside'), and the area order."""
    data = yaml.safe_load(AREAS.read_text(encoding="utf-8"))
    by_topic = {t: area for area, topics in data["areas"].items() for t in topics}
    by_topic.update({t: "outside" for t in data["outside"]})
    return data["version"], by_topic, [*data["areas"], "outside"], data["areas"] | {"outside": data["outside"]}


def assign(topic, text):
    """→ (issue_id, subtopic)."""
    for sub, pattern in subtopics()[1].get(topic, []):
        if pattern.search(text):
            return f"{topic}-{sub}", sub
    return f"{topic}-general", "general"


def default_name(topic, subtopic):
    return f"{topic.capitalize()}: {subtopic.replace('_', ' ')}"


def members(conn, label_config):
    return conn.execute("""SELECT r.review_id, r.topic, r.intent, r.severity, r.evidence_quote, r.cache_source_id,
                                  s.text_sha256, s.review_text
                           FROM records r JOIN source_reviews s ON s.review_id = r.review_id
                           WHERE r.status='completed' AND r.label_config=? AND r.intent IN ('complaint','cancellation')
                           ORDER BY r.review_id""", (label_config,)).fetchall()


def random_100k_members(conn, label_config):
    """Members in the random 100k scope (the unbiased sample; add-on and cache-filled rows favour duplicated texts)."""
    return [r[0] for r in conn.execute("""SELECT r.review_id FROM records r JOIN source_reviews s ON s.review_id = r.review_id
                                          WHERE r.status='completed' AND r.label_config=? AND s.scope='random_100k'
                                            AND r.intent IN ('complaint','cancellation') ORDER BY r.review_id""",
                                       (label_config,))]


def build(rows):
    """→ (membership [(issue_id, review_id)], issues {issue_id: {...}}) — code only, deterministic."""
    by_text, membership, issues = {}, [], {}
    topic_area = areas()[1]
    for rid, topic, intent, sev, quote, src, sha, text in rows:
        if (sha, topic) not in by_text:
            by_text[(sha, topic)] = assign(topic, text)
        iid, sub = by_text[(sha, topic)]
        membership.append((iid, rid))
        it = issues.setdefault(iid, {"issue_id": iid, "topic": topic, "subtopic": sub, "area": topic_area[topic],
                                     "member_count": 0, "_ex": {}})
        it["member_count"] += 1
        if src is None:  # examples from originals, one per distinct text
            it["_ex"].setdefault(sha, (-sev, rid, quote[:QUOTE_CHARS], sev, intent))
    for it in issues.values():
        ex = sorted(it.pop("_ex").values())[:EXAMPLES_PER_ISSUE]
        it["examples"] = [{"review_id": rid, "quote": q, "severity": s, "intent": i} for _, rid, q, s, i in ex]
    return sorted(membership), dict(sorted(issues.items()))


def naming_payload(issues):
    groups = [{"issue_id": i["issue_id"], "topic": i["topic"], "subtopic": i["subtopic"], "size": i["member_count"],
               "examples": [{"review_id": e["review_id"], "quote": e["quote"]} for e in i["examples"]]}
              for i in issues.values()]
    return {
        "max_tokens": 1500 + 80 * len(groups),
        "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": "Name each group.\n\n" + json.dumps({"groups": groups},
                                                                                       ensure_ascii=False, indent=1)}],
        "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
    }


def validate_names(body, issue_ids):
    """→ ({issue_id: {name, description}}, notes). Raises ValueError when the body is unusable."""
    if not isinstance(body, dict) or not isinstance(body.get("issues"), list):
        raise ValueError("unparseable grouper output")
    names, notes = {}, []
    for e in body["issues"]:
        iid = e.get("issue_id") if isinstance(e, dict) else None
        if iid not in issue_ids:
            notes.append(f"rejected unknown issue id {str(iid)[:60]!r}")
            continue
        name = (e.get("name") or "").strip()
        if not name or len(name) > 80 or re.search(r"\d", name):
            notes.append(f"rejected name for {iid}: {name[:60]!r}")
            continue
        names.setdefault(iid, {"name": name, "description": (e.get("description") or "").strip()[:300]})
    return names, notes


def input_sha(payload, model):
    return hashlib.sha256(canonical({"model": model, "payload": payload}).encode("utf-8")).hexdigest()


def name_issues(conn, issues, provider, label_config, run_id, max_spend=None):
    """→ (names, provenance). Cached by input hash; otherwise one `group` call (bucket analysis)."""
    payload = naming_payload(issues)
    sha = input_sha(payload, provider.model)
    hit = conn.execute("SELECT output_json, request_id FROM analysis_cache WHERE agent='A9' AND input_sha256=? "
                       "AND model=?", (sha, provider.model)).fetchone()
    if hit:
        out = json.loads(hit[0])
        return out["names"], {"input_sha256": sha, "request_id": hit[1], "cache": "hit", "notes": out["notes"]}
    ids = set(issues)
    review_ids = sorted({e["review_id"] for i in issues.values() for e in i["examples"]})
    (names, notes), rid = call_validated(
        conn, provider, role="group", agent="A9", label_config=label_config, payload=payload, run_id=run_id,
        bucket="analysis", max_spend=max_spend, review_ids=review_ids,
        meta={"shape": "group", "issue_ids": sorted(ids)},
        validate=lambda body: (validate_names(body, ids), []), retry_payload=lambda errors: dict(payload))
    with immediate(conn):
        conn.execute("INSERT OR REPLACE INTO analysis_cache (agent, input_sha256, model, output_json, request_id, "
                     "created_at) VALUES ('A9',?,?,?,?,?)",
                     (sha, provider.model, json.dumps({"names": names, "notes": notes}, sort_keys=True), rid, now_iso()))
    return names, {"input_sha256": sha, "request_id": rid, "cache": "miss", "notes": notes}


def export_enriched(conn, label_config, path):
    """The record snapshot (contract shape) for this label_config + the empty-text quarantines; sorted by review_id."""
    # gzip with mtime 0: the same records always give the same bytes (the full scope is ~150 MB uncompressed)
    with gzip.GzipFile(path, "wb", mtime=0) as raw, io.TextIOWrapper(raw, encoding="utf-8", newline="\n") as f:
        for (rid, sha, status, reason, topic, intent, sent, sev, ents, quote, nr, cfg, src) in conn.execute(
                """SELECT review_id, source_sha256, status, reason, topic, intent, sentiment, severity, entities_json,
                          evidence_quote, needs_review, label_config, cache_source_id FROM records
                   WHERE label_config = ? OR (status='quarantined' AND label_config IS NULL) ORDER BY review_id""",
                (label_config,)):
            if status == "quarantined":
                rec = {"review_id": rid, "source_sha256": sha, "status": status, "reason": reason}
            else:
                rec = {"review_id": rid, "source_sha256": sha, "status": status, "topic": topic, "intent": intent,
                       "sentiment": sent, "severity": sev, "entities": json.loads(ents), "evidence_quote": quote,
                       "needs_review": bool(nr), "label_config": cfg}
                if src:
                    rec["cache_source_id"] = src
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")


def group(conn, label_config, provider, root, run_id, max_spend=None):
    """Build membership, name the issues, save the accepted mapping (DB + files). Returns a summary."""
    rows = members(conn, label_config)
    if not rows:
        raise ValueError(f"no completed complaint/cancellation records under {label_config!r}")
    membership, issues = build(rows)
    names, prov = name_issues(conn, issues, provider, label_config, run_id, max_spend)
    for iid, it in issues.items():
        n = names.get(iid)
        it.update({"name": n["name"], "description": n["description"], "name_source": "model"} if n else
                  {"name": default_name(it["topic"], it["subtopic"]), "description": "", "name_source": "default"})
    with immediate(conn):
        conn.execute("DELETE FROM issues WHERE label_config=?", (label_config,))
        conn.execute("DELETE FROM membership WHERE label_config=?", (label_config,))
        conn.executemany("INSERT INTO issues (issue_id, label_config, topic, subtopic, area, name, description, "
                         "name_source, member_count) VALUES (?,?,?,?,?,?,?,?,?)",
                         [(i["issue_id"], label_config, i["topic"], i["subtopic"], i["area"], i["name"],
                           i["description"], i["name_source"], i["member_count"]) for i in issues.values()])
        conn.executemany("INSERT INTO membership (issue_id, review_id, label_config) VALUES (?,?,?)",
                         [(iid, rid, label_config) for iid, rid in membership])
    out = Path(root) / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "membership.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["issue_id", "review_id"])
        w.writerows(membership)
    sample = random_100k_members(conn, label_config)
    (out / "comparison_random_100k.txt").write_text("".join(rid + "\n" for rid in sample), encoding="utf-8")
    (out / "ranking_excluded.txt").unlink(missing_ok=True)  # pre-M9 layout
    doc = {"label_config": label_config, "subtopics_version": subtopics()[0], "areas_version": areas()[0],
           "grouper_prompt": PROMPT_VERSION, "model": provider.model, "naming": prov,
           "allow_multi_issue": False, "comparison": {"scope": "random_100k", "members": len(sample),
                                                      "file": "comparison_random_100k.txt"},
           "issues": list(issues.values())}
    (out / "issues.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                     encoding="utf-8")
    export_enriched(conn, label_config, out / "enriched.jsonl.gz")
    return {"issues": len(issues), "members": len(membership), "named_by_model": sum(1 for i in issues.values()
                                                                                    if i["name_source"] == "model"),
            "naming": prov}
