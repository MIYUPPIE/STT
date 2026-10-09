# launch_common.py — the building blocks the three launch files share, so sim,
# real and twin can never drift apart. Imported by launch/*.launch.py (the
# installed yoruba_robot package is on the path once the workspace is sourced).
#
# TF tree this produces (twin = both halves):
#   odom ─┬─ base_footprint ─ base_link ─ wheels, plate, ...      REAL robot
#         │    (robot_bridge: odom→base_footprint + /joint_states;
#         │     robot_state_publisher: the rest, from /robot_description)
#         └─ sim_base_footprint ─ sim_base_link ─ sim_wheels, ...  GAZEBO twin
#              (Gazebo DiffDrive: odom→sim_base_footprint via /sim_tf→/tf;
#               robot_state_publisher in /sim: the rest, from /sim/robot_description)
import os

from ament_index_python.packages import get_package_share_directory
from launch.actions import IncludeLaunchDescription, SetEnvironmentVariable
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command, LaunchConfiguration, PythonExpression
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

PKG = "yoruba_robot"


def share(*parts):
    return os.path.join(get_package_share_directory(PKG), *parts)


def description(prefix="", use_sim=False):
    """URDF string parameter. ParameterValue(value_type=str) stops Jazzy from
    trying to YAML-parse the XML ('Unable to parse ... as yaml')."""
    return ParameterValue(
        Command(["xacro ", share("description", "robot.urdf.xacro"),
                 f" prefix:={prefix}" if prefix else "",
                 f" use_sim:={'true' if use_sim else 'false'}"]),
        value_type=str)


def qt_on_x11():
    """Wayland sessions: Qt would hand Ogre a Wayland surface its GLX backend
    can't use ('Invalid parentWindowHandle'). xcb goes through Xwayland."""
    return SetEnvironmentVariable("QT_QPA_PLATFORM", "xcb")


def real_state_publisher(use_sim_time):
    return Node(package="robot_state_publisher", executable="robot_state_publisher",
                name="robot_state_publisher", output="screen",
                parameters=[{"robot_description": description(),
                             "use_sim_time": use_sim_time}])


def sim_state_publisher():
    # In namespace /sim: reads /sim/joint_states, publishes /sim/robot_description.
    # Link names already carry the sim_ prefix (xacro prefix:=sim_), so no
    # frame_prefix is needed and both robots share the one /tf tree.
    return Node(package="robot_state_publisher", executable="robot_state_publisher",
                name="robot_state_publisher", namespace="sim", output="screen",
                parameters=[{"robot_description": description(prefix="sim_"),
                             "use_sim_time": True}])


def gazebo(gui: LaunchConfiguration):
    """Gazebo server (+ client window only when gui:=true: the client's Ogre
    renderer crashes on some NVIDIA/Wayland setups; RViz shows everything)."""
    world = share("worlds", "arena.sdf")
    gz_args = PythonExpression([
        "'-r -v 2 --render-engine-gui ogre ", world, "' if '", gui,
        "' == 'true' else '-r -s -v 2 ", world, "'"])
    sim = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(
            get_package_share_directory("ros_gz_sim"), "launch", "gz_sim.launch.py")),
        launch_arguments={"gz_args": gz_args}.items())
    spawn = Node(package="ros_gz_sim", executable="create", output="screen",
                 arguments=["-name", "yoruba_robot", "-x", "0", "-y", "0", "-z", "0.01",
                            "-string", Command(["xacro ", share("description", "robot.urdf.xacro"),
                                                " use_sim:=true"])])
    bridge = Node(
        package="ros_gz_bridge", executable="parameter_bridge", output="screen",
        arguments=[
            # ']' = ROS -> Gazebo only; '[' = Gazebo -> ROS only. Gazebo is driven
            # by robot_bridge's /cmd_vel_applied, never raw /cmd_vel, so the sim
            # executes the same snapped, timed-out command as the real robot.
            "/cmd_vel_applied@geometry_msgs/msg/Twist]gz.msgs.Twist",
            "/sim_odom@nav_msgs/msg/Odometry[gz.msgs.Odometry",
            "/sim_tf@tf2_msgs/msg/TFMessage[gz.msgs.Pose_V",
            "/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock",
            "/world/yoruba/model/yoruba_robot/joint_state@sensor_msgs/msg/JointState[gz.msgs.Model",
        ],
        remappings=[("/sim_tf", "/tf"),
                    ("/world/yoruba/model/yoruba_robot/joint_state", "/sim/joint_states")],
        parameters=[{"use_sim_time": True}])
    return [sim, spawn, bridge]


def robot_bridge(use_sim_time, drive_real: LaunchConfiguration):
    return Node(package=PKG, executable="robot_bridge", name="robot_bridge",
                output="screen",
                parameters=[{"use_sim_time": use_sim_time,
                             "require_robot": False,
                             "drive_real": ParameterValue(drive_real, value_type=bool)}])


def voice_relay(voice: LaunchConfiguration):
    return Node(package=PKG, executable="voice_relay", name="voice_relay",
                output="screen", condition=IfCondition(voice))


def environment(use_sim_time):
    return Node(package=PKG, executable="environment_publisher",
                name="environment_publisher", output="screen",
                parameters=[{"use_sim_time": use_sim_time}])


def rviz(config_name, use_sim_time):
    return Node(package="rviz2", executable="rviz2", output="log",
                arguments=["-d", share("config", config_name)],
                parameters=[{"use_sim_time": use_sim_time}])
