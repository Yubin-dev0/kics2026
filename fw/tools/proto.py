"""Python side of the N1 <-> N2 line protocol (fw/PROTOCOL.md).

Used by fw/tools/a2_bench.py, fw/tools/replay_check.py and, later, the N1 UART bridge.
"""
from dataclasses import dataclass
from functools import reduce
import math
import time

PROTO_VERSION = 2

STATE_NAMES = {0: "RUN", 1: "SLOW", 2: "STOP", 3: "WDOG"}

# Thresholds and speeds, sensor-referenced, mirrored from fw/core/safety.h
D_STOP_MM = 200
D_SLOW_MM = 400
V_MAX_MMS = 220
V_FLOOR_MMS = 100


def checksum(body: str) -> int:
    return reduce(lambda x, c: x ^ c, body.encode("ascii"), 0)


def with_checksum(body: str) -> bytes:
    return f"{body}*{checksum(body):02X}\n".encode("ascii")


def range_to_mm(r_m: float) -> int:
    """Sector minimum in metres -> wire value. Truncates, so distances only ever shrink."""
    if r_m is None or math.isnan(r_m):
        return 0  # all rays invalid -> fail-safe STOP on the board
    if math.isinf(r_m) or r_m * 1000.0 >= 65535:
        return 65535  # no return -> far
    return max(0, int(math.floor(r_m * 1000.0)))


def build_s(seq: int, min_mm: int, local_v: int, local_w: int, edge_v: int, edge_w: int,
            flag: int) -> bytes:
    """local_v is the waypoint follower's speed cap in mm/s (V_MAX, or 0 while turning in
    place); the board outputs min(safety speed, local_v) in local mode."""
    return with_checksum(f"S,{seq},{min_mm},{local_v},{local_w},{edge_v},{edge_w},{flag}")


def rad_to_mrad(w: float) -> int:
    return max(-32768, min(32767, int(round(w * 1000.0))))


VERSION_QUERY = with_checksum("V")

# Port read timeout, set once when the port is opened and never changed afterwards.
# On Windows, pyserial re-applies the whole port configuration (SetCommState) whenever
# .timeout is assigned; usbser.sys then sends SET_LINE_CODING and the ST-LINK bridge
# re-initialises its UART, dropping bytes in flight. A2 run_1 lost 694 of 1000 lines this way.
PORT_TIMEOUT_S = 0.005


class LineReader:
    """Collects bytes into complete lines. A line split across USB packets is kept, never
    discarded, and the port configuration is never touched while reading."""

    def __init__(self, ser):
        self.ser = ser
        self.buf = bytearray()

    def readline(self, timeout_s):
        deadline = time.perf_counter() + timeout_s
        while True:
            i = self.buf.find(b"\n")
            if i >= 0:
                line = bytes(self.buf[: i + 1])
                del self.buf[: i + 1]
                return line
            if time.perf_counter() >= deadline:
                return None
            chunk = self.ser.read(self.ser.in_waiting or 1)  # waits at most PORT_TIMEOUT_S
            if chunk:
                self.buf.extend(chunk)

    def clear(self):
        self.ser.reset_input_buffer()
        self.buf.clear()


@dataclass
class CLine:
    seq: int
    v_out: int
    w_out: int
    mode: int
    state: int
    switch_us: int
    n_sw: int
    bad_lines: int


def _split_checked(raw: bytes):
    text = raw.decode("ascii", errors="replace").strip()
    body, sep, cs = text.rpartition("*")
    if not sep or len(cs) != 2:
        raise ValueError(f"no checksum: {text!r}")
    if int(cs, 16) != checksum(body):
        raise ValueError(f"checksum mismatch: {text!r}")
    return body.split(",")


def parse_c(raw: bytes) -> CLine:
    f = _split_checked(raw)
    if f[0] != "C" or len(f) != 9:
        raise ValueError(f"not a C line: {raw!r}")
    return CLine(*map(int, f[1:]))


def parse_v(raw: bytes) -> dict:
    f = _split_checked(raw)
    if f[0] != "V" or len(f) != 4:
        raise ValueError(f"not a V line: {raw!r}")
    return {"proto_version": int(f[1]), "build_id": f[2], "sysclk_hz": int(f[3])}


def expected_local(min_mm: int, local_v: int):
    """Board output in local mode: (safety state, min(safety speed, max(local_v, 0)))."""
    state, v = expected_safety(min_mm)
    return state, min(v, max(local_v, 0))


def expected_safety(min_mm: int):
    """Reference for the integer rule on the board: returns (state, v_mms)."""
    if min_mm >= D_SLOW_MM:
        return 0, V_MAX_MMS
    if min_mm >= D_STOP_MM:
        v = V_MAX_MMS * (min_mm - D_STOP_MM) // (D_SLOW_MM - D_STOP_MM)
        return 1, max(v, V_FLOOR_MMS)
    return 2, 0


def float_safety(r_m: float):
    """The A1-validated float rule (sim controller): returns (state, v in m/s)."""
    if r_m >= D_SLOW_MM / 1000.0:
        return 0, V_MAX_MMS / 1000.0
    if r_m >= D_STOP_MM / 1000.0:
        v = (V_MAX_MMS / 1000.0) * (r_m - 0.20) / 0.20
        return 1, max(v, V_FLOOR_MMS / 1000.0)
    return 2, 0.0
