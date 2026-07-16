# test_servo.py — gate tests. Deterministic, no network, free, <2s.
# Run: python3 -m unittest services.servo.tests.test_servo -v
import unittest

from services.servo import config
from services.servo.contract import (Intent, LEFT, RIGHT, CENTER, OPEN, CLOSE,
                                      ANGLE, STOP, NONE)
from services.servo.vocab import normalize, rule_intent
from services.servo.intent import (parse_json_intent, GrokIntent, IntentParser)
from services.servo.client import ServoClient, clamp


# ---------------- transports ----------------
def fake_chat(status, content):
    """xAI transport returning one fixed chat completion."""
    def transport(url, payload, timeout, headers=None):
        body = {"choices": [{"message": {"content": content}}]}
        return status, body
    return transport


def fake_board(status="200", text="OK: angle=90"):
    """Board GET transport; records every URL it was asked to fetch."""
    calls = []

    def transport(url, timeout):
        calls.append(url)
        return (200 if status == "200" else int(status)), text
    transport.calls = calls
    return transport


# ================== normalization ==================
class TestNormalize(unittest.TestCase):
    def test_strips_tone_and_dots(self):
        self.assertEqual(normalize("Yíjú sí ọ̀tún!"), "yiju si otun")
        self.assertEqual(normalize("ẹ̀ṣẹ̀"), "ese")

    def test_collapses_and_lowercases(self):
        self.assertEqual(normalize("  PADÀ   sí   ÀÁRÍN "), "pada si aarin")

    def test_empty(self):
        self.assertEqual(normalize(""), "")
        self.assertEqual(normalize("!!! ???"), "")


# ================== rule matcher ==================
class TestRuleIntent(unittest.TestCase):
    def act(self, text):
        return rule_intent(text).action

    def test_right(self):
        self.assertEqual(self.act("yà sí ọ̀tún"), RIGHT)
        self.assertEqual(self.act("otun"), RIGHT)

    def test_left(self):
        self.assertEqual(self.act("yíjú sí òsì"), LEFT)

    def test_center(self):
        self.assertEqual(self.act("padà sí àárín"), CENTER)
        self.assertEqual(self.act("arin"), CENTER)

    def test_stop(self):
        self.assertEqual(self.act("dúró"), STOP)

    def test_open_close_not_in_rule_layer(self):
        # open/close are ambiguous in tone-less Yoruba -> Grok handles them, so the
        # offline rules must NOT claim them (would false-trigger on normal speech).
        self.assertEqual(self.act("ṣí sílẹ̀"), NONE)
        self.assertEqual(self.act("ó ń sáré títí"), NONE)   # 'continuously', not close

    def test_angle_digits(self):
        i = rule_intent("lọ sí 45 digiri")
        self.assertEqual(i.action, ANGLE)
        self.assertEqual(i.angle, 45)

    def test_angle_yoruba_number(self):
        i = rule_intent("lọ sí ọgọ́ta digiri")   # ogota = 60
        self.assertEqual(i.action, ANGLE)
        self.assertEqual(i.angle, 60)

    def test_angle_precedence_over_direction(self):
        # explicit degree wins over the bare direction word
        i = rule_intent("yà sí ọ̀tún ní ọgbọ̀n digiri")   # ogbon = 30
        self.assertEqual(i.action, ANGLE)
        self.assertEqual(i.angle, 30)

    def test_move_verb_with_bare_number(self):
        i = rule_intent("yí sí 120")
        self.assertEqual(i.action, ANGLE)
        self.assertEqual(i.angle, 120)

    def test_non_command_is_none(self):
        self.assertEqual(self.act("báwo ni o ṣe wà"), NONE)
        self.assertEqual(self.act("kò sí ìṣòro"), NONE)   # 'kosi' must not hit 'osi'
        self.assertEqual(self.act(""), NONE)


# ================== Grok JSON parsing ==================
class TestParseJsonIntent(unittest.TestCase):
    def test_plain(self):
        i = parse_json_intent('{"action":"left","angle":null}', "x")
        self.assertEqual(i.action, LEFT)
        self.assertIsNone(i.angle)

    def test_angle(self):
        i = parse_json_intent('{"action":"angle","angle":75}', "x")
        self.assertEqual(i.action, ANGLE)
        self.assertEqual(i.angle, 75)

    def test_embedded_in_prose_and_fence(self):
        i = parse_json_intent('```json\n{"action":"right","angle":null}\n```', "x")
        self.assertEqual(i.action, RIGHT)

    def test_bad_action_degrades_to_none(self):
        i = parse_json_intent('{"action":"twerk","angle":null}', "x")
        self.assertEqual(i.action, NONE)

    def test_angle_without_number_degrades(self):
        i = parse_json_intent('{"action":"angle","angle":"lots"}', "x")
        self.assertEqual(i.action, NONE)

    def test_garbage(self):
        self.assertEqual(parse_json_intent("not json at all", "x").action, NONE)

    def test_non_angle_ignores_stray_number(self):
        i = parse_json_intent('{"action":"center","angle":42}', "x")
        self.assertEqual(i.action, CENTER)
        self.assertIsNone(i.angle)


class TestGrokIntent(unittest.TestCase):
    def test_success(self):
        g = GrokIntent(api_key="k",
                       transport=fake_chat(200, '{"action":"left","angle":null}'))
        self.assertEqual(g.parse("yà sí òsì").action, LEFT)

    def test_missing_key(self):
        g = GrokIntent(api_key="", transport=fake_chat(200, "{}"))
        i = g.parse("x")
        self.assertEqual(i.action, NONE)
        self.assertIn("XAI_API_KEY", i.error)

    def test_http_error(self):
        g = GrokIntent(api_key="k",
                       transport=fake_chat(500, ""))
        i = g.parse("x")
        self.assertEqual(i.action, NONE)
        self.assertEqual(i.source, "error")


class TestIntentParser(unittest.TestCase):
    def test_rule_wins_no_grok_call(self):
        called = {"n": 0}

        class Spy:
            def parse(self, t):
                called["n"] += 1
                return Intent(NONE, None, "grok", t)

        p = IntentParser(grok=Spy())
        self.assertEqual(p.parse("yà sí ọ̀tún").action, RIGHT)
        self.assertEqual(called["n"], 0)          # rule matched -> Grok untouched

    def test_falls_back_to_grok(self):
        g = GrokIntent(api_key="k",
                       transport=fake_chat(200, '{"action":"open","angle":null}'))
        p = IntentParser(grok=g)
        # phrasing the rules don't cover
        self.assertEqual(p.parse("jọ̀wọ́ na ilẹ̀kùn dé ìwọ̀n gíga").action, OPEN)

    def test_no_grok_returns_none(self):
        p = IntentParser(grok=None)
        self.assertEqual(p.parse("something unrecognized").action, NONE)


# ================== client: resolve + clamp + move ==================
class TestResolve(unittest.TestCase):
    def client(self, start=90):
        return ServoClient(host="http://board", start=start, step=30,
                           transport=fake_board())

    def test_relative_steps(self):
        c = self.client(90)
        self.assertEqual(c.resolve(Intent(RIGHT, None, "rule", "x")), 120)
        self.assertEqual(c.resolve(Intent(LEFT, None, "rule", "x")), 60)

    def test_absolute(self):
        c = self.client(90)
        self.assertEqual(c.resolve(Intent(CENTER, None, "rule", "x")),
                         config.CENTER_ANGLE)
        self.assertEqual(c.resolve(Intent(OPEN, None, "rule", "x")),
                         config.OPEN_ANGLE)
        self.assertEqual(c.resolve(Intent(CLOSE, None, "rule", "x")),
                         config.CLOSE_ANGLE)
        self.assertEqual(c.resolve(Intent(ANGLE, 75, "rule", "x")), 75)

    def test_noop_actions(self):
        c = self.client(90)
        self.assertIsNone(c.resolve(Intent(STOP, None, "rule", "x")))
        self.assertIsNone(c.resolve(Intent(NONE, None, "none", "x")))
        self.assertIsNone(c.resolve(Intent(ANGLE, None, "rule", "x")))

    def test_clamp_bounds(self):
        self.assertEqual(clamp(-30), config.MIN_ANGLE)
        self.assertEqual(clamp(999), config.MAX_ANGLE)
        # stepping left past 0 clamps
        c = self.client(10)
        self.assertEqual(c.resolve(Intent(LEFT, None, "rule", "x")),
                         config.MIN_ANGLE)


class TestMove(unittest.TestCase):
    def test_move_sends_angle_and_commits(self):
        t = fake_board()
        c = ServoClient(host="http://board", start=90, step=30, transport=t)
        res = c.move(Intent(RIGHT, None, "rule", "x"))
        self.assertTrue(res.moved and res.ok)
        self.assertEqual(res.angle, 120)
        self.assertEqual(c.angle, 120)               # state advanced
        self.assertEqual(t.calls[0], "http://board/servo?angle=120")

    def test_noop_move_holds_and_skips_http(self):
        t = fake_board()
        c = ServoClient(host="http://board", start=90, transport=t)
        res = c.move(Intent(NONE, None, "none", "x"))
        self.assertFalse(res.moved)
        self.assertTrue(res.ok)
        self.assertEqual(res.angle, 90)
        self.assertEqual(len(t.calls), 0)            # never hit the board

    def test_failed_move_does_not_commit(self):
        t = fake_board(status="0", text="Connection refused")
        c = ServoClient(host="http://board", start=90, step=30, transport=t)
        res = c.move(Intent(RIGHT, None, "rule", "x"))
        self.assertFalse(res.ok)
        self.assertFalse(res.moved)
        self.assertEqual(c.angle, 90)                # unchanged on failure
        self.assertIn("Connection refused", res.error)

    def test_sequential_relative_moves(self):
        c = ServoClient(host="http://board", start=90, step=30, transport=fake_board())
        c.move(Intent(RIGHT, None, "rule", "x"))     # 120
        c.move(Intent(RIGHT, None, "rule", "x"))     # 150
        self.assertEqual(c.angle, 150)


class TestHealth(unittest.TestCase):
    def test_healthy(self):
        c = ServoClient(host="http://board", transport=fake_board())
        self.assertTrue(c.health())

    def test_unreachable(self):
        c = ServoClient(host="http://board",
                        transport=fake_board(status="0", text="refused"))
        self.assertFalse(c.health())
        self.assertEqual(c.last_error, "refused")


if __name__ == "__main__":
    unittest.main()
