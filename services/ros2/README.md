# services/ros2

ROS2 digital twin of the Yoruba voice-controlled 2-wheel robot. Same voice drives
the real ESP32 (over WiFi) and the Gazebo model at the same time; RViz shows both.

```
Yoruba speech ─► live_caption.py --ros ─► TCP :7447 ─► voice_relay ─► /cmd_vel
    (conda py3.13: STT + VAD)             (localhost)   (ROS2 py3.12)      │
                                                                           ├──► gz-sim DiffDrive ─► sim robot (RViz)
                                                                           └──► robot_bridge ─► ESP32 WiFi ─► real robot
                                                                                      └─► /odom + TF ─► RViz
```

**Why the TCP relay.** ROS2 Jazzy's `rclpy` is bound to system Python 3.12;
faster-whisper lives in conda Python 3.13. Instead of fighting that, each
process stays in its own env and talks over the firmware's own five-letter line
protocol (`F,200\n` / `S\n`). One contract spans both borders.

## Package: `yoruba_robot`

Lives under `services/ros2/yoruba_robot/` (source of truth) and is symlinked
into `~/ros2_ws/src/yoruba_robot` so `colcon build` finds it. `--symlink-install`
keeps edits live without rebuilds.

| File | Role |
|---|---|
| `yoruba_robot/kinematics.py` | pure math: Twist↔F/B/L/R, dead-reckoning odometry integration, yaw→quaternion. Host-tested, no ROS. |
| `yoruba_robot/robot_bridge.py` | ROS2 node. Subscribes `/cmd_vel`, drives the ESP32 (`services.robot.link.TcpTransport`), publishes `/odom` + `odom→base_link` TF. |
| `yoruba_robot/voice_relay.py` | ROS2 node. Listens on 127.0.0.1:7447, turns each `F,200\n` line into a `/cmd_vel` Twist. |
| `description/robot.urdf.xacro` | Shared URDF for Gazebo + RViz. Includes the `gz-sim-diff-drive-system` plugin so the sim subscribes to the same `/cmd_vel`. |
| `worlds/empty.sdf` | Minimal Gazebo world (ground + sun). |
| `config/robot.rviz` | RViz layout: Grid, RobotModel, TF, two Odometry trails (green = real dead reckoning, orange = sim ground truth). |
| `launch/sim.launch.py` | Gazebo + RSP + RViz + ros_gz bridge. Simulation only. |
| `launch/real.launch.py` | `robot_bridge` + RSP + RViz. Real hardware only. |
| `launch/twin.launch.py` | **Digital twin**: everything, real + sim on one `/cmd_vel`. |

## Build

Once per machine:

```bash
ln -sfn /home/okhub/Documents/PROJECTS/STT/services/ros2/yoruba_robot \
        /home/okhub/ros2_ws/src/yoruba_robot
cd /home/okhub/ros2_ws
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install --packages-select yoruba_robot
source install/setup.bash
```

## Run

Each terminal first: `source /opt/ros/jazzy/setup.bash && source ~/ros2_ws/install/setup.bash`.

### Sim only (no hardware)

```bash
ros2 launch yoruba_robot sim.launch.py
# in another terminal:
ros2 run teleop_twist_keyboard teleop_twist_keyboard   # drive the sim robot
```

### Real robot only (no Gazebo)

```bash
ros2 launch yoruba_robot real.launch.py
ros2 run teleop_twist_keyboard teleop_twist_keyboard
```

### Digital twin (real + sim, one voice)

```bash
# shell 1: Gazebo + robot_bridge + RViz
ros2 launch yoruba_robot twin.launch.py

# shell 2: voice_relay (bridges live_caption.py -> /cmd_vel)
ros2 run yoruba_robot voice_relay

# shell 3: Yoruba STT publishing to the relay (conda py3.13)
/home/okhub/anaconda3/bin/python live_caption.py --cpu --ros --speak
```

Say `síwájú` / `sẹ́yìn` / `òsì` / `ọ̀tún` / `dúró`. The real robot moves; the
Gazebo robot moves in lockstep; RViz shows both odometries (green trail is the
real robot's dead reckoning, orange is the sim's ground truth — any divergence
is calibration drift to fix).

## Topics

| Topic | From | Note |
|---|---|---|
| `/cmd_vel` (geometry_msgs/Twist) | voice_relay or teleop | subscribed by Gazebo + robot_bridge |
| `/odom` (nav_msgs/Odometry) | robot_bridge | real-robot dead reckoning from commanded speeds |
| `/sim_odom` (nav_msgs/Odometry) | Gazebo via ros_gz_bridge | sim ground truth |
| `/tf` | robot_bridge | `odom → base_link` |
| `/joint_states` | Gazebo | wheel positions for `robot_state_publisher` → RViz |
| `/clock` | Gazebo | use_sim_time |

## Config (`robot_bridge` ROS parameters)

| Param | Default | Meaning |
|---|---|---|
| `require_robot` | `false` | fail-start if the ESP32 can't be reached |
| `publish_rate` | `50.0` Hz | odom + TF rate |
| `cmd_timeout` | `0.5` s | auto-stop if `/cmd_vel` goes quiet |
| `hold_ms` | `1000` ms | firmware move-window per command (keepalive matches) |
| `wheel_radius` / `wheel_separation` | `0.0325` / `0.15` m | override to match your chassis |
| `max_linear` / `max_angular` | `0.35` m/s / `3.5` rad/s | floor-measured max to map Twist→speed |

`voice_relay` takes `host` (default `127.0.0.1`) and `port` (`7447`).

## Tests

**Gate (free, no ROS runtime, <1s):**

```bash
python3 -m unittest services.ros2.yoruba_robot.test.test_kinematics -v
python3 -m unittest services.robot.tests.test_ros_sink -v
```

Covers the ↔ Twist math, dead-reckoning odometry (straight line, spin-in-place
no-drift, arc, yaw wrapping), the `line_to_twist` parser (REP-103 sign
convention), the live_caption → relay TCP sink (connect, send, reconnect on
peer drop, fail-fast while relay is down).

**Via colcon (same tests, plus the ROS-dependent `test_voice_relay`):**

```bash
cd ~/ros2_ws && colcon test --packages-select yoruba_robot && colcon test-result --verbose
```

## Verified real end-to-end

With both nodes running and the real robot powered on:
- `robot_bridge` discovered the ESP32 at `wifi 192.168.1.197:3333` on its own
  (mDNS/sweep), and the "voice→relay→cmd_vel→robot_bridge→ESP32" chain carried
  F,200 and L,200 to real motion.
- `/odom` published 447 samples during a 10 s window; a forward-then-left
  sequence integrated cleanly from (0, 0, 0) to (0.117 m, 0 m, 1.17 rad).
