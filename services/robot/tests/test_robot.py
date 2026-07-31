# test_robot.py — gate tests. Deterministic, no network, no serial, free, <2s.
# Run: python3 -m unittest services.robot.tests.test_robot -v
import unittest

from services.robot import config, responses
from services.robot.contract import (Intent, FORWARD, BACKWARD, LEFT, RIGHT,
                                      STOP, NONE)
from services.robot.vocab import normalize, rule_intent
from services.robot.intent import parse_json_intent, GrokIntent, IntentParser
from services.robot.link import RobotLink
from services.robot.controller import RobotController
from services.robot.intent import build_parser  # noqa: F401 (import smoke)


# ---------------- fakes ----------------
def fake_chat(status, content):
    def transport(url, payload, timeout, headers=None):
        return status, {"choices": [{"message": {"content": content}}]}
    return transport


class FakeSerial:
    """Records every line sent; returns a fixed (or scripted) ACK."""
    def __init__(self, ack="OK:F:200:900", scripted=None):
        self.sent = []
        self.ack = ack
        self.scripted = scripted or {}   # exact-line -> ack override
        self.port = "fake"

    def send(self, line):
        self.sent.append(line)
        return self.scripted.get(line, self.ack)

    def close(self):
        pass


class RaisingSerial:
    port = "fake"

    def send(self, line):
        raise OSError("device disconnected")


class HoldSerial:
    """Records sends; raises KeyboardInterrupt after `raise_after` move frames to
    simulate the user pressing Ctrl+C. The final 'S' (sent from hold's finally)
    still goes through."""
    port = "fake"

    def __init__(self, raise_after=3):
        self.sent = []
        self.raise_after = raise_after
        self.moves = 0

    def send(self, line):
        self.sent.append(line)
        if line != "S":
            self.moves += 1
            if self.moves >= self.raise_after:
                raise KeyboardInterrupt
        return "OK"

    def close(self):
        pass


# ================== normalization ==================
class TestNormalize(unittest.TestCase):
    def test_strips_tone(self):
        self.assertEqual(normalize("Máa lọ síwájú!"), "maa lo siwaju")
        self.assertEqual(normalize("padà sẹ́yìn"), "pada seyin")

    def test_empty(self):
        self.assertEqual(normalize("  ??? "), "")


# ================== rule matcher ==================
class TestRule(unittest.TestCase):
    def act(self, t):
        return rule_intent(t).action

    def test_forward(self):
        self.assertEqual(self.act("máa lọ síwájú"), FORWARD)
        self.assertEqual(self.act("síwájú"), FORWARD)

    def test_backward(self):
        self.assertEqual(self.act("padà sẹ́yìn"), BACKWARD)
        self.assertEqual(self.act("padà"), BACKWARD)

    def test_left_right(self):
        self.assertEqual(self.act("yà sí òsì"), LEFT)
        self.assertEqual(self.act("yíjú sí ọ̀tún"), RIGHT)

    def test_stop(self):
        self.assertEqual(self.act("dúró"), STOP)

    def test_stop_beats_go(self):
        # "don't go, stop" must halt even though 'lọ' (go) is present
        self.assertEqual(self.act("má lọ, dúró"), STOP)

    def test_speed_fast_slow(self):
        self.assertEqual(rule_intent("lọ síwájú kíákíá").speed, config.FAST_SPEED)
        self.assertEqual(rule_intent("lọ síwájú díẹ̀díẹ̀").speed, config.SLOW_SPEED)
        self.assertIsNone(rule_intent("lọ síwájú").speed)

    def test_split_word_transcriptions(self):
        # Whisper commonly splits the word; the despaced match must still catch it
        self.assertEqual(self.act("sí wá jù"), FORWARD)     # síwájú split
        self.assertEqual(self.act("sí wa jù"), FORWARD)
        self.assertEqual(self.act("sẹ́ yìn"), BACKWARD)      # sẹ́yìn split

    def test_non_command_none(self):
        self.assertEqual(self.act("báwo ni o ṣe wà"), NONE)
        self.assertEqual(self.act("kò sí ìṣòro"), NONE)   # 'kosi' must not hit 'osi'
        self.assertEqual(self.act("ó ń sáré títí"), NONE) # despace must not misfire
        self.assertEqual(self.act(""), NONE)


# ================== Grok JSON ==================
class TestJson(unittest.TestCase):
    def test_plain(self):
        i = parse_json_intent('{"action":"forward","speed":null}', "x")
        self.assertEqual(i.action, FORWARD)
        self.assertIsNone(i.speed)

    def test_speed_label(self):
        i = parse_json_intent('{"action":"backward","speed":"slow"}', "x")
        self.assertEqual(i.action, BACKWARD)
        self.assertEqual(i.speed, config.SLOW_SPEED)

    def test_fence_and_prose(self):
        i = parse_json_intent('```json\n{"action":"left","speed":"fast"}\n```', "x")
        self.assertEqual(i.action, LEFT)
        self.assertEqual(i.speed, config.FAST_SPEED)

    def test_bad_action_none(self):
        self.assertEqual(parse_json_intent('{"action":"fly"}', "x").action, NONE)

    def test_garbage_none(self):
        self.assertEqual(parse_json_intent("nope", "x").action, NONE)


class TestGrokIntent(unittest.TestCase):
    def test_success(self):
        g = GrokIntent(api_key="k",
                       transport=fake_chat(200, '{"action":"right","speed":null}'))
        self.assertEqual(g.parse("x").action, RIGHT)

    def test_missing_key(self):
        i = GrokIntent(api_key="", transport=fake_chat(200, "{}")).parse("x")
        self.assertEqual(i.action, NONE)
        self.assertIn("XAI_API_KEY", i.error)

    def test_http_error(self):
        i = GrokIntent(api_key="k", transport=fake_chat(500, "")).parse("x")
        self.assertEqual(i.source, "error")


class TestParser(unittest.TestCase):
    def test_rule_wins(self):
        called = {"n": 0}

        class Spy:
            def parse(self, t):
                called["n"] += 1
                return Intent(NONE, None, "grok", t)

        p = IntentParser(grok=Spy())
        self.assertEqual(p.parse("síwájú").action, FORWARD)
        self.assertEqual(called["n"], 0)

    def test_grok_fallback(self):
        g = GrokIntent(api_key="k",
                       transport=fake_chat(200, '{"action":"stop","speed":null}'))
        # a negation the rules don't cover
        self.assertEqual(IntentParser(grok=g).parse("jọ̀wọ́ má tẹ̀síwájú mọ́").action,
                         STOP)

    def test_no_grok_none(self):
        self.assertEqual(IntentParser(grok=None).parse("unknown").action, NONE)


# ================== link: wire framing + ACK ==================
class TestLink(unittest.TestCase):
    def test_move_frames_line(self):
        s = FakeSerial(ack="OK:F:200:900")
        link = RobotLink(s)
        ok, ack, err = link.move(FORWARD, 200, 900)
        self.assertTrue(ok)
        self.assertEqual(s.sent[-1], "F,200,900")
        self.assertEqual(ack, "OK:F:200:900")

    def test_turn_and_back_codes(self):
        s = FakeSerial(ack="OK")
        link = RobotLink(s)
        link.move(LEFT, 130, 550)
        link.move(RIGHT, 255, 550)
        link.move(BACKWARD, 200, 900)
        self.assertEqual(s.sent, ["L,130,550", "R,255,550", "B,200,900"])

    def test_stop_is_bare_S(self):
        s = FakeSerial(ack="OK:S")
        RobotLink(s).move(STOP, 0, 0)
        self.assertEqual(s.sent[-1], "S")

    def test_ping(self):
        self.assertTrue(RobotLink(FakeSerial(ack="PONG")).ping())
        self.assertFalse(RobotLink(FakeSerial(ack="OK:F")).ping())

    def test_no_ack_is_error(self):
        ok, ack, err = RobotLink(FakeSerial(ack="")).command("F,200,900")
        self.assertFalse(ok)
        self.assertIn("no ack", err)

    def test_transport_exception(self):
        ok, ack, err = RobotLink(RaisingSerial()).command("P")
        self.assertFalse(ok)
        self.assertIn("disconnected", err)


# ================== controller: full path ==================
class TestController(unittest.TestCase):
    def ctrl(self, serial):
        return RobotController(build_parser_no_grok(), RobotLink(serial))

    def test_move_returns_response(self):
        s = FakeSerial(ack="OK:F:200:900")
        res = self.ctrl(s).handle("máa lọ síwájú")
        self.assertTrue(res.moved and res.ok)
        self.assertEqual(res.response, responses.RESPONSES[FORWARD])
        self.assertEqual(s.sent[-1], f"F,{config.DEFAULT_SPEED},{config.DRIVE_MS}")

    def test_turn_uses_turn_ms(self):
        s = FakeSerial(ack="OK:L:200:550")
        self.ctrl(s).handle("yà sí òsì")
        self.assertEqual(s.sent[-1], f"L,{config.DEFAULT_SPEED},{config.TURN_MS}")

    def test_speed_modifier_flows_through(self):
        s = FakeSerial(ack="OK")
        self.ctrl(s).handle("lọ síwájú kíákíá")
        self.assertEqual(s.sent[-1], f"F,{config.FAST_SPEED},{config.DRIVE_MS}")

    def test_stop(self):
        s = FakeSerial(ack="OK:S")
        res = self.ctrl(s).handle("dúró")
        self.assertTrue(res.ok)
        self.assertEqual(res.response, responses.RESPONSES[STOP])
        self.assertEqual(s.sent[-1], "S")

    def test_non_command_is_silent_noop(self):
        s = FakeSerial(ack="OK")
        res = self.ctrl(s).handle("báwo ni o ṣe wà")
        self.assertFalse(res.moved)
        self.assertTrue(res.ok)
        self.assertEqual(res.response, "")
        self.assertEqual(len(s.sent), 0)              # never touched the board

    def test_board_not_open_reports_failure(self):
        ctrl = RobotController(build_parser_no_grok(), None,
                               open_error="no serial port found")
        res = ctrl.handle("síwájú")
        self.assertFalse(res.ok)
        self.assertEqual(res.response, responses.FAILED)
        self.assertEqual(res.error, "no serial port found")

    def test_board_error_speaks_failed(self):
        res = self.ctrl(FakeSerial(ack="ERR:unknown")).handle("síwájú")
        self.assertFalse(res.ok)
        self.assertEqual(res.response, responses.FAILED)

    def test_health(self):
        self.assertTrue(RobotController(build_parser_no_grok(),
                                        RobotLink(FakeSerial(ack="PONG"))).health())
        ctrl = RobotController(build_parser_no_grok(), None, open_error="boom")
        self.assertFalse(ctrl.health())
        self.assertEqual(ctrl.last_error, "boom")


class TestHold(unittest.TestCase):
    def ctrl(self, serial):
        return RobotController(build_parser_no_grok(), RobotLink(serial))

    def test_hold_resends_then_always_stops(self):
        old = config.HOLD_REFRESH
        config.HOLD_REFRESH = 0.0                     # don't sleep during the test
        try:
            s = HoldSerial(raise_after=3)
            status = self.ctrl(s).hold("máa lọ síwájú")
        finally:
            config.HOLD_REFRESH = old
        move = f"F,{config.DEFAULT_SPEED},{config.HOLD_MS}"
        self.assertEqual(s.sent.count(move), 3)       # kept resending the frame
        self.assertTrue(all(x == move for x in s.sent[:-1]))
        self.assertEqual(s.sent[-1], "S")             # Ctrl+C still stops the robot
        self.assertIn("Ctrl+C", status)

    def test_hold_stop_word(self):
        s = FakeSerial(ack="OK:S")
        self.assertEqual(self.ctrl(s).hold("dúró"), "stopped")
        self.assertEqual(s.sent, ["S"])

    def test_hold_non_command_never_drives(self):
        s = FakeSerial(ack="OK")
        self.assertEqual(self.ctrl(s).hold("báwo ni o ṣe wà"),
                         "not a movement command")
        self.assertEqual(len(s.sent), 0)

    def test_hold_board_not_open(self):
        ctrl = RobotController(build_parser_no_grok(), None, open_error="no port")
        self.assertIn("no port", ctrl.hold("síwájú"))

    def test_hold_board_error_stops(self):
        old = config.HOLD_REFRESH
        config.HOLD_REFRESH = 0.0
        try:
            s = FakeSerial(ack="ERR:unknown")         # board rejects the frame
            status = self.ctrl(s).hold("síwájú")
        finally:
            config.HOLD_REFRESH = old
        self.assertIn("board error", status)
        self.assertEqual(s.sent[-1], "S")             # still halts on the way out


def build_parser_no_grok():
    """Parser with rules only (no network) for controller tests."""
    return IntentParser(grok=None)


if __name__ == "__main__":
    unittest.main()
