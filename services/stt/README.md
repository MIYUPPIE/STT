# services/stt

Picks the local speech-to-text model that `live_caption.py` loads. Default is
**N-ATLAS Yoruba ASR** (`NCAIR1/Yoruba-ASR`, a Whisper-small fine-tune), run with
faster-whisper on a CTranslate2 build.

**No download.** The CTranslate2 build is made from the copy of
`NCAIR1/Yoruba-ASR` that is already in the Hugging Face cache
(`~/.cache/huggingface/hub`), with `HF_HUB_OFFLINE=1`. If the build dir is
missing, the app makes it on first run (one-time, a few minutes).

```
STT_MODEL=natlas ─► natlas-yoruba-asr-ct2/   exists? load it
                    else NCAIR1/Yoruba-ASR in HF cache? convert offline, load it
                    else fall back to whisper-small-yoruba-ct2/ (legacy) + note
```

## Why N-ATLAS

`evals/eval_stt.py` measures end-to-end robot command accuracy: Yoruba command
audio goes through STT, then the robot's offline parser, and the eval checks that
the right action comes out. Results with the local MMS-TTS voice:

| Model | Command accuracy | False moves on non-commands |
|---|---|---|
| N-ATLAS (`natlas`) | **78%** (14/18) | 0/3 |
| legacy `steja/whisper-small-yoruba` | 56% (10/18) | 1/3 (`báwo ni o ṣe wà` drove forward) |

The language token matters: `language="yo"` beats `"en"` on N-ATLAS
(`padà sẹ́yìn` comes out exact with `yo` and as `ada sẹ́yìn` with `en`).
Auto-detect crashes faster-whisper on this checkpoint, so the language is always
set.

## Usage

```bash
python3 -m services.stt.model             # show which model will load
python3 -m services.stt.model --convert   # (re)build natlas-yoruba-asr-ct2 offline
STT_MODEL=legacy python3 live_caption.py  # A/B against the old model
```

```python
from services.stt.model import resolve
r = resolve()            # Resolved(path, name, note)
WhisperModel(r.path, device="cpu", compute_type="int8")
```

## Config

| Var | Default | Meaning |
|---|---|---|
| `STT_MODEL` | `natlas` | `natlas`, `legacy`, or a path to any CTranslate2 Whisper dir |
| `STT_LANGUAGE` | `yo` | Whisper language token |
| `STT_CONVERT_QUANT` | `float16` | weight type for the offline build |
| `HF_HUB_CACHE` | `~/.cache/huggingface/hub` | where the cached N-ATLAS weights are |

## Tests (gate: free, no weights loaded, <1s)

```bash
python3 -m unittest services.stt.tests.test_stt -v
```

## Eval (periodic: free and local, but slow, ~5 min on CPU)

```bash
python3 services/stt/evals/eval_stt.py          # natlas vs legacy
python3 services/stt/evals/eval_stt.py --cpu
```

Passes when N-ATLAS reaches 70% command accuracy and is no worse than legacy.
MMS-TTS is a synthetic voice, so treat the absolute numbers as a floor for real
speech. The model-vs-model comparison is the signal.
