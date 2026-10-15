"""FakeProvider: a scripted, offline stand-in for a model API (PLAN §5 M3a). No network, no cost.

Behaviours (one per call, from `script` in call order, then `default`; or per review from `per_review`):
    valid       well-formed labels for every review (deterministic from the text)
    low_conf    valid labels whose top topic probability is 0.40
    malformed   call-level: the body is not JSON. Per review: that review is missing from the output
    timeout     408 (transient, billing unknown)          429   rate limited, Retry-After 0.2 s (not charged)
    402         out of credit (global stop)               fatal 400 bad request (stop the run)
    injected    per review: the output obeys an instruction in the text: an invalid label plus an extra, unknown review ID
    no_usage    valid labels, but no usage block
    hang        never answers (for kill/resume tests)
"""
import asyncio
import hashlib
import time

from pipeline.providers.base import Provider, ProviderError, Response, classify_http
from pipeline.schema import INTENTS, TOPICS

BEHAVIOURS = {"valid", "low_conf", "malformed", "timeout", "429", "402", "fatal", "injected", "no_usage", "hang"}


def fake_labels(text, top=0.9):
    h = int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16)
    topic = TOPICS[h % len(TOPICS)]
    rest = (1 - top) / (len(TOPICS) - 1)
    return {"topic": topic, "intent": INTENTS[(h >> 8) % len(INTENTS)], "sentiment": round(((h >> 16) % 201) / 100 - 1, 2),
            "severity": 1 + (h >> 24) % 5, "entities": [], "evidence_quote": text, "needs_review": top < 0.6,
            "probs": {"topic": {t: (top if t == topic else round(rest, 4)) for t in TOPICS}}}


class FakeProvider(Provider):
    name = "fake"
    rates = {"input_tokens": 1.00, "cached_tokens": 1.00, "output_tokens": 0.50}  # made up, to exercise the ledger

    def __init__(self, script=(), default="valid", per_review=None, latency=0.0, model="fake-decision"):
        unknown = ({*script, default} | {b for v in (per_review or {}).values() for b in v}) - BEHAVIOURS
        if unknown:
            raise ValueError(f"unknown fake behaviours {sorted(unknown)}")
        self.script, self.default, self.latency, self.model = list(script), default, latency, model
        self.per_review = {k: list(v) for k, v in (per_review or {}).items()}  # review_id -> behaviours per attempt
        self.calls = []  # (request_id, review_ids, behaviour) for assertions

    def _next(self):
        return self.script.pop(0) if self.script else self.default

    async def call(self, request):
        behaviour = self._next()
        self.calls.append((request.request_id, list(request.review_ids), behaviour))
        t0 = time.perf_counter()
        if behaviour == "hang":
            await asyncio.sleep(3600)
        if self.latency:
            await asyncio.sleep(self.latency)
        if behaviour in ("timeout", "429", "402", "fatal"):
            status = {"timeout": 408, "429": 429, "402": 402, "fatal": 400}[behaviour]
            raise classify_http(status, retry_after=0.2 if status == 429 else None, message=f"fake {behaviour}")
        usage = {"input_tokens": 200 + sum(len(t) // 4 + 10 for t in request.texts),
                 "output_tokens": 12 * len(request.texts), "cached_tokens": 0}
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if behaviour == "malformed":
            return Response('{"results": {"R1": ', usage, None, latency_ms)
        results = {}
        for rid, text in zip(request.review_ids, request.texts):
            per = self.per_review.get(rid)
            b = per.pop(0) if per else behaviour
            if b == "malformed":
                continue
            labels = fake_labels(text, top=0.4 if b == "low_conf" else 0.9)
            if b == "injected":
                labels["topic"] = "hacked"
                results["INJECTED-UNKNOWN-ID"] = fake_labels("x")
            results[rid] = labels
        return Response({"results": results}, None if behaviour == "no_usage" else usage, None, latency_ms)


def parse_script(text):
    """CLI form: 'valid,valid,hang' -> ['valid', 'valid', 'hang']."""
    return [s.strip() for s in text.split(",") if s.strip()] if text else []
