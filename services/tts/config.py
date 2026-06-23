# config.py — Yoruba TTS settings, env-overridable.
#
# TTS runs on the YarnGPT cloud API, keyed by YARN_API_KEY in the repo .env.
import os

from services.env_loader import load_env

load_env()

# YarnGPT TTS endpoint (returns raw audio bytes).
API_URL = os.environ.get("YARN_API_URL", "https://yarngpt.ai/api/v1/tts")
API_KEY = os.environ.get("YARN_API_KEY", "")

# Voice. "Idera" is the voice YarnGPT demos with Yoruba. 16 voices exist; set
# YARN_VOICE to switch (e.g. Emma, Zainab, Osagie).
VOICE = os.environ.get("YARN_VOICE", "Idera")

# We request wav so soundfile can decode it with no extra codec dependency.
FORMAT = os.environ.get("YARN_FORMAT", "wav")

# Master switch for the live_caption --speak/--chat audio.
ENABLED = os.environ.get("TTS_ENABLED", "1") != "0"

# Playback output device. "" = system default. Can be a PortAudio device index
# (e.g. "4") or a name substring (e.g. "ALC257"). Set this when the default is an
# HDMI port with nothing connected (run `python3 -m services.tts.speak --devices`
# to list them).
OUTPUT_DEVICE = os.environ.get("TTS_OUTPUT_DEVICE", "")

# Per-call request timeout (seconds).
TIMEOUT = float(os.environ.get("YARN_TIMEOUT", "60"))

# YarnGPT hard limit per request.
MAX_CHARS = int(os.environ.get("YARN_MAX_CHARS", "2000"))
