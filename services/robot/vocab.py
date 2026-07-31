# vocab.py — Yoruba -> robot-movement matcher with fuzzy autocorrect. Whisper's
# tone-less output is noisy and splits words ("síwájú" -> "sí wá jù", "òsì" ->
# "ó sí") and mishears them ("dúró" -> "dulo", "ọ̀tún" -> "ko tun"). So instead of
# exact keywords we normalize, then edit-distance-match every candidate n-gram of
# the utterance against a small lexicon of canonical command forms (plus common
# mishearings) and take the best above a threshold. Deterministic, offline, free.
# Grok (intent.py) still catches anything the fuzzy layer can't.
from __future__ import annotations

import difflib
import re
import unicodedata

from . import config
from .contract import Intent, FORWARD, BACKWARD, LEFT, RIGHT, STOP, NONE

_NEG = "__neg__"   # internal marker: negation / "there isn't" -> not a command


def normalize(text: str) -> str:
    """Fold tone marks / sub-dots, lowercase, strip punctuation, collapse spaces.
    'Máa lọ síwájú!' -> 'maa lo siwaju'."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# Canonical despaced command forms -> action. The first of each group is the true
# word; the rest are common Whisper mishearings (a cheap autocorrect lexicon). The
# fuzzy match generalizes to variants not listed. Negation/"there isn't" forms map
# to _NEG so "kò sí" (kosi) can never win a left turn.
_FORMS: list[tuple[str, str]] = [
    # forward — síwájú  (avoid forms shorter than ~4 chars: they over-match, e.g.
    # "siwa" would catch "ṣe wà"/"sewa")
    ("siwaju", FORWARD), ("waju", FORWARD), ("wajin", FORWARD),
    ("siwajun", FORWARD), ("siwajin", FORWARD), ("siwadyin", FORWARD),
    ("siwad", FORWARD), ("losiwaju", FORWARD),
    # backward — sẹ́yìn / padà
    ("seyin", BACKWARD), ("sehin", BACKWARD), ("seyi", BACKWARD), ("pada", BACKWARD),
    ("padaseyin", BACKWARD), ("pada seyin", BACKWARD),
    # left — òsì
    ("osi", LEFT), ("osii", LEFT), ("yasiosi", LEFT), ("apaosi", LEFT),
    # right — ọ̀tún
    ("otun", RIGHT), ("otan", RIGHT), ("otin", RIGHT), ("yasiotun", RIGHT),
    ("apaotun", RIGHT),
    # stop — dúró
    ("duro", STOP), ("dulo", STOP), ("duru", STOP), ("duo", STOP),
    ("diduro", STOP), ("daduro", STOP),
    # negation / not a command — kò sí, bẹ́ẹ̀kọ́, rárá
    ("kosi", _NEG), ("kosiro", _NEG), ("beeko", _NEG), ("rara", _NEG),
]

_FAST = ["kiakia", "kiaki", "yara", "kanju", "sare", "yarayara"]  # fast
_SLOW = ["diedie", "die", "jeeje", "pele", "rora"]                 # slow

THRESHOLD = config.MATCH_THRESHOLD


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _candidates(tok_list: list[str], joined: str) -> set[str]:
    """Whole tokens + adjacent 2- and 3-grams (despaced) + the full joined string,
    so a command split across words is recoverable."""
    c = set(tok_list)
    c.add(joined)
    for i in range(len(tok_list) - 1):
        c.add(tok_list[i] + tok_list[i + 1])
    for i in range(len(tok_list) - 2):
        c.add(tok_list[i] + tok_list[i + 1] + tok_list[i + 2])
    return c


def _best(cands: set[str], forms) -> tuple[str | None, float]:
    best_val, best_score = None, 0.0
    for c in cands:
        for form, val in forms:
            s = _ratio(c, form)
            if s > best_score:
                best_score, best_val = s, val
    return best_val, best_score


def _fuzzy_speed(cands: set[str]) -> int | None:
    fscore = max((_ratio(c, w) for c in cands for w in _FAST), default=0.0)
    sscore = max((_ratio(c, w) for c in cands for w in _SLOW), default=0.0)
    if fscore >= THRESHOLD and fscore >= sscore:
        return config.FAST_SPEED
    if sscore >= THRESHOLD:
        return config.SLOW_SPEED
    return None


def rule_intent(text: str) -> Intent:
    """Fuzzy autocorrect parse. Returns source 'rule' on a confident match, else
    action NONE / source 'none' (caller may fall back to Grok).

    Precedence: STOP wins outright (dúró halts even in "má lọ, dúró"); a "má"
    negation anywhere is handed to Grok; otherwise the best fuzzy match above
    THRESHOLD wins, and negation forms ("kò sí") resolve to NONE."""
    norm = normalize(text)
    if not norm:
        return Intent(NONE, None, "none", text)
    tok_list = norm.split()
    joined = norm.replace(" ", "")
    cands = _candidates(tok_list, joined)

    action, score = _best(cands, _FORMS)
    if score < THRESHOLD:
        return Intent(NONE, None, "none", text)
    if action == STOP:                       # stop always wins, even under negation
        return Intent(STOP, None, "rule", text)
    if action == _NEG:                       # "kò sí" / "rárá" -> not a command
        return Intent(NONE, None, "none", text)
    if "ma" in tok_list:                     # "má ..." negation -> hand to Grok
        return Intent(NONE, None, "none", text)
    return Intent(action, _fuzzy_speed(cands), "rule", text)
