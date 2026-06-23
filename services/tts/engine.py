# engine.py — Yoruba TTS via the YarnGPT cloud API.
#
# POST {text, voice, response_format} -> raw wav bytes, decoded to a float32
# waveform the caller can play or save. The HTTP transport is injectable so gate
# tests run without any network.
from __future__ import annotations

import io
import json
import re
import time
import urllib.error
import urllib.request
import wave

import numpy as np

from . import config
from .contract import SynthRequest, SynthResult

_BRACKET = re.compile(r"^\s*\[[^\]]*\]\s*")     # strip a leading "[HH:MM:SS] "


def clean_text(text: str) -> str:
    """Normalize caption text for synthesis. Keeps Yoruba diacritics; drops the
    timestamp prefix and collapses whitespace."""
    t = (text or "").strip()
    t = _BRACKET.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


_SENTENCE = re.compile(r"(?<=[.!?…])\s+")
_CLAUSE = re.compile(r"(?<=[,;:])\s+")


def chunk_text(text: str, max_chunk: int = 100):
    """Split a reply into speakable chunks: by sentence, and any long sentence
    further at clause boundaries (commas), so the player can speak the first
    chunk while later ones are still synthesizing. Bounds time-to-first-audio."""
    text = (text or "").strip()
    if not text:
        return []
    out = []
    for s in _SENTENCE.split(text):
        s = s.strip()
        if not s:
            continue
        if len(s) <= max_chunk:
            out.append(s)
            continue
        buf = ""                                   # pack clauses up to max_chunk
        for clause in _CLAUSE.split(s):
            clause = clause.strip()
            if not clause:
                continue
            if buf and len(buf) + 1 + len(clause) > max_chunk:
                out.append(buf)
                buf = clause
            else:
                buf = f"{buf} {clause}".strip()
        if buf:
            out.append(buf)
    return out


# ---------------- transport (injectable) ----------------
def urllib_transport(url: str, payload: dict, timeout: float, headers=None):
    """Returns (status, body). On success body is raw audio bytes; on failure
    body is an error string. status 0 means the request never reached the API."""
    data = json.dumps(payload).encode()
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:  # URLError, timeout, etc.
        return 0, str(e)


def _decode_with_soundfile(raw: bytes):
    """Fallback for non-WAV formats; only used if soundfile is installed."""
    import soundfile as sf
    data, sr = sf.read(io.BytesIO(raw), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1).astype("float32")
    return data, int(sr)


def decode_audio(raw: bytes):
    """Decode audio bytes -> (float32 mono in [-1, 1], sample_rate).

    We request WAV from Yarn, so the default path is the stdlib `wave` module
    (no third-party codec dependency). Non-WAV bytes fall back to soundfile if
    it happens to be installed."""
    try:
        with wave.open(io.BytesIO(raw), "rb") as w:
            ch, sw, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
            frames = w.readframes(w.getnframes())
    except (wave.Error, EOFError):
        return _decode_with_soundfile(raw)     # not a PCM WAV -> try soundfile
    if sw == 2:
        data = np.frombuffer(frames, dtype="<i2").astype("float32") / 32768.0
    elif sw == 1:                              # 8-bit PCM is unsigned
        data = (np.frombuffer(frames, dtype="u1").astype("float32") - 128) / 128.0
    elif sw == 4:
        data = np.frombuffer(frames, dtype="<i4").astype("float32") / 2147483648.0
    else:
        raise ValueError(f"unsupported WAV sample width: {sw * 8} bit")
    if ch > 1:                                 # downmix to mono
        data = data.reshape(-1, ch).mean(axis=1).astype("float32")
    return data, int(sr)


class YarnTTS:
    def __init__(self, voice=None, api_url=None, api_key=None, fmt=None,
                 timeout=None, max_chars=None, transport=urllib_transport):
        self.voice = voice or config.VOICE
        self.api_url = api_url or config.API_URL
        self.api_key = config.API_KEY if api_key is None else api_key
        self.fmt = fmt or config.FORMAT
        self.timeout = config.TIMEOUT if timeout is None else timeout
        self.max_chars = config.MAX_CHARS if max_chars is None else max_chars
        self.transport = transport
        self.last_error = None

    def _empty(self, ok, error, dt=0.0):
        self.last_error = None if ok else error
        return SynthResult(np.zeros(0, "float32"), 0, ok, error, dt, 0.0)

    def synth(self, req: SynthRequest) -> SynthResult:
        text = clean_text(req.text)
        if not text:
            return self._empty(True, None)
        if not self.api_key:
            return self._empty(False, "YARN_API_KEY not set (add it to .env)")
        payload = {
            "text": text[: self.max_chars],
            "voice": self.voice,
            "response_format": self.fmt,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        t0 = time.perf_counter()
        status, body = self.transport(self.api_url, payload, self.timeout, headers)
        dt = (time.perf_counter() - t0) * 1000
        if status != 200 or not isinstance(body, (bytes, bytearray)):
            err = body if isinstance(body, str) else f"http {status}"
            return self._empty(False, err, dt)
        try:
            audio, sr = decode_audio(bytes(body))
        except Exception as e:
            return self._empty(False, f"decode failed: {e}", dt)
        self.last_error = None
        dur = len(audio) / sr if sr else 0.0
        return SynthResult(audio, sr, True, None, dt, dur)


def build_tts():
    return YarnTTS()
