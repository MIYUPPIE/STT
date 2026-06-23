# speak.py — CLI + playback/save helpers for the Yoruba TTS service.
#
#   python3 -m services.tts.speak "Báwo ni o ṣe wà?"
#   python3 -m services.tts.speak --out hello.wav --no-play "Ẹ ṣé gan-an ni."
#   echo "Mo fẹ́ lọ sí ọjà." | python3 -m services.tts.speak
from __future__ import annotations

import argparse
import sys

import numpy as np

from . import config
from .contract import SynthRequest
from .engine import build_tts


def to_int16(wav: np.ndarray) -> np.ndarray:
    return (np.clip(wav, -1.0, 1.0) * 32767).astype("int16")


def resolve_device(spec):
    """Turn a device spec ("" / "4" / "ALC257") into None / int / name."""
    spec = (spec or "").strip()
    if not spec:
        return None
    return int(spec) if spec.lstrip("-").isdigit() else spec


def save_wav(path: str, wav: np.ndarray, sr: int) -> None:
    try:
        import soundfile as sf
        sf.write(path, wav, sr)
    except Exception:                       # vanilla fallback, no extra dep
        import wave
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(to_int16(wav).tobytes())


def play(wav: np.ndarray, sr: int, device=None) -> None:
    import sounddevice as sd
    if device is None:
        device = resolve_device(config.OUTPUT_DEVICE)
    sd.play(wav, sr, device=device)
    sd.wait()


def list_devices() -> int:
    import sounddevice as sd
    cur = resolve_device(config.OUTPUT_DEVICE)
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0:
            mark = " <- TTS_OUTPUT_DEVICE" if cur in (i, d["name"]) else ""
            print(f"  [{i}] {d['name']}  (ch={d['max_output_channels']}){mark}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Speak Yoruba text with the YarnGPT API.")
    ap.add_argument("text", nargs="*", help="text to speak (or pipe via stdin)")
    ap.add_argument("--out", help="write a WAV file")
    ap.add_argument("--no-play", action="store_true", help="don't play audio")
    ap.add_argument("--device", help="output device index or name substring")
    ap.add_argument("--devices", action="store_true",
                    help="list output devices and exit")
    args = ap.parse_args(argv)

    if args.devices:
        return list_devices()

    text = " ".join(args.text).strip() or sys.stdin.read().strip()
    if not text:
        ap.error("no text given")

    tts = build_tts()
    print(f"synth (YarnGPT, voice={tts.voice})...", file=sys.stderr)
    res = tts.synth(SynthRequest(text=text))
    if not res.ok:
        print(f"FAILED: {res.error}", file=sys.stderr)
        return 1
    print(f"[{res.duration_s:.2f}s audio in {res.latency_ms:.0f} ms]",
          file=sys.stderr)

    if args.out:
        save_wav(args.out, res.audio, res.sample_rate)
        print(f"wrote {args.out}", file=sys.stderr)
    if not args.no_play:
        device = resolve_device(args.device) if args.device else None
        play(res.audio, res.sample_rate, device=device)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
