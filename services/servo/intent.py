# intent.py — Yoruba caption -> servo Intent. Deterministic rules first (vocab.py,
# offline + free); Grok fallback only when the rules don't recognize the phrasing.
# Grok is constrained to emit a tiny JSON intent, so the latent step still yields a
# deterministic, testable shape. HTTP transport is injectable so gate tests run
# with zero network.
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from . import config
from .contract import Intent, ACTIONS, NONE, ANGLE
from .vocab import rule_intent


# ---------------- transport (injectable, shared shape with other services) ----
def urllib_transport(url, payload, timeout, headers=None):
    data = json.dumps(payload).encode()
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            body = json.loads(raw)
        except ValueError:
            body = {"error": raw}
        return e.code, body
    except Exception as e:
        return 0, {"error": str(e)}


SYSTEM = (
    "You translate a spoken Yoruba command into a servo-motor instruction. "
    "Reply with ONLY a JSON object, no prose, no code fence.\n"
    'Schema: {"action": <one of left,right,center,open,close,angle,stop,none>, '
    '"angle": <integer 0-180 or null>}\n'
    "Rules:\n"
    "- left/right: turn that way. center: middle. open/close: extremes. stop: hold.\n"
    "- angle: a specific degree was named; put it in \"angle\".\n"
    "- none: the sentence is not a servo command at all.\n"
    "- \"angle\" is null unless action is \"angle\"."
)

EXAMPLES = [
    ("yà sí ọ̀tún", '{"action": "right", "angle": null}'),
    ("yíjú sí òsì", '{"action": "left", "angle": null}'),
    ("padà sí àárín", '{"action": "center", "angle": null}'),
    ("lọ sí ọgọ́ta digiri", '{"action": "angle", "angle": 60}'),
    ("dúró", '{"action": "stop", "angle": null}'),
    ("báwo ni o ṣe wà", '{"action": "none", "angle": null}'),
]


def _build_messages(raw: str):
    msgs = [{"role": "system", "content": SYSTEM}]
    for u, a in EXAMPLES:
        msgs.append({"role": "user", "content": u})
        msgs.append({"role": "assistant", "content": a})
    msgs.append({"role": "user", "content": raw})
    return msgs


def parse_json_intent(content: str, text: str) -> Intent:
    """Pull the JSON object out of a model reply and validate it into an Intent.
    Anything malformed degrades to NONE (never crashes the pipeline)."""
    m = re.search(r"\{.*\}", content or "", re.S)
    if not m:
        return Intent(action=NONE, angle=None, source="grok", text=text)
    try:
        obj = json.loads(m.group(0))
    except ValueError:
        return Intent(action=NONE, angle=None, source="grok", text=text)
    action = str(obj.get("action", NONE)).lower().strip()
    if action not in ACTIONS:
        action = NONE
    angle = obj.get("angle")
    if action == ANGLE:
        try:
            angle = int(angle)
        except (TypeError, ValueError):
            return Intent(action=NONE, angle=None, source="grok", text=text)
    else:
        angle = None
    return Intent(action=action, angle=angle, source="grok", text=text)


class GrokIntent:
    """Grok fallback parser. Only called when the rule matcher returns NONE."""

    def __init__(self, api_key=None, api_url=None, model=None, timeout=None,
                 max_tokens=None, temperature=None, transport=urllib_transport):
        self.api_key = config.API_KEY if api_key is None else api_key
        self.api_url = api_url or config.API_URL
        self.model = model or config.MODEL
        self.timeout = config.GROK_TIMEOUT if timeout is None else timeout
        self.max_tokens = config.MAX_TOKENS if max_tokens is None else max_tokens
        self.temperature = config.TEMPERATURE if temperature is None else temperature
        self.transport = transport

    def parse(self, text: str) -> Intent:
        if not self.api_key:
            return Intent(action=NONE, angle=None, source="error", text=text,
                          error="XAI_API_KEY not set (add it to .env)")
        payload = {
            "model": self.model,
            "messages": _build_messages(text),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        status, body = self.transport(self.api_url, payload, self.timeout, headers)
        choices = body.get("choices") if isinstance(body, dict) else None
        if status == 200 and choices:
            content = choices[0].get("message", {}).get("content") or ""
            return parse_json_intent(content, text)
        err = None
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                err = err.get("message")
        return Intent(action=NONE, angle=None, source="error", text=text,
                      error=err or f"http {status}")


class IntentParser:
    """Rule matcher first; Grok only for what the rules miss (if enabled)."""

    def __init__(self, grok: GrokIntent | None = None):
        self.grok = grok

    def parse(self, text: str) -> Intent:
        intent = rule_intent(text)
        if intent.action != NONE:
            return intent
        if self.grok is not None:
            return self.grok.parse(text)
        return intent


def build_parser() -> IntentParser:
    """Factory: rule matcher, plus a Grok fallback when configured + keyed."""
    grok = None
    if config.GROK_FALLBACK and config.API_KEY:
        grok = GrokIntent()
    return IntentParser(grok=grok)
