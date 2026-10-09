# test_environment.py — gate tests for the arena, the generated Gazebo world
# and the generated RViz layouts. Pure Python, no ROS, <1 s.
#   python3 -m unittest services.ros2.yoruba_robot.test.test_environment -v
import os
import unittest
import xml.etree.ElementTree as ET

from services.ros2.yoruba_robot.yoruba_robot import arena
from services.ros2.yoruba_robot.tools import make_rviz

PKG = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class TestArena(unittest.TestCase):
    def test_default_arena_valid(self):
        self.assertEqual(arena.validate(), [])

    def test_validate_catches_bad_obstacles(self):
        bad = dict(arena.ARENA, obstacles=[
            {"name": "x", "shape": "box", "xy": (0.1, 0.0), "size": (0.2, 0.2, 0.2), "color": (1, 0, 0)},
            {"name": "y", "shape": "box", "xy": (1.45, 0.0), "size": (0.2, 0.2, 0.2), "color": (1, 0, 0)},
            {"name": "y", "shape": "cone", "xy": (1.0, 1.0), "size": (0.1,), "color": (1, 0, 0)},
        ])
        errs = " ".join(arena.validate(bad))
        self.assertIn("start pose", errs)          # x too close to the start
        self.assertIn("pokes through a wall", errs)  # y crosses the east wall
        self.assertIn("duplicate", errs)
        self.assertIn("unknown shape", errs)

    def test_walls_enclose_floor(self):
        w = {x["name"]: x for x in arena.walls()}
        hx = arena.ARENA["size_x"] / 2
        t = arena.ARENA["wall_thickness"]
        self.assertAlmostEqual(w["wall_east"]["xyz"][0] - t / 2, hx)
        self.assertAlmostEqual(w["wall_west"]["xyz"][0] + t / 2, -hx)

    def test_committed_world_matches_arena(self):
        """worlds/arena.sdf must be exactly to_sdf(): edit ARENA, then run
        python3 -m yoruba_robot.arena > worlds/arena.sdf"""
        with open(os.path.join(PKG, "worlds", "arena.sdf")) as f:
            self.assertEqual(f.read(), arena.to_sdf())

    def test_world_is_well_formed(self):
        root = ET.fromstring(arena.to_sdf())
        world = root.find("world")
        self.assertEqual(world.get("name"), "yoruba")   # bridge topic depends on it
        models = {m.get("name") for m in world.findall("model")}
        self.assertTrue({"floor", "wall_north", "wall_south", "wall_east",
                         "wall_west", "crate", "bin", "block"} <= models)


class TestStrayGazebo(unittest.TestCase):
    """launch_common.find_gz_servers: only real `gz sim` server processes."""

    def test_finds_only_servers(self):
        import subprocess
        try:
            from services.ros2.yoruba_robot.yoruba_robot.launch_common import find_gz_servers
        except ImportError:                      # launch/launch_ros not in this Python
            self.skipTest("ROS launch modules not importable")
        out = ("513620 gz sim -r -s -v 2 /x/arena.sdf\n"
               "623308 /bin/sh -c ruby /opt/ros/jazzy/opt/gz_tools_vendor/bin/gz sim -r -s\n"
               "700001 bash -c pgrep -af gz sim\n")
        fake = lambda *a, **k: subprocess.CompletedProcess(a, 0, out, "")
        self.assertEqual(find_gz_servers(run=fake), [(513620, "gz sim -r -s -v 2 /x/arena.sdf")])


class TestOwnServerCleanup(unittest.TestCase):
    def test_stops_only_own_partition(self):
        try:
            from services.ros2.yoruba_robot.yoruba_robot.launch_common import stop_own_servers
        except ImportError:
            self.skipTest("ROS launch modules not importable")
        servers = [(10, "gz sim a"), (11, "gz sim b"), (12, "gz sim c")]
        parts = {10: "yoruba_1", 11: "yoruba_2", 12: None}
        alive, signals = {10, 11, 12}, []

        def kill(pid, sig):
            if pid not in alive:
                raise ProcessLookupError
            signals.append((pid, sig))
            if sig:                                   # SIGTERM/SIGKILL -> gone
                alive.discard(pid)

        stopped = stop_own_servers("yoruba_1", find=lambda: servers,
                                   part_of=parts.get, kill=kill, wait=0.2)
        self.assertEqual(stopped, [10])
        self.assertEqual(alive, {11, 12})             # other launches untouched


class TestRvizLayouts(unittest.TestCase):
    def test_committed_layouts_match_generator(self):
        for name, text in make_rviz.LAYOUTS.items():
            with open(os.path.join(PKG, "config", name)) as f:
                self.assertEqual(f.read(), text, f"{name}: run tools/make_rviz.py")

    def test_twin_shows_both_robots(self):
        twin = make_rviz.LAYOUTS["twin.rviz"]
        self.assertIn("Value: /robot_description", twin)
        self.assertIn("Value: /sim/robot_description", twin)
        self.assertIn("Fixed Frame: odom", twin)
        # robot_description is latched: subscriber must be transient-local too
        self.assertIn("Durability Policy: Transient Local", twin)


if __name__ == "__main__":
    unittest.main()
