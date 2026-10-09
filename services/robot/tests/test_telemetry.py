# test_telemetry.py — gate tests for the robot's motor telemetry (UDP 3334).
# Real UDP + TCP sockets against services/robot/sim.py on 127.0.0.1. <2 s.
#   python3 -m unittest services.robot.tests.test_telemetry -v
import time
import unittest

from services.robot.link import RobotLink, TcpTransport
from services.robot.sim import RobotSim, duties_for, speed_to_duty
from services.robot.telemetry import TelemetryListener, parse_telemetry


def wait_for(cond, timeout=1.5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


class TestParse(unittest.TestCase):
    def test_firmware_line(self):
        """Same line the firmware's motor::formatTelemetry produces (C++ test)."""
        t = parse_telemetry("T,42,123456,-200,219,90\n")
        self.assertEqual((t.seq, t.robot_ms, t.duty_left, t.duty_right, t.min_duty),
                         (42, 123456, -200, 219, 90))

    def test_rejects_garbage(self):
        for bad in ("", "T,1,2,3", "X,1,2,3,4,5", "T,a,2,3,4,5", "T,1,2,999,0,90",
                    "T,1,2,0,0,300", "OK:F:200:900"):
            self.assertIsNone(parse_telemetry(bad), bad)


class TestSimDuties(unittest.TestCase):
    def test_commands_to_duties(self):
        d = speed_to_duty(200)
        self.assertEqual(duties_for("F,200,900"), ((d, d), 900))
        self.assertEqual(duties_for("L,200,500")[0], (-d, d))
        self.assertEqual(duties_for("R,200,500")[0], (d, -d))
        self.assertEqual(duties_for("B,200,500")[0], (-d, -d))
        self.assertEqual(duties_for("S"), ((0, 0), 0))
        self.assertIsNone(duties_for("P"))


class TestListener(unittest.TestCase):
    def setUp(self):
        self.sim = RobotSim(telem_port=0)
        self.lst = TelemetryListener("127.0.0.1", self.sim.telem_port)
        self.link = RobotLink(TcpTransport(self.sim.host, self.sim.port, timeout=0.5,
                                           connect_timeout=0.5))

    def tearDown(self):
        self.link.close()
        self.lst.close()
        self.sim.close()

    def duties(self):
        t, _ = self.lst.latest()
        return None if t is None else (t.duty_left, t.duty_right)

    def test_streams_after_subscribe(self):
        self.assertTrue(wait_for(lambda: self.lst.ever))
        self.assertEqual(self.duties(), (0, 0))

    def test_reports_what_the_motors_do(self):
        d = speed_to_duty(200)
        self.link.command("F,200,2000")
        self.assertTrue(wait_for(lambda: self.duties() == (d, d)))
        self.link.command("L,200,2000")
        self.assertTrue(wait_for(lambda: self.duties() == (-d, d)))
        self.link.command("S")
        self.assertTrue(wait_for(lambda: self.duties() == (0, 0)))

    def test_reports_robot_side_auto_stop(self):
        """The motors stop when the firmware's move window ends, with no stop
        command from the laptop. Telemetry shows it; command-based dead
        reckoning could not."""
        self.link.command("F,200,200")
        self.assertTrue(wait_for(lambda: self.duties() not in (None, (0, 0))))
        self.assertTrue(wait_for(lambda: self.duties() == (0, 0), timeout=1.0))

    def test_reports_commands_from_another_client(self):
        """Someone else (e.g. live_caption --robot) drives the robot: the
        listener still sees it, because telemetry is independent of who holds
        the TCP command link."""
        other = RobotLink(TcpTransport(self.sim.host, self.sim.port, timeout=0.5,
                                       connect_timeout=0.5))
        try:
            other.command("R,255,2000")
            self.assertTrue(wait_for(lambda: self.duties() == (255, -255)))
        finally:
            other.close()

    def test_age_grows_when_robot_disappears(self):
        self.assertTrue(wait_for(lambda: self.lst.ever))
        self.sim.close()
        time.sleep(0.4)
        _, age = self.lst.latest()
        self.assertGreater(age, 0.25)


if __name__ == "__main__":
    unittest.main()
