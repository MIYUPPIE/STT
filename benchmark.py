# benchmark.py — compare OLD (transformers fp32) vs NEW (faster-whisper fp16)
# on the SAME recorded utterance. Proves both the speedup and that the
# transcription text is unchanged.
import time
import numpy as np
import sounddevice as sd
import torch

SAMPLE_RATE = 16000
RUNS = 5  # repeat decode N times per backend, report the median


def record(seconds=4):
    print(f"🎤 Speak a Yoruba phrase now ({seconds}s)...")
    audio = sd.rec(int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                   channels=1, dtype="float32")
    sd.wait()
    return audio.flatten()


def median(xs):
    return sorted(xs)[len(xs) // 2]


def bench_old(audio):
    from transformers import pipeline
    dev = 0 if torch.cuda.is_available() else -1
    asr = pipeline("automatic-speech-recognition",
                   model="./whisper-small-yoruba", device=dev)
    asr({"array": audio, "sampling_rate": SAMPLE_RATE})  # warmup
    times, text = [], ""
    for _ in range(RUNS):
        t0 = time.perf_counter()
        text = asr({"array": audio, "sampling_rate": SAMPLE_RATE})["text"].strip()
        times.append(time.perf_counter() - t0)
    return median(times), text


def bench_new(audio):
    from faster_whisper import WhisperModel
    use_gpu = torch.cuda.is_available()
    model = WhisperModel("./whisper-small-yoruba-ct2",
                         device="cuda" if use_gpu else "cpu",
                         compute_type="float16" if use_gpu else "int8")
    opts = dict(language="yo", beam_size=1, without_timestamps=True,
                condition_on_previous_text=False)
    list(model.transcribe(audio, **opts)[0])  # warmup
    times, text = [], ""
    for _ in range(RUNS):
        t0 = time.perf_counter()
        segs, _ = model.transcribe(audio, **opts)
        text = "".join(s.text for s in segs).strip()
        times.append(time.perf_counter() - t0)
    return median(times), text


if __name__ == "__main__":
    audio = record()
    old_t, old_txt = bench_old(audio)
    new_t, new_txt = bench_new(audio)

    print("\n================ RESULTS (median of %d) ================" % RUNS)
    print(f"OLD  transformers fp32 : {old_t*1000:7.0f} ms   → {old_txt}")
    print(f"NEW  faster-whisper fp16: {new_t*1000:7.0f} ms   → {new_txt}")
    print(f"\nSpeedup: {old_t/new_t:.1f}x faster")
    print("Text identical:", old_txt == new_txt)
