# services/tts

Yoruba **text-to-speech** via the **YarnGPT** cloud API. Sends Yoruba text, gets
back spoken audio, decoded to a waveform the app plays or saves.

## API

`POST https://yarngpt.ai/api/v1/tts` — Bearer auth (`YARN_API_KEY`), body
`{text, voice, response_format}`, returns raw audio bytes.

- We request `wav` so `soundfile` decodes it with no extra codec dependency.
- Voice `Idera` is the one YarnGPT demos with Yoruba (16 voices exist).
- Diacritics drive pronunciation, so feed it the **refined**/brain text, not the
  raw caption.
- Hard limit 2000 chars/request (we truncate).

## Contract (`contract.py`)

```python
SynthRequest(text: str)
SynthResult(audio, sample_rate, ok, error, latency_ms, duration_s)
```

`audio` is float32 mono in ~[-1, 1]. On failure: `ok=False`, empty audio — the
caller (live_caption) just skips speaking; it never crashes.

## CLI

```bash
python3 -m services.tts.speak "Báwo ni o ṣe wà?"                 # play
python3 -m services.tts.speak --out hello.wav --no-play "Ẹ ṣé."  # save
echo "Mo fẹ́ lọ sí ọjà." | python3 -m services.tts.speak         # stdin
```

## Tests (gate — free, deterministic, no network, <2s)

The HTTP transport is injected with a fake that returns in-memory WAV bytes, so
nothing hits the network.

```bash
python3 -m unittest services.tts.tests.test_tts -v
```

## Evals (periodic — paid, needs YARN_API_KEY)

Calls the real API; checks each Yoruba sentence returns audible audio of
plausible length, reports real-time factor.

```bash
python3 services/tts/evals/eval_tts.py
```

## Config (env / .env)

| Var | Default | Meaning |
|-----|---------|---------|
| `YARN_API_KEY` | (from `.env`) | YarnGPT key — required |
| `YARN_API_URL` | `https://yarngpt.ai/api/v1/tts` | endpoint |
| `YARN_VOICE` | `Idera` | voice (Emma, Zainab, Osagie, ...) |
| `YARN_FORMAT` | `wav` | requested audio format |
| `YARN_TIMEOUT` | `60` | per-call seconds |
| `YARN_MAX_CHARS` | `2000` | request length cap |
| `TTS_ENABLED` | `1` | `0` disables the `--speak`/`--chat` audio |
| `TTS_OUTPUT_DEVICE` | `` (system default) | playback device index/name; set when default is a dead HDMI port |

Decoding uses the stdlib `wave` module (we request wav), so **no `soundfile`
dependency**. List playback devices with `python3 -m services.tts.speak --devices`;
pick yours with `TTS_OUTPUT_DEVICE=4` (or a name like `ALC257`).

## Use in live captioning

`python3 live_caption.py --speak` (or `--chat`) reads each line aloud. It's
**half-duplex**: the mic is muted while speaking so the audio isn't
re-transcribed (no feedback loop).
