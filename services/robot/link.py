# link.py — the line channel to the ESP32 robot, over WiFi (TCP) or USB serial.
# Owns wire framing, ACK reading and board discovery; knows nothing about Yoruba.
# Transports (the actual bytes) are injectable so gate tests run with fakes and
# never need a socket, pyserial or hardware. pyserial is imported lazily.
from __future__ import annotations

import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config
from .contract import WIRE, STOP


def is_async(line: str) -> bool:
    """Lines the board emits on its own (not a reply to our command)."""
    return line == "READY" or line.endswith(":watchdog")


# ---------------- WiFi transport (TCP, same line protocol) ----------------
class TcpTransport:
    """A persistent TCP connection to the board's port-3333 server, used as a
    request/response line channel. On a dropped connection it reconnects once
    and retries the command; reconnect attempts are throttled so a keepalive
    loop can't hammer a robot that is switched off."""

    RETRY_GAP = 1.0      # seconds between reconnect attempts after a failure

    def __init__(self, host, port=None, timeout=None, connect_timeout=None,
                 connect=socket.create_connection, connect_now=True):
        self.host = host
        self.tcp_port = config.TCP_PORT if port is None else port
        self.port = f"wifi {host}:{self.tcp_port}"
        self.timeout = config.TIMEOUT if timeout is None else timeout
        self.connect_timeout = (config.CONNECT_TIMEOUT if connect_timeout is None
                                else connect_timeout)
        self._connect_fn = connect
        self.sock = None
        self._buf = b""
        self._last_fail = 0.0
        # connect_now=False: don't take the robot's single command slot until
        # the first send (the firmware drops its current client when a new one
        # connects, and stops the motors).
        if connect_now:
            self._connect()

    @property
    def connected(self) -> bool:
        return self.sock is not None

    def _connect(self):
        self.close()
        try:
            sock = self._connect_fn((self.host, self.tcp_port),
                                    timeout=self.connect_timeout)
        except OSError:
            self._last_fail = time.monotonic()
            raise
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except (OSError, AttributeError):
            pass
        sock.settimeout(self.timeout)
        self.sock = sock
        self._buf = b""

    def _recv(self) -> bytes:
        data = self.sock.recv(1024)
        if not data:
            raise ConnectionError("robot closed the connection")
        return data

    def _drain(self):
        """Drop anything already received (READY banner, watchdog notices, a
        late ACK) so the next line read is the reply to our command."""
        self._buf = b""
        self.sock.setblocking(False)
        try:
            while True:
                self._recv()
        except (BlockingIOError, InterruptedError):
            pass
        finally:
            self.sock.settimeout(self.timeout)

    def _readline(self) -> str:
        """One reply line, or '' on timeout. Raises ConnectionError if the link
        died."""
        deadline = time.monotonic() + self.timeout
        while b"\n" not in self._buf:
            left = deadline - time.monotonic()
            if left <= 0:
                return ""
            self.sock.settimeout(left)
            try:
                self._buf += self._recv()
            except socket.timeout:
                return ""
            finally:
                self.sock.settimeout(self.timeout)
        line, self._buf = self._buf.split(b"\n", 1)
        return line.decode(errors="replace").strip()

    def _exchange(self, line: str) -> str:
        self._drain()
        self.sock.sendall((line + "\n").encode())
        for _ in range(3):
            reply = self._readline()
            if not is_async(reply):
                return reply
        return ""

    def send(self, line: str) -> str:
        if self.sock is None:
            if time.monotonic() - self._last_fail < self.RETRY_GAP:
                raise ConnectionError(f"robot unreachable at {self.host}")
            self._connect()
            return self._exchange(line)
        try:
            return self._exchange(line)
        except (ConnectionError, OSError):
            self.close()                    # link dropped: reconnect once, retry
            self._connect()
            return self._exchange(line)

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None


# ---------------- WiFi discovery ----------------
def probe(host, port=None, timeout=None, connect=socket.create_connection) -> bool:
    """True if `host` runs the robot firmware (answers P with PONG)."""
    port = config.TCP_PORT if port is None else port
    timeout = config.SCAN_TIMEOUT if timeout is None else timeout
    try:
        with connect((host, port), timeout=timeout) as s:
            s.settimeout(max(timeout, 0.8))
            s.sendall(b"P\n")
            buf = b""
            while b"PONG" not in buf and len(buf) < 256:
                data = s.recv(256)
                if not data:
                    break
                buf += data
            return b"PONG" in buf
    except OSError:
        return False


def resolve_mdns(name=None, timeout=1.5, run=subprocess.run) -> str | None:
    """IPv4 for the board's mDNS name via the system resolver (avahi/nss-mdns).
    A present board answers in well under the timeout; a missing one would block
    getaddrinfo ~5 s (and hold interpreter exit), so the lookup runs as
    `getent ahostsv4` in a child process that is killed at the timeout."""
    name = name or config.MDNS_NAME
    try:
        r = run(["getent", "ahostsv4", name], capture_output=True, text=True,
                timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0 or not r.stdout.split():
        return None
    return r.stdout.split()[0]


def local_subnet() -> str | None:
    """'a.b.c' of the interface that carries the default route (no packet is
    sent: connect() on UDP only picks a route)."""
    if config.SCAN_SUBNET:
        return config.SCAN_SUBNET.rstrip(".")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    if ip.startswith("127."):
        return None
    return ip.rsplit(".", 1)[0]


def scan_subnet(prefix, probe_fn=probe, workers=128) -> str | None:
    """Probe prefix.1..254 in parallel; return the first host answering PONG.
    Outbound TCP only, so a laptop firewall can't block it."""
    hosts = [f"{prefix}.{i}" for i in range(1, 255)]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(probe_fn, h): h for h in hosts}
        for fut in as_completed(futs):
            if fut.result():
                for f in futs:
                    f.cancel()
                return futs[fut]
    return None


def discover(mdns=resolve_mdns, probe_fn=probe, subnet=local_subnet,
             scan=scan_subnet, verify_mdns=True) -> tuple[str | None, str]:
    """Find the board on the LAN. Returns (host, how) or (None, why-not).
    Order: pinned ROBOT_HOST -> mDNS name -> /24 sweep.

    verify_mdns=False trusts the mDNS answer without a TCP probe. A probe is a
    TCP connection, and connecting makes the firmware drop (and stop) whoever
    is currently driving it, so an observer must not probe."""
    if config.HOST and config.HOST != "auto":
        return config.HOST, "ROBOT_HOST"
    ip = mdns()
    if ip and (not verify_mdns or probe_fn(ip)):
        return ip, f"mDNS {config.MDNS_NAME}"
    prefix = subnet()
    if not prefix:
        return None, "no network (laptop has no LAN IP)"
    host = scan(prefix, probe_fn=probe_fn)
    if host:
        return host, f"LAN scan {prefix}.0/24"
    return None, (f"no robot answered on {prefix}.0/24 port {config.TCP_PORT} "
                  f"(is the laptop on the robot's WiFi?)")


def open_tcp(lazy: bool = False) -> TcpTransport:
    """lazy=True: find the robot without touching its command link (mDNS
    trusted, no probe; connect on first send)."""
    host, how = discover(verify_mdns=not lazy)
    if not host:
        raise RuntimeError(how)
    t = TcpTransport(host, connect_now=not lazy)
    t.found_by = how
    return t


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
        for _ in range(3):                            # skip async boot/watchdog lines
            reply = self.ser.readline().decode(errors="replace").strip()
            if not is_async(reply):
                return reply
        return ""

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


def open_transport(lazy: bool = False):
    """Open the link ROBOT_LINK asks for. 'auto' = WiFi first, then USB serial.
    lazy=True (WiFi only) defers connecting until the first command. Raises
    with every reason on failure."""
    mode = config.LINK
    if mode not in ("auto", "wifi", "serial"):
        raise RuntimeError(f"ROBOT_LINK={mode!r} (use auto, wifi or serial)")
    errors = []
    if mode in ("auto", "wifi"):
        try:
            return open_tcp(lazy=lazy)
        except Exception as e:
            errors.append(f"wifi: {e}")
    if mode in ("auto", "serial"):
        try:
            return open_serial()
        except Exception as e:
            errors.append(f"usb: {e}")
    raise RuntimeError("; ".join(errors))


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
