# sim.py — a software stand-in for firmware/esp32s3_robot's WiFi server. Speaks
# the same line protocol on TCP (READY on connect, OK:/PONG/ERR: replies, a new
# client replaces the old one) and records every command, so the WiFi transport
# is tested over real sockets without a board.
#
#   python3 -m services.robot.sim            # listen on 0.0.0.0:3333
#   ROBOT_HOST=127.0.0.1 python3 -m services.robot.controller --health
from __future__ import annotations

import socket
import sys
import threading

DEF_SPEED, DEF_MS, MAX_MS = 200, 900, 5000


def reply_for(line: str) -> str:
    """The firmware's handle(), as a pure function."""
    s = line.strip()
    if not s:
        return ""
    cmd = s[0].upper()
    if cmd == "P":
        return "PONG"
    if cmd == "S":
        return "OK:S"
    if cmd in "FBLR":
        parts = s.split(",")
        speed = int(parts[1]) if len(parts) > 1 and parts[1].strip().lstrip("-").isdigit() else DEF_SPEED
        ms = int(parts[2]) if len(parts) > 2 and parts[2].strip().isdigit() else DEF_MS
        speed = min(speed, 255)
        ms = min(ms, MAX_MS)
        return f"OK:{cmd}:{speed if speed > 0 else DEF_SPEED}:{ms if ms else DEF_MS}"
    return "ERR:unknown"


class RobotSim:
    def __init__(self, host="127.0.0.1", port=0, banner=True):
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind((host, port))
        self.srv.listen(4)
        self.host, self.port = self.srv.getsockname()
        self.banner = banner
        self.received: list[str] = []
        self.client = None
        self.connects = 0
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._t = threading.Thread(target=self._accept_loop, daemon=True)
        self._t.start()

    def _accept_loop(self):
        self.srv.settimeout(0.05)
        while not self._stop.is_set():
            try:
                c, _ = self.srv.accept()
            except (socket.timeout, OSError):
                continue
            with self._lock:
                if self.client is not None:            # new client replaces old
                    try:
                        self.client.close()
                    except OSError:
                        pass
                self.client = c
                self.connects += 1
            if self.banner:
                c.sendall(b"READY\n")
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        buf = b""
        while not self._stop.is_set():
            try:
                data = c.recv(256)
            except OSError:
                return
            if not data:
                return
            buf += data
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                self.received.append(line)
                try:
                    c.sendall((reply_for(line) + "\n").encode())
                except OSError:
                    return

    def push(self, line: str):
        """Send an unsolicited line (e.g. 'OK:S:watchdog') to the client."""
        with self._lock:
            if self.client is not None:
                self.client.sendall((line + "\n").encode())

    def drop_client(self):
        """Simulate a WiFi blip: close the current client connection."""
        with self._lock:
            if self.client is not None:
                try:
                    self.client.shutdown(socket.SHUT_RDWR)
                    self.client.close()
                except OSError:
                    pass
                self.client = None

    def close(self):
        """Power off: stop listening FIRST, then drop the client, so a reconnect
        racing the shutdown is refused (as with a real board) instead of landing
        on a half-closed listener."""
        self._stop.set()
        try:
            self.srv.shutdown(socket.SHUT_RDWR)   # stop listening now (close()
        except OSError:                          # alone waits for a blocked
            pass                                 # accept() in the other thread)
        self._t.join(timeout=1)
        try:
            self.srv.close()
        except OSError:
            pass
        self.drop_client()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 3333
    sim = RobotSim("0.0.0.0", port)
    print(f"robot sim listening on 0.0.0.0:{sim.port} (Ctrl+C to stop)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        sim.close()
