# controller.py — the one object live_caption talks to. Combines the intent parser
# (rules + optional Grok) with the serial link. handle(text) is the whole path:
# Yoruba caption -> Intent -> wire command -> board, plus the Yoruba reply to
# speak back.
from __future__ import annotations

import sys
import time

from . import config, responses
from .contract import MoveResult, NONE, STOP, LEFT, RIGHT, WIRE
from .intent import build_parser, IntentParser
from .link import RobotLink, open_serial
from .responses import response_for


class RobotController:
    def __init__(self, parser: IntentParser, link: RobotLink | None,
                 open_error: str | None = None):
        self.parser = parser
        self.link = link
        self.open_error = open_error

    def _ms_for(self, action: str) -> int:
        return config.TURN_MS if action in (LEFT, RIGHT) else config.DRIVE_MS

    def handle(self, text: str) -> MoveResult:
        intent = self.parser.parse(text)

        if intent.action == NONE:                     # not a command -> stay silent
            return MoveResult(intent, moved=False, ok=True, ack="", response="",
                              error=intent.error, latency_ms=0.0)
        if self.link is None:                         # board never opened
            return MoveResult(intent, moved=False, ok=False, ack="",
                              response=responses.FAILED, error=self.open_error,
                              latency_ms=0.0)

        speed = config.DEFAULT_SPEED if intent.speed is None else intent.speed
        ms = self._ms_for(intent.action)
        t0 = time.perf_counter()
        ok, ack, err = self.link.move(intent.action, speed, ms)
        dt = (time.perf_counter() - t0) * 1000
        if ok:
            return MoveResult(intent, moved=True, ok=True, ack=ack,
                              response=response_for(intent.action), error=None,
                              latency_ms=dt)
        return MoveResult(intent, moved=False, ok=False, ack=ack,
                          response=responses.FAILED, error=err, latency_ms=dt)

    def hold(self, text: str, on_tick=None) -> str:
        """Continuous drive: keep moving in the command's direction until
        interrupted (KeyboardInterrupt / Ctrl+C), then always stop. Returns a
        short status string. Resends a HOLD_MS move every HOLD_REFRESH seconds so
        the board's move-window never lapses; the firmware watchdog still halts the
        motors within ~2 s if this process dies without sending stop."""
        intent = self.parser.parse(text)
        if intent.action == NONE:
            return "not a movement command"
        if self.link is None:
            return f"board not open: {self.open_error}"
        if intent.action == STOP:
            self.link.move(STOP, 0, 0)
            return "stopped"

        speed = config.DEFAULT_SPEED if intent.speed is None else intent.speed
        line = f"{WIRE[intent.action]},{speed},{config.HOLD_MS}"
        try:
            while True:
                ok, ack, err = self.link.command(line)
                if on_tick:
                    on_tick(ok, ack, err)
                if not ok:
                    return f"board error: {err}"
                time.sleep(config.HOLD_REFRESH)
        except KeyboardInterrupt:
            return "stopped (Ctrl+C)"
        finally:
            self.link.move(STOP, 0, 0)      # always halt on the way out

    def health(self) -> bool:
        if self.link is None:
            self.last_error = self.open_error
            return False
        ok = self.link.ping()
        self.last_error = None if ok else self.link.last_error
        return ok

    def close(self):
        if self.link is not None:
            self.link.close()

    @property
    def port(self):
        return self.link.port if self.link is not None else "(not open)"

    @property
    def last_error(self):
        return self._last_error

    @last_error.setter
    def last_error(self, v):
        self._last_error = v


def build_controller() -> RobotController:
    """Factory: intent parser (rules + Grok), plus the serial link. If the board
    can't be opened, returns a controller whose health() reports why (the pipeline
    keeps captioning instead of crashing)."""
    parser = build_parser()
    try:
        link = RobotLink(open_serial())
        ctrl = RobotController(parser, link)
    except Exception as e:
        ctrl = RobotController(parser, None, open_error=str(e))
    ctrl.last_error = ctrl.open_error
    return ctrl


# ---------------- CLI: drive the robot without a mic ----------------
def _main(argv):
    ctrl = build_controller()
    if "--health" in argv:
        ok = ctrl.health()
        print(f"port={ctrl.port} healthy={ok}")
        if not ok:
            print(f"reason: {ctrl.last_error}", file=sys.stderr)
        ctrl.close()
        return 0 if ok else 1
    text = " ".join(a for a in argv if not a.startswith("--")).strip()
    if not text:
        print('usage: python -m services.robot.controller "yoruba command"',
              file=sys.stderr)
        print('       python -m services.robot.controller --hold "yoruba command"',
              file=sys.stderr)
        print("       python -m services.robot.controller --health",
              file=sys.stderr)
        return 2
    if "--hold" in argv:
        intent = ctrl.parser.parse(text)
        print(f"[{intent.action}] continuous drive on {ctrl.port} — Ctrl+C to stop")
        ticks = {"n": 0}

        def tick(ok, ack, err):
            ticks["n"] += 1
            sys.stdout.write(f"\r  driving... {ticks['n']} frames  last ack={ack!r}   ")
            sys.stdout.flush()

        status = ctrl.hold(text, on_tick=tick)
        print(f"\n{status}")
        ctrl.close()
        return 0
    res = ctrl.handle(text)
    tag = "moved" if res.moved else ("no-op" if res.ok else f"FAILED: {res.error}")
    print(f"[{res.intent.action}/{res.intent.source}] {tag}  ack={res.ack!r}  "
          f"say={res.response!r}  ({res.latency_ms:.0f} ms)")
    ctrl.close()
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
