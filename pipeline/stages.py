"""Stage handlers: what one queue stage sends to a provider and how it reads the answer (PLAN §3.3).

The orchestrator is stage-agnostic. A stage builds the request for a leased batch, estimates its cost for the ledger,
and parses the response into per-item results or per-item invalid reasons. M3b replaces `PassthroughStage` with the
A3 Decision → A4 Router → A5 Escalation → A6 Extractor → A7 Validator chain; the queue, cache, ledger and call log
around it stay the same.
"""
from pipeline.schema import validate_labels


class Stage:
    stage = "enrich"     # work_items.stage
    role = "enrich"      # calls.jsonl role
    agent = "A3"
    bucket = "pilots"    # ledger bucket (PLAN §4)

    def __init__(self, provider, label_config):
        self.provider, self.label_config = provider, label_config

    @property
    def model(self):
        return self.provider.model

    def payload(self, items):
        return {}

    def estimate_tokens(self, items):
        """Upper-end input-token estimate (≈ 4 chars per token, generous overhead)."""
        return 400 + sum(len(i["review_text"]) // 3 + 20 for i in items)

    def estimate_usd(self, items):
        """Reservation size: estimated input + an output allowance, at list price, with a 25% margin."""
        usage = {"input_tokens": self.estimate_tokens(items), "output_tokens": 40 * len(items), "cached_tokens": 0}
        return self.provider.cost(usage) * 1.25

    def parse(self, body, items):
        """Returns ({item_id: result}, {item_id: invalid_reason}, notes). Raise ValueError if the body is unusable."""
        raise NotImplementedError


class PassthroughStage(Stage):
    """M3a stage: the provider returns finished labels {"results": {review_id: {label fields, probs}}}; we only validate.

    Defences that carry over to M3b: the body must be a JSON object; only IDs we sent are accepted (an extra ID, e.g. from
    a prompt injection in a review, is ignored and noted); every label is checked against the contract schema."""

    def parse(self, body, items):
        if not isinstance(body, dict) or not isinstance(body.get("results"), dict):
            raise ValueError("unparseable output")
        out = body["results"]
        sent = {i["review_id"] for i in items}
        notes = [f"ignored unknown id {rid!r}" for rid in out if rid not in sent]
        ok, bad = {}, {}
        for item in items:
            rec = out.get(item["review_id"])
            if not isinstance(rec, dict):
                bad[item["id"]] = "missing from output"
                continue
            errors = validate_labels(rec, item["review_text"])
            if errors:
                bad[item["id"]] = "; ".join(errors)
            else:
                ok[item["id"]] = {**{k: rec[k] for k in ("topic", "intent", "sentiment", "severity", "entities",
                                                         "evidence_quote", "needs_review")},
                                  "probs": rec.get("probs"), "decision_source": "decision"}
        return ok, bad, notes
