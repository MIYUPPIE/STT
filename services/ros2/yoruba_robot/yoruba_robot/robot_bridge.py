# robot_bridge.py — ROS2 node: /cmd_vel (Twist) -> real ESP32 (WiFi TCP) +
# /odom (Odometry) + odom->base_link TF so RViz sees the motion.
#
# Why reuse the existing services.robot.link.TcpTransport instead of re-opening
# the socket here: the firmware's wire protocol, auto-discovery (mDNS, LAN
# sweep) and reconnect logic are tested in services/robot/tests. This node is
# just the ROS2 shell around them.
#
# Dead reckoning: no encoders on the real robot, so odometry integrates the
# commanded wheel speeds (kinematics.command_to_wheels). Good enough for a
# matching Gazebo twin in RViz; drop in encoder feedback later if you add it.
#
# ros2 run yoruba_robot robot_bridge --ros-args -p host:=auto -p publish_rate:=50.0
import os
import sys
import threading

import rclpy
from geometry_msgs.msg import Twist, TransformStamped
from nav_msgs.msg import Odometry
from sensor_msgs.msg import JointState
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from tf2_ros import TransformBroadcaster

# Hoist the project root onto sys.path so the ROS node (installed to
# ~/ros2_ws/install/...) can still import services.robot. With
# --symlink-install the installed .py symlinks back to the source tree, so
# resolve through the symlink to find the real repo root (5 levels up from
# services/ros2/yoruba_robot/yoruba_robot/robot_bridge.py).
_REPO = os.environ.get("YORUBA_ROBOT_REPO") or os.path.abspath(
    os.path.join(os.path.realpath(__file__), *([os.pardir] * 5)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from services.robot.link import RobotLink, open_transport
from .kinematics import (OdomState, RobotSpec, STOP, advance_wheel_angles,
                         command_to_wheels, integrate, quaternion_from_yaw,
                         twist_to_command)


class RobotBridge(Node):
    def __init__(self):
        super().__init__("robot_bridge")
        self.declare_parameter("odom_frame", "odom")
        # Publish TF to base_footprint (URDF root), not base_link. base_link is
        # a child of base_footprint via a fixed joint published by RSP; if we
        # also parented base_link from odom, it would have two TF parents and
        # the model would never resolve in RViz.
        self.declare_parameter("base_frame", "base_footprint")
        self.declare_parameter("publish_rate", 50.0)        # Hz, odom + TF
        self.declare_parameter("cmd_timeout", 0.5)          # s before auto-stop
        self.declare_parameter("hold_ms", 1000)             # firmware move window
        self.declare_parameter("wheel_radius", RobotSpec.wheel_radius)
        self.declare_parameter("wheel_separation", RobotSpec.wheel_separation)
        self.declare_parameter("max_linear", RobotSpec.max_linear)
        self.declare_parameter("max_angular", RobotSpec.max_angular)
        self.declare_parameter("require_robot", False)      # True = fail if no link
        # False = never open the ESP32 link: odom/TF/joint_states come from the
        # commands alone. Use it to rehearse in RViz with the robot on the desk.
        self.declare_parameter("drive_real", True)
        self.declare_parameter("min_duty", RobotSpec.min_duty)   # firmware MIN_DUTY

        p = self.get_parameter
        self.spec = RobotSpec(
            wheel_radius=p("wheel_radius").value,
            wheel_separation=p("wheel_separation").value,
            max_linear=p("max_linear").value,
            max_angular=p("max_angular").value,
            min_duty=int(p("min_duty").value))
        self.odom_frame = p("odom_frame").value
        self.base_frame = p("base_frame").value
        self.hold_ms = int(p("hold_ms").value)
        self.cmd_timeout = float(p("cmd_timeout").value)

        self.link: RobotLink | None = None
        self.link_lock = threading.Lock()
        if p("drive_real").value:
            self._open_link(required=p("require_robot").value)
        else:
            self.get_logger().info("drive_real:=false - ESP32 link not opened")

        self.state = OdomState()
        self.wheel_angles = (0.0, 0.0)                      # left, right (rad)
        self.vl = 0.0
        self.vr = 0.0
        self.last_cmd = self.get_clock().now()
        self.last_tick = self.get_clock().now()
        self.current = (STOP, 0)                            # last sent to firmware
        self.last_sent = self.get_clock().now()

        qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)
        self.create_subscription(Twist, "cmd_vel", self.on_cmd_vel, qos)
        self.odom_pub = self.create_publisher(Odometry, "odom", qos)
        # Wheel angles for robot_state_publisher: without them the continuous
        # wheel joints have no transform and RViz marks the RobotModel red.
        self.joint_pub = self.create_publisher(JointState, "joint_states", qos)
        # The command the real robot is ACTUALLY executing: snapped to the
        # firmware's F/B/L/R moves and duty steps, zero after cmd_timeout.
        # Gazebo drives from this (not raw /cmd_vel), so the twin does exactly
        # what the real robot does, including stopping when commands go quiet.
        self.applied_pub = self.create_publisher(Twist, "cmd_vel_applied", qos)
        self.tf = TransformBroadcaster(self)
        self.create_timer(1.0 / p("publish_rate").value, self.on_tick)

        self.get_logger().info(
            f"robot_bridge: link={'open' if self.link else 'none'} "
            f"spec=wheel_r={self.spec.wheel_radius}m "
            f"wheelbase={self.spec.wheel_separation}m "
            f"max_v={self.spec.max_linear}m/s")

    # ---------------- link ----------------
    def _open_link(self, required):
        try:
            self.link = RobotLink(open_transport())
            self.get_logger().info(f"robot link: {self.link.port}")
        except Exception as e:
            if required:
                raise
            self.get_logger().warn(
                f"robot link unavailable ({e}); publishing odom from commands only")

    def _send(self, cmd: str, speed: int):
        """Push the current command to the ESP32. We resend on each tick so a
        move-window never lapses; the firmware watchdog still halts within 2 s
        if we die. Serialized because multiple timer ticks could race."""
        if self.link is None:
            return
        with self.link_lock:
            ok, ack, err = self.link.move(cmd, speed, self.hold_ms if cmd != STOP else 0)
        if not ok:
            self.get_logger().warn(f"link send failed: {err or ack}", throttle_duration_sec=2)

    # ---------------- callbacks ----------------
    def on_cmd_vel(self, msg: Twist):
        cmd, speed = twist_to_command(msg.linear.x, msg.angular.z, self.spec)
        now = self.get_clock().now()
        self.last_cmd = now
        self.vl, self.vr = command_to_wheels(cmd, speed, self.spec)
        if (cmd, speed) != self.current:                    # only resend on change
            self.current = (cmd, speed)
            self._send(cmd, speed)
            self.last_sent = now
            self.get_logger().debug(f"cmd_vel ({msg.linear.x:.2f}, {msg.angular.z:.2f}) "
                                    f"-> {cmd},{speed}")

    def on_tick(self):
        now = self.get_clock().now()
        dt = (now - self.last_tick).nanoseconds / 1e9
        self.last_tick = now

        # cmd_vel went quiet -> stop (ROS2 convention; prevents a runaway if the
        # publisher dies and keeps the firmware watchdog honest).
        if (now - self.last_cmd).nanoseconds / 1e9 > self.cmd_timeout:
            if self.current != (STOP, 0):
                self.current = (STOP, 0)
                self.vl = self.vr = 0.0
                self._send(STOP, 0)

        # Keepalive: resend the current move so the firmware's hold window never
        # lapses mid-command. Covers HOLD_REFRESH on the pure-Python side too.
        if self.current[0] != STOP and \
           (now - self.last_sent).nanoseconds / 1e9 > self.hold_ms / 1000 * 0.4:
            self._send(*self.current)
            self.last_sent = now

        self.state = integrate(self.state, self.vl, self.vr, dt, self.spec)
        self.wheel_angles = advance_wheel_angles(*self.wheel_angles, self.vl,
                                                 self.vr, dt, self.spec)
        self._publish_odom(now)
        self._publish_joints(now)
        applied = Twist()
        applied.linear.x = (self.vl + self.vr) / 2
        applied.angular.z = (self.vr - self.vl) / self.spec.wheel_separation
        self.applied_pub.publish(applied)

    def _publish_odom(self, now):
        s = self.state
        qx, qy, qz, qw = quaternion_from_yaw(s.theta)
        stamp = now.to_msg()

        odom = Odometry()
        odom.header.stamp = stamp
        odom.header.frame_id = self.odom_frame
        odom.child_frame_id = self.base_frame
        odom.pose.pose.position.x = s.x
        odom.pose.pose.position.y = s.y
        odom.pose.pose.orientation.x = qx
        odom.pose.pose.orientation.y = qy
        odom.pose.pose.orientation.z = qz
        odom.pose.pose.orientation.w = qw
        odom.twist.twist.linear.x = s.vx
        odom.twist.twist.angular.z = s.wz
        # Dead reckoning has no error model; mark pose covariance as "trust yaw,
        # linear growing" so RViz/Nav2 downstream treats it as such.
        for i, v in enumerate([0.05, 0.05, 1e9, 1e9, 1e9, 0.1]):
            odom.pose.covariance[i * 7] = v                 # diagonal only
        self.odom_pub.publish(odom)

        tf = TransformStamped()
        tf.header.stamp = stamp
        tf.header.frame_id = self.odom_frame
        tf.child_frame_id = self.base_frame
        tf.transform.translation.x = s.x
        tf.transform.translation.y = s.y
        tf.transform.rotation.x = qx
        tf.transform.rotation.y = qy
        tf.transform.rotation.z = qz
        tf.transform.rotation.w = qw
        self.tf.sendTransform(tf)

    def _publish_joints(self, now):
        js = JointState()
        js.header.stamp = now.to_msg()
        js.name = ["left_wheel_joint", "right_wheel_joint"]
        js.position = list(self.wheel_angles)
        r = self.spec.wheel_radius
        js.velocity = [self.vl / r, self.vr / r]
        self.joint_pub.publish(js)

    def destroy_node(self):
        try:
            if self.link is not None:
                with self.link_lock:
                    self.link.move(STOP, 0, 0)              # always halt on exit
                    self.link.close()
        finally:
            super().destroy_node()


def main():
    rclpy.init()
    node = RobotBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
