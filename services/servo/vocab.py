# vocab.py — the deterministic Yoruba -> servo-intent matcher (latent/deterministic
# split: this is the deterministic half). Whisper output is usually missing tone
# marks and sub-dots, so everything is matched on a *normalized* form: diacritics
# stripped, lowercased, punctuation dropped, whitespace collapsed. Same caption
# in -> same intent out, offline and free. Grok (intent.py) only handles what the
# rules here miss.
from __future__ import annotations

import re
import unicodedata

from .contract import Intent, LEFT, RIGHT, CENTER, OPEN, CLOSE, ANGLE, STOP, NONE


def normalize(text: str) -> str:
    """Fold tone marks / sub-dots, lowercase, strip punctuation, collapse spaces.
    'Yíjú sí ọ̀tún!' -> 'yiju si otun'."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))  # drop tone marks
    t = t.lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)     # keep letters/digits only
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _has(tokens: set[str], *words: str) -> bool:
    return any(w in tokens for w in words)


# Direction / action keyword sets, in normalized (diacritic-free) form. Chosen to
# be distinct enough that everyday speech does not trigger them by accident.
RIGHT_WORDS = {"otun", "otunla"}                 # ọ̀tún — right
LEFT_WORDS = {"osi"}                              # òsì — left
CENTER_WORDS = {"aarin", "arin", "aaarin"}       # àárín — middle
STOP_WORDS = {"duro", "diduro", "durosi"}        # dúró — stop / hold
# NOTE: open/close are deliberately NOT in the offline matcher. In bare, tone-less
# Yoruba they are ambiguous — títì (closed) collides with títí (continuously), ṣí
# (open) with sí (to/at) — so matching them here would false-trigger on ordinary
# speech. OPEN/CLOSE intents are handled by the Grok fallback, which has context.
# Verbs that mark a movement command but need a direction to be actionable.
MOVE_WORDS = {"yi", "yiju", "yipada", "yiju", "ya", "pada", "sun", "gbe"}
# "degree(s)" spoken/transcribed forms.
DEGREE_WORDS = {"digiri", "digri", "digirii", "degiri", "degere", "digree",
                "degree", "degrees", "digirisi"}

# Yoruba cardinals for the common servo angles (multiples the horn actually uses),
# normalized. Grok covers anything outside this curated set.
NUMBER_WORDS = {
    "odo": 0, "eero": 0,
    "ewa": 10, "mewa": 10,
    "ogun": 20,
    "ogbon": 30,
    "ogoji": 40, "ogogi": 40,
    "aadota": 50, "adota": 50,
    "ogota": 60, "ogata": 60,
    "aadorin": 70, "adorin": 70,
    "ogorin": 80, "ogarin": 80,
    "aadorun": 90, "adorun": 90, "aadorunun": 90,
    "ogorun": 100, "ogorunun": 100,
    "ogofa": 120, "ogafa": 120,
    "aadosan": 150, "adosan": 150,
    "ogosan": 180, "ogasan": 180,
}


def _find_degree(tokens: list[str], norm: str) -> int | None:
    """Pull an explicit angle out of a caption: Arabic digits first, then a Yoruba
    number word. Returns None if none present."""
    m = re.search(r"\b(\d{1,3})\b", norm)
    if m:
        return int(m.group(1))
    for tok in tokens:
        if tok in NUMBER_WORDS:
            return NUMBER_WORDS[tok]
    return None


def rule_intent(text: str) -> Intent:
    """Deterministic parse. Returns an Intent with source 'rule' on a confident
    match, else action NONE with source 'none' (caller may fall back to Grok).

    Precedence: an explicit angle ('... digiri') wins over a bare direction, so
    'yà sí ọ̀tún ní ọgbọ̀n digiri' is treated as 'go to 30', not 'step right'.
    """
    norm = normalize(text)
    if not norm:
        return Intent(action=NONE, angle=None, source="none", text=text)
    tokens = norm.split()
    tokset = set(tokens)

    # Explicit angle: a degree word present -> ANGLE to the stated number.
    if _has(tokset, *DEGREE_WORDS):
        deg = _find_degree(tokens, norm)
        if deg is not None:
            return Intent(action=ANGLE, angle=deg, source="rule", text=text)

    if _has(tokset, *STOP_WORDS):
        return Intent(action=STOP, angle=None, source="rule", text=text)
    if _has(tokset, *CENTER_WORDS):
        return Intent(action=CENTER, angle=None, source="rule", text=text)
    if _has(tokset, *RIGHT_WORDS):
        return Intent(action=RIGHT, angle=None, source="rule", text=text)
    if _has(tokset, *LEFT_WORDS):
        return Intent(action=LEFT, angle=None, source="rule", text=text)

    # A bare number with a movement verb but no unit -> treat as an angle.
    if _has(tokset, *MOVE_WORDS):
        deg = _find_degree(tokens, norm)
        if deg is not None:
            return Intent(action=ANGLE, angle=deg, source="rule", text=text)

    return Intent(action=NONE, angle=None, source="none", text=text)
