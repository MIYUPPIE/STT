# sim.launch.py — Gazebo twin only (no hardware): arena world + simulated robot
# + RViz showing the sim robot (sim_ frames) and the arena markers.
#
# ros2 launch yoruba_robot sim.launch.py             # Gazebo server + RViz
# ros2 launch yoruba_robot sim.launch.py gui:=true   # also the Gazebo window
# ros2 launch yoruba_robot sim.launch.py voice:=false
# Drive: ros2 run teleop_twist_keyboard teleop_twist_keyboard
#   or:  live_caption.py --ros --cpu --speak   (voice_relay is started here)
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration

from yoruba_robot import launch_common as lc


def generate_launch_description():
    gui = LaunchConfiguration("gui")
    voice = LaunchConfiguration("voice")
    drive_real = LaunchConfiguration("drive_real")
    return LaunchDescription([
        DeclareLaunchArgument("drive_real", default_value="false",
                              description="Sim only by default; true also drives the ESP32."),
        DeclareLaunchArgument("gui", default_value="false",
                              description="Also open the Gazebo client window."),
        DeclareLaunchArgument("voice", default_value="true",
                              description="Start voice_relay (TCP :7447 -> /cmd_vel)."),
        lc.qt_on_x11(),
        *lc.gazebo(gui),
        lc.sim_state_publisher(),
        # robot_bridge with the ESP32 link closed: it shapes /cmd_vel into the
        # exact command the real robot would run and feeds Gazebo with it.
        lc.robot_bridge(use_sim_time=True, drive_real=drive_real),
        lc.environment(use_sim_time=True),
        lc.voice_relay(voice),
        lc.rviz("sim.rviz", use_sim_time=True),
    ])
