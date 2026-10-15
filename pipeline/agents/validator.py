"""A7 Validator (PLAN §3.3, §9.8), code only: the last check before a result is committed.

The assembled labels must pass the checker's per-record rules (schema-v1: allowed topic/intent, strict int severity
1–5, sentiment in [−1, 1], strict bool needs_review, entities a list of nonempty strings, evidence_quote a nonempty
exact substring) and carry a nonempty label_config. A failure is an invalid output: retried once, then quarantined.
"""
from pipeline.schema import validate_labels


def validate(labels, review_text, label_config):
    errors = validate_labels(labels, review_text)
    if not isinstance(label_config, str) or not label_config:
        errors.append("label_config must be a nonempty string")
    return errors
