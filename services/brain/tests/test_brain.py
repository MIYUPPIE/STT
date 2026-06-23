# test_brain.py — gate tests. Deterministic, no network, free, <2s.
# Run: python3 -m unittest services.brain.tests.test_brain -v
import unittest

from services.brain.contract import ChatRequest
from services.brain.prompt import build_messages
from services.brain.brain import GrokBrain, NullBrain, sanitize_reply


def fake(status, body):
    """Transport returning a fixed (status, body); records calls."""
    calls = []

    def transport(url, payload, timeout, headers=None):
        calls.append((url, payload, timeout, headers))
        return status, body

    transport.calls = calls
    return transport


def chat_body(content):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def brain(transport, **kw):
    # api_key forced so tests never depend on the environment
    return GrokBrain(api_key="test-key", transport=transport, **kw)


class TestSanitizeReply(unittest.TestCase):
    def test_plain_passthrough(self):
        self.assertEqual(sanitize_reply("Mo wà dáadáa."), "Mo wà dáadáa.")

    def test_strips_label_and_quotes(self):
        self.assertEqual(sanitize_reply('Assistant: "Báwo ni"'), "Báwo ni")

    def test_strips_markdown_and_joins_lines(self):
        self.assertEqual(
            sanitize_reply("- Báwo ni\n- Mo wà dáadáa"), "Báwo ni Mo wà dáadáa")

    def test_strips_emoji(self):
        self.assertEqual(sanitize_reply("Ẹ ṣé 😊👍"), "Ẹ ṣé")

    def test_empty_uses_fallback(self):
        self.assertEqual(sanitize_reply("", "fb"), "fb")
        self.assertEqual(sanitize_reply("```\n```", "fb"), "fb")


class TestRespond(unittest.TestCase):
    def test_success_and_memory(self):
        b = brain(fake(200, chat_body("Mo wà dáadáa, ẹ ṣé.")))
        res = b.respond(ChatRequest(text="Báwo ni o ṣe wà?"))
        self.assertTrue(res.ok)
        self.assertEqual(res.reply, "Mo wà dáadáa, ẹ ṣé.")
        self.assertEqual(len(b.history), 2)              # remembered the exchange

    def test_sends_auth_and_model(self):
        t = fake(200, chat_body("Ẹ ṣé."))
        b = brain(t, model="grok-4.3")
        b.respond(ChatRequest(text="Ẹ ku àárọ̀"))
        url, payload, _, headers = t.calls[0]
        self.assertTrue(url.endswith("/chat/completions"))
        self.assertEqual(payload["model"], "grok-4.3")
        self.assertFalse(payload["stream"])
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][-1]["content"], "Ẹ ku àárọ̀")

    def test_http_error_is_graceful(self):
        b = brain(fake(401, {"error": {"message": "invalid key"}}))
        res = b.respond(ChatRequest(text="hi"))
        self.assertFalse(res.ok)
        self.assertEqual(res.reply, "")
        self.assertEqual(res.error, "invalid key")
        self.assertEqual(len(b.history), 0)              # failures aren't remembered

    def test_missing_key(self):
        b = GrokBrain(api_key="", transport=fake(200, chat_body("x")))
        res = b.respond(ChatRequest(text="hi"))
        self.assertFalse(res.ok)
        self.assertIn("XAI_API_KEY", res.error)

    def test_empty_input_skips_call(self):
        t = fake(200, chat_body("nope"))
        res = brain(t).respond(ChatRequest(text="   "))
        self.assertEqual(res.reply, "")
        self.assertTrue(res.ok)
        self.assertEqual(len(t.calls), 0)

    def test_history_trim(self):
        b = brain(fake(200, chat_body("ó dáa")), history_turns=2)
        for _ in range(5):
            b.respond(ChatRequest(text="sọ̀rọ̀"))
        self.assertLessEqual(len(b.history), 2 * 2)      # 2 turns * 2 msgs

    def test_reset(self):
        b = brain(fake(200, chat_body("ó dáa")))
        b.respond(ChatRequest(text="hi"))
        b.reset()
        self.assertEqual(b.history, [])


class TestHealth(unittest.TestCase):
    def test_healthy(self):
        b = brain(fake(200, chat_body("hi")))
        self.assertTrue(b.health())
        self.assertIsNone(b.last_error)

    def test_unhealthy_records_reason(self):
        b = brain(fake(500, {"error": {"message": "server down"}}))
        self.assertFalse(b.health())
        self.assertEqual(b.last_error, "server down")


class TestNullBrain(unittest.TestCase):
    def test_disabled(self):
        res = NullBrain().respond(ChatRequest(text="hi"))
        self.assertFalse(res.ok)
        self.assertEqual(res.reply, "")
        self.assertFalse(NullBrain().health())


class TestPrompt(unittest.TestCase):
    def test_message_order(self):
        msgs = build_messages("SYS", [{"role": "user", "content": "a"}], "b")
        self.assertEqual(msgs[0], {"role": "system", "content": "SYS"})
        self.assertEqual(msgs[-1], {"role": "user", "content": "b"})


if __name__ == "__main__":
    unittest.main()
