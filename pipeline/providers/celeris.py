"""Celeris System One client for A3 Decision (`celeris-1-decision`, PLAN §9.9). One HTTP attempt per call; the
orchestrator owns retries, so every error is classified here and raised once.

- POST {base}/systemone, `Authorization: Bearer $CELERIS_API_KEY`, our request_id sent as `X-Client-Trace-Id`
  (Celeris returns no request ID of its own; the header is echoed back, so ours is the call's ID).
- 408/429/502/503 (and other 5xx) → transient (Retry-After honoured); 400/401/404/413/422 → fatal; 402 → quota (global stop).
  429s and 502s are not charged; a timeout's billing is unknown and settles at the estimate.
- Usage comes from the body (`input_tokens`, `output_tokens`); output is free. No usage → None (never invented).
"""
import os
import time

import httpx

from pipeline.providers.base import Provider, ProviderError, Response, classify_http, parse_json_text

BASE = "https://inference.celeris.ai/celeris-1-decision/v1"


def _retry_after(headers):
    try:
        return float(headers.get("retry-after")) if headers.get("retry-after") else None
    except ValueError:
        return None


class CelerisProvider(Provider):
    name = "celeris"
    rates = {"input_tokens": 0.04, "cached_tokens": 0.04, "output_tokens": 0.0}  # USD / 1M tokens, list (cost/rates.csv)

    def __init__(self, model="celeris-1-decision", base=BASE, api_key=None, transport=None):
        self.model, self.base = model, base
        self.api_key = api_key or os.environ.get("CELERIS_API_KEY")
        if not self.api_key:
            raise RuntimeError("CELERIS_API_KEY is not set (.env)")
        self.transport = transport  # httpx.MockTransport in tests
        self._client = None

    def _http(self):
        if self._client is None:
            self._client = httpx.AsyncClient(transport=self.transport)
        return self._client

    async def call(self, request):
        t0 = time.perf_counter()
        body = {"model": self.model, **request.payload}
        headers = {"Authorization": f"Bearer {self.api_key}", "X-Client-Trace-Id": request.request_id}
        try:
            r = await self._http().post(f"{self.base}/systemone", json=body, headers=headers, timeout=request.timeout)
        except httpx.TimeoutException as e:
            raise ProviderError("transient", 408, f"client timeout: {e}", billed=None)
        except httpx.TransportError as e:
            raise ProviderError("transient", None, f"connection error: {e}", billed=None)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if r.status_code != 200:
            raise classify_http(r.status_code, _retry_after(r.headers), r.text[:300])
        parsed = parse_json_text(r.text)
        u = parsed.get("usage") if isinstance(parsed, dict) else None
        usage = None
        if isinstance(u, dict) and isinstance(u.get("input_tokens"), int):
            usage = {"input_tokens": u["input_tokens"], "output_tokens": int(u.get("output_tokens") or 0),
                     "cached_tokens": 0}
        return Response(parsed, usage, None, latency_ms)


class MagnusProvider(CelerisProvider):
    """`celeris-1-magnus` (M5 escalation comparison only): OpenAI-compatible chat completions, JSON mode via
    `response_format` (no Markdown fence), reasoning on by default (`reasoning_effort` low). Reasoning tokens are
    billed as completion tokens. Usage: prompt_tokens (cached subset in prompt_tokens_details.cached_tokens),
    completion_tokens. Rates: cost/rates.csv."""
    rates = {"input_tokens": 0.20, "cached_tokens": 0.02, "output_tokens": 0.70}

    def __init__(self, model="celeris-1-magnus", base="https://inference.celeris.ai/celeris-1-magnus/v1", **kw):
        super().__init__(model=model, base=base, **kw)

    async def call(self, request):
        t0 = time.perf_counter()
        body = {"model": self.model, **request.payload}
        headers = {"Authorization": f"Bearer {self.api_key}", "X-Client-Trace-Id": request.request_id}
        try:
            r = await self._http().post(f"{self.base}/chat/completions", json=body, headers=headers,
                                        timeout=request.timeout)
        except httpx.TimeoutException as e:
            raise ProviderError("transient", 408, f"client timeout: {e}", billed=None)
        except httpx.TransportError as e:
            raise ProviderError("transient", None, f"connection error: {e}", billed=None)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if r.status_code != 200:
            raise classify_http(r.status_code, _retry_after(r.headers), r.text[:300])
        data = r.json()
        u = data.get("usage") or {}
        usage = None
        if isinstance(u.get("prompt_tokens"), int):
            usage = {"input_tokens": u["prompt_tokens"], "output_tokens": int(u.get("completion_tokens") or 0),
                     "cached_tokens": int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)}
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        body = parse_json_text(text) if choice.get("finish_reason") != "length" else f"truncated (length): {text[:200]}"
        return Response(body, usage, data.get("id"), latency_ms)
