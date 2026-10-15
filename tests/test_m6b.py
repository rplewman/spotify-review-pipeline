"""M6b (offline): sentiment = most probable level, Haiku on severity for every review (gate 1.00), the re-frozen
label_config, the frequency add-on queue path (and its exclusion from the business ranking), verification at 20%,
PLAN §4 v4.2 bucket caps, per-model Claude list prices, and the topic-test adoption rule."""
import csv
import importlib.util
import json
from pathlib import Path

import pytest

from pipeline import cli
from pipeline.agents import decision, intake, router, verifier
from pipeline.db import ledger
from pipeline.db.store import connect
from pipeline.providers.claude import ClaudeBatchProvider, ClaudeProvider
from pipeline.stages import make_label_config

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ["review_id", "review_text", "review_rating", "review_likes", "app_version", "review_timestamp"]
M6B_FROZEN = ("celeris-1-decision+shapeA+gate1.00+esc-claude-haiku-5-5-1.00+escfields-severity"
              "+extract-code-v1+prompt-v1+schema-v1+sentiment-mode-v1")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def probs(*p):
    return {str(i): v for i, v in enumerate(p)}


# ---------- sentiment ----------
def test_sentiment_mode_is_the_most_probable_level_ties_toward_neutral():
    assert decision.sentiment_value(None, probs(0.6, 0.3, 0.1, 0, 0), "mode") == -1.0
    assert decision.sentiment_value(None, probs(0.1, 0.2, 0.1, 0.25, 0.35), "mode") == 1.0
    assert decision.sentiment_value(None, probs(0.4, 0.1, 0.4, 0.1, 0), "mode") == 0.0     # tie → nearer 0
    assert decision.sentiment_value(None, probs(0.1, 0.4, 0, 0.4, 0.1), "mode") == -0.5    # equal distance → lower
    # the M6 value is unchanged: Celeris's probability-weighted score
    assert decision.sentiment_value(0.9, probs(0.6, 0.3, 0.1, 0, 0), "mean") == pytest.approx(-0.55)
    with pytest.raises(ValueError):
        decision.sentiment_value(1.0, probs(0, 0, 1, 0, 0), "median")


def test_parse_applies_the_sentiment_mode():
    ans = {"R1_topic": {"type": "choice", "choice": "playback", "probabilities": {t: (1.0 if t == "playback" else 0.0)
                                                                                 for t in decision.TOPICS}},
           "R1_intent": {"type": "choice", "choice": "complaint",
                         "probabilities": {i: (1.0 if i == "complaint" else 0.0) for i in decision.INTENTS}},
           "R1_severity": {"type": "score", "score": 2.0, "probabilities": probs(0, 0.1, 0.8, 0.1, 0)},
           "R1_sentiment": {"type": "score", "score": 1.0, "probabilities": probs(0.45, 0.2, 0.1, 0.1, 0.15)}}
    assert decision.parse({"answers": ans}, 1)[0][1]["sentiment"] == -0.5          # mean (score 1.0)
    assert decision.parse({"answers": ans}, 1, "mode")[0][1]["sentiment"] == -1.0  # mode (level 0)


# ---------- label_config + gate ----------
def test_m6b_frozen_label_config_and_older_strings_unchanged():
    old = make_label_config("celeris-1-decision", "A", 0.7, "claude-haiku-5-5", 0.40, ("severity",))
    assert old == ("celeris-1-decision+shapeA+gate0.70+esc-claude-haiku-5-5-0.40+escfields-severity"
                   "+extract-code-v1+prompt-v1+schema-v1")
    args = type("A", (), {"provider": "celeris", "escalation": "anthropic", **cli.STAGE_DEFAULTS})()
    assert cli.default_label_config(args) == M6B_FROZEN


def test_gate_one_escalates_every_gated_field_even_when_certain():
    d = {"probs": {"topic": {"playback": 1.0}, "intent": {"complaint": 1.0}, "severity": {"3": 1.0, "2": 0.0}}}
    assert router.uncertain_fields(d, 1.0, ("severity",)) == ["severity"]
    assert router.uncertain_fields(d, 0.99, ("severity",)) == []


def test_resume_of_an_old_run_keeps_mean_sentiment():
    args = type("A", (), {"escalation": "anthropic", "provider": "celeris"})()
    cli.resolve_settings(args, {"gate": 0.7, "esc_fraction": 0.4, "esc_fields": "severity"})  # spec saved before M6b
    assert args.sentiment == "mean" and args.gate == 0.7
    fresh = type("A", (), {"escalation": "anthropic", "provider": "celeris"})()
    cli.resolve_settings(fresh)
    assert fresh.sentiment == "mode" and fresh.gate == 1.0 and fresh.esc_fraction == 1.0


# ---------- small corpus with out-of-scope duplicates ----------
def build(tmp_path, n=200, scope=40):
    rows = []
    for i in range(n):
        if i % 4 == 0:
            text = f"Popular complaint {i % 7}: the app crashes all the time"  # 7 texts, ~7 rows each
        elif i % 4 == 1:
            text = f"Shared complaint {i % 11}: ads are annoying"              # 11 texts, ~4-5 rows each
        else:
            text = f"Unique review {i}: the app keeps crashing"
        rows.append([f"id{i:04d}", text, "2", "0", "8.1", "2022-07-01 10:00:00"])
    path = tmp_path / "c.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(FIELDS)
        w.writerows(rows)
    db = tmp_path / "t.db"
    conn = intake.connect(db)
    intake.apply_schema(conn)
    intake.load_source(conn, path)
    intake.assign_scope(conn, size=scope)
    intake.build_texts(conn)
    intake.quarantine_empty(conn)
    conn.close()
    return db


def test_build_addon_takes_the_most_frequent_out_of_scope_texts(tmp_path):
    db = build(tmp_path)
    conn = intake.connect(db)
    in_scope = {r[0] for r in conn.execute("SELECT text_sha256 FROM texts")}
    counts = {}
    for sha, rank in conn.execute("SELECT text_sha256, scope_rank FROM source_reviews WHERE scope='out_of_scope'"):
        if sha not in in_scope:
            c, first = counts.get(sha, (0, rank))
            counts[sha] = (c + 1, min(first, rank))
    expected = [sha for sha, _ in sorted(counts.items(), key=lambda kv: (-kv[1][0], kv[1][1]))[:5]]
    got = intake.build_addon(conn, 5)
    assert got["built"] and got["texts"] == 5 and got["rows"] == sum(counts[s][0] for s in expected)
    rows = conn.execute("SELECT text_sha256, scope_rank, rows_in_scope FROM texts WHERE scope_rank > ? ORDER BY scope_rank",
                        (intake.SCOPE_SIZE,)).fetchall()
    assert [r[0] for r in rows] == expected                                       # most frequent first
    assert [r[1] for r in rows] == [intake.SCOPE_SIZE + k for k in range(1, 6)]
    assert {r[0] for r in conn.execute("SELECT DISTINCT text_sha256 FROM source_reviews WHERE scope='frequency_addon'")} \
        == set(expected)
    assert intake.build_addon(conn, 5)["built"] is False                          # idempotent
    with pytest.raises(intake.IntakeError):
        intake.build_addon(conn, 6)
    conn.close()


def test_addon_scope_enriches_and_is_grouped_with_a_random_100k_comparison(tmp_path):
    db = build(tmp_path)
    base = ["--db", str(db), "--root", str(tmp_path), "--provider", "fake", "--label-config", "cfg-a",
            "--batch-size", "10"]
    assert cli.main(["enrich", *base]) == 0                                       # default scope: the "100k" only
    conn = connect(db)
    assert conn.execute("SELECT COUNT(*) FROM work_items WHERE status='done'").fetchone()[0] == \
        conn.execute("SELECT COUNT(*) FROM texts").fetchone()[0]
    conn.close()
    assert cli.main(["enrich", *base, "--scope", "addon", "--addon-size", "12"]) == 0
    conn = connect(db)
    addon = {r[0] for r in conn.execute("SELECT review_id FROM source_reviews WHERE scope='frequency_addon'")}
    assert len(addon) > 12
    done = {r[0] for r in conn.execute("SELECT review_id FROM records WHERE status='completed' AND label_config='cfg-a'")}
    assert addon <= done
    assert conn.execute("SELECT COUNT(*) FROM work_items WHERE status!='done'").fetchone()[0] == 0
    # a later default-scope run never claims add-on work (max_rank 100,000)
    run = json.loads(conn.execute("SELECT config_json FROM runs ORDER BY started_at LIMIT 1").fetchone()[0])
    assert run["max_rank"] == intake.SCOPE_SIZE
    conn.close()
    down = ["--db", str(db), "--root", str(tmp_path), "--label-config", "cfg-a"]
    assert cli.main(["group", *down, "--provider", "fake"]) == 0
    members = {r["review_id"] for r in csv.DictReader((tmp_path / "outputs/membership.csv").open(encoding="utf-8"))}
    sample = set((tmp_path / "outputs/comparison_random_100k.txt").read_text(encoding="utf-8").split())
    assert members & addon and sample < members and not sample & addon      # the add-on is grouped (contract)
    assert not (tmp_path / "outputs/ranking_excluded.txt").exists()
    assert json.loads((tmp_path / "outputs/issues.json").read_text(encoding="utf-8"))["comparison"]["members"]         == len(sample)
    assert cli.main(["rank", "--root", str(tmp_path)]) == 0
    overall = json.loads((tmp_path / "outputs/overall_metrics.json").read_text(encoding="utf-8"))
    assert overall["members"] == len(members) and overall["comparison_random_100k"]["members"] == len(sample)
    comp = list(csv.DictReader((tmp_path / "outputs/comparison_random_100k_areas.csv").open(encoding="utf-8")))
    assert sum(int(r["complaint_count"]) for r in comp) == len(sample)


def test_m6b_defaults_send_severity_of_every_review_to_a5(tmp_path):
    db = build(tmp_path)
    assert cli.main(["enrich", "--db", str(db), "--root", str(tmp_path), "--provider", "fake", "--label-config", "cfg-b",
                     "--batch-size", "10"]) == 0
    conn = connect(db)
    routes = [json.loads(r[0]) for r in conn.execute("SELECT output_json FROM agent_outputs WHERE agent='A4'")]
    assert routes and all(r["route"] == "escalate" and r["fields"] == ["severity"] and r["gate"] == 1.0 for r in routes)
    a5 = conn.execute("SELECT COUNT(*) FROM agent_outputs WHERE agent='A5'").fetchone()[0]
    assert a5 == len(routes)
    sents = {r[0] for r in conn.execute("SELECT sentiment FROM results WHERE label_config='cfg-b'")}
    assert sents <= {-1.0, -0.5, 0.0, 0.5, 1.0}
    conn.close()


# ---------- verification, caps, prices ----------
def test_verify_rate_is_20_percent_and_a_superset_of_5():
    assert verifier.DEFAULT_RATE == 0.20
    shas = [f"{i:064x}" for i in range(4000)]
    five = {s for s in shas if verifier.in_sample(s, 0.05)}
    twenty = {s for s in shas if verifier.in_sample(s, 0.20)}
    assert five < twenty and 0.17 < len(twenty) / len(shas) < 0.23


def test_bucket_caps_match_plan_v42():
    assert ledger.BUCKET_CAPS == {"pilots": 0.50, "decisions": 2.00, "escalation": 5.00, "verification": 1.00,
                                  "analysis": 0.25, "reserve": 1.25}
    assert sum(ledger.BUCKET_CAPS.values()) == pytest.approx(ledger.PROJECT_CAP)


def test_claude_prices_per_model():
    usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert ClaudeProvider(client=object()).cost(usage) == pytest.approx(0.60)
    assert ClaudeProvider(model="claude-sonnet-5-5", client=object()).cost(usage) == pytest.approx(12.00)
    assert ClaudeBatchProvider(client=object()).cost(usage) == pytest.approx(0.30)
    with pytest.raises(ValueError):
        ClaudeProvider(model="claude-unknown", client=object())
    rates = list(csv.DictReader((ROOT / "cost/rates.csv").open(encoding="utf-8")))
    assert {(r["model"], r["item"]) for r in rates if r["model"] == "claude-sonnet-5-5"} >= {
        ("claude-sonnet-5-5", "input_tokens"), ("claude-sonnet-5-5", "output_tokens")}


# ---------- topic-test rule ----------
def test_topic_rule_needs_two_more_adjudicated_and_a_fitting_cost():
    m6b = load("m6b", ROOT / "scripts" / "m6b.py")
    refs, revs = {}, []
    for i in range(10):  # 6 adjudicated, Celeris wrong on 3 of them (all low confidence), model right on all
        rid = f"r{i}"
        gold = "playback"
        refs[rid] = {"accepted": {"topic": [gold]}, "adjudicated": i < 6}
        celeris = "other" if i < 3 else gold
        revs.append({"review_id": rid, "celeris": celeris, "p_top": 0.4 if i < 3 else 0.95, "model": gold})
    base, rows, dec = m6b.topic_decision(revs, refs, usd_per_review=0.00001)
    assert base["adjudicated"] == [3, 6]
    at = {r["gate"]: r for r in rows}
    assert at[0.50]["gain_adjudicated"] == 3 and at[0.50]["passes"]
    assert dec["adopt"] and dec["gate"] == 0.50                                  # the lowest gate with the best gain
    _, rows2, dec2 = m6b.topic_decision(revs, refs, usd_per_review=0.01)         # too expensive at 100k scale
    assert not dec2["adopt"] and not any(r["passes"] for r in rows2)
    revs[1]["model"] = revs[2]["model"] = "other"                                # gain 1 → not enough
    assert not m6b.topic_decision(revs, refs, usd_per_review=0.00001)[2]["adopt"]
