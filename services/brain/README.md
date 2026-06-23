# services/brain

The **conversational brain** of the Yoruba voice loop. Where `services/refine`
*corrects* a caption, this *responds* to it: it takes one finalized Yoruba
utterance and returns a short spoken-style Yoruba reply, using **Grok (xAI)**.

Pipeline role:

```
speech -> Silero VAD -> Whisper (STT) -> brain (Grok) -> YarnGPT (TTS) -> speaker
```

Turn it on in live captioning with `--chat`:

```bash
python3 live_caption.py --chat          # speak Yoruba, the assistant replies aloud
python3 live_caption.py --chat --cpu    # Whisper on CPU, GPU stays free
```

## Contract (`contract.py`)

```python
ChatRequest(text: str)
ChatResult(user, reply, ok, error, latency_ms)
```

Callers do:

```python
from services.brain.brain import build_brain
from services.brain.contract import ChatRequest

brain = build_brain()
if brain.health():                       # real chat probe (valid key + reachable)
    res = brain.respond(ChatRequest(text="bawo ni"))
    print(res.reply)                     # -> a short Yoruba reply
```

Guarantee: if the API is unreachable or the key is bad, `respond()` returns
`ok=False` and `reply == ""`. The caller keeps captioning and never crashes.

## Memory

The brain object is **stateful**: it keeps the recent conversation (default 6
exchanges, env `BRAIN_HISTORY_TURNS`) so replies stay coherent across turns.
History is trimmed to bound prompt growth, so latency stays flat over a long
chat. `brain.reset()` starts a fresh conversation. A new `GrokBrain()` (or each
eval case) starts empty.

## CLI

```bash
python3 -m services.brain.brain --health           # is the API usable?
python3 -m services.brain.brain "bawo ni o se wa"  # one-shot reply
python3 -m services.brain.brain --repl             # multi-turn manual test
```

## Tests (gate — free, deterministic, <2s)

No network; the HTTP transport is injected with a fake.

```bash
python3 -m unittest services.brain.tests.test_brain -v
```

## Evals (periodic — paid, needs XAI_API_KEY)

Hits the real model, scores reply quality heuristically (non-empty, no English
refusal, looks like Yoruba, TTS-safe/no markdown), threshold 70%.

```bash
python3 services/brain/evals/eval_brain.py
```

## Config (env / .env)

| Var | Default | Meaning |
|-----|---------|---------|
| `XAI_API_KEY` | (from `.env`) | xAI / Grok key — required |
| `XAI_API_URL` | `https://api.x.ai/v1/chat/completions` | endpoint |
| `BRAIN_MODEL` / `XAI_MODEL` | `grok-4.3` | Grok model |
| `BRAIN_ENABLED` | `1` | `0` = `--chat` falls back to caption-only |
| `BRAIN_TIMEOUT` | `45` | per-reply seconds |
| `BRAIN_HEALTH_TIMEOUT` | `30` | startup probe (1-token chat) |
| `BRAIN_MAX_TOKENS` | `120` | reply length cap (spoken aloud, keep short) |
| `BRAIN_TEMPERATURE` | `0.6` | reply variety; `0` = deterministic |
| `BRAIN_HISTORY_TURNS` | `6` | exchanges kept as context |

## Model choice

`grok-4.3` is xAI's flagship text model (verified available on this key via
`GET /v1/models`). It handles Yoruba directly. For lower latency you can set
`BRAIN_MODEL=grok-4.20-0309-non-reasoning`. Run the eval after any change.
