"""M1 gate. Fast unit tests on a synthetic CSV, plus full-data tests (marked `data`; skipped if data/raw is absent)
that reproduce every published number and check that a re-run is idempotent and byte-identical."""
import csv
import json
from pathlib import Path

import pytest

from pipeline.agents import intake
from pipeline.check_submission import FIELDS, row_sha

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
FULL = RAW / "spotify_reviews_18months.csv"


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(FIELDS)
        w.writerows(rows)


def synthetic(tmp_path):
    rows = [
        ["a", "Great app", "5", "0", "8.1", "2022-06-01 10:00:00"],
        ["b", "Keeps crashing\nevery time,\r\n\"really\"", "1", "2", "", "2022-07-01 10:00:00"],
        ["c", "   ", "3", "0", "8.1", "2022-07-02 10:00:00"],
        ["d", "Great app", "4", "0", "8.2", "2023-01-01 10:00:00"],
        ["e", "Café ✓ lyrics missing", "2", "1", "8.2", "2023-02-01 10:00:00"],
        ["f", "Great app", "5", "0", "", "2023-03-01 10:00:00"],
    ]
    path = tmp_path / "mini.csv"
    write_csv(path, rows)
    return path, rows


def test_parser_keeps_multiline_text_and_matches_checker_hash(tmp_path):
    path, rows = synthetic(tmp_path)
    parsed = list(intake.read_rows(path))
    assert [n for n, _ in parsed] == [1, 2, 3, 4, 5, 6]
    assert parsed[1][1]["review_text"] == rows[1][1]
    assert row_sha(parsed[4][1]) == row_sha(dict(zip(FIELDS, rows[4])))


def test_malformed_row_aborts_with_record_number(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text(",".join(FIELDS) + "\nx,ok,5,0,1,2022-06-01 00:00:00\ny,short,5\n", encoding="utf-8")
    with pytest.raises(intake.IntakeError, match="record 2"):
        list(intake.read_rows(path))


def test_scope_texts_quarantine_and_queue_are_idempotent(tmp_path):
    path, _ = synthetic(tmp_path)
    conn = intake.connect(tmp_path / "t.db")
    intake.apply_schema(conn)
    for _ in range(2):  # re-running must not change anything
        stats = intake.load_source(conn, path)
        intake.assign_scope(conn, size=4)
        intake.build_texts(conn)
        intake.quarantine_empty(conn)
        intake.enqueue(conn, "enrich", "cfg")
    assert stats["records"] == 6 and stats["empty_review_text"] == 1 and stats["distinct_nonempty_texts"] == 3
    assert stats["missing_app_version"] == 2

    ranked = conn.execute("SELECT review_id FROM source_reviews WHERE scope_rank IS NOT NULL ORDER BY scope_rank").fetchall()
    expected = sorted("abdef", key=lambda r: (intake.seed_hash(r), r))
    assert [r for (r,) in ranked] == expected
    in_scope = set(expected[:4])
    scopes = dict(conn.execute("SELECT review_id, scope FROM source_reviews").fetchall())
    assert scopes["c"] == "empty_text"
    assert {r for r, s in scopes.items() if s == "random_100k"} == in_scope

    # The original of a duplicated text is its lowest-ranked in-scope row.
    great = [r for r in expected[:4] if r in "adf"]
    row = conn.execute("SELECT original_review_id, rows_in_scope, rows_total FROM texts WHERE review_text='Great app'").fetchone()
    assert row == (great[0], len(great), 3)
    n_texts = conn.execute("SELECT COUNT(*) FROM texts").fetchone()[0]
    assert conn.execute("SELECT COUNT(*) FROM work_items").fetchone()[0] == n_texts
    assert intake.enqueue(conn, "enrich", "cfg") == 0
    assert conn.execute("SELECT review_id, status, reason FROM records").fetchall() == [("c", "quarantined", "empty_review_text")]
    conn.close()


# ---------- full data ----------

needs_data = pytest.mark.skipif(not FULL.exists(), reason="data/raw not present")


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """Run 1: fresh DB. Run 2: same DB again (idempotent upserts), outputs to a second folder."""
    base = tmp_path_factory.mktemp("ingest")
    db = base / "pipeline.db"
    r1 = intake.ingest(FULL, RAW, db, base / "run1")
    r2 = intake.ingest(FULL, RAW, db, base / "run2")
    return base, db, r1, r2


OUTPUTS = ["grading/ingestion.json", "outputs/ingestion_report.json", "outputs/data_manifest.json",
           "evals/dev_100/dev_100_to_label.csv"]


@pytest.mark.data
@needs_data
def test_rerun_is_byte_identical_and_matches_committed_outputs(runs):
    base, _, _, _ = runs
    for rel in OUTPUTS:
        a = (base / "run1" / rel).read_bytes()
        assert a == (base / "run2" / rel).read_bytes(), rel
        assert a == (ROOT / rel).read_bytes(), f"committed {rel} differs"


@pytest.mark.data
@needs_data
def test_rerun_does_not_duplicate_db_rows(runs):
    _, db, _, _ = runs
    conn = intake.connect(db)
    q = lambda sql: conn.execute(sql).fetchone()[0]
    assert q("SELECT COUNT(*) FROM source_reviews") == 660_622
    assert q("SELECT COUNT(*) FROM texts") == 78_099
    assert q("SELECT COUNT(*) FROM work_items") == 78_099
    assert q("SELECT COUNT(*) FROM records") == 13
    conn.close()


@pytest.mark.data
@needs_data
def test_published_numbers_reproduced(runs):
    r = runs[2]
    assert r["ok"] and all(c["ok"] for c in r["checks"])
    p = r["profile"]
    assert (p["records"], p["empty_review_text"], p["missing_app_version"], p["duplicate_review_ids"],
            p["distinct_nonempty_texts"]) == (660_622, 13, 159_701, 0, 484_189)
    assert (p["first_review"], p["last_review"]) == ("2022-05-17 00:01:07", "2023-11-15 23:16:10")
    assert r["scope_counts"] == {"empty_text": 13, "out_of_scope": 560_609, "random_100k": 100_000}
    assert len(r["quarantined"]["review_ids"]) == 13
    names = {c["check"] for c in r["checks"]}
    for f in intake.DEV_FILES:
        assert {f + " sha256", f + " bytes"} <= names
        assert any(n.startswith(f + " equals seed ranks") for n in names)


@pytest.mark.data
@needs_data
def test_scope_measurements(runs):
    r = runs[2]
    assert r["random_100k"]["distinct_texts"] == 78_099
    assert r["cache_fill"]["out_of_scope_rows_completed"] == 137_942
    assert r["remaining_distinct_texts"] == 484_189 - 78_099
    curve = {s["top_distinct_texts"]: s["rows_completed"] for s in r["frequency_addon_curve"]}
    assert curve == {1_000: 6_916, 5_000: 17_894, 10_000: 26_577, 25_000: 41_577, 406_090: 422_667}
    # Every nonempty row is in scope, filled from cache, or completed by the full add-on.
    assert 100_000 + 137_942 + 422_667 == 660_609
    assert r["max_abs_month_diff_pp"] < 0.5


@pytest.mark.data
@needs_data
def test_dev_100_is_checkpoint_rows_101_to_200(runs):
    base = runs[0]
    with (RAW / "checkpoint_500.csv").open(encoding="utf-8-sig", newline="") as f:
        checkpoint = [row["review_id"] for row in csv.DictReader(f)]
    with (base / "run1" / "evals/dev_100/dev_100_to_label.csv").open(encoding="utf-8", newline="") as f:
        dev = list(csv.DictReader(f))
    assert [row["review_id"] for row in dev] == checkpoint[100:200]
    assert all(row["topic"] == "" for row in dev)
    with (RAW / "cost_100.csv").open(encoding="utf-8-sig", newline="") as f:
        assert not {row["review_id"] for row in csv.DictReader(f)} & {row["review_id"] for row in dev}


@pytest.mark.data
@needs_data
def test_ingestion_json_is_the_checker_profile(runs):
    base = runs[0]
    from pipeline.check_submission import profile
    assert json.loads((base / "run1/grading/ingestion.json").read_text(encoding="utf-8")) == profile(FULL)
