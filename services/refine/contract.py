# contract.py — the boundary between live_caption (and any other caller) and
# the refine service. Both sides import these types; neither reaches inside.
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RefineRequest:
    """One raw caption to clean up."""
    text: str


@dataclass
class RefineResult:
    raw: str            # the input, unchanged
    refined: str        # corrected Yoruba (== raw if the call failed/no-op)
    ok: bool            # did the LLM call succeed
    error: str | None   # failure reason when ok is False
    latency_ms: float   # wall-clock of the LLM call
