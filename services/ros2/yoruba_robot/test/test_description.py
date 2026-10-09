# test_description.py — gate tests for description/robot.urdf.xacro. Runs the
# real xacro (skipped when ROS 2 Jazzy isn't installed) and checks the robot
# geometry the rest of the system depends on. ~1 s.
#   python3 -m unittest services.ros2.yoruba_robot.test.test_description -v
import os
import subprocess
import unittest
import xml.etree.ElementTree as ET

import numpy as np

from services.ros2.yoruba_robot.yoruba_robot.kinematics import RobotSpec

PKG = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
XACRO = os.path.join(PKG, "description", "robot.urdf.xacro")
ROS = "/opt/ros/jazzy/setup.bash"


def xacro(*args):
    cmd = f"source {ROS} && xacro {XACRO} {' '.join(args)}"
    out = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr)
    if "warning" in out.stderr.lower():
        raise RuntimeError("xacro warning: " + out.stderr)
    return ET.fromstring(out.stdout)


def link_positions(root):
    """World xyz of each link (joints translation-only, angles 0)."""
    joints = {j.find("child").get("link"): j for j in root.findall("joint")}
    pos = {}

    def p(name):
        if name not in pos:
            j = joints.get(name)
            if j is None:
                pos[name] = np.zeros(3)
            else:
                o = j.find("origin")
                xyz = np.array([float(v) for v in o.get("xyz").split()]) if o is not None else np.zeros(3)
                pos[name] = p(j.find("parent").get("link")) + xyz
        return pos[name]
    for l in root.findall("link"):
        p(l.get("name"))
    return pos


@unittest.skipUnless(os.path.exists(ROS), "ROS 2 Jazzy not installed")
class TestRobotDescription(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real = xacro()
        cls.simview = xacro("prefix:=sim_")
        cls.gz = xacro("use_sim:=true")

    def links(self, root):
        return {l.get("name") for l in root.findall("link")}

    def test_flat_2wd_parts(self):
        self.assertEqual(self.links(self.real), {
            "base_footprint", "base_link", "chassis_plate", "left_motor",
            "right_motor", "left_wheel", "right_wheel", "caster_wheel",
            "battery", "motor_driver", "controller"})
        kinds = {j.get("name"): j.get("type") for j in self.real.findall("joint")}
        self.assertEqual(kinds["left_wheel_joint"], "continuous")
        self.assertEqual(kinds["right_wheel_joint"], "continuous")
        self.assertEqual(kinds["caster_wheel_joint"], "fixed")

    def test_wheels_match_robot_bridge(self):
        """URDF wheel size/spacing must equal robot_bridge's kinematics, or the
        model and the odometry disagree."""
        pos = link_positions(self.real)
        sep = pos["left_wheel"][1] - pos["right_wheel"][1]
        self.assertAlmostEqual(sep, RobotSpec.wheel_separation, places=6)
        wheel = next(l for l in self.real.findall("link") if l.get("name") == "left_wheel")
        r = float(wheel.find("collision/geometry/cylinder").get("radius"))
        self.assertAlmostEqual(r, RobotSpec.wheel_radius, places=6)

    def test_three_contact_points_on_the_ground(self):
        """Both wheels and the caster ball touch z = 0: the robot sits level."""
        pos = link_positions(self.real)
        r = RobotSpec.wheel_radius
        self.assertAlmostEqual(pos["left_wheel"][2] - r, 0.0, places=6)
        self.assertAlmostEqual(pos["right_wheel"][2] - r, 0.0, places=6)
        caster = next(l for l in self.real.findall("link") if l.get("name") == "caster_wheel")
        cr = float(caster.find("collision/geometry/sphere").get("radius"))
        self.assertAlmostEqual(pos["caster_wheel"][2] - cr, 0.0, places=6)
        self.assertLess(pos["caster_wheel"][0], 0.0)          # caster at the rear

    def test_wheels_clear_the_plate(self):
        pos = link_positions(self.real)
        plate = next(l for l in self.real.findall("link") if l.get("name") == "chassis_plate")
        half_w = float(plate.find("visual/geometry/box").get("size").split()[1]) / 2
        wheel = next(l for l in self.real.findall("link") if l.get("name") == "left_wheel")
        w = float(wheel.find("collision/geometry/cylinder").get("length"))
        self.assertGreater(pos["left_wheel"][1] - w / 2, half_w)

    def test_materials_defined_once_at_robot_scope(self):
        top = [m.get("name") for m in self.real.findall("material")]
        self.assertEqual(len(top), len(set(top)))
        for vis in self.real.iter("visual"):
            m = vis.find("material")
            self.assertIsNotNone(m)
            self.assertIsNone(m.find("color"), "inline colour: reference a global material")
            self.assertIn(m.get("name"), top)

    def test_every_physical_link_has_inertia(self):
        for l in self.gz.findall("link"):
            if l.get("name") != "base_footprint":
                self.assertIsNotNone(l.find("inertial"), l.get("name"))

    def test_sim_prefix_on_links_not_joints(self):
        self.assertEqual(self.links(self.simview), {"sim_" + n for n in self.links(self.real)})
        joints = {j.get("name") for j in self.simview.findall("joint")}
        self.assertIn("left_wheel_joint", joints)              # matches Gazebo's names

    def test_gazebo_plugins_only_with_use_sim(self):
        self.assertIsNone(self.real.find("gazebo"))
        dd = [p for g in self.gz.findall("gazebo") for p in g.findall("plugin")
              if "DiffDrive" in p.get("name")]
        self.assertEqual(len(dd), 1)
        self.assertEqual(dd[0].findtext("topic"), "/cmd_vel_applied")
        self.assertEqual(dd[0].findtext("frame_id"), "odom")
        self.assertEqual(dd[0].findtext("child_frame_id"), "sim_base_footprint")
        self.assertAlmostEqual(float(dd[0].findtext("wheel_radius")), RobotSpec.wheel_radius)


if __name__ == "__main__":
    unittest.main()
