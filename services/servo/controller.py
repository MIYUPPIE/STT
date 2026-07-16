# controller.py — the one object live_caption talks to. Combines the intent parser
# (rules + optional Grok) with the board client. handle(text) is the whole path:
# Yoruba caption -> Intent -> resolved angle -> board.
from __future__ import annotations

import sys

from .contract import MoveResult, NONE
from .intent import build_parser, IntentParser
from .client import ServoClient


class ServoController:
    def __init__(self, parser: IntentParser, client: ServoClient):
        self.parser = parser
        self.client = client

    def handle(self, text: str) -> MoveResult:
        intent = self.parser.parse(text)
        return self.client.move(intent)      # NONE/STOP resolve to a no-op move

    def health(self) -> bool:
        return self.client.health()

    @property
    def angle(self) -> int:
        return self.client.angle

    @property
    def last_error(self):
        return self.client.last_error


def build_controller() -> ServoController:
    return ServoController(build_parser(), ServoClient())


# ---------------- CLI: test the pipeline without a mic ----------------
def _main(argv):
    ctrl = build_controller()
    if "--health" in argv:
        ok = ctrl.health()
        print(f"host={ctrl.client.host} healthy={ok}")
        if not ok:
            print(f"reason: {ctrl.last_error}", file=sys.stderr)
        return 0 if ok else 1
    text = " ".join(a for a in argv if not a.startswith("--")).strip()
    if not text:
        print('usage: python -m services.servo.controller "yoruba command"',
              file=sys.stderr)
        print("       python -m services.servo.controller --health",
              file=sys.stderr)
        return 2
    res = ctrl.handle(text)
    tag = "moved" if res.moved else ("no-op" if res.ok else f"FAILED: {res.error}")
    print(f"[{res.intent.action}/{res.intent.source}] angle={res.angle} "
          f"({tag}, {res.latency_ms:.0f} ms)")
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
