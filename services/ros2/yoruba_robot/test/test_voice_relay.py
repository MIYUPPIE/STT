# test_voice_relay.py — gate test for line_to_twist() in voice_relay. Needs
# geometry_msgs.Twist, which is in system Python 3.12 (where rclpy lives) but
# not conda 3.13, so this test is auto-skipped when geometry_msgs isn't
# importable; colcon test picks it up via ament_python's pytest.
import unittest

try:
    from geometry_msgs.msg import Twist                      # noqa: F401
    HAVE_ROS = True
except Exception:
    HAVE_ROS = False


@unittest.skipUnless(HAVE_ROS, "geometry_msgs not available (not in a ROS2 env)")
class TestLineToTwist(unittest.TestCase):
    def setUp(self):
        # Import inside setUp so test discovery doesn't fail when ROS is absent.
        from yoruba_robot.kinematics import RobotSpec
        from yoruba_robot.voice_relay import line_to_twist
        self.spec = RobotSpec()
        self.ltt = line_to_twist

    def test_stop(self):
        t = self.ltt("S", self.spec)
        self.assertAlmostEqual(t.linear.x, 0.0)
        self.assertAlmostEqual(t.angular.z, 0.0)

    def test_forward_full(self):
        t = self.ltt("F,255", self.spec)
        self.assertAlmostEqual(t.linear.x, self.spec.max_linear, places=6)
        self.assertAlmostEqual(t.angular.z, 0.0)

    def test_backward(self):
        t = self.ltt("B,200", self.spec)
        self.assertLess(t.linear.x, 0)

    def test_left_right_sign(self):
        """+wz = CCW = LEFT (REP-103)."""
        tl = self.ltt("L,255", self.spec)
        tr = self.ltt("R,255", self.spec)
        self.assertGreater(tl.angular.z, 0)
        self.assertLess(tr.angular.z, 0)
        self.assertAlmostEqual(tl.linear.x, 0.0)

    def test_default_speed_when_omitted(self):
        t = self.ltt("F", self.spec)
        self.assertGreater(t.linear.x, 0)

    def test_unknown_dropped(self):
        self.assertIsNone(self.ltt("P", self.spec))
        self.assertIsNone(self.ltt("", self.spec))
        self.assertIsNone(self.ltt("hello", self.spec))

    def test_speed_clamped(self):
        """Over-max speed clips, not throws."""
        t = self.ltt("F,999", self.spec)
        self.assertAlmostEqual(t.linear.x, self.spec.max_linear, places=6)


if __name__ == "__main__":
    unittest.main()
