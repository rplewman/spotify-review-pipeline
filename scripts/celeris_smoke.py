"""M0 smoke test: one celeris-1-decision request on a SYNTHETIC review to capture the exact System One response JSON.

Paid (well under $0.0001). Requires --paid and --max-spend, per the paid-call rule (PLAN §4).
Usage: python scripts/celeris_smoke.py --paid --max-spend 0.001
"""
import argparse
import json
import os
import sys
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv

BASE = "https://inference.celeris.ai/celeris-1-decision/v1"
INPUT_USD_PER_TOKEN = 0.04 / 1_000_000
OUT = Path("evals/smoke/celeris_smoke_response.json")

BODY = {
    "model": "celeris-1-decision",
    "state": "[R1] The app keeps logging me out every time I open it and I can't get back in. I pay for premium!",
    "questions": {
        "R1_topic": {"type": "choice", "instructions": "Topic of [R1]",
                     "criteria": {t: None for t in ("access", "usability", "playback", "downloads",
                                                     "catalog", "billing", "support", "other")}},
        "R1_intent": {"type": "choice", "instructions": "Intent of [R1]",
                      "criteria": {i: None for i in ("cancellation", "complaint", "request", "praise", "unclear")}},
        "R1_severity": {"type": "score", "instructions": "Severity of [R1]",
                        "criteria": ["no problem", "minor", "degraded", "blocked core task", "serious harm"]},
        "R1_negative": {"type": "noul", "instructions": "[R1] is negative in tone."},
    },
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--paid", action="store_true")
    ap.add_argument("--max-spend", type=float)
    args = ap.parse_args()
    if not args.paid or args.max_spend is None:
        sys.exit("Paid call: pass --paid and --max-spend.")
    est = len(json.dumps(BODY)) * INPUT_USD_PER_TOKEN  # chars >= tokens: conservative
    if est > args.max_spend:
        sys.exit(f"Estimated ${est:.6f} exceeds --max-spend ${args.max_spend}")
    load_dotenv(".env")
    trace = str(uuid.uuid4())
    r = httpx.post(f"{BASE}/systemone", json=BODY, timeout=60,
                   headers={"Authorization": f"Bearer {os.environ['CELERIS_API_KEY']}", "X-Client-Trace-Id": trace})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    keep = {k: v for k, v in r.headers.items() if k.lower() in
            ("server-timing", "x-client-trace-id", "x-request-id", "request-id", "retry-after", "content-type")}
    try:
        payload = r.json()
    except ValueError:
        payload = r.text
    OUT.write_text(json.dumps({"trace_id": trace, "status": r.status_code, "headers": keep,
                               "request": BODY, "response": payload}, indent=2), encoding="utf-8")
    print(r.status_code, json.dumps(payload, indent=2)[:3000])
    usage = payload.get("usage", {}) if isinstance(payload, dict) else {}
    if "input_tokens" in usage:
        print(f"cost ${usage['input_tokens'] * INPUT_USD_PER_TOKEN:.8f}")


if __name__ == "__main__":
    main()
