# refiner.py — turns a raw Yoruba caption into corrected Yoruba via Grok (xAI).
#
# Design:
#   - xAI /v1/chat/completions (OpenAI-compatible); few-shot sent as real turns.
#   - HTTP via stdlib urllib (no extra deps); transport injectable so gate tests
#     run with zero network.
#   - sanitize() defends against preambles, quotes, code fences, and English
#     explanations the model may add.
#   - health() does a real 1-token chat so it detects a bad key / network.
#   - NullRefiner / failed calls return refined == raw, so live captioning never
#     breaks when the API is unreachable.
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request

from . import config
from .contract import RefineRequest, RefineResult
from .prompt import build_messages


# ---------------- transport (injectable) ----------------
def urllib_transport(url: str, payload: dict, timeout: float, headers=None):
    """Returns (status_code, body_dict). status 0 means the request never
    reached the server (connection refused / DNS / timeout)."""
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
    r"^(corrected|correction|output|answer|sentence|result|yor[uù]b[aá])\s*[:\-]\s*",
    re.I,
)
_QUOTES = "\"'“”‘’«»"


def sanitize(text: str, raw: str) -> str:
    """Reduce a model reply to just the corrected sentence. Falls back to `raw`
    if nothing usable survives."""
    t = (text or "").strip()
    if not t:
        return raw
    # drop code-fence lines, keep the first real line (utterances are one line)
    lines = [ln.strip() for ln in t.splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("```")]
    if not lines:
        return raw
    t = lines[0]
    t = _LABEL.sub("", t).strip()
    t = t.strip(_QUOTES).strip()
    t = re.sub(r"\s+", " ", t).strip()
    return t or raw


# ---------------- refiners ----------------
class GrokRefiner:
    enabled = True

    def __init__(self, model=None, api_url=None, api_key=None, timeout=None,
                 max_tokens=None, health_timeout=None, temperature=None,
                 transport=urllib_transport):
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
        self.transport = transport
        self.last_error = None

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

    def refine(self, req: RefineRequest) -> RefineResult:
        raw = (req.text or "").strip()
        if not raw:
            return RefineResult(raw=req.text, refined="", ok=True,
                                error=None, latency_ms=0.0)
        t0 = time.perf_counter()
        ok, text, err = self._chat(build_messages(raw), self.max_tokens,
                                   self.timeout)
        dt = (time.perf_counter() - t0) * 1000
        if not ok:
            self.last_error = err
            return RefineResult(raw=raw, refined=raw, ok=False,
                                error=err, latency_ms=dt)
        return RefineResult(raw=raw, refined=sanitize(text, raw), ok=True,
                            error=None, latency_ms=dt)


class NullRefiner:
    """Used when refinement is disabled. Echoes input, never calls out."""
    enabled = False
    last_error = "disabled"

    def health(self) -> bool:
        return False

    def refine(self, req: RefineRequest) -> RefineResult:
        return RefineResult(raw=req.text, refined=req.text, ok=False,
                            error="disabled", latency_ms=0.0)


def build_refiner():
    """Factory used by callers. Returns a live refiner if enabled, else Null."""
    return GrokRefiner() if config.ENABLED else NullRefiner()


# ---------------- CLI ----------------
def _main(argv):
    if "--health" in argv:
        r = GrokRefiner()
        ok = r.health()
        print(f"model={r.model} url={r.api_url} healthy={ok}")
        if not ok:
            print(f"reason: {r.last_error}", file=sys.stderr)
        return 0 if ok else 1
    text = " ".join(a for a in argv if not a.startswith("--")).strip()
    if not text:
        print('usage: python -m services.refine.refiner "raw yoruba text"',
              file=sys.stderr)
        print("       python -m services.refine.refiner --health",
              file=sys.stderr)
        return 2
    res = GrokRefiner().refine(RefineRequest(text=text))
    print(res.refined)
    tag = "ok" if res.ok else f"FAILED: {res.error}"
    print(f"[{tag}  {res.latency_ms:.0f} ms]", file=sys.stderr)
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
