# kinematics.py — pure math for the Yoruba robot bridge. No ROS, no sockets.
# Host-testable (services/ros2/yoruba_robot/test/test_kinematics.py).
#
# The ESP32 firmware takes 5 commands: F/B/L/R/S with speed 0..255 and a timed
# window. ROS2 speaks Twist (linear.x, angular.z). This module maps between them
# and does the dead-reckoning odometry integration — the deterministic (code)
# half of the whole bridge, so the LLM/ROS half can stay dumb.
from __future__ import annotations

from dataclasses import dataclass
from math import cos, sin, pi

# Action names match services/robot/contract.py so RobotLink.move() takes them
# directly. The firmware wire letters (F/B/L/R/S) live only in link.WIRE.
FORWARD, BACKWARD, LEFT, RIGHT, STOP = "forward", "backward", "left", "right", "stop"
WIRE = {FORWARD: "F", BACKWARD: "B", LEFT: "L", RIGHT: "R", STOP: "S"}
WIRE_TO_ACTION = {v: k for k, v in WIRE.items()}


@dataclass(frozen=True)
class RobotSpec:
    """Physical constants of the real robot. Defaults match a small 2-wheel
    chassis (65 mm wheel, 150 mm wheelbase). All distances in metres, speeds in
    m/s and rad/s."""
    wheel_radius: float = 0.0325        # m
    wheel_separation: float = 0.15      # m, left-right distance
    max_linear: float = 0.35            # m/s at full duty, measured on the floor
    max_angular: float = 3.5            # rad/s at full spin (empirical)
    min_duty: int = 90                  # match firmware MIN_DUTY (0..255)


def twist_to_command(linear: float, angular: float, spec: RobotSpec,
                     deadband: float = 0.02) -> tuple[str, int]:
    """Twist (vx, wz) -> (direction, firmware speed 1..255; 0 only for STOP).

    The firmware only does pure moves (forward/back, spin in place), so snap to
    whichever component dominates. Below `deadband` both ways means stop.

    The returned number is the firmware's *speed* argument ("F,<speed>,<ms>"),
    NOT a PWM duty: the firmware itself maps speed onto MIN_DUTY..255
    (motor::speedToDuty). Sending a duty here would map it twice."""
    la = abs(linear)
    aa = abs(angular)
    if la < deadband and aa < deadband:
        return STOP, 0
    if la >= aa * spec.wheel_separation / 2:                # linear wins
        return (FORWARD if linear > 0 else BACKWARD), fraction_to_speed(la / spec.max_linear)
    # +wz = CCW = turn left (right wheel forward, left back) in REP-103.
    return (LEFT if angular > 0 else RIGHT), fraction_to_speed(aa / spec.max_angular)


def fraction_to_speed(frac: float) -> int:
    """0..1 of top speed -> firmware speed 1..255 (0 for 0). Inverse of
    speed_to_fraction: the firmware maps speed s to duty
    MIN + (s-1)(255-MIN)/254, so the motor's fraction of its usable range is
    exactly (s-1)/254."""
    frac = max(0.0, min(1.0, frac))
    if frac == 0.0:
        return 0
    return 1 + round(frac * 254)


def speed_to_fraction(speed: int) -> float:
    """Firmware speed 1..255 -> 0..1 of top speed (0 for speed <= 0)."""
    if speed <= 0:
        return 0.0
    return (min(speed, 255) - 1) / 254


def speed_to_duty(speed: int, min_duty: int) -> int:
    """Mirror of firmware motor::speedToDuty (integer maths, same rounding)."""
    if speed <= 0:
        return 0
    speed = min(255, max(1, speed))
    min_duty = min(255, max(0, min_duty))
    return min_duty + (speed - 1) * (255 - min_duty) // 254


def duty_to_fraction(duty: int, min_duty: int) -> float:
    """Applied PWM duty (from telemetry) -> 0..1 of top speed. Duties inside the
    dead zone (0 < |duty| < MIN_DUTY, only seen mid-ramp) count as stopped."""
    duty = abs(duty)
    if duty < max(min_duty, 1) or min_duty >= 255:
        return 0.0
    return min(1.0, (duty - min_duty) / (255 - min_duty))


def command_to_wheels(cmd: str, speed: int, spec: RobotSpec) -> tuple[float, float]:
    """Firmware command (direction, speed 1..255) -> (v_left, v_right) m/s.
    Used for dead reckoning when telemetry from the robot isn't available."""
    if cmd == STOP or speed <= 0:
        return 0.0, 0.0
    frac = speed_to_fraction(speed)
    if cmd == FORWARD:
        v = frac * spec.max_linear
        return v, v
    if cmd == BACKWARD:
        v = -frac * spec.max_linear
        return v, v
    v_wheel = frac * spec.max_angular * spec.wheel_separation / 2
    if cmd == LEFT:
        return -v_wheel, +v_wheel                           # CCW
    if cmd == RIGHT:
        return +v_wheel, -v_wheel
    return 0.0, 0.0


def duties_to_wheels(duty_left: int, duty_right: int, min_duty: int,
                     spec: RobotSpec) -> tuple[float, float]:
    """The robot's ACTUAL signed motor duties (telemetry) -> (v_left, v_right).

    Same-direction wheels roll at the calibrated straight-line speed
    (max_linear). Opposite-direction wheels (spin in place) are scaled to the
    calibrated spin rate (max_angular), because tyres scrub when spinning and
    turn slower than they roll. A wheel on its own (the other stopped) uses the
    straight-line figure."""
    fl = duty_to_fraction(duty_left, min_duty)
    fr = duty_to_fraction(duty_right, min_duty)
    sl = (duty_left > 0) - (duty_left < 0)
    sr = (duty_right > 0) - (duty_right < 0)
    if sl and sr and sl != sr:
        top = spec.max_angular * spec.wheel_separation / 2
    else:
        top = spec.max_linear
    return sl * fl * top, sr * fr * top


@dataclass
class OdomState:
    x: float = 0.0
    y: float = 0.0
    theta: float = 0.0                  # yaw, rad, wrapped to (-pi, pi]
    vx: float = 0.0                     # body-frame linear velocity (m/s)
    wz: float = 0.0                     # angular velocity (rad/s)


def integrate(state: OdomState, v_left: float, v_right: float, dt: float,
              spec: RobotSpec) -> OdomState:
    """One differential-drive integration step. Uses exact arc kinematics when
    the robot is turning (so a long spin doesn't drift linearly), straight-line
    when it isn't. Yaw stays wrapped to keep RViz quaternions sane."""
    if dt <= 0:
        return state
    v = (v_right + v_left) / 2
    w = (v_right - v_left) / spec.wheel_separation
    if abs(w) < 1e-6:
        dx = v * cos(state.theta) * dt
        dy = v * sin(state.theta) * dt
        dtheta = 0.0
    else:
        dtheta = w * dt
        r = v / w
        dx = r * (sin(state.theta + dtheta) - sin(state.theta))
        dy = -r * (cos(state.theta + dtheta) - cos(state.theta))
    theta = _wrap(state.theta + dtheta)
    return OdomState(state.x + dx, state.y + dy, theta, v, w)


def _wrap(a: float) -> float:
    """Wrap angle to (-pi, pi]."""
    a = (a + pi) % (2 * pi) - pi
    return pi if a == -pi else a


def quaternion_from_yaw(theta: float) -> tuple[float, float, float, float]:
    """(x, y, z, w) quaternion for a planar yaw rotation. ROS message order."""
    return 0.0, 0.0, sin(theta / 2), cos(theta / 2)


def advance_wheel_angles(left: float, right: float, v_left: float,
                         v_right: float, dt: float, spec: RobotSpec
                         ) -> tuple[float, float]:
    """Wheel joint angles (rad) after rolling at (v_left, v_right) m/s for dt.
    Positive = rolling forward (joint axis +y in the URDF). Wrapped to
    (-pi, pi] so the published values stay small; RViz only needs the angle."""
    if dt <= 0:
        return left, right
    return (_wrap(left + v_left / spec.wheel_radius * dt),
            _wrap(right + v_right / spec.wheel_radius * dt))


TELEMETRY_FRESH_S = 0.3        # 6 missed 20 Hz samples = no longer live


def motion_source(telemetry, age: float, telemetry_ever: bool,
                  commanded: tuple[float, float], spec: RobotSpec,
                  fresh: float = TELEMETRY_FRESH_S
                  ) -> tuple[tuple[float, float], str]:
    """Which wheel speeds the twin should show, and why.

    1. "telemetry": the robot is reporting its applied motor duties -> use them.
       This is what the motors are really doing, whoever commands them.
    2. "telemetry-lost": telemetry was flowing and stopped. The firmware stops
       the motors when WiFi or the command link drops, so show it stopped
       rather than coasting on a stale command.
    3. "commands": no telemetry ever (older firmware): dead-reckon from what
       this bridge commanded.
    """
    if telemetry is not None and age <= fresh:
        return duties_to_wheels(telemetry.duty_left, telemetry.duty_right,
                                telemetry.min_duty, spec), "telemetry"
    if telemetry_ever:
        return (0.0, 0.0), "telemetry-lost"
    return commanded, "commands"
