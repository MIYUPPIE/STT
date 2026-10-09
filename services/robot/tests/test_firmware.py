# test_firmware.py — gate tests for firmware/esp32s3_robot. Builds and runs the
# host C++ test of motor_math.h with g++ (skipped if no g++), and checks the
# sketch keeps speed on ENA/ENB (PWM) with the IN pins as plain direction lines.
# Run: python3 -m unittest services.robot.tests.test_firmware -v
import os
import re
import shutil
import subprocess
import tempfile
import unittest

FW = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..",
                                  "firmware", "esp32s3_robot"))
INO = os.path.join(FW, "esp32s3_robot.ino")


def defines(src):
    return {m.group(1): m.group(2)
            for m in re.finditer(r"^#define\s+(\w+)\s+(\S+)", src, re.M)}


class TestMotorMath(unittest.TestCase):
    @unittest.skipUnless(shutil.which("g++"), "g++ not installed")
    def test_host_cpp_suite(self):
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "t")
            build = subprocess.run(
                ["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-o", exe,
                 os.path.join(FW, "tests", "test_motor_math.cpp")],
                capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([exe], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout)


class TestSketchWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(INO) as f:
            cls.src = f.read()
        cls.d = defines(cls.src)

    def test_pins(self):
        self.assertEqual([self.d[k] for k in ("IN1", "IN2", "IN3", "IN4")],
                         ["4", "5", "6", "7"])
        self.assertEqual((self.d["ENA"], self.d["ENB"]), ("41", "42"))

    def test_enable_pins_are_esp32s3_safe(self):
        # strapping, USB D-/D+, UART0, octal PSRAM, camera bus, onboard LEDs
        unsafe = {0, 3, 45, 46, 19, 20, 43, 44, 35, 36, 37,
                  4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 16, 17, 18, 2, 21, 47}
        for k in ("ENA", "ENB"):
            self.assertNotIn(int(self.d[k]), unsafe, k)
        self.assertNotEqual(self.d["ENA"], self.d["ENB"])

    def test_pwm_only_on_enable_pins(self):
        attached = re.findall(r"ledcAttach\((\w+)", self.src)
        self.assertEqual(sorted(attached), ["ENA", "ENB"])
        writes = set(re.findall(r"ledcWrite\(([\w.]+)", self.src))
        self.assertEqual(writes, {"m.en"})

    def test_ramp_uses_dead_zone_and_stop_is_instant(self):
        self.assertIn("RAMP_STEP, MIN_DUTY", self.src)
        stop = self.src[self.src.index("void stopMotors()"):]
        stop = stop[:stop.index("}")]
        self.assertIn("applyMotor(motorL, 0)", stop)
        self.assertIn("applyMotor(motorR, 0)", stop)

    def test_telemetry_stream(self):
        """The robot reports its applied motor duties on UDP 3334 (RViz follows
        the real motors through this), using the host-tested formatter."""
        self.assertEqual(self.d["TELEM_PORT"], "3334")
        self.assertEqual(self.d["TELEM_MS"], "50")             # 20 Hz
        self.assertIn("motor::formatTelemetry(", self.src)
        self.assertIn("motorL.cur, motorR.cur, MIN_DUTY", self.src)
        self.assertIn("serviceTelemetry();", self.src)

    def test_ota_stops_motors(self):
        self.assertIn("ArduinoOTA.handle()", self.src)
        self.assertIn("ArduinoOTA.onStart([]() { stopMotors();", self.src)

    def test_tuning_in_range(self):
        self.assertTrue(0 <= int(self.d["MIN_DUTY"]) < 255)
        for k in ("LEFT_TRIM", "RIGHT_TRIM"):
            self.assertTrue(50 <= int(self.d[k]) <= 100, k)
        self.assertEqual(self.d["PWM_FREQ"], "1000")   # L298N needs ~1 kHz


if __name__ == "__main__":
    unittest.main()
