# eval_servo.py — periodic (paid) eval. Runs the FULL intent parser (offline rules
# + Grok fallback) over labeled Yoruba commands and scores exact intent match.
# This is the proof the Yoruba -> servo mapping generalizes past the gate tests'
# fixed strings, including the open/close/negation cases that only Grok resolves.
#
# Run: python3 services/servo/evals/eval_servo.py   (needs XAI_API_KEY in .env)
#
# A case PASSES if the parsed action equals the expected action AND, for "angle"
# cases, the parsed degree equals the expected degree. Threshold below.
import json
import os
import sys

# make `services` importable when run as a file
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from services.servo.intent import build_parser        # noqa: E402
from services.servo.contract import ANGLE             # noqa: E402
from services.servo import config                     # noqa: E402

PASS_THRESHOLD = 0.85   # fraction of cases that must map to the right intent


def load_cases(path):
    with open(path) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def main():
    here = os.path.dirname(__file__)
    cases = load_cases(os.path.join(here, "cases.jsonl"))

    if not config.API_KEY:
        print("XAI_API_KEY not set. open/close/negation cases need the Grok "
              "fallback. Add it to .env. (See services/servo/README.md)")
        return 2

    parser = build_parser()
    print(f"model={config.MODEL} grok_fallback={parser.grok is not None}\n")

    rows, passes = [], 0
    for c in cases:
        exp_action = c["action"]
        exp_angle = c.get("angle")
        intent = parser.parse(c["text"])
        ok = intent.action == exp_action
        if ok and exp_action == ANGLE:
            ok = intent.angle == exp_angle
        passes += ok
        rows.append((ok, c["text"], exp_action, exp_angle, intent))

    print("  pass  source  expected        got")
    print("  ----  ------  --------        ---")
    for ok, text, ea, eg, intent in rows:
        exp = ea + (f"({eg})" if eg is not None else "")
        got = intent.action + (f"({intent.angle})" if intent.angle is not None else "")
        print(f"  {'✓' if ok else '✗':>4}  {intent.source:>6}  {exp:<14}  {got:<10}  {text}")

    n = len(cases)
    rate = passes / n
    print(f"\npass rate: {passes}/{n} = {rate:.0%}  (threshold {PASS_THRESHOLD:.0%})")
    ok = rate >= PASS_THRESHOLD
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
