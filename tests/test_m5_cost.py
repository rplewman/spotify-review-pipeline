"""M5: pilot command (fake provider, offline) + cost calculator. Gate: doubling the API rates doubles the API subtotal
with times and local costs unchanged; changing the projected volume leaves the pilot results unchanged."""
import csv
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from pipeline import cli, pilot

ROOT = Path(__file__).resolve().parents[1]
COST_100 = ROOT / "data" / "raw" / "cost_100.csv"
needs_data = pytest.mark.skipif(not COST_100.exists(), reason="data/raw/cost_100.csv not extracted")

spec = importlib.util.spec_from_file_location("calculator", ROOT / "cost" / "calculator.py")
calculator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(calculator)


def fake_rates(path, factor=1.0):
    """The real rates.csv plus rows for the fake models (so a fake pilot can be priced), all × factor."""
    with open(ROOT / "cost" / "rates.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for model in ("fake-decision", "fake-escalation", "fake-verifier", "fake-analysis"):
        for item, price in (("input_tokens", "0.04"), ("cached_input_tokens", "0.04"), ("output_tokens", "0.50")):
            rows.append({**rows[0], "provider": "fake", "model": model, "tier": "standard", "item": item,
                         "usd_per_unit": price, "source": "test"})
    for r in rows:
        r["usd_per_unit"] = repr(float(r["usd_per_unit"]) * factor)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return path


@pytest.fixture(scope="module")
def pilot_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("pilot")
    out = tmp / "cost"
    # low_conf answers make A4 escalate (to the fake escalation model) so every stage has usage
    rc = cli.main(["pilot", "--db", str(tmp / "pilot.db"), "--root", str(tmp / "run"), "--out", str(out),
                   "--fake-script", "low_conf,low_conf"])
    assert rc == 0
    return tmp, out


@needs_data
def test_pilot_evidence_files_and_ids(pilot_run):
    tmp, out = pilot_run
    for name in ("pilot_records.jsonl", "pilot_calls.jsonl", "usage.csv", "pilot_measured.json"):
        assert (out / name).exists(), name
    with open(COST_100, encoding="utf-8", newline="") as f:
        ids = [r["review_id"] for r in csv.DictReader(f)]
    recs = [json.loads(line) for line in (out / "pilot_records.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["review_id"] for r in recs] == ids  # the instructor's check: the 100 IDs match cost_100.csv, in order
    assert all(r["status"] == "completed" for r in recs)
    m = json.loads((out / "pilot_measured.json").read_text(encoding="utf-8"))
    assert m["input"]["match"] and m["input"]["rows"] == 100
    assert m["routes"].get("escalate", 0) > 0
    # all stages ran cold; the warm rerun made zero calls of any kind
    assert {s["stage"] for s in m["phases"]["cold"]["stages"]} == {"ingest", "enrich", "verify", "group", "rank", "memo"}
    assert m["phases"]["warm"]["new_calls"] == 0 and m["phases"]["warm"]["new_enrich_calls"] == 0
    calls = [json.loads(line) for line in (out / "pilot_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {c["role"] for c in calls} == {"enrich", "verify", "group", "memo"}
    assert {c["pilot_phase"] for c in calls} == {"cold"}
    # every usage row maps to a logged call
    with open(out / "usage.csv", encoding="utf-8", newline="") as f:
        usage = list(csv.DictReader(f))
    assert usage and {u["request_id"] for u in usage} <= {c["request_id"] for c in calls}


@needs_data
def test_pilot_refuses_changed_input_existing_db_and_unpaid(tmp_path):
    bad = tmp_path / "cost_100.csv"
    bad.write_bytes(COST_100.read_bytes().replace(b"Great", b"Grea7", 1))
    assert cli.main(["pilot", "--input", str(bad), "--db", str(tmp_path / "a.db"), "--out", str(tmp_path)]) == 1
    existing = tmp_path / "b.db"
    existing.write_bytes(b"")
    assert cli.main(["pilot", "--db", str(existing), "--root", str(tmp_path), "--out", str(tmp_path)]) == 1
    with pytest.raises(SystemExit, match="--paid"):
        cli.main(["pilot", "--provider", "celeris", "--db", str(tmp_path / "c.db")])
    with pytest.raises(SystemExit, match="operational"):
        cli.main(["pilot", "--db", "state/pipeline.db"])


def calc(out, rates, **kw):
    return calculator.calculate(rates, out / "usage.csv", out / "pilot_measured.json", out / "pilot_calls.jsonl", **kw)


@needs_data
def test_gate_doubling_rates_doubles_api_subtotal_only(pilot_run, tmp_path):
    _, out = pilot_run
    r1 = calc(out, fake_rates(tmp_path / "r1.csv"))
    r2 = calc(out, fake_rates(tmp_path / "r2.csv", 2.0))
    m1, m2 = r1["measured"], r2["measured"]
    assert m1["api_subtotal_usd"] > 0
    assert m2["api_subtotal_usd"] == pytest.approx(2 * m1["api_subtotal_usd"])
    for key in m1["stages"]:
        assert m2["stages"][key]["cost"] == pytest.approx(2 * m1["stages"][key]["cost"])
    for name, modes in r1["projections"].items():
        for mode, p in modes.items():
            q = r2["projections"][name][mode]
            assert q["api_usd"] == pytest.approx(2 * p["api_usd"])
            assert q["time"] == p["time"]  # times unchanged
    # measured times, counts and local compute unchanged
    for k in ("cold", "warm"):
        assert m2[k]["seconds"] == m1[k]["seconds"] and m2[k]["calls"] == m1[k]["calls"]
    assert m2["local_compute"] == m1["local_compute"]
    assert m2["unknown_billing_usd"] == m1["unknown_billing_usd"]  # ledger estimates are not rate-derived


@needs_data
def test_gate_projected_volume_leaves_pilot_unchanged(pilot_run, tmp_path):
    _, out = pilot_run
    rates = fake_rates(tmp_path / "r.csv")
    a = calc(out, rates)
    b = calc(out, rates, full={"rows": 1_000_000, "outputs": 999_987, "distinct": 900_000},
             scope={"rows": 200_000, "outputs": 200_000, "distinct": 150_000})
    assert a["measured"] == b["measured"]
    assert b["projections"]["full_with_reuse"]["base"]["api_usd"] > a["projections"]["full_with_reuse"]["base"]["api_usd"]


@needs_data
def test_controls_and_warnings(pilot_run, tmp_path):
    _, out = pilot_run
    rates = fake_rates(tmp_path / "r.csv")
    base = calc(out, rates)
    tight = calc(out, rates, controls={"budget_usd": 0.0001, "max_fallback_fraction": 0.01, "max_concurrency": 1})
    assert any(w.startswith("OVER BUDGET") for w in tight["warnings"])
    assert not any(w.startswith("OVER BUDGET") for w in base["warnings"])
    for mode in ("base", "conservative"):
        assert tight["projections"]["scope_100k"][mode]["fallback_fraction"] <= 0.01
        assert (tight["projections"]["scope_100k"][mode]["time"]["stages_s"]["enrich"]
                > base["projections"]["scope_100k"][mode]["time"]["stages_s"]["enrich"])
    full = base["projections"]["full_with_reuse"]
    assert full["conservative"]["api_usd"] >= full["base"]["api_usd"]
    assert base["projections"]["full_no_reuse"]["base"]["api_usd"] > full["base"]["api_usd"]


@needs_data
def test_calculator_cli_writes_reports(pilot_run, tmp_path):
    _, out = pilot_run
    rates = fake_rates(tmp_path / "r.csv")
    rc = calculator.main(["--rates", str(rates), "--usage", str(out / "usage.csv"), "--measured",
                          str(out / "pilot_measured.json"), "--calls", str(out / "pilot_calls.jsonl"),
                          "--out", str(tmp_path)])
    assert rc == 0
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "## Measured (pilot)" in md and "## Estimated (projections)" in md and "full_no_reuse" in md
    assert (tmp_path / "report.html").read_text(encoding="utf-8").startswith("<!doctype html>")


def test_billing_items_are_mutually_exclusive():
    call = {"model": "claude-haiku-5-5", "tier": "standard", "input_tokens": 1000, "cached_tokens": 600,
            "cache_write_tokens": 300, "output_tokens": 50, "usage_available": 1, "cost_usd": 0.0}
    items = {i: u for i, u, _ in pilot.usage_items(call)}
    assert items == {"input_tokens": 100, "cache_read_tokens": 600, "cache_write_5m_tokens": 300, "output_tokens": 50}
    assert sum(v for k, v in items.items() if k != "output_tokens") == 1000  # nothing counted twice
    assert pilot.rate_tier("anthropic", "batch") == "batch_le100k"
    unknown = {**call, "usage_available": 0, "cost_usd": 0.002}
    assert pilot.usage_items(unknown) == [("unmeasured_attempt_est_usd", 0.002, "USD (ledger estimate)")]
    rates, _ = calculator.load_rates(ROOT / "cost" / "rates.csv")
    rows = [{"request_id": "x", "stage": "enrich", "agent": "A5", "pilot_phase": "cold", "provider": "anthropic",
             "model": "claude-haiku-5-5", "tier": "standard_le100k", "item": i, "units": float(u)}
            for i, u in items.items()]
    priced, unk = calculator.price_usage(rows, rates)
    expected = (100 * 0.10 + 600 * 0.01 + 300 * 0.125 + 50 * 0.50) / 1e6
    assert sum(p["cost"] for p in priced) == pytest.approx(expected) and not unk
    with pytest.raises(KeyError):
        calculator.price_usage([{**rows[0], "model": "unknown-model"}], rates)


def test_calculator_import_makes_no_calls():
    src = (ROOT / "cost" / "calculator.py").read_text(encoding="utf-8")
    assert "import anthropic" not in src and "httpx" not in src and "pipeline" not in src.split('"""', 2)[2][:400]


@needs_data
def test_comparisons_offline(pilot_run, tmp_path):
    tmp, _ = pilot_run
    spec = importlib.util.spec_from_file_location("pilot_compare", ROOT / "scripts" / "pilot_compare.py")
    pc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pc)
    pdb = str(tmp / "pilot.db")
    assert pc.main(["shape-b", "--pilot-db", pdb, "--shape-db", str(tmp_path / "b.db")]) == 0
    assert pc.main(["escalation", "--pilot-db", pdb, "--db", str(tmp_path / "c.db"), "--out", str(tmp_path)]) == 0
    assert pc.main(["report", "--pilot-db", pdb, "--shape-db", str(tmp_path / "b.db"), "--db", str(tmp_path / "c.db"),
                    "--out", str(tmp_path)]) == 0
    rep = json.loads((tmp_path / "comparisons.json").read_text(encoding="utf-8"))
    s = rep["shape_A_vs_B"]
    assert s["reviews_compared"] == 100 and s["B"]["requests"] == 100 and s["A"]["requests"] < 100
    assert set(rep["escalation_candidates"]["variants"]) == set(pc.VARIANTS)
    with pytest.raises(SystemExit, match="--paid"):
        pc.main(["escalation", "--provider", "anthropic"])


def test_magnus_client_on_mocked_http():
    import asyncio
    import httpx
    from pipeline.providers.base import Request
    from pipeline.providers.celeris import MagnusProvider
    seen = {}

    def handler(req):
        seen["url"], seen["body"] = str(req.url), json.loads(req.content)
        return httpx.Response(200, json={"id": "cmpl-1", "choices": [{"finish_reason": "stop", "message": {
            "content": '{"answers": []}'}}], "usage": {"prompt_tokens": 120, "completion_tokens": 30,
                                                       "prompt_tokens_details": {"cached_tokens": 20}}})
    p = MagnusProvider(api_key="ck_test", transport=httpx.MockTransport(handler))
    resp = asyncio.run(p.call(Request("rid", p.model, [], [], {"messages": [], "max_tokens": 10})))
    assert seen["url"].endswith("/celeris-1-magnus/v1/chat/completions") and seen["body"]["model"] == "celeris-1-magnus"
    assert resp.body == {"answers": []} and resp.usage == {"input_tokens": 120, "output_tokens": 30, "cached_tokens": 20}
    assert p.cost(resp.usage) == pytest.approx((100 * 0.20 + 20 * 0.02 + 30 * 0.70) / 1e6)
