# sim.launch.py — Gazebo (gz-sim) + robot_state_publisher + RViz, no real
# hardware. The sim robot subscribes to /cmd_vel directly (gz-sim DiffDrive
# plugin, bridged from ROS2 by ros_gz_bridge), so you can drive it with
# teleop_twist_keyboard, voice_relay, or any Twist publisher.
#
# ros2 launch yoruba_robot sim.launch.py
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import ExecuteProcess, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command
from launch_ros.actions import Node


def generate_launch_description():
    pkg = get_package_share_directory("yoruba_robot")
    urdf = os.path.join(pkg, "description", "robot.urdf.xacro")
    world = os.path.join(pkg, "worlds", "empty.sdf")
    rviz_cfg = os.path.join(pkg, "config", "robot.rviz")

    # xacro produces the URDF XML string; feed it both to RSP (for RViz /tf) and
    # to Gazebo's /world/.../create as the model to spawn.
    robot_description = {
        "robot_description": Command(["xacro ", urdf, " use_sim:=true"])
    }

    gz_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(
            get_package_share_directory("ros_gz_sim"), "launch", "gz_sim.launch.py")),
        launch_arguments={"gz_args": f"-r -v 2 {world}"}.items())

    rsp = Node(package="robot_state_publisher", executable="robot_state_publisher",
               parameters=[robot_description, {"use_sim_time": True}],
               output="screen")

    spawn = Node(package="ros_gz_sim", executable="create",
                 arguments=["-topic", "robot_description",
                            "-name", "yoruba_robot",
                            "-x", "0", "-y", "0", "-z", "0.03"],
                 output="screen")

    # Bridge the sim's /cmd_vel + /sim_odom + /clock + joint states between
    # Gazebo transport and ROS2. One direction each; see ros_gz_bridge README.
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

    teleop_hint = ExecuteProcess(
        cmd=["bash", "-c",
             "echo; echo '--- drive the sim:'; "
             "echo '  ros2 run teleop_twist_keyboard teleop_twist_keyboard'; echo"],
        output="screen")

    return LaunchDescription([gz_launch, rsp, spawn, bridge, rviz, teleop_hint])
