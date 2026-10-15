"""M2 gate: the record schema validator on hand-written fixtures, the label files, the labeling tool, and the
golden/dev_100 firewalls (labels readable only by allowlisted scripts; no golden or dev_100 text in prompts or examples)."""
import csv
import importlib.util
import re
from pathlib import Path

import pytest
import yaml

from pipeline.schema import TOPICS, validate_labels, validate_record

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
TEXT = "Since the update the app crashes every time I open a playlist. Fix it!"


def completed(**kw):
    rec = {"review_id": "r1", "source_sha256": "ab" * 32, "status": "completed", "topic": "playback", "intent": "complaint",
           "sentiment": -0.5, "severity": 4, "entities": ["playlist"], "evidence_quote": "the app crashes every time",
           "needs_review": False, "label_config": "test-config"}
    rec.update(kw)
    return rec


# --- schema validator -------------------------------------------------------------------------------------------------

def test_valid_fixtures():
    assert validate_record(completed(), TEXT) == []
    assert validate_record(completed(entities=[], sentiment=1, cache_source_id="r0"), TEXT) == []
    q = {"review_id": "r2", "source_sha256": "cd" * 32, "status": "quarantined", "reason": "empty_review_text"}
    assert validate_record(q, "   ") == []
    assert validate_record(dict(q, reason="injection_suspected"), TEXT) == []


@pytest.mark.parametrize("change, fragment", [
    ({"topic": "billing/support"}, "topic"),
    ({"intent": "praise!"}, "intent"),
    ({"severity": 4.0}, "severity"),
    ({"severity": True}, "severity"),
    ({"severity": 6}, "severity"),
    ({"sentiment": 1.5}, "sentiment"),
    ({"sentiment": "0.5"}, "sentiment"),
    ({"sentiment": float("nan")}, "sentiment"),
    ({"needs_review": "false"}, "needs_review"),
    ({"entities": "playlist"}, "entities"),
    ({"entities": [""]}, "entities"),
    ({"evidence_quote": "the app crashes every  time"}, "substring"),
    ({"evidence_quote": ""}, "evidence_quote"),
    ({"label_config": ""}, "label_config"),
    ({"source_sha256": ""}, "source_sha256"),
    ({"cache_source_id": "r1"}, "cache_source_id"),
    ({"status": "done"}, "status"),
    ({"surprise": 1}, "unexpected"),
])
def test_invalid_fixtures(change, fragment):
    errors = validate_record(completed(**change), TEXT)
    assert errors and any(fragment in e for e in errors), errors


def test_quarantine_rules():
    q = {"review_id": "r2", "source_sha256": "cd" * 32, "status": "quarantined", "reason": " "}
    assert validate_record(q, TEXT)
    assert any("empty_review_text" in e for e in validate_record(dict(q, reason="blank"), ""))
    assert any("unexpected" in e for e in validate_record(dict(q, reason="x", topic="other"), TEXT))


# --- label files ------------------------------------------------------------------------------------------------------

def load_examples():
    return yaml.safe_load((ROOT / "labels" / "examples.yaml").read_text(encoding="utf-8"))


def test_examples_follow_schema():
    examples = load_examples()
    assert len(examples) >= 15
    for ex in examples:
        assert validate_labels(ex["label"], ex["text"]) == [], ex["review_id"]
        for key, vals in ex.get("accepted", {}).items():
            assert key in ("topic", "intent", "severity") and ex["label"][key] not in vals


def test_areas_cover_every_topic_once():
    areas = yaml.safe_load((ROOT / "labels" / "areas.yaml").read_text(encoding="utf-8"))
    mapped = [t for ts in areas["areas"].values() for t in ts] + areas["outside"]
    assert sorted(mapped) == sorted(TOPICS)
    assert set(areas["areas"]) == {"access", "usability", "playback", "billing/support"}


def test_keywords_cover_topics():
    kw = yaml.safe_load((ROOT / "labels" / "keywords.yaml").read_text(encoding="utf-8"))
    assert set(kw["topic_terms"]) == set(TOPICS) - {"other"}
    assert all(t == t.lower() for ts in kw["topic_terms"].values() for t in ts)
    assert kw["entity_terms"] and all(t == t.lower() for t in kw["entity_terms"])


# --- firewalls --------------------------------------------------------------------------------------------------------

# Only these files may reference the hand-label files. M6 adds its sweep (dev_100 only) and evaluation (golden only) scripts here.
LABEL_READERS = {"scripts/label_tool.py", "scripts/m6.py", "scripts/m6b.py"}  # M6b: held-out 30 scoring
LABEL_PATTERN = re.compile(r"golden_50_labeled|dev_100_labeled|heldout_30_labeled|evals[/\\]+golden|"
                           r"evals[/\\]+dev_100[/\\]+[^\"']*labeled")
CODE_DIRS = ("pipeline", "backend", "scripts", "prompts", "labels", "cost", "dashboard", "evals")


def code_files():
    for d in CODE_DIRS:
        for p in (ROOT / d).rglob("*"):
            if p.is_file() and p.suffix in {".py", ".md", ".txt", ".yaml", ".yml", ".json", ".html", ".js", ".sql"}:
                yield p


def test_only_allowlisted_code_references_hand_labels():
    offenders = [p.relative_to(ROOT).as_posix() for p in code_files()
                 if p.relative_to(ROOT).as_posix() not in LABEL_READERS
                 and "evals/golden/" not in p.relative_to(ROOT).as_posix()
                 and LABEL_PATTERN.search(p.read_text(encoding="utf-8", errors="ignore"))]
    assert offenders == []


def heldout_texts(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {" ".join(r["review_text"].split()) for r in csv.DictReader(f)}


def prompt_files():
    return [p for d in ("prompts", "labels") for p in (ROOT / d).rglob("*") if p.is_file()]


def assert_not_leaked(texts):
    # Short texts ("Great app") are too generic to count as leakage; anything of 20+ chars must not appear.
    long_texts = [t for t in texts if len(t) >= 20]
    for p in prompt_files():
        body = " ".join(p.read_text(encoding="utf-8", errors="ignore").split())
        leaked = [t[:40] for t in long_texts if t in body]
        assert leaked == [], f"{p.relative_to(ROOT)} contains held-out text: {leaked}"


def test_no_dev_100_text_in_prompts_or_examples():
    texts = heldout_texts(ROOT / "evals" / "dev_100" / "dev_100_to_label.csv")
    assert len(texts) >= 95       # whitespace-normalized, so near-duplicates collapse
    assert_not_leaked(texts)
    assert not {" ".join(ex["text"].split()) for ex in load_examples()} & texts


@pytest.mark.data
@pytest.mark.skipif(not (RAW / "golden_50_to_label.csv").exists(), reason="data/raw absent")
def test_no_golden_text_in_prompts_or_examples():
    texts = heldout_texts(RAW / "golden_50_to_label.csv")
    assert len(texts) >= 45
    assert_not_leaked(texts)
    assert not {" ".join(ex["text"].split()) for ex in load_examples()} & texts


@pytest.mark.data
@pytest.mark.skipif(not (RAW / "analysis_10000.csv").exists(), reason="data/raw absent")
def test_examples_come_from_development_files():
    with open(RAW / "analysis_10000.csv", encoding="utf-8-sig", newline="") as f:
        source = {r["review_id"]: r["review_text"] for r in csv.DictReader(f)}
    for ex in load_examples():
        assert source.get(ex["review_id"]) == ex["text"], ex["review_id"]


# --- labeling tool ----------------------------------------------------------------------------------------------------

def load_tool():
    spec = importlib.util.spec_from_file_location("label_tool", ROOT / "scripts" / "label_tool.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_label_tool_roundtrip(tmp_path):
    tool = load_tool()
    src, out = tmp_path / "to_label.csv", tmp_path / "out" / "labeled.csv"
    with open(src, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(tool.SOURCE_FIELDS + tool.LABEL_COLUMNS)
        w.writerow(["r1", TEXT, "1", "0", "8.1", "2023-01-01 10:00:00"] + [""] * 7)
        w.writerow(["r2", "Love the lyrics, ça marche", "5", "0", "", "2023-01-02 10:00:00"] + [""] * 7)
    store = tool.Store(src, out)
    labels = {"topic": "playback", "intent": "complaint", "severity": 4, "sentiment": -0.75, "entities": [" playlist ", ""],
              "evidence_quote": "the app crashes every time", "needs_review": False, "notes": "x",
              "accepted_topics": ["usability", "playback"], "accepted_intents": [], "accepted_severities": [3]}
    assert store.save("r1", dict(labels)) == []
    assert store.save("r2", dict(labels))                       # quote not in r2's text -> rejected, nothing written for r2
    assert store.save("r1", dict(labels, accepted_topics=["bogus"]))

    reloaded = tool.Store(src, out)
    items = {i["review_id"]: i["labels"] for i in reloaded.items()}
    assert items["r2"] is None
    got = items["r1"]
    assert (got["topic"], got["severity"], got["sentiment"], got["entities"]) == ("playback", 4, -0.75, ["playlist"])
    assert got["accepted_topics"] == ["usability"] and got["accepted_severities"] == [3]
    with open(out, encoding="utf-8", newline="") as f:
        row = next(csv.DictReader(f))
    assert row["accepted_topics"] == "playback|usability" and row["needs_review"] == "false"
    assert row["review_text"] == TEXT
