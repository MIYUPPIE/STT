# voice_relay.py — ROS2 node that listens on a localhost TCP line protocol and
# republishes commands as /cmd_vel (Twist). This is the bridge between
# live_caption.py (conda py3.13, has the STT stack) and the ROS2 world (system
# py3.12, has rclpy). The STT process writes "F,200\n" / "S\n" to port 7447;
# this node turns each line into a Twist on /cmd_vel.
#
# Why a tiny TCP protocol instead of forcing one Python to host both: ROS2
# Jazzy's rclpy is bound to system Python 3.12; faster-whisper is installed in
# conda Python 3.13. The protocol is the same five letters the firmware already
# speaks, so there is one contract across both borders.
#
# ros2 run yoruba_robot voice_relay --ros-args -p port:=7447
import socket
import threading

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy

from .kinematics import (BACKWARD, FORWARD, LEFT, RIGHT, STOP, RobotSpec,
                         WIRE_TO_ACTION)


def line_to_twist(line: str, spec: RobotSpec) -> Twist | None:
    """'F,200' / 'S' / 'L,255' -> Twist. speed 0..255; speed 0 = STOP. Returns
    None for an unparseable line (silently drops: never crashes the relay)."""
    parts = [p.strip() for p in line.strip().split(",") if p.strip()]
    if not parts:
        return None
    wire = parts[0].upper()
    cmd = WIRE_TO_ACTION.get(wire)
    if cmd is None:
        return None
    speed = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else 200
    speed = max(0, min(255, speed))
    if cmd == STOP or speed == 0:
        return Twist()
    frac = (speed - spec.min_duty) / max(1, 255 - spec.min_duty) if speed > spec.min_duty else 0.1
    frac = max(0.0, min(1.0, frac))
    t = Twist()
    if cmd == FORWARD:
        t.linear.x = frac * spec.max_linear
    elif cmd == BACKWARD:
        t.linear.x = -frac * spec.max_linear
    elif cmd == LEFT:
        t.angular.z = frac * spec.max_angular
    elif cmd == RIGHT:
        t.angular.z = -frac * spec.max_angular
    return t


class VoiceRelay(Node):
    def __init__(self):
        super().__init__("voice_relay")
        self.declare_parameter("host", "127.0.0.1")
        self.declare_parameter("port", 7447)
        self.declare_parameter("wheel_separation", RobotSpec.wheel_separation)
        self.declare_parameter("max_linear", RobotSpec.max_linear)
        self.declare_parameter("max_angular", RobotSpec.max_angular)
        p = self.get_parameter
        self.spec = RobotSpec(
            wheel_separation=p("wheel_separation").value,
            max_linear=p("max_linear").value,
            max_angular=p("max_angular").value)
        self.host, self.port = p("host").value, p("port").value

        qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)
        self.pub = self.create_publisher(Twist, "cmd_vel", qos)

        self._stop = threading.Event()
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((self.host, self.port))
        self._srv.listen(1)
        self._t = threading.Thread(target=self._accept_loop, daemon=True)
        self._t.start()
        self.get_logger().info(f"voice_relay listening on {self.host}:{self.port}")

    def _accept_loop(self):
        self._srv.settimeout(0.1)
        while not self._stop.is_set():
            try:
                c, addr = self._srv.accept()
            except (socket.timeout, OSError):
                continue
            self.get_logger().info(f"voice client: {addr[0]}:{addr[1]}")
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        c.settimeout(1.0)
        buf = b""
        while not self._stop.is_set():
            try:
                data = c.recv(256)
            except socket.timeout:
                continue
            except OSError:
                return
            if not data:
                return
            buf += data
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                t = line_to_twist(line, self.spec)
                if t is None:
                    self.get_logger().debug(f"ignored: {line!r}")
                    continue
                self.pub.publish(t)
                self.get_logger().debug(
                    f"{line!r} -> vx={t.linear.x:.2f} wz={t.angular.z:.2f}")

    def destroy_node(self):
        self._stop.set()
        try:
            self._srv.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._srv.close()
        except OSError:
            pass
        self._t.join(timeout=1)
        super().destroy_node()


def main():
    rclpy.init()
    node = VoiceRelay()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
