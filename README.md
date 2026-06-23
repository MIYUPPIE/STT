# Yoruba Live Voice

Always-on **Yoruba** speech pipeline. Speak Yoruba into your mic and the system
either:

- **captions** what you said and prints a corrected, properly accented version
  (default), or
- **answers** you as a conversational voice assistant and speaks the reply aloud
  (`--chat`).

Whisper (STT) and Silero (VAD) run **locally**. Grok (xAI) does the
correction/conversation and YarnGPT does the Yoruba speech — both are cloud
APIs keyed from `.env`.

```
mic ─► Silero VAD ─► faster-whisper (STT) ─► Grok (correct | respond) ─► YarnGPT (TTS) ─► speaker
        local            local                    cloud                    cloud
```

## What it does

`live_caption.py` runs five concurrent stages so the mic is never deaf — slow
cloud calls never block capture or live decoding:

1. **PortAudio callback** pushes 512-sample frames into a queue.
2. **Streaming Silero VAD** finds speech start/end, emitting live partial
   snapshots and finalized segments.
3. **faster-whisper** decodes each segment, printing partials live (`\r`) and
   finalizing on a pause.
4. The **Grok** stage either **corrects** the caption (default refiner) or
   **responds** to it as a conversational brain (`--chat`).
5. **YarnGPT** reads the correction/reply aloud (`--speak` / `--chat`).

Speaking is **half-duplex**: the mic mutes during playback so the spoken audio
isn't transcribed back into a feedback loop.

## Repo layout

```
live_caption.py          # the always-on app (all five stages)
benchmark.py             # transformers fp32 vs faster-whisper fp16 on one utterance
download.py              # pull the base Whisper Yoruba model from Hugging Face
test.py                  # standalone record-until-silence STT loop (faster-whisper)
services/
  brain/                 # Grok conversational brain (--chat)        — responds
  refine/                # Grok caption corrector (default)          — corrects
  tts/                   # YarnGPT Yoruba text-to-speech
  env_loader.py          # loads repo-root .env into os.environ (no dotenv dep)
whisper-small-yoruba/    # base HF model        (git-ignored, see Setup)
whisper-small-yoruba-ct2/# CTranslate2 build    (git-ignored, see Setup)
```

Each service is self-contained (code, contract, tests, evals, README) and talks
to the app through a typed contract. See:

| Service | Role | Provider | README |
|---------|------|----------|--------|
| `services/refine` | corrects a raw caption into accented Yoruba | Grok (xAI) | [refine/README.md](services/refine/README.md) |
| `services/brain`  | conversational reply to each utterance | Grok (xAI) | [brain/README.md](services/brain/README.md) |
| `services/tts`    | Yoruba text → spoken audio | YarnGPT | [tts/README.md](services/tts/README.md) |

## Setup

### 1. System + Python deps

`sounddevice` needs PortAudio:

```bash
sudo apt-get install -y portaudio19-dev          # Debian/Ubuntu
```

```bash
pip install numpy sounddevice torch faster-whisper ctranslate2 \
            transformers huggingface_hub
```

(`transformers`/`huggingface_hub` are only needed for `benchmark.py` and
`download.py`. The app and TTS need no `soundfile` — WAV is decoded with the
stdlib `wave` module.)

### 2. Model

Download the base Whisper Yoruba model, then convert it to the CTranslate2
format the app loads:

```bash
python3 download.py                              # -> ./whisper-small-yoruba
ct2-transformers-converter \
  --model ./whisper-small-yoruba \
  --output_dir whisper-small-yoruba-ct2 \
  --quantization float16 \
  --copy_files preprocessor_config.json tokenizer.json
```

Both model directories are git-ignored — they're never committed.

### 3. API keys

The Grok and YarnGPT stages read keys from a repo-root `.env` (git-ignored):

```ini
XAI_API_KEY=your-xai-key
YARN_API_KEY=your-yarngpt-key
```

`services/env_loader.py` loads this automatically at import; it never overwrites
an already-exported variable. Every other config var has a sane default — see
each service README for the full table.

## Usage

```bash
# Live captioning + Grok correction (default)
python3 live_caption.py

# ... and speak each corrected line aloud (half-duplex)
python3 live_caption.py --speak

# Conversational Yoruba voice assistant: Grok replies, YarnGPT speaks it
python3 live_caption.py --chat

# Force Whisper on CPU (keeps the GPU free; Grok + TTS are cloud anyway)
python3 live_caption.py --chat --cpu

# Captioning only, no Grok
python3 live_caption.py --no-refine
```

Whisper auto-uses CUDA (`float16`) when available, else CPU (`int8`). Each cloud
stage is probed at startup; if a key is bad or the API is unreachable, the app
degrades to plain captioning instead of crashing. `Ctrl+C` to stop.

### Standalone helpers

```bash
python3 test.py        # record-until-silence STT loop (no VAD streaming, no cloud)
python3 benchmark.py   # speak once; compare fp32 transformers vs fp16 faster-whisper
```

## Tests and evals

Two lanes per service:

- **Gate tests** — free, deterministic, no network (HTTP transport is faked), <2s.

  ```bash
  python3 -m unittest services.refine.tests.test_refiner -v
  python3 -m unittest services.brain.tests.test_brain -v
  python3 -m unittest services.tts.tests.test_tts -v
  ```

- **Periodic evals** — paid, hit the real APIs, score quality against a threshold.

  ```bash
  python3 services/refine/evals/eval_yoruba.py     # needs XAI_API_KEY
  python3 services/brain/evals/eval_brain.py       # needs XAI_API_KEY
  python3 services/tts/evals/eval_tts.py           # needs YARN_API_KEY
  ```

## Notes

- **Local vs cloud**: only Whisper + Silero VAD run on your machine. Grok
  (correction/brain) and YarnGPT (TTS) are cloud APIs.
- **Half-duplex**: the mic is muted for the whole spoken response so the system
  never transcribes its own voice.
- **Playback device**: if the default output is dead (e.g. an unplugged HDMI
  port), list devices with `python3 -m services.tts.speak --devices` and set
  `TTS_OUTPUT_DEVICE` in `.env`.
