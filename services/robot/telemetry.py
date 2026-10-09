# telemetry.py — listen to the robot's own report of what its motors are doing.
#
# The firmware streams "T,<seq>,<ms>,<dutyL>,<dutyR>,<minDuty>" over UDP 3334 to
# anyone who asked with "SUB" in the last 3 s. dutyL/dutyR are the SIGNED PWM
# duties applied right now (after ramp and trim; 0 = stopped), so this is the
# truth about the motors no matter who is commanding: the ROS bridge, voice over
# WiFi (live_caption --robot), USB serial, or the firmware's own watchdog stop.
#
# TelemetryListener re-sends SUB every second from one UDP socket and keeps the
# latest sample. No ROS here, so it is gate-tested with the sim robot.
from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass


PORT = 3334


@dataclass(frozen=True)
class Telemetry:
    seq: int
    robot_ms: int          # robot's millis() when sampled
    duty_left: int         # signed applied PWM duty, -255..255
    duty_right: int
    min_duty: int          # firmware MIN_DUTY (duty -> speed mapping)


def parse_telemetry(line: str) -> Telemetry | None:
    """'T,42,123456,-200,219,90' -> Telemetry, or None if malformed."""
    parts = line.strip().split(",")
    if len(parts) != 6 or parts[0] != "T":
        return None
    try:
        seq, ms, dl, dr, md = (int(p) for p in parts[1:])
    except ValueError:
        return None
    if not (-255 <= dl <= 255 and -255 <= dr <= 255 and 0 <= md <= 255):
        return None
    return Telemetry(seq, ms, dl, dr, md)


class TelemetryListener:
    """Subscribe to one robot's telemetry and keep the newest sample.

    latest()  -> (Telemetry, age_seconds) or (None, inf)
    ever      -> True once any valid sample arrived (proves the firmware streams)
    """

    SUB_EVERY = 1.0

    def __init__(self, host: str, port: int = PORT, on_sample=None):
        self.host = host
        self.port = port
        self.on_sample = on_sample
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("0.0.0.0", 0))                 # any free local port
        self._sock.settimeout(0.2)
        self._lock = threading.Lock()
        self._latest: Telemetry | None = None
        self._latest_t = 0.0
        self.ever = False
        self.received = 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _subscribe(self):
        try:
            self._sock.sendto(b"SUB\n", (self.host, self.port))
        except OSError:
            pass                                         # WiFi blip: retry next second

    def _run(self):
        next_sub = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_sub:
                self._subscribe()
                next_sub = now + self.SUB_EVERY
            try:
                data, _ = self._sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                time.sleep(0.1)
                continue
            for raw in data.decode(errors="replace").splitlines():
                t = parse_telemetry(raw)
                if t is None:
                    continue
                with self._lock:
                    # drop out-of-order datagrams (UDP may reorder)
                    if self._latest is not None and t.seq < self._latest.seq and \
                            self._latest.seq - t.seq < 1000:
                        continue
                    self._latest, self._latest_t = t, time.monotonic()
                    self.ever = True
                    self.received += 1
                if self.on_sample:
                    self.on_sample(t)

    def latest(self) -> tuple[Telemetry | None, float]:
        with self._lock:
            if self._latest is None:
                return None, float("inf")
            return self._latest, time.monotonic() - self._latest_t

    def close(self):
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        self._t.join(timeout=1)


def _main(argv):
    """python3 -m services.robot.telemetry [host]  — print live motor duties."""
    from .link import discover
    host = argv[0] if argv else discover()[0]
    if not host:
        print("robot not found; pass its IP")
        return 1
    print(f"listening to {host}:{PORT} (Ctrl+C to stop)")
    lst = TelemetryListener(host)
    try:
        while True:
            t, age = lst.latest()
            if t is None:
                print("\r  waiting for telemetry (old firmware?)      ", end="", flush=True)
            else:
                print(f"\r  seq={t.seq:<7} left={t.duty_left:+4d}  right={t.duty_right:+4d}  "
                      f"min={t.min_duty}  age={age*1000:4.0f} ms  rx={lst.received}",
                      end="", flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        print()
    finally:
        lst.close()
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
