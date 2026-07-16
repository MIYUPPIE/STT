# client.py — drives the ESP32 servo over HTTP and owns the current-angle state.
#
# The board is a pure function (angle -> pulse); all motion logic lives here so it
# stays testable and offline-friendly: relative LEFT/RIGHT steps are resolved
# against the last commanded angle, absolute actions map to fixed angles, and the
# result is clamped to the servo's range before a single GET /servo?angle=N.
# The HTTP transport is injectable so gate tests never touch the network.
from __future__ import annotations

import time
import urllib.error
import urllib.request

from . import config
from .contract import (Intent, MoveResult,
                       LEFT, RIGHT, CENTER, OPEN, CLOSE, ANGLE, STOP, NONE)


def http_get(url, timeout):
    """GET returning (status, text). status 0 == request never reached the board."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:
        return 0, str(e)


def clamp(angle: int) -> int:
    return max(config.MIN_ANGLE, min(config.MAX_ANGLE, angle))


class ServoClient:
    def __init__(self, host=None, timeout=None, step=None, start=None,
                 health_timeout=None, transport=http_get):
        self.host = (host or config.HOST).rstrip("/")
        self.timeout = config.TIMEOUT if timeout is None else timeout
        self.health_timeout = (
            config.HEALTH_TIMEOUT if health_timeout is None else health_timeout)
        self.step = config.STEP if step is None else step
        self.angle = clamp(config.START_ANGLE if start is None else start)
        self.transport = transport
        self.last_error = None

    def resolve(self, intent: Intent) -> int | None:
        """Intent -> target angle, or None when the intent commands no move
        (STOP / NONE / a malformed ANGLE)."""
        a = intent.action
        if a == CENTER:
            return clamp(config.CENTER_ANGLE)
        if a == OPEN:
            return clamp(config.OPEN_ANGLE)
        if a == CLOSE:
            return clamp(config.CLOSE_ANGLE)
        if a == RIGHT:
            return clamp(self.angle + self.step)
        if a == LEFT:
            return clamp(self.angle - self.step)
        if a == ANGLE and intent.angle is not None:
            return clamp(intent.angle)
        return None                       # STOP, NONE, or ANGLE w/o a value

    def move(self, intent: Intent) -> MoveResult:
        target = self.resolve(intent)
        if target is None:                # nothing to command; hold position
            return MoveResult(intent=intent, angle=self.angle, moved=False,
                              ok=True, error=None, latency_ms=0.0)
        url = f"{self.host}/servo?angle={target}"
        t0 = time.perf_counter()
        status, text = self.transport(url, self.timeout)
        dt = (time.perf_counter() - t0) * 1000
        if status == 200:
            self.angle = target           # commit only on a confirmed move
            self.last_error = None
            return MoveResult(intent=intent, angle=target, moved=True, ok=True,
                              error=None, latency_ms=dt)
        err = text if status == 0 else f"http {status}: {text}"
        self.last_error = err
        return MoveResult(intent=intent, angle=self.angle, moved=False, ok=False,
                          error=err, latency_ms=dt)

    def health(self) -> bool:
        """Ping /servo with no params: the board reports its angle without moving.
        True only if the board answers 200 OK."""
        status, text = self.transport(f"{self.host}/servo", self.health_timeout)
        ok = status == 200 and text.startswith("OK")
        self.last_error = None if ok else (text if status == 0 else f"http {status}")
        return ok
