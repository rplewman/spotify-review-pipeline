"""A6 Extractor (PLAN §3.3), code only — never a model call.

- evidence_quote: the shortest sentence of the review that contains a term from the chosen topic's keyword list
  (labels/keywords.yaml, matched case-insensitively on word boundaries); ties → the first. No match (or topic
  `other`) → the full text. The quote is always a slice of the original text, so it is an exact substring.
- entities: feature / surface / third-party terms from the fixed list that are written in the text, as written
  (longest match first, no overlaps, in order of appearance, de-duplicated case-insensitively).
"""
import re
from functools import lru_cache
from pathlib import Path

import yaml

KEYWORDS = Path(__file__).resolve().parents[2] / "labels" / "keywords.yaml"
EXTRACT_VERSION = "extract-code-v1"
SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)", re.M)


def _pattern(terms):
    alts = sorted({t.lower() for t in terms}, key=len, reverse=True)
    body = "|".join(re.escape(t).replace(r"\ ", r"\s+").replace("'", "['’]") for t in alts)
    return re.compile(rf"(?<!\w)(?:{body})(?!\w)", re.I)


@lru_cache(maxsize=1)
def _lists():
    data = yaml.safe_load(KEYWORDS.read_text(encoding="utf-8"))
    return ({topic: _pattern(terms) for topic, terms in data["topic_terms"].items()}, _pattern(data["entity_terms"]),
            data["version"])


def sentences(text):
    """(start, end) spans of the sentences in `text`, stripped of surrounding whitespace."""
    spans = []
    for m in SENTENCE.finditer(text):
        s, e = m.span()
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if e > s:
            spans.append((s, e))
    return spans


def evidence_quote(text, topic):
    pattern = _lists()[0].get(topic)
    if pattern is not None:
        hits = [(e - s, s, e) for s, e in sentences(text) if pattern.search(text, s, e)]
        if hits:
            _, s, e = min(hits)
            return text[s:e]
    return text


def entities(text):
    seen, out = set(), []
    for m in _lists()[1].finditer(text):
        key = re.sub(r"\s+", " ", m.group(0).lower())
        if key not in seen:
            seen.add(key)
            out.append(m.group(0))
    return out


def keywords_version():
    return _lists()[2]
