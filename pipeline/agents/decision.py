"""A3 Decision (PLAN §3.3, §3.5, §3.8): build the Celeris System One request and read its answers.

Shape A (default): `state` = the label definitions (prompts/decision_state_v1.md, paid once per request) + up to 50
reviews tagged [R1]…[Rn]; 4 short questions per review ("Topic of [R7]") whose options are bare label names.
Shape B (flag, M5 comparison): one review per request; the definitions travel in each question's option descriptions.

Topic, intent, severity and sentiment only; every probability is kept. The answer for review n must arrive under
the keys `R<n>_<field>`. A key for a review we did not send (a foreign [R<n>]) makes the whole response suspect
(attribution can no longer be trusted) → the call is invalid → every review is retried once → quarantine.
A missing or malformed answer only invalidates that review.
"""
import re
from pathlib import Path

from pipeline.schema import INTENTS, TOPICS

PROMPT_VERSION = "prompt-v1"
STATE_PREFIX = (Path(__file__).resolve().parents[2] / "prompts" / "decision_state_v1.md").read_text(encoding="utf-8").strip()
FIELDS = ("topic", "intent", "severity", "sentiment")
SEVERITY_LEVELS = ["1 no problem", "2 minor or generic", "3 degraded", "4 blocked core task", "5 serious harm"]
SENTIMENT_LEVELS = ["very negative", "negative", "neutral", "positive", "very positive"]
REF = re.compile(r"\[R(\d+)\]")
KEY = re.compile(r"\AR(\d+)_(topic|intent|severity|sentiment)\Z")

# Shape B option descriptions (contract definitions, short).
TOPIC_DESC = {
    "access": "login, signup, password, account access",
    "usability": "navigation, controls, layout, queue/playlist management, ad interruptions",
    "playback": "playback failure, crashes, lag, connection failures, audio quality, resource use",
    "downloads": "downloading, saved music, offline listening, disappearing downloads",
    "catalog": "missing songs/artists, search/discovery, recommendations, lyrics availability",
    "billing": "price, charges, subscriptions, paywalls, premium entitlement, premium-only controls",
    "support": "contacting support and the support response",
    "other": "general praise/criticism, unrelated, no supported specific topic",
}
INTENT_DESC = {
    "cancellation": "explicitly leaving, uninstalling, cancelling, or threatening to (takes precedence)",
    "complaint": "negative experience, including mixed praise and criticism",
    "request": "a desired change without a reported failure",
    "praise": "positive, no complaint",
    "unclear": "boycott slogans, unrelated or meaningless text",
}


def neutralize(text):
    """A review that itself contains "[R7]" must not be able to pose as another review's tag."""
    return REF.sub(lambda m: f"(R{m.group(1)})", text)


def build_payload(texts, shape="A"):
    """The System One body minus `model` (the provider adds it)."""
    questions = {}
    if shape == "A":
        state = STATE_PREFIX + "\n\n## Reviews\n" + "\n\n".join(f"[R{n}] {neutralize(t)}" for n, t in enumerate(texts, 1))
        for n in range(1, len(texts) + 1):
            questions[f"R{n}_topic"] = {"type": "choice", "instructions": f"Topic of [R{n}]",
                                        "criteria": {t: None for t in TOPICS}}
            questions[f"R{n}_intent"] = {"type": "choice", "instructions": f"Intent of [R{n}]",
                                         "criteria": {i: None for i in INTENTS}}
            questions[f"R{n}_severity"] = {"type": "score", "instructions": f"Severity of [R{n}]",
                                           "criteria": SEVERITY_LEVELS}
            questions[f"R{n}_sentiment"] = {"type": "score", "instructions": f"Sentiment of [R{n}]",
                                            "criteria": SENTIMENT_LEVELS}
    elif shape == "B":
        if len(texts) != 1:
            raise ValueError("shape B sends exactly one review per request (use --batch-size 1)")
        state = f"[R1] {neutralize(texts[0])}"
        questions = {
            "R1_topic": {"type": "choice", "criteria": TOPIC_DESC,
                         "instructions": "Topic of the Spotify review [R1]: the problem with the highest severity "
                                         "(tie: first mentioned); positive review: first praised feature; general "
                                         "praise = other; a paid-plan mention alone is not billing; ads = usability."},
            "R1_intent": {"type": "choice", "criteria": INTENT_DESC,
                          "instructions": "Intent of [R1], first that applies in this order."},
            "R1_severity": {"type": "score", "criteria": SEVERITY_LEVELS,
                            "instructions": "Severity of [R1]'s chosen problem: 2 = no functional loss (price, ad "
                                            "volume), 3 = restricted/degraded, 4 = blocked core task, 5 = explicit "
                                            "financial/privacy/data harm only. Cancellation does not raise it."},
            "R1_sentiment": {"type": "score", "criteria": SENTIMENT_LEVELS, "instructions": "Sentiment of [R1]."},
        }
    else:
        raise ValueError(f"unknown shape {shape!r}")
    return {"state": state, "questions": questions}


def _probs(ans, allowed):
    p = ans.get("probabilities")
    if not isinstance(p, dict) or set(p) != set(allowed) or not all(isinstance(v, (int, float)) for v in p.values()):
        raise ValueError("bad probabilities")
    return {k: float(p[k]) for k in allowed}


def _choice(ans, allowed):
    if not isinstance(ans, dict) or ans.get("type") != "choice" or ans.get("choice") not in allowed:
        raise ValueError(f"choice {ans.get('choice') if isinstance(ans, dict) else ans!r} not allowed")
    return ans["choice"], _probs(ans, allowed)


def _score(ans, n_levels):
    if not isinstance(ans, dict) or ans.get("type") != "score" or not isinstance(ans.get("score"), (int, float)):
        raise ValueError("score answer malformed")
    if not 0 <= ans["score"] <= n_levels - 1:
        raise ValueError(f"score {ans['score']} out of range")
    return float(ans["score"]), _probs(ans, [str(i) for i in range(n_levels)])


def read_review(answers, n):
    """One review's decision from the answers dict; raises ValueError if anything is missing or malformed."""
    missing = [f for f in FIELDS if f"R{n}_{f}" not in answers]
    if missing:
        raise ValueError(f"missing answers for [R{n}]: {missing}")
    topic, topic_p = _choice(answers[f"R{n}_topic"], TOPICS)
    intent, intent_p = _choice(answers[f"R{n}_intent"], INTENTS)
    _, sev_p = _score(answers[f"R{n}_severity"], 5)
    sent_score, sent_p = _score(answers[f"R{n}_sentiment"], 5)
    level = max(range(5), key=lambda i: (sev_p[str(i)], -i))  # most probable level; ties → the lower severity
    return {
        "topic": topic, "intent": intent, "severity": level + 1,
        "sentiment": round(sent_score / 2 - 1, 2),  # probability-weighted level 0..4 → −1..1
        "probs": {"topic": topic_p, "intent": intent_p,
                  "severity": {str(i + 1): sev_p[str(i)] for i in range(5)},
                  "sentiment": {lvl: sent_p[str(i)] for i, lvl in enumerate(SENTIMENT_LEVELS)}},
        "confidence": {f: answers[f"R{n}_{f}"].get("confidence") for f in FIELDS},
    }


def parse(body, n_reviews):
    """({n: decision}, {n: invalid_reason}, notes). Raises ValueError when the response as a whole is unusable."""
    if not isinstance(body, dict) or not isinstance(body.get("answers"), dict):
        raise ValueError("unparseable output")
    answers = body["answers"]
    foreign = sorted(k for k in answers if not ((m := KEY.match(k)) and 1 <= int(m.group(1)) <= n_reviews))
    if foreign:
        raise ValueError(f"foreign answer keys {foreign[:5]}")
    ok, bad = {}, {}
    for n in range(1, n_reviews + 1):
        try:
            ok[n] = read_review(answers, n)
        except ValueError as e:
            bad[n] = str(e)
    return ok, bad, []
