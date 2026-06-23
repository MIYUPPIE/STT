# eval_tts.py — periodic eval. Calls the real YarnGPT API and checks that each
# sentence synthesizes to plausible, audible audio.
#
# Run: python3 services/tts/evals/eval_tts.py   (needs YARN_API_KEY in .env)
#
# Per case, PASS requires:
#   - ok:        synthesis succeeded
#   - audible:   RMS energy above a floor (not silence)
#   - plausible: duration scales with text length (0.03-0.20 s per character)
# Reports real-time factor (RTF) too. Threshold below.
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from services.tts.engine import build_tts                    # noqa: E402
from services.tts.contract import SynthRequest                # noqa: E402

PASS_THRESHOLD = 0.80
RMS_FLOOR = 0.005
SEC_PER_CHAR = (0.03, 0.20)     # plausible speaking rate band


def load_cases(path):
    with open(path) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def main():
    here = os.path.dirname(__file__)
    cases = load_cases(os.path.join(here, "cases.jsonl"))

    tts = build_tts()
    print(f"YarnGPT API url={tts.api_url} voice={tts.voice}")
    if not tts.api_key:
        print("\nYARN_API_KEY not set. Add it to .env, then re-run.")
        return 2

    rows, passes, rtfs = [], 0, []
    for c in cases:
        text = c["text"]
        res = tts.synth(SynthRequest(text=text))
        if not res.ok:
            rows.append((False, text, f"ERROR: {res.error}"))
            continue
        rms = float(np.sqrt(np.mean(res.audio**2))) if len(res.audio) else 0.0
        spc = res.duration_s / max(1, len(text))
        rtf = (res.latency_ms / 1000) / res.duration_s if res.duration_s else 0
        rtfs.append(rtf)
        audible = rms >= RMS_FLOOR
        plausible = SEC_PER_CHAR[0] <= spc <= SEC_PER_CHAR[1]
        ok = audible and plausible
        passes += ok
        rows.append((ok, text,
                     f"{res.duration_s:.2f}s rms={rms:.3f} {spc*1000:.0f}ms/ch "
                     f"RTF={rtf:.2f}"))

    print("\n  pass  text -> metrics")
    print("  ----  --------------")
    for ok, text, info in rows:
        print(f"  {'✓' if ok else '✗':>4}  {text}  |  {info}")

    n = len(cases)
    rate = passes / n
    print(f"\npass rate : {passes}/{n} = {rate:.0%}  (threshold {PASS_THRESHOLD:.0%})")
    if rtfs:
        print(f"median RTF: {sorted(rtfs)[len(rtfs)//2]:.2f}  (<1 = faster than real time)")
    ok = rate >= PASS_THRESHOLD
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
