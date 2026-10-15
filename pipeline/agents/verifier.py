"""A8 Verifier (PLAN §3.3, brief step 3): a separate model task re-labels a declared random sample of texts without
seeing the first prediction; code compares the two and writes the disagreement report.

- Sample (declared, seeded): a distinct text with a result under the enrichment label_config is in the sample when
  sha256(f"{seed}:{text_sha256}") / 2^256 < rate. Same seed + a higher rate = a superset, so the sample can grow.
- Independence: the request is built from review texts only (`build_payload(texts)`); the first labels, their
  probabilities and the label_config never enter it. Its own prompt (prompts/verifier_system_v1.md), its own schema.
- Transport: the Anthropic Message Batches API (pipeline/batch_runner.py), or `--standard` real-time calls through the
  orchestrator with this module's `VerifyStage`. Both store `verifier_outputs`; invalid output → retry once → left
  unverified (reported, never a record quarantine).
- Planted errors: `planted_error_test` corrupts a separate copy of the first labels and shows the comparison flags them.
"""
import csv
import hashlib
import json
from pathlib import Path

from pipeline.agents.decision import neutralize
from pipeline.db.store import now_iso
from pipeline.schema import INTENTS, TOPICS
from pipeline.stages import Call, Stage, payload_tokens

PROMPT_VERSION = "verifier-prompt-v1"
SYSTEM = (Path(__file__).resolve().parents[2] / "prompts" / "verifier_system_v1.md").read_text(encoding="utf-8").strip()
EFFORT = "low"
DEFAULT_RATE = 0.05
DEFAULT_SEED = "verify-v1"
FIELDS = ("topic", "intent", "severity")
SCHEMA = {
    "type": "object",
    "properties": {"labels": {"type": "array", "items": {
        "type": "object",
        "properties": {"ref": {"type": "string"}, "topic": {"type": "string", "enum": list(TOPICS)},
                       "intent": {"type": "string", "enum": list(INTENTS)},
                       "severity": {"type": "string", "enum": ["1", "2", "3", "4", "5"]},
                       "needs_review": {"type": "boolean"}},
        "required": ["ref", "topic", "intent", "severity", "needs_review"], "additionalProperties": False}}},
    "required": ["labels"], "additionalProperties": False,
}


def verify_config(model):
    return f"{model}+{PROMPT_VERSION}+effort-{EFFORT}"


def queue_key(label_config, vcfg):
    """work_items.label_config for the verify stage: what is checked (#) by what."""
    return f"{label_config}#{vcfg}"


def in_sample(text_sha, rate, seed=DEFAULT_SEED):
    return int(hashlib.sha256(f"{seed}:{text_sha}".encode()).hexdigest(), 16) < rate * 2 ** 256


def enqueue_sample(conn, label_config, key, rate, seed=DEFAULT_SEED):
    """Enqueue the sampled texts (in scope, with a result under label_config). Returns (added, sample_size)."""
    rows = conn.execute("""SELECT r.text_sha256, t.scope_rank FROM results r JOIN texts t ON t.text_sha256 = r.text_sha256
                           WHERE r.label_config = ?""", (label_config,)).fetchall()
    chosen = [(sha, rank) for sha, rank in rows if in_sample(sha, rate, seed)]
    with conn:
        added = sum(conn.execute("""INSERT INTO work_items (text_sha256, stage, label_config, scope_rank)
                                    VALUES (?, 'verify', ?, ?) ON CONFLICT DO NOTHING""", (sha, key, rank)).rowcount
                    for sha, rank in chosen)
    return added, len(chosen)


def build_payload(texts):
    """Messages API body minus `model`, from review texts only (the first prediction is never an input)."""
    blocks = "\n\n".join(f"[R{n}] {neutralize(t)}" for n, t in enumerate(texts, 1))
    return {
        "max_tokens": 2000 + 60 * len(texts),  # low-effort thinking + ~30 tokens per label, generous
        "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": f"Label each review.\n\n{blocks}"}],
        "output_config": {"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
    }


def parse(body, n_reviews):
    """→ ({n: labels}, {n: reason}, notes). Raises ValueError when the body is not the expected object."""
    if not isinstance(body, dict) or not isinstance(body.get("labels"), list):
        raise ValueError("unparseable verifier output")
    ok, notes = {}, []
    for lab in body["labels"]:
        ref = lab.get("ref") if isinstance(lab, dict) else None
        n = int(ref[1:]) if isinstance(ref, str) and ref[:1] == "R" and ref[1:].isdigit() else None
        if n is None or not 1 <= n <= n_reviews:
            notes.append(f"ignored label {str(lab)[:80]!r}")
            continue
        if n in ok:
            notes.append(f"duplicate label for R{n} ignored")
            continue
        if (lab.get("topic") not in TOPICS or lab.get("intent") not in INTENTS
                or lab.get("severity") not in ("1", "2", "3", "4", "5") or type(lab.get("needs_review")) is not bool):
            continue  # → missing → invalid for that review
        ok[n] = {"topic": lab["topic"], "intent": lab["intent"], "severity": int(lab["severity"]),
                 "needs_review": lab["needs_review"]}
    bad = {n: "missing or invalid verifier label" for n in range(1, n_reviews + 1) if n not in ok}
    return ok, bad, notes


class VerifyStage(Stage):
    """The verifier as a queue stage. Used by the orchestrator (--standard) and by the batch runner (make_call)."""
    stage, role, agent, bucket = "verify", "verify", "A8", "verification"

    def __init__(self, provider, label_config, vcfg=None):
        self.enrich_config = label_config
        self.vcfg = vcfg or verify_config(provider.model)
        super().__init__(provider, queue_key(label_config, self.vcfg))

    def describe(self):
        return {"stage": "VerifyStage", "checks": self.enrich_config, "verify_config": self.vcfg}

    def make_call(self, items):
        payload = build_payload([i["review_text"] for i in items])
        tokens = payload_tokens(payload)
        index = {n: i["id"] for n, i in enumerate(items, 1)}

        def parse_body(body):
            ok, bad, notes = parse(body, len(items))
            return {index[n]: v for n, v in ok.items()}, {index[n]: r for n, r in bad.items()}, notes

        est = self.provider.cost({"input_tokens": tokens, "output_tokens": payload["max_tokens"]}) * 1.25
        return Call("A8", "A8", self.provider, items, payload, parse_body, tokens, est, meta={"shape": "verify"})

    def next_call(self, conn, ctx):
        if ctx["sent"]:
            return None
        ctx["sent"] = True
        return self.make_call(ctx["items"])

    def cached(self, conn, text_shas):
        if not text_shas:
            return set()
        marks = ",".join("?" * len(text_shas))
        return {r[0] for r in conn.execute(
            f"SELECT text_sha256 FROM verifier_outputs WHERE label_config=? AND verify_config=? "
            f"AND text_sha256 IN ({marks})", [self.enrich_config, self.vcfg, *text_shas])}

    def reuse(self, conn, text_shas):
        pass  # already verified: nothing to write

    def save(self, conn, done):
        for item, v in done:
            conn.execute("""INSERT INTO verifier_outputs (text_sha256, label_config, verify_config, topic, intent, severity,
                                                          needs_review, request_id, created_at)
                            VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
                         (item["text_sha256"], self.enrich_config, self.vcfg, v["topic"], v["intent"], v["severity"],
                          int(v["needs_review"]), v.get("request_id"), now_iso()))

    def quarantine(self, conn, reasons):
        pass  # the queue item is quarantined; the text stays unverified and is counted in the report


# ---------- comparison (code only) ----------
def compare_rows(first, verified):
    """first / verified = {key: {topic, intent, severity}} → [(key, field, first, verifier)] for every disagreement."""
    out = []
    for key in sorted(set(first) & set(verified)):
        for f in FIELDS:
            if first[key][f] != verified[key][f]:
                out.append((key, f, first[key][f], verified[key][f]))
    return out


def load_pairs(conn, label_config, vcfg):
    rows = conn.execute("""SELECT t.original_review_id, v.text_sha256, r.topic, r.intent, r.severity, r.needs_review,
                                  r.decision_source, v.topic, v.intent, v.severity, v.needs_review, v.request_id,
                                  t.review_text
                           FROM verifier_outputs v
                           JOIN results r ON r.text_sha256 = v.text_sha256 AND r.label_config = v.label_config
                           JOIN texts t ON t.text_sha256 = v.text_sha256
                           WHERE v.label_config = ? AND v.verify_config = ? ORDER BY t.original_review_id""",
                        (label_config, vcfg)).fetchall()
    first, verified, meta = {}, {}, {}
    for rid, sha, t1, i1, s1, n1, src, t2, i2, s2, n2, req, text in rows:
        first[rid] = {"topic": t1, "intent": i1, "severity": s1}
        verified[rid] = {"topic": t2, "intent": i2, "severity": s2}
        meta[rid] = {"text_sha256": sha, "first_needs_review": bool(n1), "decision_source": src,
                     "verifier_needs_review": bool(n2), "request_id": req, "review_text": text}
    return first, verified, meta


def report(conn, label_config, vcfg, rate, seed, root):
    """Write evals/verify/{verifier_predictions.jsonl, disagreements.csv, verify_report.json}; return the report."""
    out = Path(root) / "evals" / "verify"
    out.mkdir(parents=True, exist_ok=True)
    first, verified, meta = load_pairs(conn, label_config, vcfg)
    diffs = compare_rows(first, verified)
    key = queue_key(label_config, vcfg)
    status = dict(conn.execute("SELECT status, COUNT(*) FROM work_items WHERE stage='verify' AND label_config=? "
                               "GROUP BY status", (key,)).fetchall())
    with (out / "verifier_predictions.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for rid in sorted(verified):
            f.write(json.dumps({"review_id": rid, "text_sha256": meta[rid]["text_sha256"], **verified[rid],
                                "needs_review": meta[rid]["verifier_needs_review"], "request_id": meta[rid]["request_id"],
                                "verify_config": vcfg}, ensure_ascii=False, sort_keys=True) + "\n")
    with (out / "disagreements.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["review_id", "field", "first_label", "verifier_label", "decision_source", "first_needs_review",
                    "verifier_needs_review", "review_text"])
        for rid, field, a, b in diffs:
            m = meta[rid]
            w.writerow([rid, field, a, b, m["decision_source"], m["first_needs_review"], m["verifier_needs_review"],
                        m["review_text"][:300]])
    n = len(verified)
    by_field = {f: sum(1 for r in verified if first[r][f] == verified[r][f]) for f in FIELDS}
    all_three = sum(1 for r in verified if all(first[r][f] == verified[r][f] for f in FIELDS))
    within1 = sum(1 for r in verified if abs(first[r]["severity"] - verified[r]["severity"]) <= 1)
    by_source = {}
    for r in verified:
        s = by_source.setdefault(meta[r]["decision_source"], {"verified": 0, "all_fields_agree": 0})
        s["verified"] += 1
        s["all_fields_agree"] += all(first[r][f] == verified[r][f] for f in FIELDS)
    rep = {
        "label_config": label_config, "verify_config": vcfg,
        "sample": {"method": "sha256(seed:text_sha256)/2^256 < rate over distinct texts with a result",
                   "rate": rate, "seed": seed, "size": sum(status.values()), "queue": status},
        "verified": n, "unverified": sum(status.values()) - n,
        "agreement": {f: {"agree": by_field[f], "rate": round(by_field[f] / n, 4) if n else None} for f in FIELDS},
        "all_fields_agree": {"agree": all_three, "rate": round(all_three / n, 4) if n else None},
        "severity_within_1": {"agree": within1, "rate": round(within1 / n, 4) if n else None},
        "by_decision_source": by_source,
        "disagreements": len(diffs), "disagreeing_reviews": len({d[0] for d in diffs}),
        "files": ["evals/verify/verifier_predictions.jsonl", "evals/verify/disagreements.csv"],
    }
    (out / "verify_report.json").write_text(json.dumps(rep, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return rep


def planted_error_test(conn, label_config, vcfg, root, n=3):
    """SYNTHETIC: copy the first labels, flip the topic of up to `n` reviews where first and verifier agree on topic,
    and re-run the comparison on the copy. Every planted error must be flagged. Never touches the real records."""
    first, verified, _ = load_pairs(conn, label_config, vcfg)
    agree = sorted((r for r in verified if first[r]["topic"] == verified[r]["topic"]),
                   key=lambda r: hashlib.sha256(f"plant:{r}".encode()).hexdigest())[:n]
    copy = {r: dict(v) for r, v in first.items()}
    planted = []
    for r in agree:
        wrong = TOPICS[(TOPICS.index(copy[r]["topic"]) + 1) % len(TOPICS)]
        planted.append({"review_id": r, "field": "topic", "true_first_label": copy[r]["topic"], "planted": wrong})
        copy[r]["topic"] = wrong
    flagged = {(k, f) for k, f, _, _ in compare_rows(copy, verified)}
    detected = [p for p in planted if (p["review_id"], "topic") in flagged]
    out = {"synthetic": True, "note": "test copy only; excluded from business aggregates", "label_config": label_config,
           "verify_config": vcfg, "planted": planted, "detected": len(detected), "missed": len(planted) - len(detected)}
    path = Path(root) / "evals" / "verify" / "planted_errors.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return out
