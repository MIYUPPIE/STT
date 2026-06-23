# test_refiner.py — gate tests. Deterministic, no network, free, <2s.
# Run: python3 -m unittest services.refine.tests.test_refiner -v
import unittest

from services.refine.contract import RefineRequest
from services.refine.prompt import build_messages
from services.refine.refiner import GrokRefiner, NullRefiner, sanitize


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


def refiner(transport, **kw):
    return GrokRefiner(api_key="test-key", transport=transport, **kw)


class TestSanitize(unittest.TestCase):
    def test_clean_passthrough(self):
        self.assertEqual(sanitize("Báwo ni o ṣe wà?", "x"), "Báwo ni o ṣe wà?")

    def test_strips_label(self):
        self.assertEqual(sanitize("Corrected: Báwo ni", "x"), "Báwo ni")
        self.assertEqual(sanitize("Output - Báwo ni", "x"), "Báwo ni")

    def test_strips_quotes(self):
        self.assertEqual(sanitize('"Báwo ni"', "x"), "Báwo ni")
        self.assertEqual(sanitize("“Báwo ni”", "x"), "Báwo ni")

    def test_strips_code_fence(self):
        self.assertEqual(sanitize("```\nBáwo ni\n```", "x"), "Báwo ni")

    def test_takes_first_line_drops_explanation(self):
        self.assertEqual(
            sanitize('Báwo ni\n\nThis means "How are you".', "x"), "Báwo ni"
        )

    def test_empty_falls_back_to_raw(self):
        self.assertEqual(sanitize("", "fallback"), "fallback")
        self.assertEqual(sanitize("```\n```", "fallback"), "fallback")


class TestRefine(unittest.TestCase):
    def test_success(self):
        r = refiner(fake(200, chat_body("Corrected: Báwo ni")))
        res = r.refine(RefineRequest(text="bawo ni"))
        self.assertTrue(res.ok)
        self.assertEqual(res.refined, "Báwo ni")
        self.assertEqual(res.raw, "bawo ni")
        self.assertIsNone(res.error)

    def test_sends_auth_and_model_and_text(self):
        t = fake(200, chat_body("Báwo ni"))
        refiner(t, model="grok-4.3").refine(RefineRequest(text="bawo ni"))
        url, payload, _, headers = t.calls[0]
        self.assertTrue(url.endswith("/chat/completions"))
        self.assertEqual(payload["model"], "grok-4.3")
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        self.assertEqual(payload["messages"][-1]["content"], "bawo ni")

    def test_http_error_falls_back_to_raw(self):
        r = refiner(fake(500, {"error": {"message": "server down"}}))
        res = r.refine(RefineRequest(text="bawo ni"))
        self.assertFalse(res.ok)
        self.assertEqual(res.refined, "bawo ni")     # graceful: echo input
        self.assertEqual(res.error, "server down")

    def test_missing_key(self):
        r = GrokRefiner(api_key="", transport=fake(200, chat_body("x")))
        res = r.refine(RefineRequest(text="bawo ni"))
        self.assertFalse(res.ok)
        self.assertEqual(res.refined, "bawo ni")
        self.assertIn("XAI_API_KEY", res.error)

    def test_empty_input_skips_call(self):
        t = fake(200, chat_body("nope"))
        res = refiner(t).refine(RefineRequest(text="   "))
        self.assertEqual(res.refined, "")
        self.assertTrue(res.ok)
        self.assertEqual(len(t.calls), 0)


class TestHealth(unittest.TestCase):
    def test_healthy(self):
        r = refiner(fake(200, chat_body("hi")))
        self.assertTrue(r.health())
        self.assertIsNone(r.last_error)

    def test_unhealthy_records_reason(self):
        r = refiner(fake(401, {"error": {"message": "invalid key"}}))
        self.assertFalse(r.health())
        self.assertEqual(r.last_error, "invalid key")


class TestNullRefiner(unittest.TestCase):
    def test_echoes_input(self):
        res = NullRefiner().refine(RefineRequest(text="bawo ni"))
        self.assertEqual(res.refined, "bawo ni")
        self.assertFalse(res.ok)

    def test_unhealthy(self):
        self.assertFalse(NullRefiner().health())


class TestPrompt(unittest.TestCase):
    def test_messages_have_system_and_raw(self):
        msgs = build_messages("bawo ni o se wa")
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[-1], {"role": "user", "content": "bawo ni o se wa"})


if __name__ == "__main__":
    unittest.main()
