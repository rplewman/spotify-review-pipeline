"""Synthetic test corpora (PLAN §5 M7). Every row is marked: review_id starts with SYNTH-, and the corpus lives in its own
DB (never state/pipeline.db), so synthetic records can't reach business aggregates or the grading export."""
import csv
from pathlib import Path

from pipeline.agents import intake
from pipeline.check_submission import FIELDS

PREFIX = "SYNTH-"


def default_texts(n):
    """Plain review-like texts, ~1 in 6 an exact duplicate (to exercise cache reuse)."""
    out = []
    for i in range(n):
        if i % 6 == 5:
            out.append(f"Synthetic duplicate {i % 4}: ads interrupt every song")
        else:
            topic = ("the app crashes on start", "songs skip randomly", "premium charged twice",
                     "offline downloads vanish", "love the playlists")[i % 5]
            out.append(f"Synthetic review {i}: {topic}.")
    return out


def build_db(db_path, texts, label_config=None, stage="enrich", tmp_dir=None):
    """Ingest `texts` as a synthetic CSV into a fresh DB (all rows in scope), quarantine empties, and enqueue."""
    db_path = Path(db_path)
    if db_path.exists():
        raise SystemExit(f"refused: {db_path} exists; synthetic DBs are built fresh")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path = Path(tmp_dir or db_path.parent) / (db_path.stem + ".csv")
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(FIELDS)
        for i, text in enumerate(texts):
            w.writerow([f"{PREFIX}{i:05d}", text, str(1 + i % 5), "0", "9.0.0", f"2026-0{1 + i % 9}-01 12:00:00"])
    conn = intake.connect(db_path)
    intake.apply_schema(conn)
    intake.load_source(conn, csv_path)
    intake.assign_scope(conn, size=len(texts))
    intake.build_texts(conn)
    intake.quarantine_empty(conn)
    if label_config:
        intake.enqueue(conn, stage, label_config)
    conn.close()
    return db_path
