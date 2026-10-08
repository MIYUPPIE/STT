# services/robot

Drives a **2-wheel (differential-drive) robot** from Yoruba voice. The ESP32-S3
is reached over **WiFi** (TCP, no cable) or **USB serial**, and this laptop is the
server: each finalized caption becomes a movement command, the board ACKs it, and
the robot **answers in Yoruba** (spoken with `--speak`). Used by
`live_caption.py --robot`.

```
caption ─► IntentParser ─► RobotLink ─► "F,200,900\n" ─► ESP32-S3 ─► motors
            rules first     wire+ACK    WiFi TCP :3333     H-bridge
            Grok fallback               or USB serial      + Yoruba reply back
```

Mic, STT (N-ATLAS, `services/stt`) and TTS speaker stay on the laptop; only the
movement commands travel over WiFi.

## WiFi link

`ROBOT_LINK=auto` (default) tries WiFi first, then USB. The laptop finds the
board in this order:

1. `ROBOT_HOST` if set (e.g. `ROBOT_HOST=192.168.43.57`, fastest, no discovery)
2. mDNS `yoruba-robot.local` (avahi, bounded to 1.5 s)
3. a TCP sweep of the laptop's own /24 for a host answering `P` with `PONG`
   (outbound only, so the laptop firewall can't block it; ~1.3 s)

The connection is persistent (TCP_NODELAY on both ends, WiFi modem sleep off on
the board). If it drops, the next command reconnects once and retries; while the
robot is unreachable, reconnects are throttled to one per second.

Safety over WiFi: the board stops the motors when the TCP client disconnects,
when WiFi drops, when a new client connects, and (as before) when no command
byte arrives for 2 s. A crashed laptop or a dead router can never leave it
driving.

**The laptop must be on the same network as the robot.** The ESP32-S3 joins only
2.4 GHz networks; the SSID and password are in `firmware/esp32s3_robot/secrets.h`
(git-ignored, copy `secrets.h.example`).

No board handy? `python3 -m services.robot.sim` runs a software stand-in for the
firmware on port 3333, and the CLI below will find and drive it.

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

Guarantees: a non-command resolves to a silent no-op (no write to the board); a
failed exchange leaves the robot un-driven and returns the "didn't respond" reply;
if no link can be opened at all, `health()` returns False with the reason for
each link tried and the pipeline keeps captioning.

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

**Continuous voice control** (`live_caption.py --robot`) uses the same mechanism:
a direction command **latches** (`controller.drive(action, speed)`) so the robot
keeps moving until another command or `dúró` (`controller.halt()`). A keepalive
thread (`start_keepalive`) resends the current move. The spoken confirmations are
pre-synthesized once at startup and replayed from cache, so each reply is instant
instead of a per-command cloud call.

## Board (`firmware/esp32s3_robot/`)

1. `cp firmware/esp32s3_robot/secrets.h.example firmware/esp32s3_robot/secrets.h`
   and set `WIFI_SSID` / `WIFI_PASSWORD` (2.4 GHz network).
2. Flash `firmware/esp32s3_robot/esp32s3_robot.ino` (Arduino IDE, "ESP32S3 Dev
   Module").
3. Open Serial Monitor at 115200. On boot it prints
   `WiFi CONNECTED  ip=…  tcp=3333  host=yoruba-robot.local`. After that the USB
   cable is only needed for power; a battery works.

The USB serial channel still accepts the same commands. **Match "USB CDC On Boot"
to the port your cable is in** (this only affects USB control and the boot log):

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
| `ROBOT_LINK` | `auto` | `auto` (WiFi then USB), `wifi`, or `serial` |
| `ROBOT_HOST` | `auto` | robot IP/hostname; `auto` = mDNS then LAN sweep |
| `ROBOT_TCP_PORT` | `3333` | firmware TCP port |
| `ROBOT_MDNS_NAME` | `yoruba-robot.local` | mDNS name to try |
| `ROBOT_SCAN_SUBNET` | (own /24) | sweep this prefix instead, e.g. `192.168.43` |
| `ROBOT_CONNECT_TIMEOUT` / `SCAN_TIMEOUT` | `2.0` / `0.4` | seconds |
| `ROBOT_PORT` | `auto` | serial port; `auto` scans USB, or pin `/dev/ttyACM0` |
| `ROBOT_BAUD` | `115200` | serial baud |
| `ROBOT_SPEED` | `200` | default PWM (0-255) |
| `ROBOT_FAST_SPEED` / `SLOW_SPEED` | `255` / `130` | speed-word targets |
| `ROBOT_DRIVE_MS` / `TURN_MS` | `900` / `550` | nudge duration per command |
| `ROBOT_ENABLED` | `1` | master switch |
| `ROBOT_GROK_FALLBACK` | `1` | use Grok for unmatched phrasing |

USB needs `pyserial` (`pip install pyserial`); WiFi needs nothing extra.

## Tests (gate — free, deterministic, no serial, <2s)

The serial transport and HTTP transport are injected with fakes; the WiFi tests
run the real TCP transport against `sim.py` on 127.0.0.1 (ping, moves, async
watchdog lines, reconnect after a WiFi blip, robot powered off, keepalive,
discovery order, WiFi-to-USB fallback).

```bash
python3 -m unittest services.robot.tests.test_robot services.robot.tests.test_wifi -v
```

## Evals (periodic — paid, needs `XAI_API_KEY`, no board)

Runs the full parser (rules + Grok) over labeled Yoruba commands; scores exact
intent match, including the negation/paraphrase cases only Grok resolves.

```bash
python3 services/robot/evals/eval_robot.py
```
