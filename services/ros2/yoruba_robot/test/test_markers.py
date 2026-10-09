# test_markers.py — environment_publisher's MarkerArray matches ARENA. Needs
# visualization_msgs (system Python with ROS 2 sourced); skipped elsewhere.
#   source /opt/ros/jazzy/setup.bash && python3 -m pytest test/test_markers.py
import unittest

try:
    from visualization_msgs.msg import Marker               # noqa: F401
    HAVE_ROS = True
except Exception:
    HAVE_ROS = False


@unittest.skipUnless(HAVE_ROS, "visualization_msgs not available (source ROS 2)")
class TestMarkers(unittest.TestCase):
    def setUp(self):
        from yoruba_robot.arena import ARENA
        from yoruba_robot.environment_publisher import build_markers
        self.arena = ARENA
        self.markers = build_markers("odom").markers

    def test_one_marker_per_part(self):
        by_ns = {}
        for m in self.markers:
            by_ns.setdefault(m.ns, []).append(m)
        self.assertEqual(len(by_ns["walls"]), 4)
        self.assertEqual(len(by_ns["obstacles"]), len(self.arena["obstacles"]))
        self.assertEqual(len(by_ns["labels"]), len(self.arena["obstacles"]))
        self.assertEqual(len(by_ns["floor"]), 1)

    def test_ids_unique_and_frame_odom(self):
        self.assertEqual(len({(m.ns, m.id) for m in self.markers}), len(self.markers))
        self.assertTrue(all(m.header.frame_id == "odom" for m in self.markers))

    def test_obstacles_sit_on_the_floor(self):
        for m in self.markers:
            if m.ns == "obstacles":
                self.assertAlmostEqual(m.pose.position.z, m.scale.z / 2, places=6)


if __name__ == "__main__":
    unittest.main()
