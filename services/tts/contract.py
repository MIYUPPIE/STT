# contract.py — boundary between callers (live_caption, CLI) and the TTS engine.
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class SynthRequest:
    """Text to speak. Diacritics matter for Yoruba pronunciation — keep them."""
    text: str


@dataclass
class SynthResult:
    audio: np.ndarray   # float32 mono in ~[-1, 1]; empty array on failure
    sample_rate: int    # 0 when audio is empty
    ok: bool
    error: str | None
    latency_ms: float
    duration_s: float
