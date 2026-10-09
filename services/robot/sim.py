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
import time

DEF_SPEED, DEF_MS, MAX_MS = 200, 900, 5000
MIN_DUTY = 90


def speed_to_duty(speed: int, min_duty: int = MIN_DUTY) -> int:
    """Firmware motor::speedToDuty."""
    if speed <= 0:
        return 0
    speed = min(255, max(1, speed))
    return min_duty + (speed - 1) * (255 - min_duty) // 254


def duties_for(line: str):
    """Command line -> ((dutyL, dutyR), window_ms), like the firmware's motors()
    (no ramp). None for non-motion lines."""
    s = line.strip()
    if not s:
        return None
    cmd = s[0].upper()
    if cmd == "S":
        return (0, 0), 0
    if cmd not in "FBLR":
        return None
    parts = s.split(",")
    speed = int(parts[1]) if len(parts) > 1 and parts[1].strip().lstrip("-").isdigit() else DEF_SPEED
    ms = int(parts[2]) if len(parts) > 2 and parts[2].strip().isdigit() else DEF_MS
    speed = DEF_SPEED if speed <= 0 else min(speed, 255)
    ms = DEF_MS if ms == 0 else min(ms, MAX_MS)
    d = speed_to_duty(speed)
    return {"F": (d, d), "B": (-d, -d), "L": (-d, d), "R": (d, -d)}[cmd], ms


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
    def __init__(self, host="127.0.0.1", port=0, banner=True, telem_port=None):
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
        # motor state + UDP telemetry, like the firmware (telem_port=0 -> any)
        self._duty = (0, 0)
        self._until = 0.0
        self._seq = 0
        self.subscribers = {}
        self.telem_port = None
        if telem_port is not None:
            self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._udp.bind((host, telem_port))
            self._udp.settimeout(0.02)
            self.telem_port = self._udp.getsockname()[1]
            self._tu = threading.Thread(target=self._telemetry_loop, daemon=True)
            self._tu.start()

    def duties(self):
        """Motor duties right now (auto-stop after the move window)."""
        with self._lock:
            return self._duty if time.monotonic() < self._until else (0, 0)

    def _apply(self, line):
        r = duties_for(line)
        if r is None:
            return
        (dl, dr), ms = r
        with self._lock:
            self._duty = (dl, dr)
            self._until = time.monotonic() + ms / 1000 if ms else 0.0

    def _telemetry_loop(self):
        next_tx = time.monotonic()
        while not self._stop.is_set():
            try:
                data, addr = self._udp.recvfrom(64)
                if data.startswith(b"SUB"):
                    self.subscribers[addr] = time.monotonic()
            except (socket.timeout, OSError):
                pass
            now = time.monotonic()
            if now < next_tx:
                continue
            next_tx = now + 0.05
            dl, dr = self.duties()
            line = f"T,{self._seq},{int(now * 1000)},{dl},{dr},{MIN_DUTY}\n".encode()
            self._seq += 1
            for addr, seen in list(self.subscribers.items()):
                if now - seen <= 3.0:
                    try:
                        self._udp.sendto(line, addr)
                    except OSError:
                        pass

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
                self._apply(line)
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
        if self.telem_port is not None:
            self._tu.join(timeout=1)
            try:
                self._udp.close()
            except OSError:
                pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 3333
    sim = RobotSim("0.0.0.0", port, telem_port=3334)
    print(f"robot sim listening on 0.0.0.0:{sim.port}, telemetry UDP {sim.telem_port} (Ctrl+C to stop)")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        sim.close()
