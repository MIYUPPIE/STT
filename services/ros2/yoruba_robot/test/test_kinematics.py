# test_kinematics.py — gate tests for yoruba_robot/kinematics.py.
# No ROS, no sockets. Run with system python or any python (pure stdlib + math).
#   python3 -m unittest services.ros2.yoruba_robot.test.test_kinematics -v
# Also discovered by colcon test (ament_python's pytest).
import math
import unittest

from services.ros2.yoruba_robot.yoruba_robot.kinematics import (
    FORWARD, BACKWARD, LEFT, RIGHT, STOP, OdomState, RobotSpec,
    command_to_wheels, integrate, quaternion_from_yaw, twist_to_command, _wrap)


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

    def test_slow_still_above_min_duty(self):
        """A tiny Twist must not come out below MIN_DUTY — the wheels would
        just hum. Matches the firmware's speedToDuty."""
        _, s = twist_to_command(0.05, 0, SPEC)
        self.assertGreaterEqual(s, SPEC.min_duty)

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


if __name__ == "__main__":
    unittest.main()
