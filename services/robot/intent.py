# intent.py — Yoruba caption -> robot Intent. Deterministic rules first (vocab.py,
# offline + free); Grok fallback only when the rules don't recognize the phrasing
# (negations like "má ṣe dúró", mixed wording). Grok is constrained to a tiny JSON
# intent so the latent step stays deterministic and testable. HTTP transport is
# injectable so gate tests run with zero network.
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from . import config
from .contract import Intent, ACTIONS, NONE
from .vocab import rule_intent


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
    "You turn a spoken Yoruba command into a robot movement instruction. "
    "Reply with ONLY a JSON object, no prose, no code fence.\n"
    'Schema: {"action": <one of forward,backward,left,right,stop,none>, '
    '"speed": <"fast"|"slow"|null>}\n'
    "Rules:\n"
    "- forward=síwájú, backward=sẹ́yìn/padà, left=òsì, right=ọ̀tún, stop=dúró.\n"
    "- speed: \"fast\" for kíákíá/yára, \"slow\" for díẹ̀díẹ̀/jẹ́ẹ́jẹ́, else null.\n"
    "- none: the sentence is not a movement command at all.\n"
    "- A negation that cancels motion (\"má ṣe lọ\", \"dúró\") is \"stop\"."
)

EXAMPLES = [
    ("máa lọ síwájú", '{"action": "forward", "speed": null}'),
    ("padà sẹ́yìn díẹ̀díẹ̀", '{"action": "backward", "speed": "slow"}'),
    ("yà sí òsì", '{"action": "left", "speed": null}'),
    ("yíjú sí ọ̀tún kíákíá", '{"action": "right", "speed": "fast"}'),
    ("dúró", '{"action": "stop", "speed": null}'),
    ("báwo ni o ṣe wà", '{"action": "none", "speed": null}'),
]


def _build_messages(raw: str):
    msgs = [{"role": "system", "content": SYSTEM}]
    for u, a in EXAMPLES:
        msgs.append({"role": "user", "content": u})
        msgs.append({"role": "assistant", "content": a})
    msgs.append({"role": "user", "content": raw})
    return msgs


def _speed_from_label(label) -> int | None:
    if label == "fast":
        return config.FAST_SPEED
    if label == "slow":
        return config.SLOW_SPEED
    return None


def parse_json_intent(content: str, text: str) -> Intent:
    """Pull the JSON object out of a model reply and validate it into an Intent.
    Anything malformed degrades to NONE (never crashes the pipeline)."""
    m = re.search(r"\{.*\}", content or "", re.S)
    if not m:
        return Intent(NONE, None, "grok", text)
    try:
        obj = json.loads(m.group(0))
    except ValueError:
        return Intent(NONE, None, "grok", text)
    action = str(obj.get("action", NONE)).lower().strip()
    if action not in ACTIONS:
        action = NONE
    speed = None if action == NONE else _speed_from_label(obj.get("speed"))
    return Intent(action, speed, "grok", text)


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
            return Intent(NONE, None, "error", text,
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
        return Intent(NONE, None, "error", text, error=err or f"http {status}")


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
    grok = None
    if config.GROK_FALLBACK and config.API_KEY:
        grok = GrokIntent()
    return IntentParser(grok=grok)
