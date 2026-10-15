"""Provider interface (PLAN §3.3). A provider turns one request (≤ 50 reviews) into one HTTP-level attempt.

Errors are classified once, here, so the orchestrator's policy is provider-independent:
    transient → backoff and requeue (408, 429, 5xx, timeouts)   quota → global stop (402)   fatal → stop the run (400/401/404/413/422)
`billed` says whether the failed attempt is charged: False → the ledger reservation is released; None (unknown) →
settled at the estimate, conservatively.
"""
from dataclasses import dataclass, field


@dataclass
class Request:
    request_id: str            # local UUID, sent as X-Client-Trace-Id; replaced by a provider ID if one is returned
    model: str
    review_ids: list
    texts: list
    payload: dict = field(default_factory=dict)  # stage-specific request body parts (built by the stage)
    timeout: float = 120.0


@dataclass
class Response:
    body: object               # parsed JSON (dict) or raw text when it did not parse
    usage: dict | None         # {"input_tokens", "output_tokens", "cached_tokens"} or None when the provider gives none
    provider_request_id: str | None = None
    latency_ms: int = 0


class ProviderError(Exception):
    def __init__(self, kind, status=None, message="", retry_after=None, billed=None):
        assert kind in ("transient", "quota", "fatal")
        super().__init__(f"{status} {kind}: {message}".strip())
        self.kind, self.status, self.retry_after, self.billed = kind, status, retry_after, billed


def classify_http(status, retry_after=None, message=""):
    """Map an HTTP status to a ProviderError (Celeris semantics, PLAN §9.9: 429 and 502 are not charged)."""
    if status == 402:
        return ProviderError("quota", status, message or "insufficient_quota", billed=False)
    if status in (408, 429, 500, 502, 503, 504):
        return ProviderError("transient", status, message, retry_after=retry_after,
                             billed=False if status in (429, 502) else None)
    return ProviderError("fatal", status, message, billed=False)


class Provider:
    name = "base"
    tier = "standard"
    rates = {"input_tokens": 0.0, "cached_tokens": 0.0, "output_tokens": 0.0}  # USD per 1M tokens, list price

    async def call(self, request: Request) -> Response:
        raise NotImplementedError

    def cost(self, usage):
        """List-price USD for a usage dict (cached input tokens priced separately when reported)."""
        cached = usage.get("cached_tokens") or 0
        return ((usage.get("input_tokens", 0) - cached) * self.rates["input_tokens"]
                + cached * self.rates.get("cached_tokens", self.rates["input_tokens"])
                + usage.get("output_tokens", 0) * self.rates["output_tokens"]) / 1e6
