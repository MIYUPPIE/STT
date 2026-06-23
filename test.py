# live_stt.py — Yoruba live speech-to-text (faster-whisper / CTranslate2 backend)
import time
import sounddevice as sd
import numpy as np
import torch
from faster_whisper import WhisperModel

# ---- Load model once (CTranslate2 float16, GPU) ----
USE_GPU = torch.cuda.is_available()
DEVICE = "cuda" if USE_GPU else "cpu"
COMPUTE_TYPE = "float16" if USE_GPU else "int8"   # int8 is the fast CPU path

model = WhisperModel(
    "./whisper-small-yoruba-ct2",
    device=DEVICE,
    compute_type=COMPUTE_TYPE,
)

SAMPLE_RATE = 16000  # Whisper expects 16kHz

# Decode options tuned for short Yoruba utterances (matches greedy decoding,
# skips language detection + timestamp tokens for speed).
DECODE_OPTS = dict(
    language="yo",
    beam_size=1,                     # greedy — fastest, matches old pipeline
    without_timestamps=True,
    condition_on_previous_text=False,
)


def warmup():
    """First GPU call compiles kernels; do it on dummy audio so the first
    real utterance isn't slow."""
    silent = np.zeros(SAMPLE_RATE, dtype="float32")
    list(model.transcribe(silent, **DECODE_OPTS)[0])


def record_until_silence(max_seconds=8, silence_threshold=0.01, silence_duration=0.6):
    """Record from mic; start when you speak, stop after a short silence."""
    block = int(SAMPLE_RATE * 0.1)              # 100ms chunks
    silent_blocks_needed = int(silence_duration / 0.1)
    max_blocks = int(max_seconds / 0.1)

    frames, silent_count, started = [], 0, False
    print("🎤 Speak now...")

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as stream:
        for _ in range(max_blocks):
            data, _ = stream.read(block)
            data = data.flatten()
            frames.append(data)
            volume = np.sqrt(np.mean(data**2))   # RMS loudness

            if volume > silence_threshold:
                started = True
                silent_count = 0
            elif started:
                silent_count += 1
                if silent_count >= silent_blocks_needed:
                    break

    return np.concatenate(frames) if frames else np.array([], dtype="float32")


def transcribe(audio):
    if len(audio) == 0:
        return "", 0.0
    t0 = time.perf_counter()
    segments, _ = model.transcribe(audio, **DECODE_OPTS)
    text = "".join(s.text for s in segments).strip()
    return text, time.perf_counter() - t0


# ---- Live loop ----
print(f"Loading on {DEVICE} ({COMPUTE_TYPE})...")
warmup()
print("Yoruba STT ready. Press Ctrl+C to stop.\n")
try:
    while True:
        audio = record_until_silence()
        text, dt = transcribe(audio)
        print(f"  → {text}    [{dt*1000:.0f} ms]\n")
except KeyboardInterrupt:
    print("\nStopped.")
