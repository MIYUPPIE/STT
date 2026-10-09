# ros_sink.py — a tiny TCP line writer that stands in for the ESP32 serial link
# when live_caption.py runs with --ros. It speaks the exact same line protocol
# the firmware does (F,200\n / S\n), but the destination is the ROS2
# voice_relay node (services/ros2/yoruba_robot/.../voice_relay.py), which turns
# each line into a /cmd_vel Twist. One contract across both borders.
#
# The ROS stack lives in system Python 3.12 and live_caption runs in conda
# 3.13; this is the glue between them. See services/ros2/README.md.
from __future__ import annotations

import os
import socket
import threading
import time

from .contract import FORWARD, BACKWARD, LEFT, RIGHT, STOP, WIRE


HOST = os.environ.get("ROS_SINK_HOST", "127.0.0.1")
PORT = int(os.environ.get("ROS_SINK_PORT", "7447"))
RETRY_GAP = 1.0
CONNECT_TIMEOUT = float(os.environ.get("ROS_SINK_CONNECT_TIMEOUT", "0.5"))


class RosSink:
    """Serialized line writer with reconnect-once-on-failure, like TcpTransport
    but a client-side sink (we only write; voice_relay never replies). All sends
    are serialized because the keepalive thread shares the socket with the main
    thread.
    """

    def __init__(self, host=None, port=None,
                 connect=socket.create_connection):
        self.host = host or HOST
        self.port = port or PORT
        self._connect_fn = connect
        self._sock = None
        self._lock = threading.Lock()
        self._last_fail = 0.0

    def _connect(self):
        try:
            s = self._connect_fn((self.host, self.port), timeout=CONNECT_TIMEOUT)
        except OSError as e:
            self._last_fail = time.monotonic()
            raise RuntimeError(f"ros_sink: {e}")
        try:
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        self._sock = s

    def _half_closed(self) -> bool:
        """TCP: after the server closes the client fd, our next sendall buffers
        locally and only the second send errors. So before sending we peek the
        socket: a readable zero-byte recv means the peer sent FIN."""
        try:
            self._sock.setblocking(False)
            data = self._sock.recv(1, socket.MSG_PEEK)
            return not data                                 # b'' = FIN received
        except BlockingIOError:
            return False
        except OSError:
            return True
        finally:
            try:
                self._sock.setblocking(True)
            except OSError:
                pass

    def send(self, line: str):
        with self._lock:
            if self._sock is None:
                if time.monotonic() - self._last_fail < RETRY_GAP:
                    raise RuntimeError("ros_sink: voice_relay unreachable")
                self._connect()
            elif self._half_closed():                       # peer went away
                try: self._sock.close()
                except OSError: pass
                self._sock = None
                self._connect()
            try:
                self._sock.sendall((line + "\n").encode())
                if self._half_closed():                     # buffered to a dead
                    raise OSError("peer closed after send")  # peer: force retry
            except OSError:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
                self._connect()
                self._sock.sendall((line + "\n").encode())

    def close(self):
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None


class RosController:
    """Drop-in stand-in for RobotController when --ros is on. Only the three
    methods live_caption actually calls (drive / halt / start_keepalive /
    stop_keepalive, parser, link, port) are implemented; everything else that
    the direct-serial path uses stays on the real controller.

    No odometry, no replies: the ROS node downstream owns both."""

    def __init__(self, parser, sink: RosSink | None, open_error=None, hold_refresh=0.4):
        self.parser = parser
        self.sink = sink
        self.open_error = open_error
        self.last_error = open_error
        self._target = None
        self._lock = threading.Lock()
        self._ka = None
        self._ka_stop = threading.Event()
        self._hold_refresh = hold_refresh

    @property
    def port(self):
        return f"ros voice_relay {self.sink.host}:{self.sink.port}" if self.sink else "(not open)"

    @property
    def link(self):
        return None                                         # live_caption inspects .link.transport

    def health(self) -> bool:
        if self.sink is None:
            self.last_error = self.open_error
            return False
        try:
            self.sink.send("P")                             # voice_relay ignores unknown lines
            self.last_error = None
            return True
        except Exception as e:
            self.last_error = str(e)
            return False

    def _line(self, action: str, speed: int) -> str:
        if action == STOP:
            return "S"
        return f"{WIRE[action]},{speed}"

    def drive(self, action: str, speed: int):
        with self._lock:
            self._target = (action, speed)
        try:
            self.sink.send(self._line(action, speed))
            return True, "ok", None
        except Exception as e:
            return False, "", str(e)

    def halt(self):
        with self._lock:
            self._target = None
        try:
            self.sink.send("S")
            return True, "ok", None
        except Exception as e:
            return False, "", str(e)

    def start_keepalive(self):
        if self._ka is not None or self.sink is None:
            return

        def loop():
            while not self._ka_stop.is_set():
                with self._lock:
                    tgt = self._target
                if tgt is not None:
                    try:
                        self.sink.send(self._line(*tgt))
                    except Exception:
                        pass                                # relay down: next drive() reconnects
                self._ka_stop.wait(self._hold_refresh)
        self._ka = threading.Thread(target=loop, daemon=True)
        self._ka.start()

    def stop_keepalive(self):
        self._ka_stop.set()

    def close(self):
        self.stop_keepalive()
        if self.sink is not None:
            self.sink.close()


def build_ros_controller(parser, host=None, port=None) -> RosController:
    try:
        sink = RosSink(host, port)
        sink._connect()                                     # fail fast if relay is down
        return RosController(parser, sink)
    except Exception as e:
        return RosController(parser, None, open_error=str(e))
