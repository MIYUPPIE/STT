# sim.launch.py — Gazebo physics (server) + robot_state_publisher + RViz. No
# Gazebo client window by default: its Ogre renderer crashes on some NVIDIA +
# Noble combos (observed here: driver 580 / RTX 2060 / Jazzy). The physics
# server, DiffDrive plugin, bridges and RViz all work fine without the Gazebo
# window — RViz is the visualizer either way.
#
# ros2 launch yoruba_robot sim.launch.py             # server + RViz only
# ros2 launch yoruba_robot sim.launch.py gui:=true   # also open Gazebo
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, ExecuteProcess,
                            IncludeLaunchDescription,
                            SetEnvironmentVariable)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command, LaunchConfiguration, PythonExpression
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    pkg = get_package_share_directory("yoruba_robot")
    urdf = os.path.join(pkg, "description", "robot.urdf.xacro")
    world = os.path.join(pkg, "worlds", "empty.sdf")
    rviz_cfg = os.path.join(pkg, "config", "robot.rviz")

    gui = LaunchConfiguration("gui")
    # -s = server only; drop -s to also start the client window. GUI engine kept
    # on Ogre1 for the (rare) case the Gazebo client does open on this box.
    gz_args = PythonExpression([
        "'-r -v 2 --render-engine-gui ogre ", world, "' if '", gui,
        "' == 'true' else '-r -s -v 2 ", world, "'"])

    # ParameterValue(..., value_type=str): xacro returns URDF XML; without this
    # Jazzy YAML-parses it and the launch fails.
    robot_description = {
        "robot_description": ParameterValue(
            Command(["xacro ", urdf, " use_sim:=true"]), value_type=str)
    }

    gz_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(
            get_package_share_directory("ros_gz_sim"), "launch", "gz_sim.launch.py")),
        launch_arguments={"gz_args": gz_args}.items())

    rsp = Node(package="robot_state_publisher", executable="robot_state_publisher",
               parameters=[robot_description, {"use_sim_time": True}],
               output="screen")

    spawn = Node(package="ros_gz_sim", executable="create",
                 arguments=["-topic", "robot_description",
                            "-name", "yoruba_robot",
                            "-x", "0", "-y", "0", "-z", "0.03"],
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
                  parameters=[{"use_sim_time": True}],
                  output="screen")

    rviz = Node(package="rviz2", executable="rviz2", arguments=["-d", rviz_cfg],
                parameters=[{"use_sim_time": True}], output="log")

    hint = ExecuteProcess(
        cmd=["bash", "-c",
             "echo; echo '--- drive the sim:'; "
             "echo '  ros2 run teleop_twist_keyboard teleop_twist_keyboard'; echo"],
        output="screen")

    return LaunchDescription([
        # Wayland sessions: Qt would open a Wayland surface Ogre's GLX can't
        # attach to ('Invalid parentWindowHandle'). xcb routes through
        # Xwayland, which Ogre speaks fluently. No-op on pure X11 sessions.
        SetEnvironmentVariable('QT_QPA_PLATFORM', 'xcb'),
        DeclareLaunchArgument("gui", default_value="false",
                              description="Also open the Gazebo client window "
                              "(crashes on some NVIDIA+Noble combos; RViz "
                              "shows the robot either way)."),
        gz_launch, rsp, spawn, bridge, rviz, hint,
    ])
