"""Record schema (schema-v1): the contract's completed/quarantined record shapes (PLAN §9.3, SPEC_NOTES §9.8).

`validate_record(rec, review_text)` returns a list of error strings (empty = valid). It mirrors the checker's per-record
rules so bad records are caught before export; cache-reuse and call-log rules need the whole run and live in the exporter.
"""
import math

SCHEMA_VERSION = "schema-v1"
TOPICS = ("access", "usability", "playback", "downloads", "catalog", "billing", "support", "other")
INTENTS = ("cancellation", "complaint", "request", "praise", "unclear")
SEVERITIES = (1, 2, 3, 4, 5)
EMPTY_TEXT_REASON = "empty_review_text"

LABEL_FIELDS = ("topic", "intent", "sentiment", "severity", "entities", "evidence_quote", "needs_review")
COMPLETED_KEYS = {"review_id", "source_sha256", "status", *LABEL_FIELDS, "label_config"}
QUARANTINED_KEYS = {"review_id", "source_sha256", "status", "reason"}


def validate_labels(rec, review_text):
    """Errors in the seven label fields (shared by records, hand labels and examples)."""
    errors = []
    if rec.get("topic") not in TOPICS:
        errors.append(f"topic {rec.get('topic')!r} not in {TOPICS}")
    if rec.get("intent") not in INTENTS:
        errors.append(f"intent {rec.get('intent')!r} not in {INTENTS}")
    sev = rec.get("severity")
    if type(sev) is not int or sev not in SEVERITIES:
        errors.append(f"severity {sev!r} must be an int 1-5")
    s = rec.get("sentiment")
    if type(s) not in (int, float) or not math.isfinite(s) or not -1 <= s <= 1:
        errors.append(f"sentiment {s!r} must be a number in [-1, 1]")
    if type(rec.get("needs_review")) is not bool:
        errors.append(f"needs_review {rec.get('needs_review')!r} must be a bool")
    ents = rec.get("entities")
    if not isinstance(ents, list) or not all(isinstance(e, str) and e.strip() for e in ents):
        errors.append(f"entities {ents!r} must be a list of nonempty strings")
    q = rec.get("evidence_quote")
    if not isinstance(q, str) or not q:
        errors.append("evidence_quote must be a nonempty string")
    elif q not in review_text:
        errors.append("evidence_quote is not an exact substring of the review text")
    return errors


def validate_record(rec, review_text):
    """Errors for one export record against its source review text."""
    if not isinstance(rec, dict):
        return ["record must be an object"]
    errors = []
    for key in ("review_id", "source_sha256"):
        if not isinstance(rec.get(key), str) or not rec.get(key):
            errors.append(f"{key} must be a nonempty string")
    status = rec.get("status")
    if status == "quarantined":
        reason = rec.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append("quarantined record needs a nonempty reason")
        elif not review_text.strip() and reason != EMPTY_TEXT_REASON:
            errors.append(f"empty text must be quarantined with reason {EMPTY_TEXT_REASON!r}")
        extra = set(rec) - QUARANTINED_KEYS
    elif status == "completed":
        errors += validate_labels(rec, review_text)
        if not isinstance(rec.get("label_config"), str) or not rec["label_config"]:
            errors.append("label_config must be a nonempty string")
        if "cache_source_id" in rec and (not isinstance(rec["cache_source_id"], str) or not rec["cache_source_id"]
                                         or rec["cache_source_id"] == rec.get("review_id")):
            errors.append("cache_source_id must name a different review")
        extra = set(rec) - COMPLETED_KEYS - {"cache_source_id"}
    else:
        return errors + [f"status {status!r} must be 'completed' or 'quarantined'"]
    if extra:
        errors.append(f"unexpected keys {sorted(extra)}")
    return errors
