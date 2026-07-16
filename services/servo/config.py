# config.py — servo service settings, all env-overridable.
#
# Two concerns configured here:
#   1. The ESP32 board endpoint (where /servo lives).
#   2. The optional Grok fallback that parses Yoruba phrasing the offline rule
#      matcher does not recognize. Grok reuses the same XAI_* keys as the other
#      services; it is a fallback, so the pipeline works fully offline without it.
import os

from services.env_loader import load_env

load_env()

# ---- ESP32 board ----
# The firmware advertises http://esp32-servo.local via mDNS, so the default needs
# no hard-coded DHCP IP. Override with SERVO_HOST if mDNS is unavailable.
HOST = os.environ.get("SERVO_HOST", "http://esp32-servo.local").rstrip("/")

# Per-request timeout for a move (seconds). Servos are local-network fast.
TIMEOUT = float(os.environ.get("SERVO_TIMEOUT", "3"))

# Startup health-ping timeout.
HEALTH_TIMEOUT = float(os.environ.get("SERVO_HEALTH_TIMEOUT", "3"))

# Master switch. SERVO_ENABLED=0 -> live_caption ignores --servo.
ENABLED = os.environ.get("SERVO_ENABLED", "1") != "0"

# ---- Motion model ----
MIN_ANGLE = int(os.environ.get("SERVO_MIN_ANGLE", "0"))
MAX_ANGLE = int(os.environ.get("SERVO_MAX_ANGLE", "180"))
CENTER_ANGLE = int(os.environ.get("SERVO_CENTER_ANGLE", "90"))
OPEN_ANGLE = int(os.environ.get("SERVO_OPEN_ANGLE", "180"))
CLOSE_ANGLE = int(os.environ.get("SERVO_CLOSE_ANGLE", "0"))
START_ANGLE = int(os.environ.get("SERVO_START_ANGLE", "90"))
# Degrees a single "left"/"right" command moves the horn.
STEP = int(os.environ.get("SERVO_STEP", "30"))

# ---- Grok fallback (optional) ----
GROK_FALLBACK = os.environ.get("SERVO_GROK_FALLBACK", "1") != "0"
API_URL = os.environ.get("XAI_API_URL", "https://api.x.ai/v1/chat/completions")
API_KEY = os.environ.get("XAI_API_KEY", "")
MODEL = os.environ.get(
    "SERVO_MODEL", os.environ.get("XAI_MODEL", "grok-4.20-0309-non-reasoning"))
GROK_TIMEOUT = float(os.environ.get("SERVO_GROK_TIMEOUT", "10"))
MAX_TOKENS = int(os.environ.get("SERVO_MAX_TOKENS", "32"))
TEMPERATURE = float(os.environ.get("SERVO_TEMPERATURE", "0"))
