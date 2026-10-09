# test_wifi.py — gate tests for the WiFi (TCP) link. Deterministic, no hardware,
# no LAN: the board is services/robot/sim.py on 127.0.0.1. <2s.
# Run: python3 -m unittest services.robot.tests.test_wifi -v
import time
import unittest
from unittest import mock

from services.robot import config, link
from services.robot.contract import FORWARD, LEFT, STOP
from services.robot.controller import RobotController
from services.robot.intent import IntentParser
from services.robot.link import (RobotLink, TcpTransport, discover, is_async,
                                 open_transport, probe, scan_subnet)
from services.robot.sim import RobotSim, reply_for


def tcp(sim, **kw):
    return TcpTransport(sim.host, sim.port, timeout=0.5, connect_timeout=0.5, **kw)


def wait_for(cond, timeout=1.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False


class TestSimProtocol(unittest.TestCase):
    """The sim must answer exactly like the firmware's handle()."""

    def test_replies(self):
        self.assertEqual(reply_for("P"), "PONG")
        self.assertEqual(reply_for("s"), "OK:S")
        self.assertEqual(reply_for("F,200,900"), "OK:F:200:900")
        self.assertEqual(reply_for("L"), "OK:L:200:900")
        self.assertEqual(reply_for("R,999,99999"), "OK:R:255:5000")
        self.assertEqual(reply_for("X"), "ERR:unknown")

    def test_async_lines(self):
        self.assertTrue(is_async("READY"))
        self.assertTrue(is_async("OK:S:watchdog"))
        self.assertFalse(is_async("OK:S"))
        self.assertFalse(is_async("PONG"))


class TestTcpTransport(unittest.TestCase):
    def setUp(self):
        self.sim = RobotSim()

    def tearDown(self):
        self.sim.close()

    def test_ping_skips_ready_banner(self):
        t = tcp(self.sim)
        self.assertEqual(t.send("P"), "PONG")      # READY drained/skipped
        t.close()

    def test_move_roundtrip(self):
        rl = RobotLink(tcp(self.sim))
        ok, ack, err = rl.move(FORWARD, 200, 1000)
        self.assertTrue(ok)
        self.assertEqual(ack, "OK:F:200:1000")
        self.assertIsNone(err)
        ok, ack, _ = rl.move(STOP, 0, 0)
        self.assertEqual(ack, "OK:S")
        self.assertEqual(self.sim.received[-2:], ["F,200,1000", "S"])
        self.assertTrue(rl.ping())
        rl.close()

    def test_skips_unsolicited_watchdog_line(self):
        t = tcp(self.sim)
        t.send("P")
        self.sim.push("OK:S:watchdog")              # async line sitting in buffer
        time.sleep(0.02)
        self.assertEqual(t.send("L,200,550"), "OK:L:200:550")
        t.close()

    def test_reconnects_after_wifi_blip(self):
        rl = RobotLink(tcp(self.sim))
        self.assertTrue(rl.ping())
        self.sim.drop_client()                       # robot side drops us
        time.sleep(0.02)
        ok, ack, _ = rl.move(LEFT, 180, 550)         # reconnect + retry, once
        self.assertTrue(ok, ack)
        self.assertEqual(ack, "OK:L:180:550")
        self.assertEqual(self.sim.connects, 2)
        rl.close()

    def test_robot_gone_reports_error_not_crash(self):
        rl = RobotLink(tcp(self.sim))
        self.sim.close()                             # board powered off
        ok, ack, err = rl.move(FORWARD, 200, 900)
        self.assertFalse(ok)
        self.assertTrue(err)
        # throttled: a second attempt right away fails fast without connecting
        t0 = time.monotonic()
        ok, _, err = rl.move(FORWARD, 200, 900)
        self.assertFalse(ok)
        self.assertLess(time.monotonic() - t0, 0.2)

    def test_controller_over_wifi(self):
        ctrl = RobotController(IntentParser(grok=None), RobotLink(tcp(self.sim)))
        self.assertTrue(ctrl.health())
        res = ctrl.handle("máa lọ síwájú")
        self.assertTrue(res.moved)
        self.assertTrue(res.ack.startswith("OK:F:"))
        ok, ack, _ = ctrl.drive(LEFT, 200)
        self.assertEqual(ack, f"OK:L:200:{config.HOLD_MS}")
        ok, ack, _ = ctrl.halt()
        self.assertEqual(ack, "OK:S")
        ctrl.close()

    def test_keepalive_resends_over_wifi(self):
        ctrl = RobotController(IntentParser(grok=None), RobotLink(tcp(self.sim)))
        with mock.patch.object(config, "HOLD_REFRESH", 0.02):
            ctrl.start_keepalive()
            ctrl.drive(FORWARD, 200)
            self.assertTrue(wait_for(
                lambda: self.sim.received.count(f"F,200,{config.HOLD_MS}") >= 4))
            ctrl.halt()
            ctrl.stop_keepalive()
        self.assertEqual(self.sim.received[-1], "S")
        ctrl.close()


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self.sim = RobotSim()

    def tearDown(self):
        self.sim.close()

    def test_probe(self):
        self.assertTrue(probe(self.sim.host, self.sim.port, timeout=0.5))
        self.sim.close()
        self.assertFalse(probe("127.0.0.1", self.sim.port, timeout=0.2))

    def test_pinned_host_wins(self):
        with mock.patch.object(config, "HOST", "10.1.2.3"):
            self.assertEqual(discover(mdns=lambda: self.fail("no mdns")),
                             ("10.1.2.3", "ROBOT_HOST"))

    def test_mdns_then_scan(self):
        with mock.patch.object(config, "HOST", "auto"):
            host, how = discover(mdns=lambda: "192.168.43.57",
                                 probe_fn=lambda h: h == "192.168.43.57")
            self.assertEqual(host, "192.168.43.57")
            self.assertIn("mDNS", how)
            # mDNS fails -> sweep the /24
            host, how = discover(mdns=lambda: None,
                                 probe_fn=lambda h: h == "192.168.43.88",
                                 subnet=lambda: "192.168.43")
            self.assertEqual(host, "192.168.43.88")
            self.assertIn("LAN scan", how)
            # nothing answers -> clear reason
            host, how = discover(mdns=lambda: None, probe_fn=lambda h: False,
                                 subnet=lambda: "192.168.43")
            self.assertIsNone(host)
            self.assertIn("robot's WiFi", how)

    def test_resolve_mdns_parses_getent(self):
        import subprocess
        from services.robot.link import resolve_mdns

        def ok(cmd, **kw):
            self.assertEqual(cmd[:2], ["getent", "ahostsv4"])
            return subprocess.CompletedProcess(
                cmd, 0, "192.168.43.57   STREAM yoruba-robot.local\n", "")

        def missing(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 2, "", "")

        def hangs(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])

        self.assertEqual(resolve_mdns(run=ok), "192.168.43.57")
        self.assertIsNone(resolve_mdns(run=missing))
        self.assertIsNone(resolve_mdns(run=hangs))

    def test_observer_trusts_mdns_without_probing(self):
        """verify_mdns=False must not open a TCP connection to the robot: that
        would kick (and stop) whoever is driving it."""
        with mock.patch.object(config, "HOST", "auto"):
            host, how = discover(mdns=lambda: "192.168.1.197",
                                 probe_fn=lambda h: self.fail("probed the robot"),
                                 verify_mdns=False)
        self.assertEqual(host, "192.168.1.197")

    def test_lazy_transport_connects_on_first_send(self):
        t = TcpTransport(self.sim.host, self.sim.port, timeout=0.5,
                         connect_timeout=0.5, connect_now=False)
        time.sleep(0.05)
        self.assertEqual(self.sim.connects, 0)
        self.assertFalse(t.connected)
        self.assertEqual(t.send("P"), "PONG")
        self.assertEqual(self.sim.connects, 1)
        t.close()

    def test_scan_subnet_finds_one(self):
        self.assertEqual(scan_subnet("10.0.0", probe_fn=lambda h: h == "10.0.0.42"),
                         "10.0.0.42")
        self.assertIsNone(scan_subnet("10.0.0", probe_fn=lambda h: False))


class TestOpenTransport(unittest.TestCase):
    def test_auto_prefers_wifi(self):
        sim = RobotSim()
        try:
            with mock.patch.object(config, "LINK", "auto"), \
                 mock.patch.object(config, "HOST", sim.host), \
                 mock.patch.object(config, "TCP_PORT", sim.port), \
                 mock.patch.object(link, "open_serial",
                                   side_effect=AssertionError("serial used")):
                t = open_transport()
                self.assertIsInstance(t, TcpTransport)
                self.assertEqual(t.send("P"), "PONG")
                t.close()
        finally:
            sim.close()

    def test_auto_falls_back_to_serial(self):
        fake = object()
        with mock.patch.object(config, "LINK", "auto"), \
             mock.patch.object(link, "open_tcp", side_effect=RuntimeError("no wifi")), \
             mock.patch.object(link, "open_serial", return_value=fake):
            self.assertIs(open_transport(), fake)

    def test_errors_list_both_links(self):
        with mock.patch.object(config, "LINK", "auto"), \
             mock.patch.object(link, "open_tcp", side_effect=RuntimeError("no wifi")), \
             mock.patch.object(link, "open_serial", side_effect=RuntimeError("no usb")):
            with self.assertRaises(RuntimeError) as cm:
                open_transport()
        self.assertIn("wifi: no wifi", str(cm.exception))
        self.assertIn("usb: no usb", str(cm.exception))

    def test_wifi_only_never_touches_serial(self):
        with mock.patch.object(config, "LINK", "wifi"), \
             mock.patch.object(link, "open_tcp", side_effect=RuntimeError("x")), \
             mock.patch.object(link, "open_serial",
                               side_effect=AssertionError("serial used")):
            with self.assertRaises(RuntimeError):
                open_transport()

    def test_bad_mode(self):
        with mock.patch.object(config, "LINK", "bluetooth"):
            with self.assertRaises(RuntimeError):
                open_transport()


if __name__ == "__main__":
    unittest.main()
