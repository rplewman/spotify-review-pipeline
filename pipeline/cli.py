"""Pipeline command line. Runs without Claude (PLAN §3.1).

Usage: python -m pipeline.cli ingest [--csv data/raw/spotify_reviews_18months.csv] [--db state/pipeline.db]
"""
import argparse
import sys
from pathlib import Path

from pipeline.agents import intake


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("ingest", help="A1: load, verify and scope the full CSV (no API calls)")
    p.add_argument("--csv", type=Path, default=Path("data/raw/spotify_reviews_18months.csv"))
    p.add_argument("--raw-dir", type=Path, default=Path("data/raw"), help="folder with manifest.json and the dev files")
    p.add_argument("--db", type=Path, default=Path("state/pipeline.db"))
    p.add_argument("--root", type=Path, default=Path("."), help="where outputs/, grading/ and evals/ are written")
    p.add_argument("--label-config", default=intake.PROVISIONAL_LABEL_CONFIG)
    args = parser.parse_args(argv)

    if args.command == "ingest":
        try:
            r = intake.ingest(args.csv, args.raw_dir, args.db, args.root, label_config=args.label_config)
        except intake.IntakeError as e:
            print(f"INGEST FAILED: {e}", file=sys.stderr)
            return 1
        s = r["random_100k"]
        print(f"ingest ok: {r['profile']['records']} rows, {sum(c['ok'] for c in r['checks'])}/{len(r['checks'])} checks passed")
        print(f"scope: {r['scope_counts']}")
        print(f"random_100k: {s['distinct_texts']} distinct texts ({s['duplicate_rows']} duplicate rows); "
              f"cache fill completes {r['cache_fill']['out_of_scope_rows_completed']} out-of-scope rows")
        for step in r["frequency_addon_curve"]:
            print(f"  add-on top {step['top_distinct_texts']}: +{step['rows_completed']} rows, "
                  f"{step['total_chars']} chars, coverage point {step['coverage_point_with_100k_and_fill']}")
        print(f"queue: {r['queue']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
