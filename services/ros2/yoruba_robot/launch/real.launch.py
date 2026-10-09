# real.launch.py — the real ESP32 robot only (no Gazebo): robot_bridge drives it
# over WiFi and publishes odom/TF/joint_states; RViz shows it in the arena.
#
# ros2 launch yoruba_robot real.launch.py
# ros2 launch yoruba_robot real.launch.py drive_real:=false   # rehearse, robot untouched
# Drive: live_caption.py --ros --cpu --speak   (voice_relay is started here)
#   or:  ros2 run teleop_twist_keyboard teleop_twist_keyboard
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration

from yoruba_robot import launch_common as lc


def generate_launch_description():
    voice = LaunchConfiguration("voice")
    drive_real = LaunchConfiguration("drive_real")
    return LaunchDescription([
        DeclareLaunchArgument("voice", default_value="true",
                              description="Start voice_relay (TCP :7447 -> /cmd_vel)."),
        DeclareLaunchArgument("drive_real", default_value="true",
                              description="false = never open the ESP32 link."),
        lc.qt_on_x11(),
        lc.real_state_publisher(use_sim_time=False),
        lc.robot_bridge(use_sim_time=False, drive_real=drive_real),
        lc.environment(use_sim_time=False),
        lc.voice_relay(voice),
        lc.rviz("real.rviz", use_sim_time=False),
    ])
