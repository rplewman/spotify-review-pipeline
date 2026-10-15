"""Stage handlers: what one queue stage does with a leased batch (PLAN §3.3).

The orchestrator is stage-agnostic. For each leased batch it asks the stage for calls until there are none left:

    ctx = stage.begin(conn, items)
    while (call := stage.next_call(conn, ctx)):      # a Call = provider + payload + estimate + parser for ≤ 50 items
        reserve → limiter → log attempt → provider → ONE transaction: call outcome, ledger, stage.absorb(...)
    ok, bad = stage.finish(ctx)                       # then ONE fenced transaction: results, fan-out, queue

`PassthroughStage` (M3a) is a one-call stage. `EnrichStage` (M3b) is the A3 Decision → A4 Router → A5 Escalation →
A6 Extractor → A7 Validator chain. A3 and A5 outputs are saved in the same transaction as their call (agent_outputs),
so a retry, a lost lease or a resume never pays for a finished step twice.
"""
import json
from dataclasses import dataclass, field

from pipeline.agents import cache, decision, escalation, extractor, router, validator
from pipeline.db.store import immediate, now_iso
from pipeline.schema import SCHEMA_VERSION, validate_labels


@dataclass
class Call:
    step: str                 # A3 | A5 (log/meta)
    agent: str                # calls.agent
    provider: object
    items: list               # the leased items this call is about
    payload: dict             # request body parts (sent)
    parse: object             # body -> (ok {item_id: x}, bad {item_id: reason}, notes); raises ValueError if unusable
    est_tokens: int
    est_usd: float
    bucket: str | None = None         # None → the run's bucket
    bucket_cap: float | None = None   # override PLAN §4's cap for this call's bucket
    meta: dict = field(default_factory=dict)  # never sent; for the fake provider and logs


def payload_tokens(payload):
    """Upper-end input-token estimate from the real request size (≈ 3 UTF-8 bytes per token, + overhead)."""
    return 50 + len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) // 3


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

    def describe(self):
        return {"stage": type(self).__name__}

    # ----- single-call API (PassthroughStage) -----
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

    # ----- chain API (what the orchestrator calls) -----
    def begin(self, conn, items):
        return {"items": items, "ok": {}, "bad": {}, "sent": False}

    def next_call(self, conn, ctx):
        if ctx["sent"]:
            return None
        ctx["sent"] = True
        items = ctx["items"]
        return Call("A3", self.agent, self.provider, items, self.payload(items), lambda body: self.parse(body, items),
                    self.estimate_tokens(items), self.estimate_usd(items))

    def absorb(self, conn, ctx, call, ok, bad, request_id, call_error=None):
        """Inside the call's transaction. `call_error` is set when the response as a whole was unusable."""
        ctx["ok"].update({k: {**v, "request_id": request_id} for k, v in ok.items()})
        ctx["bad"].update(bad)

    def on_budget(self, conn, ctx, call, message):
        """The ledger refused `call`. True = handled (carry on without it); False = stop the run."""
        return False

    def finish(self, ctx):
        return ctx["ok"], ctx["bad"]

    def forget(self, conn, text_shas):
        """An item was invalid: drop its saved intermediate outputs so the retry starts clean (inside a transaction)."""

    # ----- where results live (enrichment: the A2 cache + record fan-out; the verifier overrides these) -----
    def cached(self, conn, text_shas):
        """Pre-call check: the subset of `text_shas` already finished under this stage's label_config."""
        return cache.cached(conn, text_shas, self.label_config)

    def reuse(self, conn, text_shas):
        """Finish items whose text was already done (inside the commit transaction)."""
        cache.fan_out(conn, self.label_config, text_shas)

    def save(self, conn, done):
        """Commit validated outputs: done = [(item, result)] (inside the fenced commit transaction)."""
        for item, res in done:
            cache.write_result(conn, item["text_sha256"], self.label_config, res, res.get("request_id"))
        cache.fan_out(conn, self.label_config, [item["text_sha256"] for item, _ in done])

    def quarantine(self, conn, reasons):
        """Texts that failed validation twice ({text_sha: reason})."""
        cache.quarantine_texts(conn, self.label_config, reasons)


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


def make_label_config(decision_model, shape, gate, esc_model, esc_fraction):
    """PLAN §3.7: every setting that changes a label is in the string; any change → new work, no cache reuse."""
    return (f"{decision_model}+shape{shape}+gate{gate:.2f}+esc-{esc_model or 'none'}-{esc_fraction:.2f}"
            f"+{extractor.EXTRACT_VERSION}+{decision.PROMPT_VERSION}+{SCHEMA_VERSION}")


class EnrichStage(Stage):
    """A3 → A4 → A5 → A6 → A7 for one leased batch.

    A3: one Celeris call for the items without a saved decision; per-review invalid → retry once → quarantine.
        A response unusable as a whole is bisected (halves re-sent at once) until the culprit is alone.
    A4: route each decided text (saved): accept / escalate (within the cap) / capped (needs_review).
    A5: one call for this batch's escalated texts without a saved answer (bucket `escalation`; when the ledger refuses
        it, those texts are re-routed `capped_budget`). Missing or invalid answers keep A3's value with needs_review.
    A6 + A7: quote and entities by code, then the schema check; a failure is an invalid output (retry once → quarantine).
    """

    def __init__(self, provider, label_config, escalation_provider=None, shape="A", gate=0.60, esc_fraction=0.15,
                 esc_bucket="escalation", esc_bucket_cap=None):
        super().__init__(provider, label_config)
        self.escalation_provider, self.shape, self.gate = escalation_provider, shape, gate
        self.esc_fraction = esc_fraction if escalation_provider is not None else 0.0
        self.esc_bucket, self.esc_bucket_cap = esc_bucket, esc_bucket_cap
        self._allowance = None

    def describe(self):
        return {"stage": "EnrichStage", "shape": self.shape, "gate": self.gate, "esc_fraction": self.esc_fraction,
                "escalation_model": getattr(self.escalation_provider, "model", None), "esc_bucket": self.esc_bucket}

    # ----- saved intermediate outputs -----
    def _load(self, conn, agent, shas):
        if not shas:
            return {}
        marks = ",".join("?" * len(shas))
        return {sha: json.loads(j) for sha, j in conn.execute(
            f"SELECT text_sha256, output_json FROM agent_outputs WHERE label_config=? AND agent=? "
            f"AND text_sha256 IN ({marks})", [self.label_config, agent, *shas])}

    def _save(self, conn, agent, sha, request_id, output):
        conn.execute("INSERT INTO agent_outputs (text_sha256, label_config, agent, request_id, output_json, created_at) "
                     "VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                     (sha, self.label_config, agent, request_id, json.dumps(output, ensure_ascii=False), now_iso()))

    def forget(self, conn, text_shas):
        for sha in text_shas:
            conn.execute("DELETE FROM agent_outputs WHERE text_sha256=? AND label_config=?", (sha, self.label_config))

    # ----- chain -----
    def begin(self, conn, items):
        shas = [i["text_sha256"] for i in items]
        return {"items": items, "by_id": {i["id"]: i for i in items}, "bad": {}, "notes": [],
                "a3": self._load(conn, "A3", shas), "a4": self._load(conn, "A4", shas), "a5": self._load(conn, "A5", shas),
                "a3_queue": None, "a5_sent": False}

    def next_call(self, conn, ctx):
        live = [i for i in ctx["items"] if i["id"] not in ctx["bad"]]
        if ctx["a3_queue"] is None:
            ctx["a3_queue"] = [[i for i in live if i["text_sha256"] not in ctx["a3"]]]
        while ctx["a3_queue"]:
            group = [i for i in ctx["a3_queue"].pop(0) if i["id"] not in ctx["bad"] and i["text_sha256"] not in ctx["a3"]]
            if group:
                return self._a3_call(group)
        unrouted = {i["text_sha256"]: ctx["a3"][i["text_sha256"]] for i in live
                    if i["text_sha256"] in ctx["a3"] and i["text_sha256"] not in ctx["a4"]}
        if unrouted:
            if self._allowance is None:
                self._allowance = router.escalation_allowance(conn, self.stage, self.label_config, self.esc_fraction)
            ctx["a4"].update(router.route(conn, self.label_config, unrouted, self.gate, self._allowance))
        if not ctx["a5_sent"]:
            ctx["a5_sent"] = True
            todo = [i for i in live if ctx["a4"].get(i["text_sha256"], {}).get("route") == "escalate"
                    and i["text_sha256"] not in ctx["a5"]]
            todo = list({i["text_sha256"]: i for i in todo}.values())
            if todo:
                return self._a5_call(ctx, todo)
        return None

    def _a3_call(self, items):
        payload = decision.build_payload([i["review_text"] for i in items], self.shape)
        tokens = payload_tokens(payload)
        index = {n: i["id"] for n, i in enumerate(items, 1)}

        def parse(body):
            ok, bad, notes = decision.parse(body, len(items))
            return ({index[n]: d for n, d in ok.items()}, {index[n]: r for n, r in bad.items()}, notes)

        return Call("A3", "A3", self.provider, items, payload, parse, tokens,
                    self.provider.cost({"input_tokens": tokens, "output_tokens": 0}) * 1.25,
                    meta={"shape": "celeris", "refs": [f"R{n}" for n in index]})

    def _a5_call(self, ctx, items):
        entries, expected, questions, index = [], {}, [], {}
        for n, i in enumerate(items, 1):
            ref, sha = f"R{n}", i["text_sha256"]
            fields = ctx["a4"][sha]["fields"]
            qs = [(f, router.top2(ctx["a3"][sha], f)) for f in fields]
            entries.append((ref, i["review_text"], qs))
            expected[ref] = fields
            questions += [(ref, f, [v for v, _ in c]) for f, c in qs]
            index[ref] = i["id"]
        payload = escalation.build_payload(entries)
        tokens = payload_tokens(payload)

        def parse(body):
            got, notes = escalation.parse(body, expected)
            return {index[ref]: g for ref, g in got.items()}, {}, notes

        cost = self.escalation_provider.cost({"input_tokens": tokens, "output_tokens": payload["max_tokens"]})
        return Call("A5", "A5", self.escalation_provider, items, payload, parse, tokens, cost * 1.25,
                    bucket=self.esc_bucket, bucket_cap=self.esc_bucket_cap,
                    meta={"shape": "escalation", "questions": questions})

    def absorb(self, conn, ctx, call, ok, bad, request_id, call_error=None):
        if call.step == "A3":
            if call_error and len(call.items) > 1:
                # The whole response is unusable (malformed, or a foreign [R<n>] that breaks attribution). Don't charge
                # every review an invalid attempt for one bad review: bisect, so only the culprit retries → quarantines.
                half = len(call.items) // 2
                ctx["a3_queue"][:0] = [call.items[:half], call.items[half:]]
                ctx["notes"].append(f"A3 call {request_id} unusable ({call_error}); bisected {len(call.items)} reviews")
                return
            for item_id, d in ok.items():
                sha = ctx["by_id"][item_id]["text_sha256"]
                d = {**d, "request_id": request_id}
                self._save(conn, "A3", sha, request_id, d)
                ctx["a3"][sha] = d
            ctx["bad"].update(bad)
            return
        # A5: answers, or a fallback to A3's labels (call unusable / answer missing) — never a lost review
        for item in call.items:
            sha = item["text_sha256"]
            if item["id"] in ok:
                out = {**ok[item["id"]], "request_id": request_id}
            else:
                out = {"fields": {}, "needs_review": True, "missing": ctx["a4"][sha]["fields"],
                       "fallback": bad.get(item["id"], "no answer")}
            self._save(conn, "A5", sha, request_id, out)
            ctx["a5"][sha] = out

    def on_budget(self, conn, ctx, call, message):
        if call.step != "A5":
            return False
        shas = [i["text_sha256"] for i in call.items]
        router.reroute_capped(conn, self.label_config, shas, message)
        for sha in shas:
            ctx["a4"][sha] = {**ctx["a4"][sha], "route": "capped_budget", "reason": message}
        return True

    def finish(self, ctx):
        ok = {}
        for item in ctx["items"]:
            if item["id"] in ctx["bad"]:
                continue
            sha, text = item["text_sha256"], item["review_text"]
            d, r = ctx["a3"].get(sha), ctx["a4"].get(sha)
            if d is None or r is None:
                ctx["bad"][item["id"]] = "no decision"
                continue
            labels = {k: d[k] for k in ("topic", "intent", "severity", "sentiment")}
            needs_review, source, esc = False, "decision", None
            if r["route"] == "escalate":
                esc = ctx["a5"].get(sha)
                if esc is None:
                    ctx["bad"][item["id"]] = "escalation answer missing"
                    continue
                for f, v in esc["fields"].items():
                    labels[f] = int(v) if f == "severity" else v
                needs_review = bool(esc["needs_review"] or esc["missing"])
                source = "escalation" if esc["fields"] else "decision"
            elif r["route"] in ("capped", "capped_budget"):
                needs_review = True
            labels["evidence_quote"] = extractor.evidence_quote(text, labels["topic"])
            labels["entities"] = extractor.entities(text)
            labels["needs_review"] = needs_review
            errors = validator.validate(labels, text, self.label_config)
            if errors:
                ctx["bad"][item["id"]] = "; ".join(errors)
                continue
            ok[item["id"]] = {**labels, "decision_source": source, "request_id": d.get("request_id"),
                              "probs": {"a3": d["probs"], "a3_confidence": d.get("confidence"), "a4": r, "a5": esc}}
        return ok, ctx["bad"]
