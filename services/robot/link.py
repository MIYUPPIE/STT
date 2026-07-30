# link.py — the serial line to the ESP32 robot. Owns wire framing and ACK reading;
# knows nothing about Yoruba. The transport (the actual bytes) is injectable so
# gate tests run with a fake and never need pyserial or hardware. pyserial is
# imported lazily, only when a real port is opened.
from __future__ import annotations

import time

from . import config
from .contract import WIRE, STOP


# ---------------- real serial transport (lazy pyserial) ----------------
def find_port() -> str | None:
    """Scan USB serial ports for the most ESP32-looking one. /dev/ttyACM* (native
    USB CDC) and known USB-UART bridges rank highest."""
    from serial.tools import list_ports
    best, best_score = None, 0
    for p in list_ports.comports():
        dev = (p.device or "")
        blob = f"{p.description} {p.hwid}".lower()
        score = 0
        if "ttyacm" in dev.lower():
            score += 3
        if "ttyusb" in dev.lower():
            score += 2
        if any(k in blob for k in ("esp32", "espressif", "cp210", "ch340",
                                   "ch910", "usb-serial", "usb serial", "jtag")):
            score += 3
        if score > best_score:
            best, best_score = dev, score
    return best


class SerialTransport:
    """Wraps a pyserial port as a request/response line channel."""

    def __init__(self, port, baud=None, timeout=None, settle=None):
        import serial  # lazy: only needed for a real board
        self.port = port
        self.ser = serial.Serial(port, baud or config.BAUD,
                                 timeout=timeout or config.TIMEOUT)
        time.sleep(config.OPEN_SETTLE if settle is None else settle)  # let it boot
        self.ser.reset_input_buffer()

    def send(self, line: str) -> str:
        """Write one command line, return the board's reply line (stripped)."""
        self.ser.reset_input_buffer()                 # drop any async watchdog line
        self.ser.write((line + "\n").encode())
        self.ser.flush()
        reply = self.ser.readline().decode(errors="replace").strip()
        if reply == "READY":                          # skip a stray boot banner
            reply = self.ser.readline().decode(errors="replace").strip()
        return reply

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


def open_serial() -> SerialTransport:
    """Open the configured (or auto-detected) port. Raises on failure."""
    port = config.PORT
    if port == "auto":
        port = find_port()
        if not port:
            raise RuntimeError("no USB serial port found; set ROBOT_PORT in .env")
    return SerialTransport(port)


# ---------------- link (transport-agnostic) ----------------
class RobotLink:
    def __init__(self, transport):
        self.transport = transport
        self.port = getattr(transport, "port", "?")
        self.last_error = None

    def command(self, line: str):
        """Send a raw line; classify the ACK. Returns (ok, ack, error)."""
        try:
            ack = self.transport.send(line)
        except Exception as e:
            self.last_error = str(e)
            return False, "", str(e)
        ok = ack.startswith("OK") or ack == "PONG"
        self.last_error = None if ok else (ack or "no ack from board")
        return ok, ack, None if ok else self.last_error

    def move(self, action: str, speed: int, ms: int):
        if action == STOP:
            return self.command("S")
        return self.command(f"{WIRE[action]},{speed},{ms}")

    def ping(self) -> bool:
        ok, ack, _ = self.command("P")
        return ok and ack == "PONG"

    def close(self):
        if hasattr(self.transport, "close"):
            self.transport.close()
