# eval_robot.py — periodic (paid) eval. Runs the FULL intent parser (offline rules
# + Grok fallback) over labeled Yoruba commands and scores exact intent match. No
# board or serial port needed — this measures language understanding only. It is
# the proof the Yoruba -> movement mapping generalizes past the gate tests, incl.
# the negation/paraphrase cases only Grok resolves.
#
# Run: python3 services/robot/evals/eval_robot.py   (needs XAI_API_KEY in .env)
#
# A case PASSES if action matches and, when a speed label is given, the resolved
# speed matches too. Threshold below.
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from services.robot.intent import build_parser          # noqa: E402
from services.robot import config                        # noqa: E402

PASS_THRESHOLD = 0.85

SPEED_LABEL = {config.FAST_SPEED: "fast", config.SLOW_SPEED: "slow", None: None}


def load_cases(path):
    with open(path) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def main():
    here = os.path.dirname(__file__)
    cases = load_cases(os.path.join(here, "cases.jsonl"))

    if not config.API_KEY:
        print("XAI_API_KEY not set. The negation/paraphrase cases need the Grok "
              "fallback. Add it to .env. (See services/robot/README.md)")
        return 2

    parser = build_parser()
    print(f"model={config.MODEL} grok_fallback={parser.grok is not None}\n")

    rows, passes = [], 0
    for c in cases:
        intent = parser.parse(c["text"])
        ok = intent.action == c["action"]
        if ok and c.get("speed"):
            ok = SPEED_LABEL.get(intent.speed) == c["speed"]
        passes += ok
        rows.append((ok, c["text"], c["action"], c.get("speed"), intent))

    print("  pass  source  expected        got")
    print("  ----  ------  --------        ---")
    for ok, text, ea, es, intent in rows:
        exp = ea + (f"/{es}" if es else "")
        got = intent.action + (f"/{SPEED_LABEL.get(intent.speed)}"
                               if intent.speed is not None else "")
        print(f"  {'✓' if ok else '✗':>4}  {intent.source:>6}  {exp:<14}  {got:<12}  {text}")

    n = len(cases)
    rate = passes / n
    print(f"\npass rate: {passes}/{n} = {rate:.0%}  (threshold {PASS_THRESHOLD:.0%})")
    ok = rate >= PASS_THRESHOLD
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
