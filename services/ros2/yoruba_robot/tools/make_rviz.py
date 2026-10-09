# make_rviz.py — writes config/{sim,real,twin}.rviz from one template, so the
# three RViz layouts share the same grid, arena, camera and colours.
#
#   python3 tools/make_rviz.py        (from services/ros2/yoruba_robot/)
#
# A gate test regenerates them and fails if the committed files differ.
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "..", "config")


def topic(value, durability="Volatile"):
    return (f"        Topic:\n"
            f"          Depth: 5\n"
            f"          Durability Policy: {durability}\n"
            f"          History Policy: Keep Last\n"
            f"          Reliability Policy: Reliable\n"
            f"          Value: {value}\n")


def grid():
    return ("      - Class: rviz_default_plugins/Grid\n"
            "        Name: Grid (10 cm)\n"
            "        Enabled: true\n"
            "        Cell Size: 0.1\n"
            "        Plane Cell Count: 36\n"
            "        Color: 160; 160; 164\n"
            "        Alpha: 0.35\n"
            "        Line Style:\n"
            "          Line Width: 0.03\n"
            "          Value: Lines\n"
            "        Offset: {X: 0, Y: 0, Z: 0.001}\n"
            "        Plane: XY\n"
            "        Reference Frame: <Fixed Frame>\n"
            "        Value: true\n")


def environment():
    return ("      - Class: rviz_default_plugins/MarkerArray\n"
            "        Name: Environment (arena)\n"
            "        Enabled: true\n"
            "        Namespaces: {}\n"
            + topic("/environment", "Transient Local") +
            "        Value: true\n")


def robot(name, description_topic, alpha):
    return ("      - Class: rviz_default_plugins/RobotModel\n"
            f"        Name: {name}\n"
            "        Enabled: true\n"
            f"        Alpha: {alpha}\n"
            "        Collision Enabled: false\n"
            "        Visual Enabled: true\n"
            "        Description Source: Topic\n"
            "        Description File: \"\"\n"
            "        Description Topic:\n"
            "          Depth: 5\n"
            "          Durability Policy: Transient Local\n"
            "          History Policy: Keep Last\n"
            "          Reliability Policy: Reliable\n"
            f"          Value: {description_topic}\n"
            "        TF Prefix: \"\"\n"
            "        Update Interval: 0\n"
            "        Value: true\n")


def odometry(name, topic_name, rgb):
    return ("      - Class: rviz_default_plugins/Odometry\n"
            f"        Name: {name}\n"
            "        Enabled: true\n"
            "        Keep: 300\n"
            "        Position Tolerance: 0.03\n"
            "        Angle Tolerance: 0.15\n"
            "        Covariance:\n"
            "          Value: false\n"
            "        Shape:\n"
            "          Value: Arrow\n"
            f"          Color: {rgb}\n"
            "          Alpha: 0.9\n"
            "          Shaft Length: 0.06\n"
            "          Shaft Radius: 0.006\n"
            "          Head Length: 0.025\n"
            "          Head Radius: 0.014\n"
            + topic(topic_name) +
            "        Value: true\n")


def tf_display(enabled):
    return ("      - Class: rviz_default_plugins/TF\n"
            "        Name: TF frames\n"
            f"        Enabled: {'true' if enabled else 'false'}\n"
            "        Marker Scale: 0.25\n"
            "        Show Arrows: false\n"
            "        Show Axes: true\n"
            "        Show Names: false\n"
            "        Update Interval: 0\n"
            f"        Value: {'true' if enabled else 'false'}\n")


def document(displays, target_frame):
    return ("Panels:\n"
            "  - Class: rviz_common/Displays\n"
            "    Name: Displays\n"
            "  - Class: rviz_common/Views\n"
            "    Name: Views\n"
            "Visualization Manager:\n"
            "  Class: \"\"\n"
            "  Displays:\n"
            + "".join(displays) +
            "  Enabled: true\n"
            "  Global Options:\n"
            "    Background Color: 38; 41; 48\n"
            "    Fixed Frame: odom\n"
            "    Frame Rate: 30\n"
            "  Name: root\n"
            "  Tools:\n"
            "    - Class: rviz_default_plugins/Interact\n"
            "    - Class: rviz_default_plugins/MoveCamera\n"
            "    - Class: rviz_default_plugins/Select\n"
            "    - Class: rviz_default_plugins/Measure\n"
            "  Value: true\n"
            "  Views:\n"
            "    Current:\n"
            "      Class: rviz_default_plugins/Orbit\n"
            "      Name: Follow robot\n"
            "      Distance: 1.1\n"
            "      Focal Point: {X: 0, Y: 0, Z: 0.04}\n"
            "      Focal Shape Fixed Size: true\n"
            "      Focal Shape Size: 0.03\n"
            "      Near Clip Distance: 0.005\n"
            "      Pitch: 0.62\n"
            "      Yaw: 3.6\n"
            f"      Target Frame: {target_frame}\n"
            "      Value: Orbit (rviz)\n"
            "    Saved: ~\n"
            "Window Geometry:\n"
            "  Height: 860\n"
            "  Width: 1400\n"
            "  Hide Left Dock: false\n"
            "  Hide Right Dock: true\n")


REAL_GREEN = "40; 200; 60"
SIM_ORANGE = "240; 130; 30"

LAYOUTS = {
    "sim.rviz": document([
        grid(), environment(),
        robot("Gazebo robot", "/sim/robot_description", 1),
        odometry("Gazebo path", "/sim_odom", SIM_ORANGE),
        tf_display(False),
    ], "sim_base_footprint"),
    "real.rviz": document([
        grid(), environment(),
        robot("Real robot", "/robot_description", 1),
        odometry("Real path (dead reckoning)", "/odom", REAL_GREEN),
        tf_display(False),
    ], "base_footprint"),
    "twin.rviz": document([
        grid(), environment(),
        robot("Real robot", "/robot_description", 1),
        robot("Gazebo twin", "/sim/robot_description", 0.45),
        odometry("Real path (dead reckoning)", "/odom", REAL_GREEN),
        odometry("Gazebo path", "/sim_odom", SIM_ORANGE),
        tf_display(False),
    ], "base_footprint"),
}


def write_all(dst=CONFIG):
    for name, text in LAYOUTS.items():
        with open(os.path.join(dst, name), "w") as f:
            f.write(text)
    return sorted(LAYOUTS)


if __name__ == "__main__":
    print("wrote", ", ".join(write_all()))
