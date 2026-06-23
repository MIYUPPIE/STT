# brain.py — turns a Yoruba utterance into a spoken Yoruba reply via Grok (xAI).
#
# This is the "brain" of the live voice loop: speech -> Whisper -> brain -> TTS.
# Where services/refine *corrects* a caption, this *responds* to it.
#
# Design:
#   - xAI /v1/chat/completions (OpenAI-compatible role messages) so multi-turn
#     memory is native.
#   - The brain object is stateful: it keeps the recent conversation so replies
#     stay coherent across turns. reset() starts a fresh conversation.
#   - HTTP via stdlib urllib (no extra deps); transport is injectable so gate
#     tests run with zero network.
#   - sanitize_reply() defends against markdown, emoji, labels, quotes, and code
#     fences — none of which the TTS can speak.
#   - health() does a real 1-token chat so it detects a bad key / network, not
#     just config presence.
#   - NullBrain / failed calls return an empty reply with ok=False, so the caller
#     (live captioning) keeps captioning when the API is unreachable.
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request

from . import config
from .contract import ChatRequest, ChatResult
from .prompt import SYSTEM, build_messages


# ---------------- transport (injectable) ----------------
def urllib_transport(url: str, payload: dict, timeout: float, headers=None):
    """Returns (status_code, body_dict). status 0 means the request never
    reached the server (connection refused / DNS / timeout). Kept self-contained
    so this service never imports another service's internals."""
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
    except Exception as e:  # URLError, timeout, etc.
        return 0, {"error": str(e)}


# ---------------- output cleanup ----------------
_LABEL = re.compile(
    r"^(assistant|reply|answer|response|yor[uù]b[aá]|olùrànlọ́wọ́)\s*[:\-]\s*",
    re.I,
)
_QUOTES = "\"'“”‘’«»"
# Emoji / pictographs / dingbats / flags — TTS can't speak them.
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002190-\U000021FF]"
)


def sanitize_reply(text: str, fallback: str = "") -> str:
    """Reduce a model reply to clean, speakable Yoruba. A reply may be one or two
    sentences, so lines are joined (not truncated). Falls back to `fallback`
    (default empty) if nothing usable survives."""
    t = (text or "").strip()
    if not t:
        return fallback
    # drop code-fence lines and markdown bullet/heading markers, join the rest
    lines = []
    for ln in t.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("```"):
            continue
        ln = re.sub(r"^[#>\-\*•]+\s*", "", ln)   # bullets / headings
        lines.append(ln)
    t = " ".join(lines)
    t = _LABEL.sub("", t).strip()
    t = _EMOJI.sub("", t)
    t = t.strip(_QUOTES).strip()
    t = re.sub(r"\s+", " ", t).strip()
    return t or fallback


# ---------------- brains ----------------
class GrokBrain:
    enabled = True

    def __init__(self, model=None, api_url=None, api_key=None, timeout=None,
                 max_tokens=None, health_timeout=None, temperature=None,
                 history_turns=None, system=SYSTEM, transport=urllib_transport):
        self.model = model or config.MODEL
        self.api_url = api_url or config.API_URL
        self.api_key = config.API_KEY if api_key is None else api_key
        self.timeout = config.TIMEOUT if timeout is None else timeout
        self.max_tokens = config.MAX_TOKENS if max_tokens is None else max_tokens
        self.health_timeout = (
            config.HEALTH_TIMEOUT if health_timeout is None else health_timeout
        )
        self.temperature = (
            config.TEMPERATURE if temperature is None else temperature
        )
        self.history_turns = (
            config.HISTORY_TURNS if history_turns is None else history_turns
        )
        self.system = system
        self.transport = transport
        self.history: list[dict] = []     # [{"role","content"}, ...] recent turns
        self.last_error = None

    def reset(self):
        """Forget the conversation and start fresh."""
        self.history = []

    def _trim(self):
        """Keep only the most recent `history_turns` exchanges (2 msgs each)."""
        keep = self.history_turns * 2
        if keep >= 0 and len(self.history) > keep:
            self.history = self.history[-keep:]

    def _chat(self, messages, max_tokens, timeout):
        if not self.api_key:
            return False, "", "XAI_API_KEY not set (add it to .env)"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        status, body = self.transport(self.api_url, payload, timeout, headers)
        choices = body.get("choices") if isinstance(body, dict) else None
        if status == 200 and choices:
            return True, (choices[0].get("message", {}).get("content") or ""), None
        err = None
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                err = err.get("message")
        return False, "", err or f"http {status}"

    def health(self) -> bool:
        """True only if a real chat call succeeds (valid key + reachable API)."""
        ok, _, err = self._chat(
            [{"role": "user", "content": "hi"}], 1, self.health_timeout
        )
        self.last_error = None if ok else err
        return ok

    def respond(self, req: ChatRequest) -> ChatResult:
        user = (req.text or "").strip()
        if not user:
            return ChatResult(user=req.text, reply="", ok=True,
                              error=None, latency_ms=0.0)
        messages = build_messages(self.system, self.history, user)
        t0 = time.perf_counter()
        ok, text, err = self._chat(messages, self.max_tokens, self.timeout)
        dt = (time.perf_counter() - t0) * 1000
        if not ok:
            self.last_error = err
            return ChatResult(user=user, reply="", ok=False,
                              error=err, latency_ms=dt)
        reply = sanitize_reply(text, "")
        if reply:                          # only remember turns that produced a reply
            self.history.append({"role": "user", "content": user})
            self.history.append({"role": "assistant", "content": reply})
            self._trim()
        return ChatResult(user=user, reply=reply, ok=bool(reply),
                          error=None if reply else "empty reply", latency_ms=dt)


class NullBrain:
    """Used when the brain is disabled. Never calls out, never replies."""
    enabled = False
    last_error = "disabled"

    def reset(self):
        pass

    def health(self) -> bool:
        return False

    def respond(self, req: ChatRequest) -> ChatResult:
        return ChatResult(user=req.text, reply="", ok=False,
                          error="disabled", latency_ms=0.0)


def build_brain():
    """Factory used by callers. Returns a live brain if enabled, else Null."""
    return GrokBrain() if config.ENABLED else NullBrain()


# ---------------- CLI ----------------
def _main(argv):
    if "--health" in argv:
        b = GrokBrain()
        ok = b.health()
        print(f"model={b.model} url={b.api_url} healthy={ok}")
        if not ok:
            print(f"reason: {b.last_error}", file=sys.stderr)
        return 0 if ok else 1

    if "--repl" in argv:                   # multi-turn manual test
        b = GrokBrain()
        print(f"Yoruba brain REPL ({b.model}). Empty line or Ctrl-D to quit.")
        if not b.health():
            print(f"unavailable: {b.last_error}", file=sys.stderr)
            return 1
        try:
            while True:
                user = input("you> ").strip()
                if not user:
                    break
                res = b.respond(ChatRequest(text=user))
                if res.ok:
                    print(f"  ↳ {res.reply}   [{res.latency_ms:.0f} ms]")
                else:
                    print(f"  FAILED: {res.error}", file=sys.stderr)
        except (EOFError, KeyboardInterrupt):
            print()
        return 0

    text = " ".join(a for a in argv if not a.startswith("--")).strip()
    if not text:
        print('usage: python -m services.brain.brain "yoruba utterance"',
              file=sys.stderr)
        print("       python -m services.brain.brain --repl", file=sys.stderr)
        print("       python -m services.brain.brain --health", file=sys.stderr)
        return 2
    res = GrokBrain().respond(ChatRequest(text=text))
    print(res.reply)
    tag = "ok" if res.ok else f"FAILED: {res.error}"
    print(f"[{tag}  {res.latency_ms:.0f} ms]", file=sys.stderr)
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
