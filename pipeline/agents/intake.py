"""A1 Intake/Parser (PLAN §3.3, M1). Code only, no model calls.

Streams the full CSV with a multiline-safe parser, hashes every row with the checker's `row_sha`, loads all rows into
`source_reviews`, assigns the 100k scope in the official seed order, builds `texts` (distinct in-scope texts), quarantines
the empty texts, enqueues `work_items`, verifies the dev files, and writes deterministic reports. Re-running is idempotent.
"""
import csv
import hashlib
import json
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

from pipeline.check_submission import FIELDS, read_json, row_sha, sha

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "pipeline" / "db" / "schema.sql"
CHECKER = ROOT / "pipeline" / "check_submission.py"

SEED = "berkeley-fall-2026-assignment-5-v1"
SCOPE_SIZE = 100_000
VALID_RATINGS = {"1", "2", "3", "4", "5"}
GOLD_FIELDS = ["topic", "intent", "sentiment", "severity", "entities", "evidence_quote", "needs_review"]
EMPTY_REASON = "empty_review_text"
# Placeholder until the label_config is defined (M3) and frozen (M6); workers only claim items of their own config.
PROVISIONAL_LABEL_CONFIG = "provisional-v0"
ADDON_STEPS = (1_000, 5_000, 10_000, 25_000)

# Seed-order rank ranges (1-based, inclusive) of the official samples (prepare_dataset.py) and our dev_100 (§3.5).
SUBSETS = {
    "golden_50": (1, 50),
    "cost_100": (51, 150),
    "checkpoint_500": (51, 550),
    "dev_100": (151, 250),            # checkpoint_500 rows 101-200
    "analysis_10000": (51, 10_050),
    "random_100k": (1, SCOPE_SIZE),
}
DEV_FILES = {"golden_50_to_label.csv": "golden_50", "cost_100.csv": "cost_100",
             "checkpoint_500.csv": "checkpoint_500", "analysis_10000.csv": "analysis_10000"}

# Published facts (dataset README / manifest.json, PLAN §9.1). Ingest fails if any differs.
EXPECTED = {
    "records": 660_622,
    "file_bytes": 97_400_616,
    "file_sha256": "1fc85de68a304dd8978b537cfa58793d5f41cbaf417fa32cb53899f83a2fcef6",
    "empty_review_text": 13,
    "missing_app_version": 159_701,
    "duplicate_review_ids": 0,
    "distinct_nonempty_texts": 484_189,
    "first_review": "2022-05-17 00:01:07",
    "last_review": "2023-11-15 23:16:10",
}
WINDOW = ("2022-05-17", "2023-11-17")  # start inclusive, end exclusive
NONEMPTY_TOTAL = EXPECTED["records"] - EXPECTED["empty_review_text"]


class IntakeError(Exception):
    pass


def sha_text(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def seed_hash(review_id):
    return hashlib.sha256((SEED + ":" + review_id).encode()).hexdigest()


def read_rows(path):
    """Yield (record_number, row). Same parser settings as the checker; aborts with the record number on any defect."""
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, strict=True)
        if reader.fieldnames is None or tuple(reader.fieldnames[:len(FIELDS)]) != FIELDS:
            raise IntakeError(f"{path}: unexpected header {reader.fieldnames}")
        n = 0
        try:
            for row in reader:
                n += 1
                if None in row or any(v is None for v in row.values()):
                    raise IntakeError(f"{path}: malformed CSV record {n} (ending line {reader.line_num})")
                yield n, row
        except csv.Error as e:
            raise IntakeError(f"{path}: CSV parse error at record {n + 1} (line {reader.line_num}): {e}") from e


def connect(db_path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def apply_schema(conn):
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))


UPSERT_SOURCE = """
INSERT INTO source_reviews (review_id, csv_row, source_sha256, text_sha256, seed_hash, review_text, review_rating,
                            review_likes, app_version, review_timestamp, eligible, scope)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (review_id) DO UPDATE SET
    csv_row=excluded.csv_row, source_sha256=excluded.source_sha256, text_sha256=excluded.text_sha256,
    seed_hash=excluded.seed_hash, review_text=excluded.review_text, review_rating=excluded.review_rating,
    review_likes=excluded.review_likes, app_version=excluded.app_version,
    review_timestamp=excluded.review_timestamp, eligible=excluded.eligible, scope=excluded.scope
"""


def load_source(conn, csv_path, chunk=20_000):
    """Stream every row into source_reviews; return profile stats computed on the way."""
    ids, texts, months = set(), set(), Counter()
    stats = Counter(records=0, duplicate_review_ids=0, empty_review_text=0, missing_app_version=0,
                    outside_window=0, ineligible_nonempty=0)
    first = last = None
    batch = []
    with conn:
        for n, row in read_rows(csv_path):
            rid, text, ts = row["review_id"], row["review_text"], row["review_timestamp"]
            duplicate = rid in ids
            if duplicate:  # never silently overwrite a row (the dataset has none)
                raise IntakeError(f"duplicate review_id {rid!r} at record {n}")
            ids.add(rid)
            empty = not text.strip()
            stats["records"] += 1
            stats["empty_review_text"] += empty
            stats["missing_app_version"] += not row["app_version"].strip()
            stats["outside_window"] += not (WINDOW[0] <= ts[:10] < WINDOW[1])
            months[ts[:7]] += 1
            first = min(first, ts) if first else ts
            last = max(last, ts) if last else ts
            eligible = not empty and bool(rid) and row["review_rating"] in VALID_RATINGS
            stats["ineligible_nonempty"] += not empty and not eligible
            if not empty:
                texts.add(text)
            batch.append((rid, n, row_sha(row), sha_text(text), seed_hash(rid), text, row["review_rating"],
                          row["review_likes"], row["app_version"], ts, int(eligible),
                          "empty_text" if empty else None))
            if len(batch) >= chunk:
                conn.executemany(UPSERT_SOURCE, batch)
                batch.clear()
        conn.executemany(UPSERT_SOURCE, batch)
    stats["distinct_nonempty_texts"] = len(texts)
    return {**stats, "first_review": first, "last_review": last, "reviews_by_month": dict(sorted(months.items()))}


def assign_scope(conn, size=SCOPE_SIZE):
    """Rank eligible rows in the official seed order (ties by review_id, as prepare_dataset.py) and take the first `size`."""
    with conn:
        conn.execute("UPDATE source_reviews SET scope_rank = NULL")
        conn.execute("""
            WITH r AS (SELECT review_id, ROW_NUMBER() OVER (ORDER BY seed_hash, review_id) AS rk
                       FROM source_reviews WHERE eligible = 1)
            UPDATE source_reviews SET scope_rank = r.rk FROM r WHERE source_reviews.review_id = r.review_id""")
        conn.execute("""UPDATE source_reviews SET scope = CASE
                            WHEN scope_rank <= ? THEN 'random_100k'
                            WHEN scope = 'empty_text' THEN 'empty_text'
                            ELSE 'out_of_scope' END""", (size,))


def build_texts(conn, scopes=("random_100k",)):
    """One `texts` row per distinct in-scope text; the original is the lowest-ranked in-scope row with that text."""
    marks = ",".join("?" * len(scopes))
    with conn:
        conn.execute(f"""
            WITH ins AS (
                SELECT text_sha256, review_text, review_id, scope_rank,
                       ROW_NUMBER() OVER (PARTITION BY text_sha256 ORDER BY scope_rank) AS rn,
                       COUNT(*) OVER (PARTITION BY text_sha256) AS n_in
                FROM source_reviews WHERE scope IN ({marks})),
            tot AS (SELECT text_sha256, COUNT(*) AS n FROM source_reviews GROUP BY text_sha256)
            INSERT INTO texts (text_sha256, review_text, original_review_id, scope_rank, rows_in_scope, rows_total)
            SELECT ins.text_sha256, ins.review_text, ins.review_id, ins.scope_rank, ins.n_in, tot.n
            FROM ins JOIN tot ON tot.text_sha256 = ins.text_sha256 WHERE ins.rn = 1
            ON CONFLICT (text_sha256) DO UPDATE SET
                review_text=excluded.review_text, original_review_id=excluded.original_review_id,
                scope_rank=excluded.scope_rank, rows_in_scope=excluded.rows_in_scope, rows_total=excluded.rows_total""",
                     scopes)


def quarantine_empty(conn):
    with conn:
        conn.execute("""INSERT INTO records (review_id, source_sha256, status, reason)
                        SELECT review_id, source_sha256, 'quarantined', ? FROM source_reviews WHERE scope = 'empty_text'
                        ON CONFLICT (review_id) DO NOTHING""", (EMPTY_REASON,))


def enqueue(conn, stage, label_config):
    """Enqueue every distinct text for (stage, label_config). Idempotent: existing items keep their status."""
    with conn:
        cur = conn.execute("""INSERT INTO work_items (text_sha256, stage, label_config, scope_rank)
                              SELECT text_sha256, ?, ?, scope_rank FROM texts WHERE true
                              ON CONFLICT (text_sha256, stage, label_config) DO NOTHING""", (stage, label_config))
    return cur.rowcount


def ranked_rows(conn, lo, hi):
    return conn.execute("""SELECT review_id, source_sha256 FROM source_reviews
                           WHERE scope_rank BETWEEN ? AND ? ORDER BY scope_rank""", (lo, hi)).fetchall()


def verify_dev_files(conn, raw_dir, manifest):
    """Dev files must equal the seed-order slices row for row, and match manifest checksums."""
    checks = []
    for name, subset in DEV_FILES.items():
        path = Path(raw_dir) / name
        expected = manifest["files"][name]
        checks.append(check(f"{name} sha256", expected["sha256"], sha(path)))
        checks.append(check(f"{name} bytes", expected["bytes"], path.stat().st_size))
        lo, hi = SUBSETS[subset]
        file_rows = [(row["review_id"], row_sha(row)) for _, row in read_rows(path)]
        checks.append(check(f"{name} equals seed ranks {lo}-{hi}", True, file_rows == [tuple(r) for r in ranked_rows(conn, lo, hi)]))
    return checks


def check(name, expected, actual):
    return {"check": name, "expected": expected, "actual": actual, "ok": expected == actual}


def coverage(completed_nonempty, accounted=EXPECTED["records"]):
    """The checker's coverage-point formula, assuming ingestion.json matches and every row is accounted for."""
    return round(0.2 + 0.2 * accounted / EXPECTED["records"] + 0.6 * completed_nonempty / NONEMPTY_TOTAL, 4)


def scope_report(conn, full_months):
    q = lambda sql, *a: conn.execute(sql, a).fetchone()
    in_rows, in_texts = q("SELECT COUNT(*), COUNT(DISTINCT text_sha256) FROM source_reviews WHERE scope='random_100k'")
    text_chars = q("SELECT SUM(LENGTH(review_text)) FROM texts")[0]
    scope_months = dict(conn.execute("""SELECT SUBSTR(review_timestamp, 1, 7), COUNT(*) FROM source_reviews
                                        WHERE scope='random_100k' GROUP BY 1 ORDER BY 1""").fetchall())
    months = {}
    for m, n in full_months.items():
        s = scope_months.get(m, 0)
        full_share, scope_share = n / EXPECTED["records"], s / in_rows
        months[m] = {"full": n, "full_share": round(full_share, 6), "scope": s, "scope_share": round(scope_share, 6),
                     "diff_pp": round(100 * (scope_share - full_share), 3)}

    # Exact-text cache fill: out-of-scope nonempty rows whose text equals an in-scope text (§1.1).
    fill_rows, fill_texts = q("""SELECT COUNT(*), COUNT(DISTINCT s.text_sha256) FROM source_reviews s
                                 JOIN texts t ON t.text_sha256 = s.text_sha256 WHERE s.scope = 'out_of_scope'""")

    # Frequency add-on: remaining distinct texts ranked by how many out-of-scope rows share them.
    remaining = conn.execute("""SELECT COUNT(*) AS n, LENGTH(MIN(review_text)) AS chars, MIN(scope_rank) AS first_rank
                                FROM source_reviews WHERE scope = 'out_of_scope'
                                  AND text_sha256 NOT IN (SELECT text_sha256 FROM texts)
                                GROUP BY text_sha256 ORDER BY n DESC, first_rank ASC""").fetchall()
    base = in_rows + fill_rows
    curve, rows_acc, chars_acc = [], 0, 0
    steps = [s for s in ADDON_STEPS if s < len(remaining)] + [len(remaining)]
    for i, (n, chars, _) in enumerate(remaining, 1):
        rows_acc += n
        chars_acc += chars
        if i in steps:
            curve.append({"top_distinct_texts": i, "rows_completed": rows_acc, "total_chars": chars_acc,
                          "rows_per_text": round(rows_acc / i, 3),
                          "coverage_point_with_100k_and_fill": coverage(base + rows_acc)})
    curve[-1]["label"] = "full_corpus"
    singletons = sum(1 for n, _, _ in remaining if n == 1)

    return {
        "random_100k": {"rows": in_rows, "distinct_texts": in_texts, "duplicate_rows": in_rows - in_texts,
                        "distinct_text_chars": text_chars, "rank_range": [1, SCOPE_SIZE]},
        "month_distribution": months,
        "max_abs_month_diff_pp": max(abs(v["diff_pp"]) for v in months.values()),
        "cache_fill": {"out_of_scope_rows_completed": fill_rows, "distinct_texts_reused": fill_texts},
        "frequency_addon_curve": curve,
        "remaining_distinct_texts": len(remaining),
        "remaining_singleton_texts": singletons,
        "coverage_point_estimates": {
            "random_100k_only": coverage(in_rows),
            "random_100k_plus_cache_fill": coverage(base),
            "full_corpus": coverage(NONEMPTY_TOTAL),
        },
    }


def write_dev_100(conn, path):
    lo, hi = SUBSETS["dev_100"]
    rows = conn.execute(f"""SELECT {", ".join(FIELDS)} FROM source_reviews
                            WHERE scope_rank BETWEEN ? AND ? ORDER BY scope_rank""", (lo, hi)).fetchall()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(FIELDS) + GOLD_FIELDS, lineterminator="\n", restval="")
        writer.writeheader()
        writer.writerows(dict(zip(FIELDS, r)) for r in rows)
    return len(rows)


def dump_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")


def run_profile_helper(csv_path, out_path):
    """grading/ingestion.json must come from the provided helper (contract); run it unchanged."""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(CHECKER), "profile", "--full", str(csv_path), "--out", str(out_path)],
                   check=True, capture_output=True)
    return read_json(out_path)


def ingest(csv_path, raw_dir, db_path, root, label_config=PROVISIONAL_LABEL_CONFIG, stage="enrich"):
    root = Path(root)
    csv_path, raw_dir = Path(csv_path), Path(raw_dir)
    manifest = read_json(raw_dir / "manifest.json")

    conn = connect(db_path)
    try:
        apply_schema(conn)
        stats = load_source(conn, csv_path)
        assign_scope(conn)
        build_texts(conn)
        quarantine_empty(conn)
        enqueue(conn, stage, label_config)

        profile = run_profile_helper(csv_path, root / "grading" / "ingestion.json")
        file_sha, file_bytes = profile["file_sha256"], csv_path.stat().st_size
        checks = [
            check("records", EXPECTED["records"], stats["records"]),
            check("file_bytes", EXPECTED["file_bytes"], file_bytes),
            check("file_sha256", EXPECTED["file_sha256"], file_sha),
            check("file_sha256 = manifest", manifest["files"][csv_path.name]["sha256"], file_sha),
            check("empty_review_text", EXPECTED["empty_review_text"], stats["empty_review_text"]),
            check("missing_app_version", EXPECTED["missing_app_version"], stats["missing_app_version"]),
            check("duplicate_review_ids", EXPECTED["duplicate_review_ids"], stats["duplicate_review_ids"]),
            check("distinct_nonempty_texts", EXPECTED["distinct_nonempty_texts"], stats["distinct_nonempty_texts"]),
            check("first_review", EXPECTED["first_review"], stats["first_review"]),
            check("last_review", EXPECTED["last_review"], stats["last_review"]),
            check("rows outside window", 0, stats["outside_window"]),
            check("ineligible nonempty rows", 0, stats["ineligible_nonempty"]),
            check("reviews_by_month = manifest", manifest["reviews_by_month"], stats["reviews_by_month"]),
            check("checker profile counts", {k: stats[k] for k in profile["counts"]}, profile["counts"]),
            check("checker profile months", stats["reviews_by_month"], dict(sorted(profile["reviews_by_month"].items()))),
            check("eligible rows ranked", NONEMPTY_TOTAL,
                  conn.execute("SELECT COUNT(scope_rank) FROM source_reviews").fetchone()[0]),
            check("quarantined empty records", EXPECTED["empty_review_text"],
                  conn.execute("SELECT COUNT(*) FROM records WHERE reason = ?", (EMPTY_REASON,)).fetchone()[0]),
            *verify_dev_files(conn, raw_dir, manifest),
        ]
        scope = scope_report(conn, stats["reviews_by_month"])
        n_dev = write_dev_100(conn, root / "evals" / "dev_100" / "dev_100_to_label.csv")
        checks.append(check("dev_100 rows", 100, n_dev))
        queue = dict(conn.execute("""SELECT status, COUNT(*) FROM work_items WHERE stage = ? AND label_config = ?
                                     GROUP BY status""", (stage, label_config)).fetchall())
        checks.append(check("work_items = distinct in-scope texts", scope["random_100k"]["distinct_texts"], sum(queue.values())))
        empty_ids = [r[0] for r in conn.execute(
            "SELECT review_id FROM source_reviews WHERE scope = 'empty_text' ORDER BY csv_row")]
        scope_counts = dict(conn.execute("SELECT scope, COUNT(*) FROM source_reviews GROUP BY scope ORDER BY scope").fetchall())
    finally:
        conn.close()

    report = {
        "agent": "A1 intake",
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
        "profile": {k: stats[k] for k in ("records", "empty_review_text", "missing_app_version", "duplicate_review_ids",
                                         "distinct_nonempty_texts", "first_review", "last_review")},
        "scope_counts": scope_counts,
        "quarantined": {"reason": EMPTY_REASON, "review_ids": empty_ids},
        "queue": {"stage": stage, "label_config": label_config, "by_status": queue},
        **scope,
    }
    data_manifest = {
        "input": {"file": csv_path.name, "bytes": file_bytes, "sha256": file_sha,
                  "parsed_rows_sha256": profile["parsed_rows_sha256"]},
        "dev_files": {name: manifest["files"][name] for name in DEV_FILES},
        "parser": "csv.DictReader(strict=True), encoding utf-8-sig, newline=''; row hash = check_submission.row_sha",
        "text_identity": "sha256 of the exact review_text (UTF-8), no normalization",
        "scope": {"seed": SEED, "size": SCOPE_SIZE,
                  "method": "First N by SHA-256(seed + ':' + review_id) among unique nonempty valid-rating rows "
                            "(the official sampling order; ties by review_id)",
                  "subsets_rank_ranges": SUBSETS},
        "schema_sha256": sha(SCHEMA),
        "outputs": ["grading/ingestion.json", "outputs/ingestion_report.json", "evals/dev_100/dev_100_to_label.csv"],
    }
    dump_json(root / "outputs" / "ingestion_report.json", report)
    dump_json(root / "outputs" / "data_manifest.json", data_manifest)
    if not report["ok"]:
        failed = [c["check"] for c in checks if not c["ok"]]
        raise IntakeError(f"ingest verification failed: {failed}")
    return report
