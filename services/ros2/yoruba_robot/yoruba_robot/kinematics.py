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
    """Twist (vx, wz) -> (direction, speed 0..255).

    A differential drive could in general mix linear + angular, but the firmware
    only supports pure moves (forward/back + spin-in-place turns). So we snap to
    whichever component dominates. Below `deadband` both ways means stop. speed
    = 0 only for STOP; a direction with speed = 0 is nonsense."""
    la = abs(linear)
    aa = abs(angular)
    if la < deadband and aa < deadband:
        return STOP, 0
    if la >= aa * spec.wheel_separation / 2:                # linear wins
        duty = _scale(la / spec.max_linear, spec)
        return (FORWARD if linear > 0 else BACKWARD), duty
    duty = _scale(aa / spec.max_angular, spec)
    # +wz = CCW = turn left (right wheel forward, left back) in REP-103.
    return (LEFT if angular > 0 else RIGHT), duty


def _scale(frac: float, spec: RobotSpec) -> int:
    """Clamp 0..1, then map onto min_duty..255 so slow commands still turn the
    wheels (same shape as motor::speedToDuty on the firmware). Matches so a
    0.1 m/s Twist produces the same floor speed as 'díẹ̀díẹ̀'."""
    frac = max(0.0, min(1.0, frac))
    if frac == 0.0:
        return 0
    span = 255 - spec.min_duty
    return spec.min_duty + round(frac * span)


def command_to_wheels(cmd: str, speed: int, spec: RobotSpec) -> tuple[float, float]:
    """Firmware command -> commanded (v_left, v_right) in m/s. Used to drive the
    odometry integrator so the bridge's /odom tracks what the robot was told to
    do (dead reckoning; good enough for RViz + Gazebo match without encoders)."""
    if cmd == STOP or speed <= 0:
        return 0.0, 0.0
    frac = _duty_to_fraction(speed, spec)
    if cmd == FORWARD:
        v = frac * spec.max_linear
        return v, v
    if cmd == BACKWARD:
        v = -frac * spec.max_linear
        return v, v
    w = frac * spec.max_angular                             # rad/s intended
    v_wheel = w * spec.wheel_separation / 2
    if cmd == LEFT:
        return -v_wheel, +v_wheel                           # CCW
    if cmd == RIGHT:
        return +v_wheel, -v_wheel
    return 0.0, 0.0


def _duty_to_fraction(duty: int, spec: RobotSpec) -> float:
    """Inverse of _scale, so round-trips are stable."""
    duty = max(0, min(255, duty))
    if duty <= spec.min_duty:
        return 0.0 if duty == 0 else 1.0 / (255 - spec.min_duty + 1)
    return (duty - spec.min_duty) / (255 - spec.min_duty)


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
