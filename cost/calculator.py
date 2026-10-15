"""Cost calculator (COST_CALCULATOR.md, PLAN §5 M5, §9.6). Offline replay by default: it never calls an API.

    python cost/calculator.py                       # replay the saved pilot: usage.csv × rates.csv → report.{md,html,json}
    python cost/calculator.py --rates my_rates.csv --budget 10 --max-concurrency 8 --max-fallback-fraction 0.10

Inputs (all saved by `python -m pipeline.cli pilot`): usage.csv (one row per billed item per attempt), rates.csv
(editable; per-million prices), pilot_measured.json (checksum, counts, stage configs, stage times),
pilot_calls.jsonl (every attempt). Standard library only, so it runs anywhere.

Billing math: every usage row is one mutually exclusive item (plain input, cache read, cache write, output), and
item_cost = units × usd_per_unit / 1,000,000. Cached input is already subtracted from plain input in usage.csv;
thinking tokens are inside output tokens and counted once; each row is priced at the tier the call actually used.
Attempts with no provider usage carry the ledger estimate as "unknown billing", reported apart and never as 0.
Local compute is reported apart from API spend (unknown, not metered).

Projections reuse the pilot's per-unit token counts (not its dollars), so editing rates.csv changes every API figure
proportionally and nothing else; editing the projected volumes never changes the measured section.
"""
import argparse
import csv
import html
import json
import math
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Contract volumes (GRADING_CONTRACT / dataset README, PLAN §9.1) and our scope (PLAN §1.1, M1 measurement).
FULL = {"rows": 660_622, "outputs": 660_609, "quarantined": 13, "distinct": 484_189}
SCOPE = {"rows": 100_000, "outputs": 100_000, "quarantined": 0, "distinct": 78_099}
# PLAN §4 bucket caps (list USD) checked against the 100k scope scenario.
BUCKET_CAPS = {"A3": ("decisions", 4.50), "A5": ("escalation", 2.00), "A8": ("verification", 0.50),
               "fixed": ("analysis", 0.25)}
STAGES = [  # (key, calls.role, calls.agent, unit, scaling)
    ("A3", "enrich", "A3", "distinct text decided", "distinct"),
    ("A5", "enrich", "A5", "escalated text", "fallback"),
    ("A8", "verify", "A8", "verified text", "verify"),
    ("A9", "group", "A9", "run", "fixed"),
    ("A11", "memo", "A11", "run", "fixed"),
]
CONSERVATIVE = {"token_factor": 1.25, "min_retry_overhead": 0.10, "fixed_factor": 2.0, "concurrency_efficiency": 0.5,
                "output_factor": 1.5}
BATCH_HOURS = {"base": 1.0, "conservative": 24.0}  # Anthropic Batch API: most < 1 h, max 24 h (PLAN §9.10)


# ---------- inputs ----------
def load_rates(path):
    rates, sources = {}, {}
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            per = 1_000_000 if r["unit"].strip().lower().startswith("1m") else 1
            key = (r["provider"], r["model"], r["tier"], r["item"])
            rates[key] = float(r["usd_per_unit"]) / per
            sources[key] = (r["source"], r["checked"], r["currency"])
    return rates, sources


def load_usage(path):
    with open(path, encoding="utf-8", newline="") as f:
        return [{**r, "units": float(r["units"])} for r in csv.DictReader(f)]


def load_calls(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def rate_for(rates, provider, model, tier, item):
    """Exact (provider, model, tier, item) rate; a cache item missing at a tier is priced at that tier's plain input
    rate (conservative for reads, used only in projections)."""
    if (provider, model, tier, item) in rates:
        return rates[(provider, model, tier, item)]
    if item != "output_tokens" and (provider, model, tier, "input_tokens") in rates:
        return rates[(provider, model, tier, "input_tokens")]
    raise KeyError(f"no rate for {provider}/{model}/{tier}/{item} in rates.csv")


# ---------- measured ----------
def price_usage(usage, rates):
    """Each usage row → cost. Returns (priced rows, unknown-billing rows)."""
    priced, unknown = [], []
    for u in usage:
        if u["item"] == "unmeasured_attempt_est_usd":
            unknown.append(u)
            continue
        key = (u["provider"], u["model"], u["tier"], u["item"])
        if key not in rates:
            raise KeyError(f"usage row {u['request_id']} item {key} has no rate in rates.csv")
        priced.append({**u, "usd_per_unit": rates[key], "cost": u["units"] * rates[key]})
    return priced, unknown


def stage_of(role, agent):
    for key, r, a, *_ in STAGES:
        if (r, a) == (role, agent):
            return key
    return f"{role}/{agent}"


def measured_section(rates, sources, usage, calls, m):
    priced, unknown = price_usage(usage, rates)
    by_stage = defaultdict(lambda: {"cost": 0.0, "items": defaultdict(float), "cold_cost": 0.0, "warm_cost": 0.0})
    for p in priced:
        s = by_stage[stage_of(p["stage"], p["agent"])]
        s["cost"] += p["cost"]
        s[f"{p['pilot_phase']}_cost" if p["pilot_phase"] in ("cold", "warm") else "cold_cost"] += p["cost"]
        s["items"][(p["provider"], p["model"], p["tier"], p["item"])] += p["units"]
    attempts = defaultdict(lambda: defaultdict(int))
    for c in calls:
        attempts[stage_of(c["role"], c["agent"])][c["outcome"]] += 1
    units = unit_counts(m)
    stages = {}
    for key in sorted(set(by_stage) | set(attempts) | {k for k, *_ in STAGES}, key=stage_order):
        s, a = by_stage.get(key), attempts.get(key, {})
        n = sum(a.values())
        stages[key] = {
            "unit": next((u for k, _, _, u, _ in STAGES if k == key), "call"),
            "units": units.get(key, 0),
            "attempts": n, "succeeded": a.get("succeeded", 0), "failed": n - a.get("succeeded", 0),
            "items": {"/".join(k): v for k, v in (s["items"].items() if s else [])},
            "cost": s["cost"] if s else 0.0, "cold_cost": s["cold_cost"] if s else 0.0,
            "warm_cost": s["warm_cost"] if s else 0.0,
        }
    api = sum(p["cost"] for p in priced)
    by_provider = defaultdict(float)
    for p in priced:
        by_provider[p["provider"]] += p["cost"]
    cold, warm = m["phases"]["cold"], m["phases"]["warm"]
    rows = m["records"]["rows"]
    completed = m["records"]["completed"]
    used = sorted({(p["provider"], p["model"], p["tier"], p["item"]) for p in priced})
    return {
        "label": m.get("label", "100-review pilot"),
        "input": m["input"],
        "records": m["records"], "unique_texts": m["unique_texts"], "routes": m["routes"],
        "verification": m["verification"], "label_config": m["label_config"], "stage_config": m["stages"],
        "stages": stages,
        "api_subtotal_usd": api,
        "api_by_provider_usd": dict(by_provider),
        "unknown_billing_usd": sum(u["units"] for u in unknown),
        "unknown_billing_attempts": len(unknown),
        "cold": {"seconds": cold["total_seconds"], "calls": cold["new_calls"], "stages": cold["stages"],
                 "api_usd": sum(s["cold_cost"] for s in stages.values())},
        "warm": {"seconds": warm["total_seconds"], "calls": warm["new_calls"], "enrich_calls": warm["new_enrich_calls"],
                 "stages": warm["stages"], "incremental_api_usd": sum(s["warm_cost"] for s in stages.values())},
        "throughput_rows_per_s": rows / cold["total_seconds"] if cold["total_seconds"] else None,
        "cost_per_1000_rows_usd": api / rows * 1000 if rows else None,
        "cost_per_completed_record_usd": api / completed if completed else None,
        "rates_used": [{"key": "/".join(k), "usd_per_million": rates[k] * 1e6, "source": sources[k][0],
                        "checked": sources[k][1], "currency": sources[k][2]} for k in used],
        "local_compute": m["local_compute"],
    }


def stage_order(key):
    order = [k for k, *_ in STAGES]
    return order.index(key) if key in order else len(order)


def unit_counts(m):
    routes = m.get("routes") or {}
    return {"A3": sum(routes.values()), "A5": routes.get("escalate", 0),
            "A8": m["verification"]["verified_texts"], "A9": 1, "A11": 1}


def stage_seconds(m, name):
    return sum(s["seconds"] for s in m["phases"]["cold"]["stages"] if s["stage"] == name)


# ---------- projections ----------
def projection_tier(key, provider, tier, controls):
    if key == "A8" and provider == "anthropic":
        return "batch_le100k" if controls["verify_tier"] == "batch" else "standard_le100k"
    return tier


def project(meas, m, rates, volume, controls, mode):
    """One scenario (volume) in one mode (base | conservative). Pure function of its inputs."""
    cons = mode == "conservative"
    distinct = volume["distinct"]
    units_a3 = meas["stages"]["A3"]["units"] or 1
    measured_fb = meas["stages"]["A5"]["units"] / units_a3
    fb = controls["max_fallback_fraction"] if cons else min(measured_fb, controls["max_fallback_fraction"])
    scale = {"distinct": distinct, "fallback": distinct * fb, "verify": distinct * controls["verify_rate"], "fixed": 1}
    out = {"stages": {}, "fallback_fraction": fb}
    total = 0.0
    for key, role, agent, unit, how in STAGES:
        st = meas["stages"].get(key)
        if not st or not st["units"] or not st["items"]:
            out["stages"][key] = {"units": scale[how], "usd": 0.0, "note": "not measured in the pilot"}
            continue
        succeeded = max(st["succeeded"], 1)
        retry = max(st["attempts"] / succeeded - 1, 0.0)
        if cons:
            retry = max(retry, CONSERVATIVE["min_retry_overhead"])
        units_per_request = st["units"] / succeeded
        usd, tokens = 0.0, defaultdict(float)
        for item_key, units in st["items"].items():
            provider, model, tier, item = item_key.split("/")
            per_unit = units / st["units"] / (1 + max(st["attempts"] / succeeded - 1, 0.0))  # per unit, one attempt
            if cons:
                if how == "fixed":
                    per_unit *= CONSERVATIVE["fixed_factor"]
                elif item == "output_tokens":
                    per_unit *= CONSERVATIVE["output_factor"]
                    if controls["output_token_cap"]:  # a request can never return more than the cap
                        per_unit = min(per_unit, controls["output_token_cap"] / units_per_request)
                else:
                    per_unit *= CONSERVATIVE["token_factor"]
            n = per_unit * scale[how] * (1 + retry)
            tokens[item] += n
            usd += n * rate_for(rates, provider, model, projection_tier(key, provider, tier, controls), item)
        out["stages"][key] = {"units": scale[how], "retry_overhead": retry, "tokens": dict(tokens), "usd": usd}
        total += usd
    out["api_usd"] = total
    out["time"] = project_time(meas, m, volume, controls, cons, scale)
    out["cost_per_1000_rows_usd"] = total / volume["rows"] * 1000
    out["cost_per_output_usd"] = total / volume["outputs"] if volume["outputs"] else None
    return out


def project_time(meas, m, volume, controls, cons, scale):
    """Per-stage elapsed time from that stage's own measured throughput (1 worker in the pilot)."""
    conc = controls["max_concurrency"] * (CONSERVATIVE["concurrency_efficiency"] if cons else 1.0)
    pilot_rows = meas["records"]["rows"] or 1
    t = {}
    t["ingest"] = stage_seconds(m, "ingest") / pilot_rows * volume["rows"]  # local, linear in rows
    a3 = meas["stages"]["A3"]["units"] or 1
    t["enrich"] = stage_seconds(m, "enrich") / a3 * scale["distinct"] / conc
    if controls["verify_tier"] == "batch":
        t["verify"] = BATCH_HOURS["conservative" if cons else "base"] * 3600
    else:
        a8 = meas["stages"]["A8"]["units"] or 1
        t["verify"] = stage_seconds(m, "verify") / a8 * scale["verify"] / conc
    for name in ("group", "rank", "memo"):  # fixed work, counted once
        t[name] = stage_seconds(m, name) * (CONSERVATIVE["fixed_factor"] if cons else 1.0)
    total = sum(t.values())
    return {"stages_s": t, "total_s": total, "throughput_rows_per_s": volume["rows"] / total if total else None}


def calculate(rates_path=HERE / "rates.csv", usage_path=HERE / "usage.csv", measured_path=HERE / "pilot_measured.json",
              calls_path=HERE / "pilot_calls.jsonl", controls=None, full=None, scope=None):
    controls = {"budget_usd": 10.0, "output_token_cap": 4000, "max_concurrency": 4, "max_fallback_fraction": 0.15,
                "verify_rate": 0.05, "verify_tier": "batch", **(controls or {})}
    full, scope = {**FULL, **(full or {})}, {**SCOPE, **(scope or {})}
    rates, sources = load_rates(rates_path)
    m = json.loads(Path(measured_path).read_text(encoding="utf-8"))
    meas = measured_section(rates, sources, load_usage(usage_path), load_calls(calls_path), m)
    scenarios = {
        "full_with_reuse": {**full},
        "full_no_reuse": {**full, "distinct": full["outputs"]},
        "scope_100k": {**scope},
    }
    proj = {name: {mode: project(meas, m, rates, vol, controls, mode) for mode in ("base", "conservative")}
            for name, vol in scenarios.items()}
    warnings = []
    for name, modes in proj.items():
        for mode, p in modes.items():
            if p["api_usd"] > controls["budget_usd"]:
                warnings.append(f"OVER BUDGET: {name} ({mode}) ${p['api_usd']:.2f} > budget ${controls['budget_usd']:.2f}")
    for mode, p in proj["scope_100k"].items():
        fixed = sum(p["stages"][k]["usd"] for k in ("A9", "A11"))
        for key, (bucket, cap) in BUCKET_CAPS.items():
            usd = fixed if key == "fixed" else p["stages"][key]["usd"]
            if usd > cap:
                warnings.append(f"BUCKET CAP: scope_100k ({mode}) {bucket} ${usd:.2f} > ${cap:.2f} (PLAN §4)")
    return {"controls": controls, "volumes": scenarios, "measured": meas, "projections": proj, "warnings": warnings}


# ---------- report ----------
def usd(x):
    return "unknown" if x is None else f"${x:,.6f}" if abs(x) < 0.01 else f"${x:,.4f}"


def hms(s):
    if s is None:
        return "unknown"
    return f"{s:.1f} s" if s < 120 else f"{s / 60:.1f} min" if s < 7200 else f"{s / 3600:.1f} h"


def report_md(r, credit_providers=("celeris",)):
    me, c = r["measured"], r["controls"]
    credits = sum(v for k, v in me["api_by_provider_usd"].items() if k in credit_providers)
    L = [f"# Cost report — {me['label']} + projections", "",
         "Offline replay: `python cost/calculator.py` (usage.csv × rates.csv; no API calls). List prices; credits and "
         "cash are shown apart; local compute is not metered (unknown, not 0).", ""]
    if r["warnings"]:
        L += ["## Warnings", ""] + [f"- **{w}**" for w in r["warnings"]] + [""]
    i = me["input"]
    L += [f"## Measured ({'pilot' if me['label'] == '100-review pilot' else me['label']})", "",
          "| Item | Value |", "|---|---|",
          f"| Input | `{i['file']}` sha256 `{i['sha256'][:16]}…` (manifest match: {i['match']}) |",
          f"| Review IDs | {i['rows']} (ids sha256 `{i['ids_sha256'][:16]}…`) |",
          f"| Records completed / quarantined | {me['records']['completed']} / {me['records']['quarantined']} |",
          f"| Unique texts | {me['unique_texts']} |",
          f"| Exact-text cache hits (rows completed by reuse) | {me['records']['completed_via_exact_text_reuse']} |",
          f"| A4 routes | {json.dumps(me['routes'])} |",
          f"| Verification sample | rate {me['verification']['declared_sample_rate']} (declared pilot rate), "
          f"{me['verification']['verified_texts']} texts verified, {me['verification']['tier']} |",
          f"| label_config | `{me['label_config']}` |",
          f"| Cold run | {hms(me['cold']['seconds'])}, {me['cold']['calls']} calls, {usd(me['cold']['api_usd'])} |",
          f"| Warm rerun | {hms(me['warm']['seconds'])}, {me['warm']['calls']} calls "
          f"({me['warm']['enrich_calls']} enrichment), incremental {usd(me['warm']['incremental_api_usd'])} |",
          f"| Throughput (cold) | {me['throughput_rows_per_s']:.2f} rows/s |" if me["throughput_rows_per_s"] else "",
          f"| API subtotal (list) | **{usd(me['api_subtotal_usd'])}** |",
          f"| Credits applied (prepaid {', '.join(credit_providers)}) | {usd(credits)} |",
          f"| Cash spent | {usd(me['api_subtotal_usd'] - credits)} |",
          f"| Unknown-billing attempts (ledger estimate, not in subtotal) | {me['unknown_billing_attempts']} "
          f"({usd(me['unknown_billing_usd'])}) |",
          f"| Cost per 1,000 input rows | {usd(me['cost_per_1000_rows_usd'])} |",
          f"| Cost per completed record | {usd(me['cost_per_completed_record_usd'])} |",
          f"| Local compute | {me['local_compute']['usd']} |", ""]
    L += ["### By stage", "", "| Stage | Config | Units | Attempts (ok/failed) | Usage | Cost (list) |",
          "|---|---|---|---|---|---|"]
    cfg_key = {"A3": "enrich_decision", "A5": "enrich_escalation", "A8": "verify", "A9": "group", "A11": "memo"}
    for key, s in me["stages"].items():
        cfg = me["stage_config"].get(cfg_key.get(key, ""), {})
        desc = ", ".join(f"{k}={v}" for k, v in cfg.items() if k != "agent")
        items = "<br>".join(f"{k.split('/')[-1]}: {int(v):,}" for k, v in s["items"].items()) or "—"
        L.append(f"| {key} | {desc} | {s['units']} {s['unit']} | {s['attempts']} ({s['succeeded']}/{s['failed']}) | "
                 f"{items} | {usd(s['cost'])} |")
    L += ["", "### Stage times (cold → warm)", "", "| Stage | Cold | Warm | Cold calls | Warm calls |", "|---|---|---|---|---|"]
    warm = {s["stage"]: s for s in me["warm"]["stages"]}
    for s in me["cold"]["stages"]:
        w = warm.get(s["stage"], {})
        L.append(f"| {s['stage']} | {hms(s['seconds'])} | {hms(w.get('seconds'))} | {s['new_calls']} | "
                 f"{w.get('new_calls', '—')} |")
    L += ["", "### Rates used (rates.csv)", "", "| Provider/model/tier/item | USD per 1M | Source | Checked |",
          "|---|---|---|---|"]
    L += [f"| {x['key']} | {x['usd_per_million']:.4f} | {x['source']} | {x['checked']} |" for x in me["rates_used"]]
    L += ["", "## Estimated (projections)", "",
          f"Controls: budget ${c['budget_usd']:.2f}, output-token cap {c['output_token_cap']} per request, max "
          f"concurrency {c['max_concurrency']}, max fallback fraction {c['max_fallback_fraction']}, verification "
          f"{c['verify_rate']:.0%} at the {c['verify_tier']} tier. Fixed work (group naming, memo) is counted once.",
          f"Conservative = tokens ×{CONSERVATIVE['token_factor']}, output ×{CONSERVATIVE['output_factor']} (≤ cap), "
          f"retries ≥ {CONSERVATIVE['min_retry_overhead']:.0%}, fallback at the cap, fixed work ×"
          f"{CONSERVATIVE['fixed_factor']}, concurrency efficiency {CONSERVATIVE['concurrency_efficiency']:.0%}, "
          f"Batch API {BATCH_HOURS['conservative']:.0f} h (base {BATCH_HOURS['base']:.0f} h).", ""]
    L += ["| Scenario | Rows / outputs / quarantined / paid distinct | Mode | A3 | A5 | A8 | Fixed | **Total API** | "
          "Per 1,000 rows | Elapsed | Rows/s |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, modes in r["projections"].items():
        v = r["volumes"][name]
        for mode, p in modes.items():
            s = p["stages"]
            L.append(f"| {name} | {v['rows']:,} / {v['outputs']:,} / {v['quarantined']} / {v['distinct']:,} | {mode} | "
                     f"{usd(s['A3']['usd'])} | {usd(s['A5']['usd'])} | {usd(s['A8']['usd'])} | "
                     f"{usd(s['A9']['usd'] + s['A11']['usd'])} | **{usd(p['api_usd'])}** | "
                     f"{usd(p['cost_per_1000_rows_usd'])} | {hms(p['time']['total_s'])} | "
                     f"{p['time']['throughput_rows_per_s']:.1f} |")
    L += ["", "`full_no_reuse` pays every non-empty row; `full_with_reuse` pays each distinct text once (exact-text "
          "cache). `scope_100k` is our submission scope (PLAN §1.1); its out-of-scope cache fill costs $0.", ""]
    return "\n".join(line for line in L if line is not None) + "\n"


def md_to_html(md):
    """Tiny renderer for this report's Markdown (headings, tables, bullets, paragraphs)."""
    out, table = [], []

    def inline(s):
        s = html.escape(s).replace("&lt;br&gt;", "<br>")
        parts = s.split("**")
        s = "".join(f"<b>{p}</b>" if k % 2 else p for k, p in enumerate(parts))
        parts = s.split("`")
        return "".join(f"<code>{p}</code>" if k % 2 else p for k, p in enumerate(parts))

    def flush():
        if table:
            rows = [r for r in table if not set(r.replace("|", "").strip()) <= {"-"}]
            cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
            out.append("<table><tr>" + "".join(f"<th>{inline(c)}</th>" for c in cells[0]) + "</tr>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in cells[1:])
                       + "</table>")
            table.clear()

    for line in md.splitlines():
        if line.startswith("|"):
            table.append(line)
            continue
        flush()
        if line.startswith("#"):
            n = len(line) - len(line.lstrip("#"))
            out.append(f"<h{n}>{inline(line[n:].strip())}</h{n}>")
        elif line.startswith("- "):
            out.append(f"<li>{inline(line[2:])}</li>")
        elif line.strip():
            out.append(f"<p>{inline(line)}</p>")
    flush()
    style = ("body{font:14px/1.45 system-ui,sans-serif;margin:24px;max-width:1200px}table{border-collapse:collapse;"
             "margin:8px 0}td,th{border:1px solid #ccc;padding:4px 8px;text-align:left;vertical-align:top}"
             "th{background:#f3f3f3}code{background:#f3f3f3;padding:0 3px}")
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>Pilot cost report</title><style>{style}</style>"
            f"</head><body>{''.join(out)}</body></html>\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rates", type=Path, default=HERE / "rates.csv")
    ap.add_argument("--usage", type=Path, default=HERE / "usage.csv")
    ap.add_argument("--measured", type=Path, default=HERE / "pilot_measured.json")
    ap.add_argument("--calls", type=Path, default=HERE / "pilot_calls.jsonl")
    ap.add_argument("--out", type=Path, default=HERE, help="where report.md / report.html / report.json go")
    ap.add_argument("--budget", type=float, default=10.0, help="editable budget, USD list")
    ap.add_argument("--output-token-cap", type=int, default=4000, help="max output tokens per request (0 = none)")
    ap.add_argument("--max-concurrency", type=int, default=4)
    ap.add_argument("--max-fallback-fraction", type=float, default=0.15, help="escalation (A5) share cap")
    ap.add_argument("--verify-rate", type=float, default=0.05)
    ap.add_argument("--verify-tier", choices=("batch", "standard"), default="batch")
    ap.add_argument("--full-rows", type=int, default=FULL["rows"])
    ap.add_argument("--full-distinct", type=int, default=FULL["distinct"])
    ap.add_argument("--scope-rows", type=int, default=SCOPE["rows"])
    ap.add_argument("--scope-distinct", type=int, default=SCOPE["distinct"])
    a = ap.parse_args(argv)
    controls = {"budget_usd": a.budget, "output_token_cap": a.output_token_cap or None,
                "max_concurrency": a.max_concurrency, "max_fallback_fraction": a.max_fallback_fraction,
                "verify_rate": a.verify_rate, "verify_tier": a.verify_tier}
    full = {"rows": a.full_rows, "distinct": a.full_distinct,
            "outputs": a.full_rows - FULL["quarantined"]}
    scope = {"rows": a.scope_rows, "outputs": a.scope_rows, "distinct": a.scope_distinct}
    r = calculate(a.rates, a.usage, a.measured, a.calls, controls, full, scope)
    a.out.mkdir(parents=True, exist_ok=True)
    md = report_md(r)
    (a.out / "report.md").write_text(md, encoding="utf-8", newline="\n")
    (a.out / "report.html").write_text(md_to_html(md), encoding="utf-8", newline="\n")
    (a.out / "report.json").write_text(json.dumps(r, indent=1, default=str) + "\n", encoding="utf-8", newline="\n")
    me = r["measured"]
    print(f"pilot API subtotal (list): {usd(me['api_subtotal_usd'])}; unknown-billing attempts: "
          f"{me['unknown_billing_attempts']}")
    for name, modes in r["projections"].items():
        print(f"{name}: base {usd(modes['base']['api_usd'])}, conservative {usd(modes['conservative']['api_usd'])}")
    for w in r["warnings"]:
        print("WARNING:", w)
    print(f"wrote {a.out / 'report.md'}, report.html, report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
