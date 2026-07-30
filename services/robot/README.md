# services/robot

Drives a **2-wheel (differential-drive) robot** from Yoruba voice. The ESP32-S3 is
on **USB** and this laptop is the server: each finalized caption becomes a movement
command sent over the serial line, the board ACKs it, and the robot **answers in
Yoruba** (spoken with `--speak`). Used by `live_caption.py --robot`.

```
caption ─► IntentParser ─► RobotLink ─► "F,200,900\n" ─► ESP32-S3 ─► motors
            rules first     wire+ACK      (USB serial)     H-bridge
            Grok fallback                                  + Yoruba reply back
```

The latent/deterministic split:

- **Deterministic** (`vocab.py`): the five movement commands map by rule —
  normalize the caption (strip tone marks / sub-dots, lowercase), then whole-token
  keyword-match. Free, instant, offline. Handles **forward / backward / left /
  right / stop**, plus fast/slow speed words.
- **Latent** (`intent.py`, Grok): only for phrasing the rules miss (negations like
  "má ṣe lọ", paraphrases). Grok returns a tiny JSON intent — deterministic and
  testable. Off with no `XAI_API_KEY`; the rule path still works fully offline.

## Vocabulary (offline rules)

| Say (Yoruba) | Move | Wire |
|---|---|---|
| `síwájú`, `máa lọ síwájú` | forward | `F,<speed>,<ms>` |
| `sẹ́yìn`, `padà sẹ́yìn` | backward | `B,…` |
| `òsì`, `yà sí òsì` | spin left | `L,…` |
| `ọ̀tún`, `yíjú sí ọ̀tún` | spin right | `R,…` |
| `dúró` | stop now | `S` |
| + `kíákíá` / `yára` … | faster | `speed=255` |
| + `díẹ̀díẹ̀` / `jẹ́ẹ́jẹ́` … | slower | `speed=130` |

`dúró` (stop) is matched first, so "má lọ, dúró" halts. Each move is a timed nudge
(`ROBOT_DRIVE_MS`, turns `ROBOT_TURN_MS`) that **auto-stops** on the board — nothing
latches, and a firmware watchdog halts the motors if USB drops.

## The Yoruba reply

Every executed command speaks/prints a confirmation (`responses.py`):
forward → *"Mo ń lọ síwájú."*, left → *"Mo yà sí òsì."*, stop → *"Mo dúró."*, etc.
A command the board didn't ACK says *"Ẹ̀rọ náà kò dáhùn."*

## Contract (`contract.py`)

```python
Intent(action, speed, source, text, error)                 # action ∈ ACTIONS
MoveResult(intent, moved, ok, ack, response, error, latency_ms)
```

```python
from services.robot.controller import build_controller

ctrl = build_controller()
if ctrl.health():                         # serial ping (P -> PONG)
    res = ctrl.handle("máa lọ síwájú")    # parse → drive → reply
    print(res.ack, res.response)          # "OK:F:200:900"  "Mo ń lọ síwájú."
```

Guarantees: a non-command resolves to a silent no-op (no serial write); a failed
serial exchange leaves the robot un-driven and returns the "didn't respond" reply;
if the port can't be opened at all, `health()` returns False with the reason and
the pipeline keeps captioning.

## CLI

```bash
python3 -m services.robot.controller --health
python3 -m services.robot.controller "yà sí òsì"          # one nudge, auto-stops
python3 -m services.robot.controller "dúró"
python3 -m services.robot.controller --hold "máa lọ síwájú"   # continuous, Ctrl+C stops
```

`--hold` drives continuously in the command's direction, resending a keepalive
every `ROBOT_HOLD_REFRESH` s until you press Ctrl+C, which always stops the robot.
If the process is killed instead, the firmware watchdog halts the motors within
~2 s. No reflash needed — it works with the timed-nudge firmware.

## Board (`firmware/esp32s3_robot/`)

Flash `firmware/esp32s3_robot/esp32s3_robot.ino` (Arduino IDE, "ESP32S3 Dev
Module"). **Match "USB CDC On Boot" to the port your cable is in:**

- Cable in the **UART/COM port** (a CH340/CH343/CP210x bridge — `/dev/ttyACM0`
  with USB VID `1A86` or `10C4`): set **USB CDC On Boot: Disabled** so `Serial`
  is UART0, which the bridge is wired to. This is the common case.
- Cable in the S3's **native USB port**: set **USB CDC On Boot: Enabled**.

If `--health` opens the port but gets "no ack from board", this setting is the
mismatch (Serial is talking to the other USB port). It reads newline commands and
drives the H-bridge:

```
IN1 -> GPIO 4   IN2 -> GPIO 5   : left  motor    ENA/ENB jumpered HIGH
IN3 -> GPIO 6   IN4 -> GPIO 7   : right motor     motor V+ to driver, GND common
```

Speed is PWM on the IN pins. **On an L298N the ENA/ENB jumpers must be ON** (or
the enable pins tied HIGH) or the outputs stay dead — the board will ACK commands
but nothing moves. Keep input PWM at ~1 kHz for the L298N's slow transistors
(`PWM_FREQ`); a MOSFET driver (TB6612/DRV8833) can go to 20 kHz. Turns spin in
place (one wheel each way).

## Config (`config.py`, all env-overridable)

| Var | Default | Meaning |
|---|---|---|
| `ROBOT_PORT` | `auto` | serial port; `auto` scans USB, or pin `/dev/ttyACM0` |
| `ROBOT_BAUD` | `115200` | serial baud |
| `ROBOT_SPEED` | `200` | default PWM (0-255) |
| `ROBOT_FAST_SPEED` / `SLOW_SPEED` | `255` / `130` | speed-word targets |
| `ROBOT_DRIVE_MS` / `TURN_MS` | `900` / `550` | nudge duration per command |
| `ROBOT_ENABLED` | `1` | master switch |
| `ROBOT_GROK_FALLBACK` | `1` | use Grok for unmatched phrasing |

Needs `pyserial` (`pip install pyserial`).

## Tests (gate — free, deterministic, no serial, <2s)

The serial transport and HTTP transport are injected with fakes.

```bash
python3 -m unittest services.robot.tests.test_robot -v
```

## Evals (periodic — paid, needs `XAI_API_KEY`, no board)

Runs the full parser (rules + Grok) over labeled Yoruba commands; scores exact
intent match, including the negation/paraphrase cases only Grok resolves.

```bash
python3 services/robot/evals/eval_robot.py
```
