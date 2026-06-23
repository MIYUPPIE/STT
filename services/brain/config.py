# config.py — brain service settings, all env-overridable.
#
# The brain runs on xAI's Grok API (cloud), keyed by XAI_API_KEY in the repo .env.
import os

from services.env_loader import load_env

load_env()

# xAI / Grok chat-completions endpoint (OpenAI-compatible).
API_URL = os.environ.get("XAI_API_URL", "https://api.x.ai/v1/chat/completions")
API_KEY = os.environ.get("XAI_API_KEY", "")

# Fast Grok text model — picked for low latency in the live voice loop (~1s vs
# ~4s for grok-4.3/reasoning, same Yoruba quality on the eval). Override with
# XAI_MODEL (e.g. grok-4.3 for max quality at higher latency).
MODEL = os.environ.get(
    "BRAIN_MODEL", os.environ.get("XAI_MODEL", "grok-4.20-0309-non-reasoning"))

# Master switch. BRAIN_ENABLED=0 -> --chat falls back to plain captioning.
ENABLED = os.environ.get("BRAIN_ENABLED", "1") != "0"

# Per-reply request timeout (seconds). Grok replies in ~3-6s.
TIMEOUT = float(os.environ.get("BRAIN_TIMEOUT", "45"))

# Startup health probe timeout (a 1-token chat).
HEALTH_TIMEOUT = float(os.environ.get("BRAIN_HEALTH_TIMEOUT", "30"))

# Cap reply length. Replies are spoken aloud, so keep them to a sentence or two.
MAX_TOKENS = int(os.environ.get("BRAIN_MAX_TOKENS", "120"))

# A touch of warmth so replies don't read like a manual. 0 = deterministic.
TEMPERATURE = float(os.environ.get("BRAIN_TEMPERATURE", "0.6"))

# How many prior exchanges to keep as context (1 exchange = user + assistant).
# Bounds prompt growth so latency stays flat over a long conversation.
HISTORY_TURNS = int(os.environ.get("BRAIN_HISTORY_TURNS", "6"))
