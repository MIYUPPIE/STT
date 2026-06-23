# config.py — refine service settings, all env-overridable.
#
# The refiner runs on xAI's Grok API (cloud), keyed by XAI_API_KEY in the .env.
import os

from services.env_loader import load_env

load_env()

# xAI / Grok chat-completions endpoint (OpenAI-compatible).
API_URL = os.environ.get("XAI_API_URL", "https://api.x.ai/v1/chat/completions")
API_KEY = os.environ.get("XAI_API_KEY", "")

# Fast Grok text model — low latency for the live loop, same correction quality
# as grok-4.3 on the eval (10/10, ~0.9s vs ~3.4s). Override with XAI_MODEL.
MODEL = os.environ.get(
    "REFINE_MODEL", os.environ.get("XAI_MODEL", "grok-4.20-0309-non-reasoning"))

# Master switch. REFINE_ENABLED=0 -> live_caption runs plain captioning.
ENABLED = os.environ.get("REFINE_ENABLED", "1") != "0"

# Per-call request timeout (seconds).
TIMEOUT = float(os.environ.get("REFINE_TIMEOUT", "30"))

# Startup health probe timeout (a 1-token chat).
HEALTH_TIMEOUT = float(os.environ.get("REFINE_HEALTH_TIMEOUT", "30"))

# Cap output length; a single Yoruba utterance never needs more.
MAX_TOKENS = int(os.environ.get("REFINE_MAX_TOKENS", "96"))

# Correction is precise work — keep it deterministic.
TEMPERATURE = float(os.environ.get("REFINE_TEMPERATURE", "0"))
