"""A4 Router (PLAN §3.3, §3.5), code only: accept A3's labels or send the uncertain fields to A5.

- Gate: a field among topic / intent / severity whose top probability is < `gate` (default 0.60, part of label_config)
  is uncertain. Sentiment is not gated (it is a probability-weighted value, not a choice).
- Caps: escalations under one label_config are limited to floor(esc_fraction × the stage's work items). The count and
  the new routes are written in one write transaction, so concurrent workers can never overshoot it. Over the cap →
  `capped`: A3's labels with needs_review=true. The $ sub-cap is the ledger's `escalation` bucket: when it refuses
  the A5 reservation, the stage re-routes those reviews to `capped_budget` (same outcome).
- Routes are saved (agent_outputs A4), so a retry or resume replays the same decision instead of re-counting.
"""
import json
import math

from pipeline.db.store import immediate, now_iso

GATED = ("topic", "intent", "severity")


def top_prob(decision, field):
    return max(decision["probs"][field].values())


def uncertain_fields(decision, gate):
    return [f for f in GATED if top_prob(decision, f) < gate]


def top2(decision, field):
    """A3's two most probable values for a field, [(value, p), …] (severity values are "1".."5")."""
    return sorted(decision["probs"][field].items(), key=lambda kv: (-kv[1], kv[0]))[:2]


def escalation_allowance(conn, stage, label_config, esc_fraction):
    total = conn.execute("SELECT COUNT(*) FROM work_items WHERE stage=? AND label_config=?",
                         (stage, label_config)).fetchone()[0]
    return math.floor(esc_fraction * total + 1e-9)


def route(conn, label_config, decisions, gate, allowance):
    """decisions = {text_sha: decision} for texts without a saved route. Returns {text_sha: route dict} (saved)."""
    out = {}
    with immediate(conn):
        used = conn.execute("SELECT COUNT(*) FROM agent_outputs WHERE label_config=? AND agent='A4' "
                            "AND json_extract(output_json, '$.route')='escalate'", (label_config,)).fetchone()[0]
        for sha, d in decisions.items():
            fields = uncertain_fields(d, gate)
            if not fields:
                r = {"route": "accept", "fields": []}
            elif used < allowance:
                r = {"route": "escalate", "fields": fields}
                used += 1
            else:
                r = {"route": "capped", "fields": fields}
            r["gate"] = gate
            conn.execute("INSERT INTO agent_outputs (text_sha256, label_config, agent, request_id, output_json, created_at) "
                         "VALUES (?,?,'A4',NULL,?,?) ON CONFLICT DO NOTHING", (sha, label_config, json.dumps(r), now_iso()))
            out[sha] = json.loads(conn.execute("SELECT output_json FROM agent_outputs WHERE text_sha256=? AND "
                                               "label_config=? AND agent='A4'", (sha, label_config)).fetchone()[0])
    return out


def reroute_capped(conn, label_config, text_shas, reason):
    """The escalation $ sub-cap refused these reviews: keep A3's labels, needs_review=true (frees their cap slots)."""
    with immediate(conn):
        for sha in text_shas:
            conn.execute("UPDATE agent_outputs SET output_json=json_set(output_json, '$.route', 'capped_budget', "
                         "'$.reason', ?) WHERE text_sha256=? AND label_config=? AND agent='A4'", (reason, sha, label_config))
