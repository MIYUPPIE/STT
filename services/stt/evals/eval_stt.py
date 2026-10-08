# eval_stt.py — periodic eval. Free (fully local) but slow: synthesizes each
# Yoruba robot command with the cached facebook/mms-tts-yor voice, transcribes it
# with each STT model, and runs the robot's offline parser (rules only, no Grok)
# on the transcript. Scores end-to-end command accuracy: audio -> right action.
#
# Run: python3 services/stt/evals/eval_stt.py [--cpu] [--models natlas,legacy]
#
# PASS requires the default model (natlas) to reach PASS_THRESHOLD and to be no
# worse than legacy. MMS-TTS is a synthetic voice, so absolute numbers are a
# floor for real speech; the comparison between models is the signal.
import json
import os
import sys
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")          # never download anything
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

import numpy as np                                       # noqa: E402

from services.stt import config                          # noqa: E402
from services.stt.model import resolve                   # noqa: E402
from services.robot.vocab import rule_intent             # noqa: E402

PASS_THRESHOLD = 0.70
TTS_REPO = "facebook/mms-tts-yor"


def load_cases(path):
    with open(path) as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def synthesize(texts):
    import torch
    from scipy.signal import resample_poly
    from transformers import AutoTokenizer, VitsModel
    tok = AutoTokenizer.from_pretrained(TTS_REPO)
    tts = VitsModel.from_pretrained(TTS_REPO)
    torch.manual_seed(0)                                 # VITS is stochastic
    sr = tts.config.sampling_rate
    pad = np.zeros(4000, "float32")                      # 250 ms silence each side
    clips = []
    for t in texts:
        with torch.no_grad():
            w = tts(**tok(t, return_tensors="pt")).waveform[0].numpy()
        w = resample_poly(w, 16000, sr).astype("float32") if sr != 16000 else w
        clips.append(np.concatenate([pad, w.astype("float32"), pad]))
    return clips


def load_model(path, cpu):
    from faster_whisper import WhisperModel
    if not cpu:
        try:
            m = WhisperModel(path, device="cuda", compute_type="float16")
            list(m.transcribe(np.zeros(16000, "float32"), language="yo")[0])
            return m, "cuda"
        except Exception:
            pass
    return WhisperModel(path, device="cpu", compute_type="int8"), "cpu"


def main(argv):
    cpu = "--cpu" in argv
    names = ["natlas", "legacy"]
    for a in argv:
        if a.startswith("--models"):
            names = argv[argv.index(a) + 1].split(",") if a == "--models" else a.split("=", 1)[1].split(",")
    cases = load_cases(os.path.join(os.path.dirname(__file__), "cases.jsonl"))
    print(f"synthesizing {len(cases)} commands with {TTS_REPO} (local)...")
    clips = synthesize([c["text"] for c in cases])

    scores = {}
    for name in names:
        r = resolve(name, auto_convert=False)
        if r.name != name:
            print(f"\n[{name}] not available ({r.note}); skipped")
            continue
        model, dev = load_model(r.path, cpu)
        hits, t0 = 0, time.time()
        print(f"\n== {name} ({dev}, language={config.LANGUAGE})")
        for c, clip in zip(cases, clips):
            segs, _ = model.transcribe(clip, language=config.LANGUAGE, beam_size=1,
                                       without_timestamps=True,
                                       condition_on_previous_text=False)
            text = "".join(s.text for s in segs).strip()
            got = rule_intent(text).action
            ok = got == c["action"]
            hits += ok
            print(f"  {'PASS' if ok else 'FAIL'}  {c['text']!r:26} -> {text!r:32} "
                  f"{got}" + ("" if ok else f" (want {c['action']})"))
        acc = hits / len(cases)
        scores[name] = acc
        print(f"  accuracy {hits}/{len(cases)} = {acc:.0%}  "
              f"({(time.time() - t0) / len(cases) * 1000:.0f} ms/clip)")

    print("\nsummary: " + "  ".join(f"{k}={v:.0%}" for k, v in scores.items()))
    if "natlas" not in scores:
        print("FAIL: natlas model not available")
        return 1
    ok = scores["natlas"] >= PASS_THRESHOLD and \
        scores["natlas"] >= scores.get("legacy", 0.0)
    print(f"{'PASS' if ok else 'FAIL'} (threshold {PASS_THRESHOLD:.0%}, "
          "natlas must be >= legacy)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
