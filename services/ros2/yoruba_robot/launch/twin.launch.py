# twin.launch.py — DIGITAL TWIN: Gazebo + the real ESP32 driven in lockstep.
# One /cmd_vel topic feeds Gazebo's DiffDrive and robot_bridge (which pushes to
# the real robot over WiFi). RViz shows both odometries side by side (green =
# real dead reckoning, orange = sim ground truth), so you can watch calibration
# drift live.
#
# ros2 launch yoruba_robot twin.launch.py
# then: ros2 run yoruba_robot voice_relay   (or teleop_twist_keyboard)
#       and: live_caption.py --ros --cpu --speak
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    pkg = get_package_share_directory("yoruba_robot")
    urdf = os.path.join(pkg, "description", "robot.urdf.xacro")
    world = os.path.join(pkg, "worlds", "empty.sdf")
    rviz_cfg = os.path.join(pkg, "config", "robot.rviz")

    # ParameterValue(..., value_type=str): xacro returns URDF XML; without
    # this Jazzy tries to YAML-parse it and fails.
    robot_description = {
        "robot_description": ParameterValue(
            Command(["xacro ", urdf, " use_sim:=true"]), value_type=str)
    }

    gz_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(
            get_package_share_directory("ros_gz_sim"), "launch", "gz_sim.launch.py")),
        launch_arguments={"gz_args": f"-r -v 2 --render-engine-gui ogre {world}"}.items())

    rsp = Node(package="robot_state_publisher", executable="robot_state_publisher",
               parameters=[robot_description], output="screen")

    spawn = Node(package="ros_gz_sim", executable="create",
                 arguments=["-topic", "robot_description",
                            "-name", "yoruba_robot", "-z", "0.03"],
                 output="screen")

    bridge = Node(package="ros_gz_bridge", executable="parameter_bridge",
                  arguments=[
                      "/cmd_vel@geometry_msgs/msg/Twist@gz.msgs.Twist",
                      "/sim_odom@nav_msgs/msg/Odometry[gz.msgs.Odometry",
                      "/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock",
                      "/world/yoruba/model/yoruba_robot/joint_state@"
                      "sensor_msgs/msg/JointState[gz.msgs.Model"],
                  remappings=[("/world/yoruba/model/yoruba_robot/joint_state",
                               "/joint_states")],
                  output="screen")

    # The real robot: same /cmd_vel, published to the ESP32 over WiFi, with its
    # own /odom (dead reckoning) and TF for RViz. require_robot=false so this
    # launch still works if the robot is powered off (sim alone).
    real = Node(package="yoruba_robot", executable="robot_bridge",
                name="robot_bridge", output="screen",
                parameters=[{"require_robot": False}])

    rviz = Node(package="rviz2", executable="rviz2", arguments=["-d", rviz_cfg],
                output="log")

    return LaunchDescription([gz_launch, rsp, spawn, bridge, real, rviz])
