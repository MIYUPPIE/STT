# twin.launch.py — DIGITAL TWIN: the real ESP32 robot and its Gazebo copy driven
# by the same /cmd_vel, both drawn in RViz inside the same arena.
#   solid model       = real robot (robot_bridge dead reckoning)
#   see-through model = Gazebo robot (physics ground truth; it stops at walls)
# Their gap is calibration drift (or the real robot's surroundings differing
# from the arena).
#
# Every node runs on Gazebo's clock (use_sim_time) so real and sim timestamps
# never conflict in the TF tree.
#
# ros2 launch yoruba_robot twin.launch.py
# ros2 launch yoruba_robot twin.launch.py drive_real:=false   # robot untouched
# ros2 launch yoruba_robot twin.launch.py gui:=true           # + Gazebo window
# Drive: live_caption.py --ros --cpu --speak   (voice_relay is started here)
#   or:  ros2 run teleop_twist_keyboard teleop_twist_keyboard
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration

from yoruba_robot import launch_common as lc


def generate_launch_description():
    gui = LaunchConfiguration("gui")
    voice = LaunchConfiguration("voice")
    drive_real = LaunchConfiguration("drive_real")
    return LaunchDescription([
        DeclareLaunchArgument("gui", default_value="false",
                              description="Also open the Gazebo client window."),
        DeclareLaunchArgument("voice", default_value="true",
                              description="Start voice_relay (TCP :7447 -> /cmd_vel)."),
        DeclareLaunchArgument("drive_real", default_value="true",
                              description="false = never open the ESP32 link."),
        lc.qt_on_x11(),
        *lc.gazebo(gui),
        lc.sim_state_publisher(),
        lc.real_state_publisher(use_sim_time=True),
        lc.robot_bridge(use_sim_time=True, drive_real=drive_real),
        lc.environment(use_sim_time=True),
        lc.voice_relay(voice),
        lc.rviz("twin.rviz", use_sim_time=True),
    ])
