# contract.py — boundary between live_caption (and any other caller) and the
# brain service. Both sides import these types; neither reaches inside.
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChatRequest:
    """One user utterance (a finalized Yoruba caption) to respond to."""
    text: str


@dataclass
class ChatResult:
    user: str           # the input utterance, unchanged
    reply: str          # the assistant's Yoruba reply ("" if the call failed)
    ok: bool            # did the LLM call succeed and produce a reply
    error: str | None   # failure reason when ok is False
    latency_ms: float   # wall-clock of the LLM call
