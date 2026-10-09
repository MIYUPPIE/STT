# test_kinematics.py — gate tests for yoruba_robot/kinematics.py.
# No ROS, no sockets. Run with system python or any python (pure stdlib + math).
#   python3 -m unittest services.ros2.yoruba_robot.test.test_kinematics -v
# Also discovered by colcon test (ament_python's pytest).
import math
import unittest

from services.ros2.yoruba_robot.yoruba_robot.kinematics import (
    FORWARD, BACKWARD, LEFT, RIGHT, STOP, OdomState, RobotSpec,
    advance_wheel_angles, command_to_wheels, duties_to_wheels, duty_to_fraction,
    fraction_to_speed, integrate, motion_source, quaternion_from_yaw, speed_to_duty,
    speed_to_fraction, twist_to_command, _wrap)


SPEC = RobotSpec()


class TestTwistToCommand(unittest.TestCase):
    def test_deadband_is_stop(self):
        self.assertEqual(twist_to_command(0, 0, SPEC), (STOP, 0))
        self.assertEqual(twist_to_command(0.01, -0.01, SPEC), (STOP, 0))

    def test_forward_back(self):
        cmd, s = twist_to_command(SPEC.max_linear, 0, SPEC)
        self.assertEqual((cmd, s), (FORWARD, 255))
        cmd, s = twist_to_command(-SPEC.max_linear, 0, SPEC)
        self.assertEqual((cmd, s), (BACKWARD, 255))

    def test_slow_still_turns_the_wheels(self):
        """A tiny Twist becomes a small firmware speed, which the firmware maps
        to a duty at or above MIN_DUTY: the wheels turn instead of humming."""
        _, s = twist_to_command(0.05, 0, SPEC)
        self.assertGreaterEqual(s, 1)
        self.assertGreaterEqual(speed_to_duty(s, SPEC.min_duty), SPEC.min_duty)

    def test_speed_is_not_mapped_twice(self):
        """Regression: the bridge used to send a DUTY (90-255) as the firmware
        SPEED, and the firmware mapped it to duty again, so 'slow' ran fast.
        A Twist at 40% of top speed must reach the motors at 40% of the
        usable duty range."""
        _, s = twist_to_command(0.4 * SPEC.max_linear, 0, SPEC)
        duty = speed_to_duty(s, SPEC.min_duty)
        self.assertAlmostEqual(duty_to_fraction(duty, SPEC.min_duty), 0.4, delta=0.01)

    def test_turns_use_rep103_sign(self):
        """+wz = CCW = turn LEFT. If this flips, the robot drives mirror-image
        of RViz."""
        cmd, s = twist_to_command(0, SPEC.max_angular, SPEC)
        self.assertEqual((cmd, s), (LEFT, 255))
        cmd, s = twist_to_command(0, -SPEC.max_angular, SPEC)
        self.assertEqual((cmd, s), (RIGHT, 255))

    def test_dominant_component_wins(self):
        # linear dominates: a small turn + full forward -> FORWARD
        self.assertEqual(twist_to_command(0.3, 0.1, SPEC)[0], FORWARD)
        # angular dominates
        self.assertEqual(twist_to_command(0.02, 2.0, SPEC)[0], LEFT)

    def test_clamped_at_max(self):
        _, s = twist_to_command(10.0, 0, SPEC)
        self.assertEqual(s, 255)


class TestCommandToWheels(unittest.TestCase):
    def test_stop_is_zero(self):
        self.assertEqual(command_to_wheels(STOP, 0, SPEC), (0.0, 0.0))
        self.assertEqual(command_to_wheels(FORWARD, 0, SPEC), (0.0, 0.0))

    def test_full_forward(self):
        vl, vr = command_to_wheels(FORWARD, 255, SPEC)
        self.assertAlmostEqual(vl, SPEC.max_linear, places=4)
        self.assertAlmostEqual(vr, SPEC.max_linear, places=4)

    def test_full_backward(self):
        vl, vr = command_to_wheels(BACKWARD, 255, SPEC)
        self.assertLess(vl, 0)
        self.assertAlmostEqual(vl, vr)

    def test_turns_are_opposite(self):
        vl, vr = command_to_wheels(LEFT, 255, SPEC)
        self.assertAlmostEqual(vl, -vr)
        self.assertLess(vl, 0)                              # left wheel back
        vl, vr = command_to_wheels(RIGHT, 255, SPEC)
        self.assertGreater(vl, 0)


class TestIntegrate(unittest.TestCase):
    def test_straight_line(self):
        s = integrate(OdomState(), SPEC.max_linear, SPEC.max_linear, 1.0, SPEC)
        self.assertAlmostEqual(s.x, SPEC.max_linear, places=4)
        self.assertAlmostEqual(s.y, 0.0, places=6)
        self.assertAlmostEqual(s.theta, 0.0, places=6)

    def test_spin_in_place_doesnt_drift(self):
        """A pure spin: x,y must stay at origin; after 2π/w seconds the yaw
        must be back where it started. If the integrator is linear-only, x
        drifts and the test fails."""
        vl, vr = command_to_wheels(LEFT, 255, SPEC)
        s = OdomState()
        dt = 0.01
        steps = int(2 * math.pi / SPEC.max_angular / dt)
        for _ in range(steps):
            s = integrate(s, vl, vr, dt, SPEC)
        self.assertAlmostEqual(s.x, 0.0, places=3)
        self.assertAlmostEqual(s.y, 0.0, places=3)
        self.assertAlmostEqual(abs(s.theta), 0.0, places=1)  # wrapped near 0

    def test_arc(self):
        """Right wheel twice as fast as left -> curves toward the slower wheel."""
        s = OdomState()
        for _ in range(100):
            s = integrate(s, 0.1, 0.2, 0.01, SPEC)
        self.assertGreater(s.y, 0.0)                        # curves left (CCW)
        self.assertGreater(s.theta, 0.0)

    def test_yaw_wrapped(self):
        s = OdomState()
        for _ in range(1000):
            s = integrate(s, -1.0, 1.0, 0.01, SPEC)         # keep spinning
        self.assertLessEqual(s.theta, math.pi)
        self.assertGreater(s.theta, -math.pi)

    def test_dt_zero_noop(self):
        s = OdomState(x=1, y=2, theta=0.5)
        self.assertIs(integrate(s, 1, 1, 0, SPEC), s)


class TestQuaternion(unittest.TestCase):
    def test_zero_yaw_is_identity(self):
        self.assertEqual(quaternion_from_yaw(0.0), (0.0, 0.0, 0.0, 1.0))

    def test_90_deg_yaw(self):
        x, y, z, w = quaternion_from_yaw(math.pi / 2)
        self.assertAlmostEqual(z, math.sin(math.pi / 4))
        self.assertAlmostEqual(w, math.cos(math.pi / 4))

    def test_wrap(self):
        self.assertAlmostEqual(_wrap(3 * math.pi), math.pi)
        self.assertAlmostEqual(_wrap(-3 * math.pi), math.pi)
        self.assertAlmostEqual(_wrap(0.5), 0.5)


class TestFirmwareMapping(unittest.TestCase):
    def test_speed_to_duty_matches_firmware(self):
        """Same values as firmware/esp32s3_robot/tests/test_motor_math.cpp."""
        self.assertEqual(speed_to_duty(0, 90), 0)
        self.assertEqual(speed_to_duty(1, 90), 90)
        self.assertEqual(speed_to_duty(255, 90), 255)
        self.assertEqual(speed_to_duty(999, 90), 255)
        self.assertEqual(speed_to_duty(128, 0), 127)
        self.assertEqual(speed_to_duty(200, 90), 90 + 199 * 165 // 254)

    def test_fraction_speed_round_trip(self):
        for f in (0.05, 0.25, 0.5, 0.9, 1.0):
            self.assertAlmostEqual(speed_to_fraction(fraction_to_speed(f)), f, delta=0.003)
        self.assertEqual(fraction_to_speed(0), 0)
        self.assertEqual(speed_to_fraction(0), 0.0)

    def test_duty_dead_zone_is_stopped(self):
        self.assertEqual(duty_to_fraction(0, 90), 0.0)
        self.assertEqual(duty_to_fraction(60, 90), 0.0)          # mid-ramp, can't turn
        self.assertEqual(duty_to_fraction(-255, 90), 1.0)


class TestTelemetryWheels(unittest.TestCase):
    """Telemetry duties -> wheel speeds must agree with the command model, so
    the twin doesn't jump when telemetry arrives."""

    def test_forward_matches_command_model(self):
        for speed in (60, 130, 200, 255):
            d = speed_to_duty(speed, SPEC.min_duty)
            vl, vr = duties_to_wheels(d, d, SPEC.min_duty, SPEC)
            cl, cr = command_to_wheels(FORWARD, speed, SPEC)
            self.assertAlmostEqual(vl, cl, delta=0.003)
            self.assertAlmostEqual(vr, cr, delta=0.003)

    def test_spin_matches_command_model(self):
        d = speed_to_duty(200, SPEC.min_duty)
        vl, vr = duties_to_wheels(-d, d, SPEC.min_duty, SPEC)
        cl, cr = command_to_wheels(LEFT, 200, SPEC)
        self.assertAlmostEqual(vl, cl, delta=0.003)
        self.assertAlmostEqual(vr, cr, delta=0.003)

    def test_stopped_and_backward(self):
        self.assertEqual(duties_to_wheels(0, 0, 90, SPEC), (0.0, 0.0))
        vl, vr = duties_to_wheels(-255, -255, 90, SPEC)
        self.assertAlmostEqual(vl, -SPEC.max_linear)
        self.assertAlmostEqual(vr, -SPEC.max_linear)


class TestMotionSource(unittest.TestCase):
    class T:
        def __init__(self, dl, dr, md=90):
            self.duty_left, self.duty_right, self.min_duty = dl, dr, md

    def test_live_telemetry_wins_over_commands(self):
        """The robot reports it is spinning even though THIS bridge commanded
        nothing (someone else is driving): the twin must spin."""
        (vl, vr), src = motion_source(self.T(-255, 255), 0.05, True, (0.0, 0.0), SPEC)
        self.assertEqual(src, "telemetry")
        self.assertLess(vl, 0)
        self.assertGreater(vr, 0)

    def test_telemetry_says_stopped_while_commanded_forward(self):
        """Firmware watchdog/WiFi stop: commanded forward, motors at 0 -> 0."""
        (vl, vr), src = motion_source(self.T(0, 0), 0.05, True, (0.3, 0.3), SPEC)
        self.assertEqual((vl, vr, src), (0.0, 0.0, "telemetry"))

    def test_stale_telemetry_means_stopped(self):
        (vl, vr), src = motion_source(self.T(200, 200), 1.0, True, (0.3, 0.3), SPEC)
        self.assertEqual((vl, vr, src), (0.0, 0.0, "telemetry-lost"))

    def test_no_telemetry_ever_uses_commands(self):
        (vl, vr), src = motion_source(None, float("inf"), False, (0.2, 0.2), SPEC)
        self.assertEqual((vl, vr, src), (0.2, 0.2, "commands"))


class TestWheelAngles(unittest.TestCase):
    def test_one_revolution_per_circumference(self):
        """Rolling 2*pi*r metres turns the wheel exactly once (back to 0)."""
        circ = 2 * math.pi * SPEC.wheel_radius
        left = right = 0.0
        for _ in range(1000):
            left, right = advance_wheel_angles(left, right, circ, circ, 0.001, SPEC)
        self.assertAlmostEqual(math.sin(left), 0.0, places=6)
        self.assertAlmostEqual(math.cos(left), 1.0, places=6)

    def test_quarter_turn_and_sign(self):
        q = math.pi / 2 * SPEC.wheel_radius                  # metres for 90 deg
        left, right = advance_wheel_angles(0.0, 0.0, q, -q, 1.0, SPEC)
        self.assertAlmostEqual(left, math.pi / 2)
        self.assertAlmostEqual(right, -math.pi / 2)          # backwards = negative

    def test_spin_in_place_wheels_opposite(self):
        vl, vr = command_to_wheels(LEFT, 255, SPEC)
        left, right = advance_wheel_angles(0.0, 0.0, vl, vr, 0.1, SPEC)
        self.assertLess(left, 0)
        self.assertGreater(right, 0)

    def test_dt_zero(self):
        self.assertEqual(advance_wheel_angles(0.3, -0.2, 1, 1, 0, SPEC), (0.3, -0.2))


if __name__ == "__main__":
    unittest.main()
