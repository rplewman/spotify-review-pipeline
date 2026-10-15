"""A5 Escalation (PLAN §3.3, §3.5): a reasoning model (Claude Haiku 5.5, effort low) decides only the fields A4 found
uncertain. Real time, not the Batch API.

One request carries the escalated reviews of one leased batch (≤ 50). Each question is a single field of a single
review with A3's top-2 candidates; the answer is strict JSON under a schema (`output_config.format`). Values must be
in the contract's allowed set (the model cannot change the taxonomy); quotes and entities never come from here (A6).

A missing or invalid answer does not cost the review: it keeps A3's label for that field with needs_review=true.
"""
from pathlib import Path

from pipeline.agents.decision import neutralize
from pipeline.schema import INTENTS, TOPICS

SYSTEM = (Path(__file__).resolve().parents[2] / "prompts" / "escalation_system_v1.md").read_text(encoding="utf-8").strip()
ALLOWED = {"topic": list(TOPICS), "intent": list(INTENTS), "severity": ["1", "2", "3", "4", "5"]}
SCHEMA = {
    "type": "object",
    "properties": {"answers": {"type": "array", "items": {
        "type": "object",
        "properties": {"ref": {"type": "string"}, "field": {"type": "string", "enum": list(ALLOWED)},
                       "value": {"type": "string", "enum": sorted({v for vs in ALLOWED.values() for v in vs})},
                       "needs_review": {"type": "boolean"}},
        "required": ["ref", "field", "value", "needs_review"], "additionalProperties": False}}},
    "required": ["answers"], "additionalProperties": False,
}
EFFORT = "low"


def build_payload(entries):
    """entries = [(ref, text, [(field, [(value, p), (value, p)]), …]), …] → Messages API body minus `model`."""
    blocks, n_questions = [], 0
    for ref, text, questions in entries:
        qs = []
        for field, cands in questions:
            shown = ", ".join(f"{v} ({p:.2f})" for v, p in cands)
            qs.append(f"- {field}: candidates {shown}")
            n_questions += 1
        blocks.append(f"[{ref}] {neutralize(text)}\nQuestions:\n" + "\n".join(qs))
    user = "Decide each question. Answer with one entry per question.\n\n" + "\n\n".join(blocks)
    return {
        "max_tokens": 2000 + 120 * n_questions,  # low-effort thinking + ~40 tokens per answer, generous
        "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
        "output_config": {"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
    }


def parse(body, expected):
    """expected = {ref: [fields]} → ({ref: {"fields": {field: value}, "needs_review": bool, "missing": [...]}}, notes).
    Raises ValueError when the body is not the expected JSON object."""
    if not isinstance(body, dict) or not isinstance(body.get("answers"), list):
        raise ValueError("unparseable escalation output")
    got, notes = {}, []
    for a in body["answers"]:
        if not isinstance(a, dict) or a.get("ref") not in expected or a.get("field") not in expected[a["ref"]]:
            notes.append(f"ignored answer {str(a)[:80]!r}")
            continue
        if a.get("value") not in ALLOWED[a["field"]]:
            notes.append(f"invalid value {a.get('value')!r} for {a['ref']}.{a['field']}")
            continue
        got.setdefault(a["ref"], {"fields": {}, "needs_review": False})
        got[a["ref"]]["fields"].setdefault(a["field"], a["value"])  # first answer wins
        got[a["ref"]]["needs_review"] |= a.get("needs_review") is True
    out = {}
    for ref, fields in expected.items():
        g = got.get(ref, {"fields": {}, "needs_review": False})
        g["missing"] = [f for f in fields if f not in g["fields"]]
        out[ref] = g
    return out, notes
