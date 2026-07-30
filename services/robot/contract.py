# contract.py — boundary between live_caption (and any other caller) and the robot
# service. Both sides import these types; neither reaches inside.
from __future__ import annotations

from dataclasses import dataclass

# Movement vocabulary the parser emits and the serial link consumes. FORWARD/
# BACKWARD drive both wheels; LEFT/RIGHT spin in place; STOP halts now; NONE means
# the caption was not a robot command and must be ignored.
FORWARD = "forward"
BACKWARD = "backward"
LEFT = "left"
RIGHT = "right"
STOP = "stop"
NONE = "none"

ACTIONS = {FORWARD, BACKWARD, LEFT, RIGHT, STOP, NONE}

# Single-char wire codes the firmware understands (see esp32s3_robot.ino).
WIRE = {FORWARD: "F", BACKWARD: "B", LEFT: "L", RIGHT: "R", STOP: "S"}


@dataclass
class Intent:
    """A parsed robot command. `speed` is None when the speaker didn't qualify it
    (fast/slow), in which case the controller uses the configured default."""
    action: str          # one of ACTIONS
    speed: int | None    # 0-255, or None for default
    source: str          # "rule" | "grok" | "none" | "error"
    text: str            # the raw caption this came from
    error: str | None = None


@dataclass
class MoveResult:
    """Outcome of driving the robot for one intent, plus the Yoruba reply to
    speak/print back ('' when there is nothing to say)."""
    intent: Intent
    moved: bool          # did the robot receive a drive command
    ok: bool             # did the serial exchange succeed (or correctly no-op)
    ack: str             # the board's ACK line (e.g. "OK:F:200:900")
    response: str        # Yoruba confirmation for the user ('' if silent)
    error: str | None
    latency_ms: float
