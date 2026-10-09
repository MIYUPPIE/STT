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
import signal
import subprocess
import time

from ament_index_python.packages import get_package_share_directory
from launch.actions import (IncludeLaunchDescription, LogInfo, OpaqueFunction,
                            RegisterEventHandler, SetEnvironmentVariable)
from launch.event_handlers import OnShutdown
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


def find_gz_servers(run=subprocess.run) -> list[tuple[int, str]]:
    """(pid, command) of every `gz sim` server process already running."""
    try:
        out = run(["pgrep", "-af", "gz sim"], capture_output=True, text=True).stdout
    except OSError:
        return []
    found = []
    for line in out.splitlines():
        pid, _, cmd = line.partition(" ")
        # the real server process, not the ruby wrapper or a shell mentioning it
        if pid.isdigit() and cmd.startswith("gz sim"):
            found.append((int(pid), cmd))
    return found


def _warn_stray_servers(context, *args, **kwargs):
    stray = find_gz_servers()
    if not stray:
        return []
    pids = " ".join(str(p) for p, _ in stray)
    return [LogInfo(msg=f"WARNING: {len(stray)} other Gazebo server(s) still running "
                        f"(pid {pids}), probably left over from an earlier launch. "
                        f"This launch is isolated from them (own GZ_PARTITION), but they "
                        f"use CPU: stop them with `kill {pids}`.")]


def process_partition(pid: int) -> str | None:
    """GZ_PARTITION a process was started with (None if unset/unreadable)."""
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            for var in f.read().split(b"\0"):
                if var.startswith(b"GZ_PARTITION="):
                    return var.split(b"=", 1)[1].decode()
    except OSError:
        pass
    return None


def stop_own_servers(partition: str, find=find_gz_servers, part_of=process_partition,
                     kill=os.kill, wait=1.5) -> list[int]:
    """Terminate the gz servers started in `partition` (and only those).

    Needed because ros2 launch signals the `sh -c ruby gz sim` wrapper, which
    exits without passing the signal on: the real `gz sim` server survives as
    an orphan, keeps the CPU busy, and (without partitions) blocks the next
    launch's spawn. Returns the pids it stopped."""
    mine = [pid for pid, _ in find() if part_of(pid) == partition]
    for pid in mine:
        try:
            kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + wait
    for pid in mine:
        while time.monotonic() < deadline:
            try:
                kill(pid, 0)                       # still alive?
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            try:
                kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    return mine


def gazebo_isolation():
    """Give this launch its own Gazebo transport network. Without it, a stray
    gz server from an earlier launch (same world name) answers too, and
    ros_gz_sim `create` hangs forever on 'Requesting list of world names'."""
    partition = f"yoruba_{os.getpid()}"
    return [
        OpaqueFunction(function=_warn_stray_servers),
        SetEnvironmentVariable("GZ_PARTITION", partition),
        LogInfo(msg=f"Gazebo partition: {partition}  "
                    f"(inspect with: GZ_PARTITION={partition} gz model --list)"),
        RegisterEventHandler(OnShutdown(on_shutdown=[OpaqueFunction(
            function=lambda context: [LogInfo(msg="stopped own Gazebo server(s): "
                                              f"{stop_own_servers(partition) or 'none left'}")])])),
    ]


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
    return [*gazebo_isolation(), sim, spawn, bridge]


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
