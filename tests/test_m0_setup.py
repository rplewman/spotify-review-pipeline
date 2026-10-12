import csv
from decimal import Decimal
from pathlib import Path

from pipeline.check_submission import FIELDS, canonical, mean_string, row_sha

ROOT = Path(__file__).resolve().parents[1]


def test_row_sha_matches_checker_canonical_form():
    row = dict(zip(FIELDS, ["id1", "Café ✓", "5", "0", "", "2023-01-01 00:00:00"]))
    assert canonical([row[k] for k in FIELDS]) == '["id1","Café ✓","5","0","","2023-01-01 00:00:00"]'
    assert len(row_sha(row)) == 64


def test_mean_string_half_up():
    assert mean_string(5, 3) == "1.666667"


def test_rates_csv_is_well_formed():
    rows = list(csv.DictReader((ROOT / "cost" / "rates.csv").open(encoding="utf-8")))
    assert rows
    for r in rows:
        assert Decimal(r["usd_per_unit"]) >= 0
        assert r["unit"] == "1M tokens" and r["source"].startswith("https://") and r["checked"]
