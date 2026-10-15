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
    # Haiku 5.5, prompts ≤ 100K tokens, USD / 1M tokens (cost/rates.csv)
    rates = {"input_tokens": 0.10, "cached_tokens": 0.01, "cache_write_tokens": 0.125, "output_tokens": 0.50}

    def __init__(self, model="claude-haiku-5-5", api_key=None, client=None):
        self.model = model
        if client is None:
            key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY is not set (.env)")
            client = anthropic.AsyncAnthropic(api_key=key, max_retries=0)
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
        latency_ms = int((time.perf_counter() - t0) * 1000)
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
        return Response(body, usage, getattr(msg, "_request_id", None), latency_ms)
