# eval_brain.py — periodic (paid) eval. Hits the real Grok API and scores
# whether the brain gives a usable spoken Yoruba reply, since we don't have
# hand-verified gold answers for open conversation.
#
# Run: python3 services/brain/evals/eval_brain.py   (needs XAI_API_KEY in .env)
#
# What it checks per case (each case a fresh conversation):
#   - ok:         the call succeeded and produced a non-empty reply
#   - no_refusal: not an English "I can't / as an AI" reply
#   - in_yoruba:  reply looks like Yoruba (diacritics or common tokens) and is
#                 not an English drift
#   - speakable:  no markdown/emoji survived into the reply (TTS-safe)
# A case PASSES if ok and no_refusal and in_yoruba and speakable. Threshold below.
import json
import os
import re
import sys
import unicodedata

# make `services` importable when run as a file
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from services.brain.brain import GrokBrain                 # noqa: E402
from services.brain.contract import ChatRequest            # noqa: E402
from services.brain.prompt import PROMPT_VERSION           # noqa: E402

PASS_THRESHOLD = 0.70   # fraction of cases that must pass

_REFUSAL = re.compile(
    r"\b(i cannot|i can'?t|i'?m sorry|i am sorry|as an ai|i don'?t|unable to|"
    r"i'?m unable|language model)\b",
    re.I,
)
# English words a drifting model emits; excludes Yoruba-common short tokens
# (ni, o, a, mo, se, wa ...) to avoid false positives.
_ENGLISH = re.compile(
    r"\b(the|is|are|was|were|you|your|how|what|this|that|these|those|mean|means|"
    r"hello|good|morning|name|water|music|story|study|think|about|today|"
    r"english|sorry|sure|here|please|thanks|thank)\b",   # not "yoruba": a
    re.I,                                                 # language name occurs
)                                                        # in legit Yoruba text
# markdown/emoji that should never reach TTS
_UNSPEAKABLE = re.compile(
    r"[`*#_>]|\[[^\]]*\]\([^)]*\)|"
    r"[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]"
)
_MARKS = set("àáèéìíòóùúẹọṣÀÁÈÉÌÍÒÓÙÚẸỌṢ")
_COMBINING = {"̀", "́", "̣", "̄"}
_YO_TOKENS = re.compile(
    r"\b(mo|wà|wa|ẹ|e|ọ|o|ni|kí|ki|àwa|èmi|jọ̀|ṣé|se|dáadáa|báwo|bawo|"
    r"olúwa|ọlọ́run|ọmọ|ilé|ile|orúkọ|oruko)\b",
    re.I,
)


def has_marks(s: str) -> bool:
    if any(ch in _MARKS for ch in s):
        return True
    return any(ch in _COMBINING for ch in unicodedata.normalize("NFD", s))


def looks_yoruba(s: str) -> bool:
    english = len(_ENGLISH.findall(s))
    yoruba = has_marks(s) or bool(_YO_TOKENS.search(s))
    return yoruba and english <= 1


def load_cases(path):
    with open(path) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def main():
    here = os.path.dirname(__file__)
    cases = load_cases(os.path.join(here, "cases.jsonl"))

    probe = GrokBrain()
    print(f"prompt={PROMPT_VERSION} model={probe.model} url={probe.api_url}")
    print("Probing Grok...")
    if not probe.health():
        print(f"\nGrok not usable: {probe.last_error}")
        print("Check XAI_API_KEY in .env. (See services/brain/README.md)")
        return 2

    rows, passes, lats = [], 0, []
    for c in cases:
        user = c["user"]
        b = GrokBrain()                    # fresh conversation per case
        res = b.respond(ChatRequest(text=user))
        refusal = bool(_REFUSAL.search(res.reply))
        yoruba = looks_yoruba(res.reply)
        speakable = not bool(_UNSPEAKABLE.search(res.reply))
        ok = res.ok and bool(res.reply) and not refusal and yoruba and speakable
        passes += ok
        lats.append(res.latency_ms)
        rows.append((ok, user, res.reply, res.latency_ms))

    print("\n  pass  latency  user -> reply")
    print("  ----  -------  -------------")
    for ok, user, reply, ms in rows:
        print(f"  {'✓' if ok else '✗':>4}  {ms:5.0f}ms  {user}  ->  {reply}")

    n = len(cases)
    rate = passes / n
    med = sorted(lats)[len(lats) // 2]
    print(f"\npass rate   : {passes}/{n} = {rate:.0%}  (threshold {PASS_THRESHOLD:.0%})")
    print(f"median lat. : {med:.0f} ms")

    ok = rate >= PASS_THRESHOLD
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
