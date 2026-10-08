# config.py — robot service settings, all env-overridable.
#
# The robot is an ESP32-S3 reached over WiFi (TCP) or USB serial; this laptop is
# the server. Movement commands go out as text lines; the board ACKs each one. An optional Grok fallback
# parses Yoruba phrasing the offline rules miss (reusing the shared XAI_* keys);
# it is a fallback, so the robot works fully offline without it.
import os

from services.env_loader import load_env

load_env()

# ---- Which link to the ESP32 ----
# "auto" tries WiFi first (no cable), then falls back to USB serial.
# "wifi" / "serial" force one.
LINK = os.environ.get("ROBOT_LINK", "auto").lower()

# ---- WiFi (TCP) link ----
# "auto" discovers the board: mDNS yoruba-robot.local, then a TCP sweep of the
# laptop's /24 for a board answering PONG. Or pin it, e.g. 192.168.43.57.
HOST = os.environ.get("ROBOT_HOST", "auto")
TCP_PORT = int(os.environ.get("ROBOT_TCP_PORT", "3333"))
MDNS_NAME = os.environ.get("ROBOT_MDNS_NAME", "yoruba-robot.local")
# Seconds for one TCP connect / for each host probed during the LAN sweep.
CONNECT_TIMEOUT = float(os.environ.get("ROBOT_CONNECT_TIMEOUT", "2.0"))
SCAN_TIMEOUT = float(os.environ.get("ROBOT_SCAN_TIMEOUT", "0.4"))
# Override the swept subnet (e.g. "192.168.43"); default = the laptop's own /24.
SCAN_SUBNET = os.environ.get("ROBOT_SCAN_SUBNET", "")

# ---- Serial link to the ESP32 ----
# "auto" scans the USB ports for the board; or pin it, e.g. /dev/ttyACM0.
PORT = os.environ.get("ROBOT_PORT", "auto")
BAUD = int(os.environ.get("ROBOT_BAUD", "115200"))
# Seconds to wait for a command ACK / for the port to settle after opening.
TIMEOUT = float(os.environ.get("ROBOT_TIMEOUT", "1.0"))
OPEN_SETTLE = float(os.environ.get("ROBOT_OPEN_SETTLE", "2.0"))

# Master switch. ROBOT_ENABLED=0 -> live_caption ignores --robot.
ENABLED = os.environ.get("ROBOT_ENABLED", "1") != "0"

# ---- Motion ----
DEFAULT_SPEED = int(os.environ.get("ROBOT_SPEED", "200"))     # 0-255
FAST_SPEED = int(os.environ.get("ROBOT_FAST_SPEED", "255"))
SLOW_SPEED = int(os.environ.get("ROBOT_SLOW_SPEED", "130"))

# Fuzzy command match threshold (0-1). Lower = more tolerant of Whisper noise but
# more false matches. Tuned against real mishearings; override if needed.
MATCH_THRESHOLD = float(os.environ.get("ROBOT_MATCH_THRESHOLD", "0.72"))
# How long a single nudge runs before the board auto-stops (ms). Turns are
# shorter than straight moves so one command yields a sensible angle.
DRIVE_MS = int(os.environ.get("ROBOT_DRIVE_MS", "900"))
TURN_MS = int(os.environ.get("ROBOT_TURN_MS", "550"))

# Continuous ("hold") drive: keep the motor alive by resending the move every
# HOLD_REFRESH seconds, each frame asking the board for HOLD_MS of motion. Refresh
# must be < HOLD_MS and < the firmware watchdog (2 s) so there is never a gap.
HOLD_MS = int(os.environ.get("ROBOT_HOLD_MS", "1000"))
HOLD_REFRESH = float(os.environ.get("ROBOT_HOLD_REFRESH", "0.4"))

# ---- Grok fallback (optional) ----
GROK_FALLBACK = os.environ.get("ROBOT_GROK_FALLBACK", "1") != "0"
API_URL = os.environ.get("XAI_API_URL", "https://api.x.ai/v1/chat/completions")
API_KEY = os.environ.get("XAI_API_KEY", "")
MODEL = os.environ.get(
    "ROBOT_MODEL", os.environ.get("XAI_MODEL", "grok-4.20-0309-non-reasoning"))
GROK_TIMEOUT = float(os.environ.get("ROBOT_GROK_TIMEOUT", "10"))
MAX_TOKENS = int(os.environ.get("ROBOT_MAX_TOKENS", "32"))
TEMPERATURE = float(os.environ.get("ROBOT_TEMPERATURE", "0"))
