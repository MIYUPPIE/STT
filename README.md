# Yoruba Voice Robot — speech → ESP32 → ROS2 digital twin

Speak Yoruba. A real 2-wheel ESP32 robot drives on your floor, a matching robot
drives in Gazebo, and RViz shows both at the same time. The STT model, the voice
assistant, the robot firmware, the ROS2 package, the Gazebo world and the RViz
layout all live in this one repo. One voice, one `/cmd_vel` topic, two robots.

```
                    ┌─ live_caption.py ─ Silero VAD ─ N-ATLAS STT ─ Yoruba parser ─┐
                    │  (conda py3.13, local)                                        │ TCP :7447
Yoruba mic  ────────┤                                                               │  (loopback)
                    │                                                               ▼
                    │                                              ┌─ voice_relay ─ /cmd_vel ─┬─► robot_bridge ─ WiFi ─ ESP32 ─ motors
                    │                                              │  (ROS2 py3.12)           │       └► /odom + TF ─┐
                    │                                              │                          │                       │
                    │                                              │                          └─► gz-sim DiffDrive ──┤
                    │                                              │                                        │         │
                    │                                              │                                   /sim_odom      │
                    │                                              │                                        └─► RViz ◄┘
                    │                                              │
         (optional) └─► Grok caption refine / Yoruba chat brain
                        YarnGPT TTS confirmations (speaker)
```

**Local by default.** STT, VAD, kinematics and firmware control run on your
machine. Grok (xAI) and YarnGPT are optional cloud APIs for correction/chat and
Yoruba speech synthesis. The robot works fully offline.

## What's in the box

| Piece | Where | Role |
|---|---|---|
| **STT** | [services/stt/](services/stt/README.md) | N-ATLAS Yoruba ASR (NCAIR1/Yoruba-ASR). Built once offline from your Hugging Face cache. **Nothing downloads.** 78% command accuracy on a synthetic voice; legacy model 56%. |
| **Live app** | [live_caption.py](live_caption.py) | Mic → VAD → STT → (chat/refine) → (robot/servo/ROS2). Half-duplex: mic mutes during TTS playback. |
| **Robot service** | [services/robot/](services/robot/README.md) | Yoruba parser + wire link to the ESP32. **WiFi (TCP + mDNS + /24 sweep)** or USB serial. Rules first; Grok fallback for unmatched phrasing. |
| **ESP32 firmware** | [firmware/esp32s3_robot/](firmware/esp32s3_robot/esp32s3_robot.ino) | Dual-channel command server: WiFi TCP :3333 + USB serial. **L298N speed control on ENA/ENB** (GPIO 41/42); IN1-4 are direction only; 150 ms ramp with reversal through zero; firmware watchdog + disconnect-stops-motors. |
| **ROS2 digital twin** | [services/ros2/](services/ros2/README.md) | `yoruba_robot` package: `voice_relay` + `robot_bridge` nodes, URDF/xacro, Gazebo world, three launch files (`sim`, `real`, `twin`). |
| **Refiner** | [services/refine/](services/refine/README.md) | Grok corrects raw Yoruba captions in place. |
| **Brain** | [services/brain/](services/brain/README.md) | Grok conversational Yoruba reply (`--chat`). |
| **TTS** | [services/tts/](services/tts/README.md) | YarnGPT Yoruba speech. |
| **Servo** | [services/servo/](services/servo/README.md) | ESP32-S3 servo over HTTP (`--servo`). |

## Quick start

### One-time setup

```bash
# System deps (PortAudio for the mic)
sudo apt-get install -y portaudio19-dev
pip install numpy sounddevice torch faster-whisper ctranslate2 \
            transformers huggingface_hub pyserial

# Grok + YarnGPT keys for the optional cloud stages (leave empty if you only
# want the robot path; the pipeline degrades gracefully).
cat > .env <<EOF
XAI_API_KEY=your-xai-key
YARN_API_KEY=your-yarngpt-key
EOF

# STT model: if NCAIR1/Yoruba-ASR is already in your HF cache, this is one
# offline conversion; nothing downloads. First run of the app does it too.
python3 -m services.stt.model --convert       # ~5 min, one time
```

### ESP32 firmware (one time)

1. `cp firmware/esp32s3_robot/secrets.h.example firmware/esp32s3_robot/secrets.h`
   and set `WIFI_SSID` / `WIFI_PASSWORD`. 2.4 GHz network, same one the laptop uses.
2. Wire the L298N (take the ENA/ENB jumper caps off):
   ```
   IN1 → GPIO 4   IN2 → GPIO 5   ENA → GPIO 41   (left motor)
   IN3 → GPIO 6   IN4 → GPIO 7   ENB → GPIO 42   (right motor)
   GND → GND (shared)             +12V/VS ← motor battery
   ```
3. Arduino IDE → Board: **ESP32S3 Dev Module**, **USB CDC On Boot: Enabled** if
   your cable is in the native USB port. Upload. Serial Monitor at 115200
   should print `WiFi CONNECTED  ip=...  tcp=3333  host=yoruba-robot.local`.

### ROS2 workspace (one time)

```bash
ln -sfn "$(pwd)/services/ros2/yoruba_robot" ~/ros2_ws/src/yoruba_robot
cd ~/ros2_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install --packages-select yoruba_robot
```

Every new terminal needs these two lines:

```bash
source /opt/ros/jazzy/setup.bash
source ~/ros2_ws/install/setup.bash
```

(Add them to `~/.bashrc` if you want them automatic.)

## Run

### Just the robot, by voice (no ROS, no sim)

```bash
python3 live_caption.py --cpu --robot --speak
```

Say `síwájú` (forward), `sẹ́yìn` (back), `òsì` (left), `ọ̀tún` (right),
`dúró` (stop). Add `kíákíá` for fast, `díẹ̀díẹ̀` for slow.

### Simulator only (no real robot)

```bash
# shell 1:
ros2 launch yoruba_robot sim.launch.py
# shell 2:
ros2 run teleop_twist_keyboard teleop_twist_keyboard
```

### Full digital twin (real + sim, driven by voice)

Three terminals, each sourced:

```bash
# shell 1 — Gazebo + robot_bridge + RViz
ros2 launch yoruba_robot twin.launch.py

# shell 2 — voice line listener
ros2 run yoruba_robot voice_relay

# shell 3 — your voice
/home/okhub/anaconda3/bin/python live_caption.py --cpu --ros --speak
```

RViz shows both odometries: green trail = real robot dead reckoning, orange =
Gazebo ground truth. Divergence is calibration drift you can tune out of
`robot_bridge` parameters.

### Other modes

```bash
python3 live_caption.py                  # live Yoruba captioning + Grok refine
python3 live_caption.py --chat --speak   # Yoruba voice assistant (Grok + YarnGPT)
python3 live_caption.py --servo          # ESP32-S3 servo
python3 live_caption.py --no-refine      # captioning only, no Grok
```

Everything combines: `--chat --robot --speak`, `--ros --robot --speak`, etc.

## Repo layout

```
live_caption.py           # the always-on app (all stages)
benchmark.py              # fp32 transformers vs fp16 faster-whisper on one utterance
download.py               # pull a Whisper Yoruba model from Hugging Face
test.py                   # record-until-silence STT loop
services/
  stt/                    # N-ATLAS Yoruba ASR picker (offline from HF cache)
  refine/                 # Grok caption corrector
  brain/                  # Grok conversational Yoruba brain (--chat)
  tts/                    # YarnGPT Yoruba text-to-speech
  servo/                  # ESP32-S3 servo over HTTP (--servo)
  robot/                  # ESP32-S3 robot over WiFi/USB (--robot) + ROS sink
  ros2/
    yoruba_robot/         # the ROS2 package: nodes, URDF, worlds, launches
firmware/
  esp32s3_robot/          # dual-channel (WiFi + USB) H-bridge motor controller
  esp32s3_servo/          # camera + LED + servo HTTP server
whisper-small-yoruba-ct2/ # legacy CT2 build  (git-ignored)
natlas-yoruba-asr-ct2/    # N-ATLAS CT2 build (git-ignored, built offline)
```

Each service directory holds its own code, contract, tests, evals and README.
See [CLAUDE.md](CLAUDE.md) for the design rules.

## Tests

Two lanes. Gate tests are **free, deterministic, no network, <2s**:

```bash
python3 -m unittest services.robot.tests.test_robot \
                    services.robot.tests.test_wifi \
                    services.robot.tests.test_firmware \
                    services.robot.tests.test_ros_sink \
                    services.stt.tests.test_stt \
                    services.servo.tests.test_servo \
                    services.tts.tests.test_tts \
                    services.refine.tests.test_refiner \
                    services.brain.tests.test_brain \
                    services.ros2.yoruba_robot.test.test_kinematics
```

195 tests pass; this covers:
- STT model resolution + offline build from the HF cache
- Yoruba parser (fuzzy autocorrect, negations, split words, speed words)
- WiFi link (TCP transport, mDNS, LAN sweep, reconnect on drops)
- Firmware wiring guard + host C++ test of the ENA/ENB speed math
- ROS line relay (reconnect, fail-fast)
- ROS kinematics (Twist↔F/B/L/R, dead-reckoning odometry including spin-no-drift)
- Grok and YarnGPT services (injected HTTP transport)

Paid evals hit the real APIs and score quality against a threshold:

```bash
python3 services/refine/evals/eval_yoruba.py     # needs XAI_API_KEY
python3 services/brain/evals/eval_brain.py       # needs XAI_API_KEY
python3 services/tts/evals/eval_tts.py           # needs YARN_API_KEY
python3 services/stt/evals/eval_stt.py           # local, free, slow: N-ATLAS vs legacy
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Package 'yoruba_robot' not found` | `source ~/ros2_ws/install/setup.bash` in that terminal |
| `Unable to parse the value of parameter robot_description as yaml` | pull latest; launch files wrap xacro in `ParameterValue(value_type=str)` |
| `no robot answered on <subnet> port 3333` | laptop + ESP32 must be on the same WiFi; pin with `ROBOT_HOST=<ip>` in `.env` |
| `--health` ACK fails but port opens | Arduino "USB CDC On Boot" doesn't match the USB port the cable is in |
| Slow commands only hum, don't move | raise `MIN_DUTY` in [esp32s3_robot.ino](firmware/esp32s3_robot/esp32s3_robot.ino) |
| Robot curves on a straight command | tune `LEFT_TRIM` / `RIGHT_TRIM` (motor imbalance) |
| STT prints junk on real speech | still CPU-only; GPU libs (`libcublas.so.12`) aren't loadable in this Python |
| `ros2 topic echo` says "topic not published yet" | DDS discovery lag; start the subscriber before the publisher, or use `--once` after both exist |
