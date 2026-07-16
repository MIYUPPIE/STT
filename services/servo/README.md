# services/servo

Turns a Yoruba voice command into a servo-motor move on an **ESP32-S3** over
HTTP. Used by `live_caption.py --servo`: each finalized caption becomes a servo
intent, which drives the board.

```
caption ─► IntentParser ─► ServoClient ─► GET /servo?angle=N ─► ESP32-S3 servo
             rules first      resolve angle          (firmware/esp32s3_servo)
             Grok fallback     + clamp
```

The latent/deterministic split (per house rules):

- **Deterministic** (`vocab.py`): the common commands map by rule — normalize the
  caption (strip tone marks / sub-dots, lowercase), then keyword-match. Free,
  instant, offline, same input → same intent. Handles **left / right / center /
  stop / angle**.
- **Latent** (`intent.py`, Grok): only for phrasing the rules don't recognize
  (negations, **open / close**, unusual wording). Grok returns a tiny JSON intent,
  so even the latent step yields a deterministic, testable shape. Off with no
  `XAI_API_KEY` — the rule path still works fully offline.

Open/close are **deliberately not** in the rule layer: in tone-less Yoruba `títì`
(closed) collides with `títí` (continuously) and `ṣí` (open) with `sí` (to/at), so
matching them offline would false-trigger on normal speech. Grok has the context.

## Vocabulary (offline rules)

| Say (Yoruba) | Intent | Servo |
|---|---|---|
| `yà sí ọ̀tún`, `ọ̀tún` | right | +`SERVO_STEP`° (default 30) |
| `yà sí òsì`, `òsì` | left | −`SERVO_STEP`° |
| `padà sí àárín` | center | 90° |
| `dúró` | stop | hold |
| `lọ sí ọgọ́ta digiri`, `45 digiri` | angle | absolute degrees |
| (open / close / anything else) | via Grok | 180° / 0° / … |

An explicit degree wins over a bare direction: `yà sí ọ̀tún ní ọgbọ̀n digiri` →
go to 30°, not step right. Yoruba number words for the common angles (0–180) are
recognized; digits (`45`) too; Grok covers the rest.

## Contract (`contract.py`)

```python
Intent(action, angle, source, text, error)      # action ∈ ACTIONS
MoveResult(intent, angle, moved, ok, error, latency_ms)
```

```python
from services.servo.controller import build_controller

ctrl = build_controller()
if ctrl.health():                         # pings the board (no move)
    res = ctrl.handle("yà sí ọ̀tún")      # parse → resolve → drive
    print(res.moved, res.angle)           # True 120
```

Guarantees: a caption that isn't a command resolves to a no-op (`moved=False`,
no HTTP call); a failed HTTP call leaves the tracked angle **unchanged**; every
target is clamped to `[SERVO_MIN_ANGLE, SERVO_MAX_ANGLE]` before it leaves.

## CLI

```bash
python3 -m services.servo.controller --health
python3 -m services.servo.controller "yà sí ọ̀tún"
python3 -m services.servo.controller "lọ sí ọgọ́ta digiri"
```

## Board (`firmware/esp32s3_servo/`)

Flash `firmware/esp32s3_servo/esp32s3_servo.ino` (Arduino IDE, board
"ESP32S3 Dev Module", PSRAM enabled). It adds a `/servo` endpoint to the existing
camera + LED server on its own LEDC timer/channel (timer 1 / channel 2 @ 50 Hz)
so it never collides with the camera XCLK (timer 0 / channel 0). It advertises
`http://esp32-servo.local` via mDNS, so `SERVO_HOST` needs no hard-coded IP.

Servo wiring: signal → **GPIO 14**, V+ → **external 5 V** (never the board's 3V3 —
a servo browns it out), GND → common ground with the ESP32.

## Config (`config.py`, all env-overridable)

| Var | Default | Meaning |
|---|---|---|
| `SERVO_HOST` | `http://esp32-servo.local` | board base URL |
| `SERVO_STEP` | `30` | degrees per left/right |
| `SERVO_CENTER_ANGLE` / `OPEN` / `CLOSE` | `90` / `180` / `0` | fixed targets |
| `SERVO_MIN_ANGLE` / `MAX_ANGLE` | `0` / `180` | clamp range |
| `SERVO_ENABLED` | `1` | master switch |
| `SERVO_GROK_FALLBACK` | `1` | use Grok for unmatched phrasing |

## Tests (gate — free, deterministic, <2s)

No network; HTTP transports are injected with fakes.

```bash
python3 -m unittest services.servo.tests.test_servo -v
```

## Evals (periodic — paid, needs `XAI_API_KEY`)

Runs the full parser (rules + Grok) over labeled Yoruba commands; scores exact
intent match, including the open/close/negation cases only Grok resolves.

```bash
python3 services/servo/evals/eval_servo.py
```
