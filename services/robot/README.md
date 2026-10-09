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

**Telemetry (UDP 3334).** Send `SUB` and the robot streams
`T,<seq>,<ms>,<dutyL>,<dutyR>,<minDuty>` at 20 Hz for 3 s (re-send every
second). The duties are what the motors are doing right now, whoever commands
them. Watch it live with `python3 -m services.robot.telemetry [ip]`;
`telemetry.py` holds the parser and listener used by the ROS bridge. The robot
is also flashable over WiFi (ArduinoOTA, `OTA_PASSWORD` in `secrets.h`).

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
mismatch (Serial is talking to the other USB port).

### L298N wiring (ESP32-S3)

```
L298N    ESP32-S3        role
IN1  ->  GPIO 4          left  motor direction
IN2  ->  GPIO 5          left  motor direction
ENA  ->  GPIO 41         left  motor SPEED (PWM)
IN3  ->  GPIO 6          right motor direction
IN4  ->  GPIO 7          right motor direction
ENB  ->  GPIO 42         right motor SPEED (PWM)
GND  ->  GND             common ground (required)
+12V/VS <- motor battery   (not the ESP32 3V3/5V pin)
```

**Pull the two ENA/ENB jumper caps off the L298N** before wiring ENA/ENB to the
ESP32. With the caps on, the enables are tied HIGH: the motors run full speed
and speed commands do nothing. 3.3 V logic is enough for the L298N inputs.

GPIO 41/42 are free on Freenove and DevKitC-1 ESP32-S3 boards. They avoid the
strapping pins (0/3/45/46), USB (19/20), UART0 (43/44), octal PSRAM (35-37), the
camera bus and the onboard LEDs (2/21/47).

How speed works (`esp32s3_robot.ino`, math in `motor_math.h`):

- IN pins are plain HIGH/LOW direction lines; speed is 1 kHz LEDC PWM on ENA/ENB
  (the L298N's slow transistors need ~1 kHz; a TB6612/DRV8833 can go to 20 kHz).
- Command speed `1..255` maps onto `MIN_DUTY..255` (default 90), so slow
  commands (`díẹ̀díẹ̀` = 130) still turn the wheels instead of humming.
- Starts and direction changes ramp over `RAMP_MS` (150 ms). A reversal always
  passes through 0 and never flips the bridge at speed, because that current
  spike can brown out the ESP32. The ramp skips the dead zone below `MIN_DUTY`.
- `dúró`, the watchdog, move timeouts and link loss stop the motors **instantly**
  (no ramp).
- Turns spin in place (one wheel each way).

Tuning (top of the `.ino`, reflash after changing):

| Define | Default | Tune when |
|---|---|---|
| `MIN_DUTY` | `90` | slow commands only hum: raise it; slow is too fast: lower it |
| `LEFT_TRIM` / `RIGHT_TRIM` | `100` | robot curves going straight: lower the faster wheel's trim (e.g. `92`) |
| `RAMP_MS` | `150` | jerky starts: raise it; sluggish: lower it (`0` = no ramp) |
| `PWM_FREQ` | `1000` | leave at 1 kHz for an L298N |

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
python3 -m unittest services.robot.tests.test_robot services.robot.tests.test_wifi \
                    services.robot.tests.test_firmware -v
```

`test_firmware` compiles and runs the host C++ test of `motor_math.h` (speed map,
trim, ramp, dead zone, reversal through 0) with g++, and checks that the sketch
keeps PWM on ENA/ENB only, on ESP32-S3-safe pins.

## Evals (periodic — paid, needs `XAI_API_KEY`, no board)

Runs the full parser (rules + Grok) over labeled Yoruba commands; scores exact
intent match, including the negation/paraphrase cases only Grok resolves.

```bash
python3 services/robot/evals/eval_robot.py
```
