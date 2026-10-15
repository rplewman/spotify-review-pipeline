"""Anthropic Messages client for A5 Escalation (Claude Haiku 5.5, PLAN §9.10). Real time, not the Batch API.

The stage builds the request body (system prompt with a cache breakpoint, one user message, `output_config` with
effort `low` and a JSON schema); this client sends it with the official SDK, `max_retries=0` (the orchestrator owns
retries), and classifies errors the same way as Celeris: 408/429/5xx/529 transient, 402 quota, other 4xx fatal.

Usage: `input_tokens` here = uncached + cache-read + cache-write input tokens (everything the model read);
`cached_tokens` = cache reads; `cache_write_tokens` = cache writes, priced separately in `cost`.
A refusal or a truncated answer comes back as raw text and is reported as malformed by the stage.
"""
import os
import time

import anthropic

from pipeline.providers.base import Provider, ProviderError, Response, classify_http, parse_json_text


class ClaudeProvider(Provider):
    name = "anthropic"
    # USD / 1M tokens, prompts ≤ 100K tokens (cost/rates.csv). Sonnet 5.5 only for the M6b topic-model test.
    RATES = {"claude-haiku-5-5": {"input_tokens": 0.10, "cached_tokens": 0.01, "cache_write_tokens": 0.125,
                                  "output_tokens": 0.50},
             "claude-sonnet-5-5": {"input_tokens": 2.00, "cached_tokens": 0.20, "cache_write_tokens": 2.50,
                                   "output_tokens": 10.00}}
    rates = RATES["claude-haiku-5-5"]

    def __init__(self, model="claude-haiku-5-5", api_key=None, client=None):
        if model not in self.RATES:
            raise ValueError(f"no list price for {model!r} (add it to ClaudeProvider.RATES and cost/rates.csv)")
        self.model, self.rates = model, self.RATES[model]
        if client is None:
            key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY is not set (.env)")
            # a key not scoped to a workspace needs the workspace ID on every request (ANTHROPIC_WORKSPACE_ID, .env)
            ws = os.environ.get("ANTHROPIC_WORKSPACE_ID")
            client = anthropic.AsyncAnthropic(api_key=key, max_retries=0,
                                              default_headers={"anthropic-workspace-id": ws} if ws else None)
        self.client = client

    def cost(self, usage):
        cached = usage.get("cached_tokens") or 0
        write = usage.get("cache_write_tokens") or 0
        plain = usage.get("input_tokens", 0) - cached - write
        return (plain * self.rates["input_tokens"] + cached * self.rates["cached_tokens"]
                + write * self.rates["cache_write_tokens"] + usage.get("output_tokens", 0) * self.rates["output_tokens"]) / 1e6

    async def call(self, request):
        t0 = time.perf_counter()
        try:
            msg = await self.client.messages.create(model=self.model, **request.payload, timeout=request.timeout,
                                                    extra_headers={"X-Client-Trace-Id": request.request_id})
        except anthropic.APITimeoutError as e:
            raise ProviderError("transient", 408, f"client timeout: {e}", billed=None)
        except anthropic.APIConnectionError as e:
            raise ProviderError("transient", None, f"connection error: {e}", billed=None)
        except anthropic.APIStatusError as e:
            ra = e.response.headers.get("retry-after") if e.response is not None else None
            try:
                ra = float(ra) if ra else None
            except ValueError:
                ra = None
            err = classify_http(e.status_code, ra, str(e.message)[:300])
            if e.status_code == 429:
                err.billed = False
            raise err
        return to_response(msg, int((time.perf_counter() - t0) * 1000))


def to_response(msg, latency_ms=0):
    """A Messages API message (standard or a batch result) → Response: parsed JSON body (or raw text) + usage."""
    u = msg.usage
    cached = getattr(u, "cache_read_input_tokens", None) or 0
    write = getattr(u, "cache_creation_input_tokens", None) or 0
    usage = {"input_tokens": u.input_tokens + cached + write, "output_tokens": u.output_tokens,
             "cached_tokens": cached, "cache_write_tokens": write}
    if msg.stop_reason == "refusal":
        body = f"refusal: {getattr(msg, 'stop_details', None)}"
    else:
        text = "".join(b.text for b in msg.content if b.type == "text")
        body = parse_json_text(text) if msg.stop_reason != "max_tokens" else f"truncated (max_tokens): {text[:200]}"
    return Response(body, usage, getattr(msg, "_request_id", None) or getattr(msg, "id", None), latency_ms)


class ClaudeBatchProvider(ClaudeProvider):
    """Message Batches API (PLAN §3.6, §9.10): 50% off input and output; results arrive in any order, keyed by custom_id.

    submit(requests) → provider job ID · status(job_id) → ("in_progress" | "canceling" | "ended", request_counts)
    · results(job_id) → [(custom_id, kind, Response | None, error)] with kind ∈ succeeded | errored | canceled | expired.
    Errored, canceled and expired requests are not billed."""
    tier = "batch"
    RATES = {m: {k: v / 2 for k, v in r.items()} for m, r in ClaudeProvider.RATES.items()}  # 50% off
    rates = RATES["claude-haiku-5-5"]

    async def call(self, request):
        raise RuntimeError("ClaudeBatchProvider has no real-time call; use ClaudeProvider (--standard)")

    async def _guard(self, coro):
        try:
            return await coro
        except anthropic.APIConnectionError as e:
            raise ProviderError("transient", None, f"connection error: {e}", billed=False)
        except anthropic.APIStatusError as e:
            raise classify_http(e.status_code, None, str(e.message)[:300])

    async def submit(self, requests):
        """requests = [(custom_id, params incl. model)] → provider job ID."""
        job = await self._guard(self.client.messages.batches.create(
            requests=[{"custom_id": cid, "params": params} for cid, params in requests]))
        return job.id

    async def status(self, job_id):
        job = await self._guard(self.client.messages.batches.retrieve(job_id))
        counts = job.request_counts
        return job.processing_status, {k: getattr(counts, k, 0) for k in
                                       ("processing", "succeeded", "errored", "canceled", "expired")}

    async def results(self, job_id):
        out = []
        async for entry in await self._guard(self.client.messages.batches.results(job_id)):
            r = entry.result
            if r.type == "succeeded":
                out.append((entry.custom_id, "succeeded", to_response(r.message), None))
            else:
                err = getattr(r, "error", None)
                out.append((entry.custom_id, r.type, None, str(getattr(err, "error", err) or r.type)[:300]))
        return out
