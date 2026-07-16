# contract.py — boundary between live_caption (and any other caller) and the
# servo service. Both sides import these types; neither reaches inside.
from __future__ import annotations

from dataclasses import dataclass

# The intent vocabulary the parser emits and the client consumes. Relative moves
# (LEFT/RIGHT) step by config.STEP from the current angle; absolute moves
# (CENTER/OPEN/CLOSE/ANGLE) set a fixed angle; STOP holds; NONE means the caption
# was not a servo command and must be ignored.
LEFT = "left"
RIGHT = "right"
CENTER = "center"
OPEN = "open"
CLOSE = "close"
ANGLE = "angle"       # go to an explicit degree (Intent.angle is set)
STOP = "stop"
NONE = "none"

ACTIONS = {LEFT, RIGHT, CENTER, OPEN, CLOSE, ANGLE, STOP, NONE}


@dataclass
class Intent:
    """A parsed servo command. `angle` is set only for ANGLE."""
    action: str          # one of ACTIONS
    angle: int | None    # target degrees for ANGLE, else None
    source: str          # "rule" | "grok" | "none" | "error"
    text: str            # the raw caption this came from
    error: str | None = None


@dataclass
class MoveResult:
    """Outcome of driving the board for one intent."""
    intent: Intent
    angle: int           # angle actually commanded (== previous angle on no-op)
    moved: bool          # did the servo receive a new position
    ok: bool             # did the HTTP call succeed (or was correctly skipped)
    error: str | None
    latency_ms: float
