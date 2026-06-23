# eval_yoruba.py — periodic (paid) eval. Hits the real Grok API and scores
# Yoruba correction quality with deterministic heuristics, since we don't have
# hand-verified gold for every case.
#
# Run: python3 services/refine/evals/eval_yoruba.py   (needs XAI_API_KEY in .env)
#
# What it checks per case:
#   - ok:        the call succeeded
#   - no_refusal: not an English "I can't / as an AI" reply
#   - no_drift:  did not wander into English explanation
#   - (bonus) added_marks: produced Yoruba diacritics
# A case PASSES if ok and no_refusal and no_drift. Threshold below.
import json
import os
import sys
import time
import re
import unicodedata

# make `services` importable when run as a file
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from services.refine.refiner import GrokRefiner            # noqa: E402
from services.refine.contract import RefineRequest          # noqa: E402
from services.refine.prompt import PROMPT_VERSION           # noqa: E402

PASS_THRESHOLD = 0.70   # fraction of cases that must pass

_REFUSAL = re.compile(
    r"\b(i cannot|i can'?t|i'?m sorry|i am sorry|as an ai|i don'?t|unable to)\b",
    re.I,
)
# English words a drifting model emits; deliberately excludes Yoruba-common
# short tokens (ni, o, a, mo, se ...) to avoid false positives.
_ENGLISH = re.compile(
    r"\b(the|is|are|was|you|your|how|this|that|these|mean|means|translat\w*|"
    r"sentence|correct\w*|here|english|word|hello|good|morning)\b",
    re.I,
)
_MARKS = set("àáèéìíòóùúẹọṣÀÁÈÉÌÍÒÓÙÚẸỌṢ")
_COMBINING = {"̀", "́", "̣", "̄"}


def has_marks(s: str) -> bool:
    if any(ch in _MARKS for ch in s):
        return True
    return any(ch in _COMBINING for ch in unicodedata.normalize("NFD", s))


def load_cases(path):
    with open(path) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def main():
    here = os.path.dirname(__file__)
    cases = load_cases(os.path.join(here, "cases.jsonl"))

    r = GrokRefiner()
    print(f"prompt={PROMPT_VERSION} model={r.model} url={r.api_url}")
    print("Probing Grok...")
    if not r.health():
        print(f"\nGrok not usable: {r.last_error}")
        print("Check XAI_API_KEY in .env. (See services/refine/README.md)")
        return 2

    rows, passes, marked, lats = [], 0, 0, []
    for c in cases:
        raw = c["raw"]
        res = r.refine(RefineRequest(text=raw))
        refusal = bool(_REFUSAL.search(res.refined))
        drift = len(_ENGLISH.findall(res.refined)) > 1
        ok = res.ok and not refusal and not drift
        marks = has_marks(res.refined)
        passes += ok
        marked += marks
        lats.append(res.latency_ms)
        rows.append((ok, marks, raw, res.refined, res.latency_ms))

    print("\n  pass  marks  latency  raw -> refined")
    print("  ----  -----  -------  --------------")
    for ok, marks, raw, refined, ms in rows:
        print(f"  {'✓' if ok else '✗':>4}  {'✓' if marks else ' ':>5}  "
              f"{ms:5.0f}ms  {raw}  ->  {refined}")

    n = len(cases)
    rate = passes / n
    med = sorted(lats)[len(lats) // 2]
    print(f"\npass rate   : {passes}/{n} = {rate:.0%}  (threshold {PASS_THRESHOLD:.0%})")
    print(f"added marks : {marked}/{n} = {marked / n:.0%}")
    print(f"median lat. : {med:.0f} ms")

    ok = rate >= PASS_THRESHOLD
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
