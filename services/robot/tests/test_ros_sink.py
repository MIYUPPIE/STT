# test_ros_sink.py — gate tests for services/robot/ros_sink.py.
# Deterministic, no ROS, no live socket (we spin a tiny TCP server inline).
# Run: python3 -m unittest services.robot.tests.test_ros_sink -v
import socket
import threading
import time
import unittest

from services.robot.contract import FORWARD, LEFT, STOP
from services.robot.ros_sink import RosController, RosSink, build_ros_controller


class FakeRelay:
    """Minimal TCP server that collects lines and never replies (just like
    voice_relay)."""

    def __init__(self):
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(2)
        self.host, self.port = self.srv.getsockname()
        self.lines: list[str] = []
        self._stop = threading.Event()
        self._clients: list[socket.socket] = []
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self):
        self.srv.settimeout(0.05)
        while not self._stop.is_set():
            try:
                c, _ = self.srv.accept()
            except (socket.timeout, OSError):
                continue
            self._clients.append(c)
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        c.settimeout(0.5)
        buf = b""
        while not self._stop.is_set():
            try:
                data = c.recv(256)
            except (socket.timeout, OSError):
                continue
            if not data:
                return
            buf += data
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                self.lines.append(raw.decode(errors="replace").strip())

    def drop_all_clients(self):
        for c in self._clients:
            try: c.close()
            except OSError: pass
        self._clients.clear()

    def close(self):
        self._stop.set()
        try: self.srv.shutdown(socket.SHUT_RDWR)
        except OSError: pass
        self._t.join(timeout=1)
        try: self.srv.close()
        except OSError: pass
        self.drop_all_clients()


def wait_for(cond, timeout=1.5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond(): return True
        time.sleep(0.005)
    return False


class FakeParser:
    def __init__(self, action, speed=None):
        from services.robot.contract import Intent
        self.result = Intent(action, speed, "test", "")
    def parse(self, _): return self.result


class TestRosSink(unittest.TestCase):
    def setUp(self):
        self.relay = FakeRelay()

    def tearDown(self):
        self.relay.close()

    def test_sends_lines(self):
        sink = RosSink("127.0.0.1", self.relay.port); sink._connect()
        sink.send("F,200"); sink.send("S")
        self.assertTrue(wait_for(lambda: self.relay.lines[-2:] == ["F,200", "S"]))
        sink.close()

    def test_reconnect_on_broken_pipe(self):
        """On a peer drop the kernel can swallow one buffered send; the keepalive
        thread resends until it lands. This test models that: a short burst of
        sends after the drop must eventually deliver (same pattern voice_relay
        and the keepalive loop produce in production)."""
        sink = RosSink("127.0.0.1", self.relay.port); sink._connect()
        sink.send("F,255")
        self.assertTrue(wait_for(lambda: "F,255" in self.relay.lines))
        self.relay.drop_all_clients(); time.sleep(0.05)
        for _ in range(5):
            try: sink.send("S")
            except Exception: pass
            if wait_for(lambda: "S" in self.relay.lines, timeout=0.2):
                break
            time.sleep(0.05)
        self.assertIn("S", self.relay.lines)
        sink.close()

    def test_fail_fast_when_relay_down(self):
        sink = RosSink("127.0.0.1", self.relay.port); sink._connect()
        self.relay.close(); time.sleep(0.05)
        with self.assertRaises(Exception):
            sink.send("F,200")
        # throttled: second attempt returns fast with the throttled message
        t0 = time.monotonic()
        with self.assertRaises(RuntimeError) as cm:
            sink.send("F,200")
        self.assertLess(time.monotonic() - t0, 0.2)
        self.assertIn("unreachable", str(cm.exception))


class TestRosController(unittest.TestCase):
    def setUp(self):
        self.relay = FakeRelay()
        self.ctrl = build_ros_controller(FakeParser(FORWARD, 200),
                                         "127.0.0.1", self.relay.port)
        self.ctrl._hold_refresh = 0.02

    def tearDown(self):
        self.ctrl.close(); self.relay.close()

    def test_health_sends_ping(self):
        self.assertTrue(self.ctrl.health())
        self.assertTrue(wait_for(lambda: "P" in self.relay.lines))

    def test_drive_and_halt(self):
        ok, _, _ = self.ctrl.drive(FORWARD, 200)
        self.assertTrue(ok)
        self.assertTrue(wait_for(lambda: "F,200" in self.relay.lines))
        self.ctrl.halt()
        self.assertTrue(wait_for(lambda: self.relay.lines[-1] == "S"))

    def test_keepalive_resends_current_target(self):
        self.ctrl.start_keepalive()
        self.ctrl.drive(LEFT, 180)
        self.assertTrue(wait_for(
            lambda: self.relay.lines.count("L,180") >= 3, timeout=1.0))
        self.ctrl.halt()
        self.ctrl.stop_keepalive()

    def test_build_fails_gracefully_without_relay(self):
        self.relay.close(); time.sleep(0.05)
        ctrl = build_ros_controller(FakeParser(STOP), "127.0.0.1", self.relay.port)
        self.assertFalse(ctrl.health())
        self.assertIsNotNone(ctrl.open_error)


if __name__ == "__main__":
    unittest.main()
