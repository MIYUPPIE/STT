# vocab.py — deterministic Yoruba -> robot-movement matcher (the deterministic
# half of the split). Whisper output is usually tone-less, so everything is
# matched on a normalized form: diacritics stripped, lowercased, punctuation
# dropped. Same caption in -> same intent out, offline and free. Grok (intent.py)
# only handles what these rules miss.
#
# Robot movement words are far less ambiguous than the servo's open/close, so all
# five actions live here: forward, backward, left, right, stop.
from __future__ import annotations

import re
import unicodedata

from . import config
from .contract import Intent, FORWARD, BACKWARD, LEFT, RIGHT, STOP, NONE


def normalize(text: str) -> str:
    """Fold tone marks / sub-dots, lowercase, strip punctuation, collapse spaces.
    'Máa lọ síwájú!' -> 'maa lo siwaju'."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# Normalized (diacritic-free) keyword sets. Matched as whole tokens so common
# words don't false-trigger (e.g. 'kò sí' -> 'ko si' never hits 'osi').
FORWARD_WORDS = {"siwaju", "waju", "sisiwaju"}      # síwájú — ahead
BACKWARD_WORDS = {"seyin", "eyin", "pada", "padasehin", "sehin"}  # sẹ́yìn / padà — back
LEFT_WORDS = {"osi"}                                # òsì — left
RIGHT_WORDS = {"otun"}                              # ọ̀tún — right
STOP_WORDS = {"duro", "diduro", "durode", "dawoduro"}  # dúró — stop

# Speed qualifiers.
FAST_WORDS = {"kiakia", "yara", "kanju", "sare"}    # kíákíá / yára — fast
SLOW_WORDS = {"diedie", "jeeje", "pele", "rora"}     # díẹ̀díẹ̀ / jẹ́ẹ́jẹ́ — slow


def _has(tokens: set[str], words: set[str]) -> bool:
    return not tokens.isdisjoint(words)


def _speed(tokens: set[str]) -> int | None:
    if _has(tokens, FAST_WORDS):
        return config.FAST_SPEED
    if _has(tokens, SLOW_WORDS):
        return config.SLOW_SPEED
    return None


def rule_intent(text: str) -> Intent:
    """Deterministic parse. Returns source 'rule' on a confident match, else action
    NONE / source 'none' (caller may fall back to Grok).

    STOP is checked first: 'má lọ, dúró' must halt even though 'lọ' (go) is present.
    """
    norm = normalize(text)
    if not norm:
        return Intent(NONE, None, "none", text)
    tokens = set(norm.split())

    if _has(tokens, STOP_WORDS):
        return Intent(STOP, None, "rule", text)
    speed = _speed(tokens)
    if _has(tokens, FORWARD_WORDS):
        return Intent(FORWARD, speed, "rule", text)
    if _has(tokens, BACKWARD_WORDS):
        return Intent(BACKWARD, speed, "rule", text)
    if _has(tokens, LEFT_WORDS):
        return Intent(LEFT, speed, "rule", text)
    if _has(tokens, RIGHT_WORDS):
        return Intent(RIGHT, speed, "rule", text)
    return Intent(NONE, None, "none", text)
