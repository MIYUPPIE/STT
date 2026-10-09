# environment_publisher.py — draws the arena (yoruba_robot/arena.py) in RViz as
# a MarkerArray on /environment, in the odom frame. Gazebo builds its world from
# the same ARENA, so what you see in RViz is what the simulated robot can bump.
#
# Latched (transient-local QoS): RViz gets the markers even if it starts after
# this node. Re-published every 2 s in case RViz is restarted with a fresh QoS.
#
# ros2 run yoruba_robot environment_publisher
import rclpy
from rclpy.executors import ExternalShutdownException
from geometry_msgs.msg import Point
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from visualization_msgs.msg import Marker, MarkerArray

from .arena import ARENA, obstacle_height, walls


def _marker(mid, kind, xyz, scale, rgba, ns, frame, text=""):
    m = Marker()
    m.header.frame_id = frame
    # stamp 0 = "use the latest transform": markers stay visible regardless of
    # whether RViz runs on wall time or sim time.
    m.ns = ns
    m.id = mid
    m.type = kind
    m.action = Marker.ADD
    m.pose.position.x, m.pose.position.y, m.pose.position.z = xyz
    m.pose.orientation.w = 1.0
    m.scale.x, m.scale.y, m.scale.z = scale
    m.color.r, m.color.g, m.color.b, m.color.a = rgba
    m.frame_locked = True
    m.text = text
    return m


def build_markers(frame="odom", a=ARENA) -> MarkerArray:
    out = MarkerArray()
    mid = 0
    # floor slab just below z=0 so the grid and robot sit on top of it
    fx, fy = a["size_x"], a["size_y"]
    out.markers.append(_marker(mid, Marker.CUBE, (0.0, 0.0, -0.003), (fx, fy, 0.006),
                               (*a["floor_color"], 1.0), "floor", frame))
    for w in walls(a):
        mid += 1
        out.markers.append(_marker(mid, Marker.CUBE, w["xyz"], w["size"],
                                   (*a["wall_color"], 0.85), "walls", frame))
    for o in a["obstacles"]:
        mid += 1
        x, y = o["xy"]
        h = obstacle_height(o)
        if o["shape"] == "box":
            out.markers.append(_marker(mid, Marker.CUBE, (x, y, h / 2), o["size"],
                                       (*o["color"], 1.0), "obstacles", frame))
        else:
            r = o["size"][0]
            out.markers.append(_marker(mid, Marker.CYLINDER, (x, y, h / 2),
                                       (2 * r, 2 * r, h), (*o["color"], 1.0),
                                       "obstacles", frame))
        mid += 1
        out.markers.append(_marker(mid, Marker.TEXT_VIEW_FACING, (x, y, h + 0.06),
                                   (0.0, 0.0, 0.06), (1.0, 1.0, 1.0, 0.9),
                                   "labels", frame, o["name"]))
    # start pad + heading arrow: where the robot begins, facing +x
    mid += 1
    out.markers.append(_marker(mid, Marker.CYLINDER, (0.0, 0.0, 0.001),
                               (0.30, 0.30, 0.002), (0.20, 0.70, 0.30, 0.35),
                               "start", frame))
    mid += 1
    arrow = _marker(mid, Marker.ARROW, (0.0, 0.0, 0.0), (0.012, 0.025, 0.03),
                    (0.20, 0.70, 0.30, 0.9), "start", frame)
    arrow.points = [Point(x=0.12, y=0.0, z=0.003), Point(x=0.24, y=0.0, z=0.003)]
    out.markers.append(arrow)
    return out


class EnvironmentPublisher(Node):
    def __init__(self):
        super().__init__("environment_publisher")
        self.declare_parameter("frame", "odom")
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.pub = self.create_publisher(MarkerArray, "environment", qos)
        self.markers = build_markers(self.get_parameter("frame").value)
        self.pub.publish(self.markers)
        self.create_timer(2.0, lambda: self.pub.publish(self.markers))
        self.get_logger().info(
            f"environment: {ARENA['size_x']}x{ARENA['size_y']} m arena, "
            f"{len(ARENA['obstacles'])} obstacles, {len(self.markers.markers)} markers")


def main():
    rclpy.init()
    node = EnvironmentPublisher()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        # Ctrl+C: rclpy's signal handler has usually shut the context down
        # already; a plain shutdown() would raise 'already called'.
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
