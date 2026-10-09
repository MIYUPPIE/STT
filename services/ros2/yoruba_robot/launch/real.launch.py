# real.launch.py — real ESP32 (over WiFi) + RViz, no Gazebo. The robot_bridge
# node drives the ESP32 and publishes odom+TF so RViz shows where it thinks it
# is. Combine with teleop_twist_keyboard or voice_relay.
#
# ros2 launch yoruba_robot real.launch.py
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.substitutions import Command
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    pkg = get_package_share_directory("yoruba_robot")
    urdf = os.path.join(pkg, "description", "robot.urdf.xacro")
    rviz_cfg = os.path.join(pkg, "config", "robot.rviz")

    robot_description = {
        "robot_description": ParameterValue(
            Command(["xacro ", urdf, " use_sim:=false"]), value_type=str)
    }

    rsp = Node(package="robot_state_publisher", executable="robot_state_publisher",
               parameters=[robot_description], output="screen")

    bridge = Node(package="yoruba_robot", executable="robot_bridge",
                  name="robot_bridge", output="screen")

    rviz = Node(package="rviz2", executable="rviz2", arguments=["-d", rviz_cfg],
                output="log")

    return LaunchDescription([rsp, bridge, rviz])
