#!/usr/bin/env python3
r"""A2 bench: exercises the N2 board (or fw/host/fake_stm32) over the line protocol.

Phases
  version  V query -> build id, protocol version, SYSCLK
  paced    1000 S lines at 20 Hz in edge mode       -> loss, pass-through, host round trip
  sweep    min_mm 0..600 and 65535 in local mode,   -> safety rule and local speed cap
           plus local_v cap cases                     match, bit for bit
  switch   400 lines at 20 Hz, flag toggled every 10 -> n_sw bookkeeping, switch_us (= C)
  wdog     10 lines, then silence                   -> one unsolicited WDOG line after ~150 ms

Writes run_N.csv (one row per line) and run_N_meta.json into --out (default data/a2), and
refuses to overwrite an existing run. Register the run afterwards, in WSL:
  python3 analysis/append_run.py A2 N

Usage
  python fw\tools\a2_bench.py --port COM5 --run 1 --out <repo>\data\a2   (Windows, board)
  python3 fw/tools/a2_bench.py --port /dev/ttyACM0 --run 1 --stage A3 --out data/a3  (WSL2)
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

import serial

sys.path.insert(0, str(Path(__file__).resolve().parent))
import proto  # noqa: E402

PERIOD_S = 0.050
REPLY_TIMEOUT_S = 0.2
SWITCH_US_LIMIT = 1000   # provisional pass bar, see fw/PROTOCOL.md
WDOG_WINDOW_MS = (140, 300)  # 150 ms on the board plus host scheduling noise

FIELDS = ["phase", "seq", "flag", "min_mm", "local_v", "local_w", "edge_v", "edge_w",
          "t_send_s", "rtt_ms", "v_out", "w_out", "mode", "state",
          "switch_us", "n_sw", "bad_lines", "ok"]


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def git_info():
    """Short commit and dirty flag of the fw/ sources. None when not run inside a git clone
    (for example the Windows working copy); code_sha1 still identifies the sources."""
    root = repo_root()
    try:
        commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                                         text=True, stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(
            ["git", "-C", str(root), "status", "--porcelain", "--", "fw"],
            text=True, stderr=subprocess.DEVNULL).strip())
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = None, None
    return commit, dirty


def code_sha1():
    """SHA-1 over every firmware and bench source, first 12 hex digits (same idea as A1)."""
    fw = repo_root() / "fw"
    files = sorted(list((fw / "core").glob("*.[ch]")) + list((fw / "stm32" / "port").glob("*.[ch]"))
                   + [fw / "tools" / "proto.py", Path(__file__).resolve()])
    h = hashlib.sha1()
    for f in files:
        h.update(f.relative_to(fw).as_posix().encode())
        h.update(f.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:12]


class Bench:
    def __init__(self, ser, writer):
        self.ser = ser
        self.rd = proto.LineReader(ser)
        self.w = writer
        self.seq = 0
        self.stray = 0
        self.bad0 = None
        self.bad_last = None
        self.t0 = time.perf_counter()

    def read_line(self, timeout):
        return self.rd.readline(timeout)

    def bad_delta(self):
        """Board-side rejected lines since the first reply of the run."""
        if self.bad0 is None or self.bad_last is None:
            return None
        return self.bad_last - self.bad0

    def exchange(self, phase, min_mm, local_v, local_w, edge_v, edge_w, flag):
        """Send one S line and wait for the C line with the same seq."""
        self.seq += 1
        seq = self.seq
        t_send = time.perf_counter()
        self.ser.write(proto.build_s(seq, min_mm, local_v, local_w, edge_v, edge_w, flag))
        c, rtt_ms = None, None
        deadline = t_send + REPLY_TIMEOUT_S
        while time.perf_counter() < deadline:
            raw = self.read_line(max(0.0, deadline - time.perf_counter()))
            if raw is None:
                continue
            try:
                line = proto.parse_c(raw)
            except ValueError:
                self.stray += 1
                continue
            if line.seq == seq and line.state != 3:
                rtt_ms = (time.perf_counter() - t_send) * 1000.0
                c = line
                if self.bad0 is None:
                    self.bad0 = line.bad_lines
                self.bad_last = line.bad_lines
                break
            self.stray += 1  # late reply or an unsolicited WDOG line
        row = {"phase": phase, "seq": seq, "flag": flag, "min_mm": min_mm,
               "local_v": local_v, "local_w": local_w, "edge_v": edge_v, "edge_w": edge_w,
               "t_send_s": f"{t_send - self.t0:.6f}",
               "rtt_ms": "" if rtt_ms is None else f"{rtt_ms:.3f}"}
        if c is not None:
            row.update({k: getattr(c, k) for k in
                        ("v_out", "w_out", "mode", "state", "switch_us", "n_sw", "bad_lines")})
        return row, c, rtt_ms

    def emit(self, row, ok):
        row["ok"] = int(ok)
        self.w.writerow(row)


def paced(n):
    """Yields at a fixed 20 Hz cadence without drift."""
    start = time.perf_counter()
    for i in range(n):
        target = start + i * PERIOD_S
        delay = target - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        yield i


def phase_paced(b, res, n=1000):
    rtts, lost, bad = [], 0, 0
    for _ in paced(n):
        mm = random.randint(150, 800)
        ew = random.randint(-1820, 1820)
        row, c, rtt = b.exchange("paced", mm, proto.V_MAX_MMS, 0, 150, ew, 0)
        ok = c is not None and c.mode == 0 and c.v_out == 150 and c.w_out == ew
        if c is None:
            lost += 1
        elif not ok:
            bad += 1
        else:
            rtts.append(rtt)
        b.emit(row, ok)
    res["paced"] = {"sent": n, "lost": lost, "wrong": bad, "bad_lines_delta": b.bad_delta(),
                    **stats(rtts, "rtt_ms")}
    return lost == 0 and bad == 0


def phase_sweep(b, res):
    # (min_mm, local_v): the full rule range with no cap, then cap cases
    cases = [(mm, proto.V_MAX_MMS) for mm in list(range(0, 601)) + [65535]]
    cases += [(500, 0), (500, 150), (300, 150), (300, 80), (150, 220), (500, -50), (500, 400)]
    lost, wrong = 0, 0
    for mm, lv in cases:
        lw = random.randint(-1820, 1820)
        row, c, _ = b.exchange("sweep", mm, lv, lw, 999, 999, 1)
        exp_state, exp_v = proto.expected_local(mm, lv)
        ok = (c is not None and c.mode == 1 and c.state == exp_state
              and c.v_out == exp_v and c.w_out == lw)
        lost += c is None
        wrong += (c is not None and not ok)
        b.emit(row, ok)
    res["sweep"] = {"sent": len(cases), "lost": lost, "wrong": wrong,
                    "bad_lines_delta": b.bad_delta()}
    return lost == 0 and wrong == 0


def phase_switch(b, res, n=400, every=10):
    # settle in edge mode and take the starting counters
    row, c, _ = b.exchange("switch", 500, proto.V_MAX_MMS, 0, 150, 0, 0)
    b.emit(row, c is not None)
    if c is None:
        res["switch"] = {"error": "no reply to settle line"}
        return False
    mode, n0 = c.mode, c.n_sw
    flag = 0
    lost, wrong, sw = 0, 0, []
    for i in paced(n):
        if i and i % every == 0:
            flag ^= 1
        row, c, _ = b.exchange("switch", 500, proto.V_MAX_MMS, 0, 150, 0, flag)
        if c is None:
            lost += 1
            b.emit(row, False)
            continue
        expect_switch = flag != mode
        ok = c.mode == flag and bool(c.switch_us) == expect_switch
        if c.switch_us:
            sw.append(c.switch_us)
        mode = c.mode
        wrong += not ok
        b.emit(row, ok)
        last = c
    n_sw_delta = last.n_sw - n0
    res["switch"] = {"sent": n, "lost": lost, "wrong": wrong, "bad_lines_delta": b.bad_delta(),
                     "switches": len(sw),
                     "n_sw_delta": n_sw_delta, **stats(sw, "switch_us")}
    return bool(lost == 0 and wrong == 0 and sw and n_sw_delta == len(sw)
                and max(sw) < SWITCH_US_LIMIT)


def phase_wdog(b, res):
    last_rx = None
    for _ in paced(10):
        row, c, _ = b.exchange("wdog", 500, proto.V_MAX_MMS, 0, 150, 0, 0)
        b.emit(row, c is not None)
        if c is not None:
            last_rx = time.perf_counter()
    got = []
    end = time.perf_counter() + 0.6
    while time.perf_counter() < end:
        raw = b.read_line(max(0.0, end - time.perf_counter()))
        if raw is None:
            continue
        try:
            c = proto.parse_c(raw)
        except ValueError:
            continue
        got.append(((time.perf_counter() - last_rx) * 1000.0, c))
    wd = [(ms, c) for ms, c in got if c.state == 3]
    gap = wd[0][0] if wd else None
    ok = (len(got) == 1 and len(wd) == 1 and wd[0][1].v_out == 0 and wd[0][1].w_out == 0
          and WDOG_WINDOW_MS[0] <= gap <= WDOG_WINDOW_MS[1])
    res["wdog"] = {"lines_after_silence": len(got), "wdog_lines": len(wd),
                   "gap_ms": None if gap is None else round(gap, 1)}
    return ok


def stats(xs, name):
    if not xs:
        return {f"{name}_n": 0}
    s = sorted(xs)
    p99 = s[min(len(s) - 1, int(round(0.99 * (len(s) - 1))))]
    return {f"{name}_n": len(s), f"{name}_median": round(statistics.median(s), 3),
            f"{name}_p99": round(p99, 3), f"{name}_max": round(s[-1], 3),
            f"{name}_min": round(s[0], 3)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=921600)
    ap.add_argument("--run", type=int, required=True, help="run number N -> run_N.csv")
    ap.add_argument("--stage", default="A2", choices=["A2", "A3"])
    ap.add_argument("--target", default="board", choices=["board", "fake"])
    ap.add_argument("--out", default=None, help="output folder (default data/<stage>)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    out = Path(args.out) if args.out else repo_root() / "data" / args.stage.lower()
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / f"run_{args.run}.csv"
    meta_path = out / f"run_{args.run}_meta.json"
    if csv_path.exists() or meta_path.exists():
        sys.exit(f"{csv_path.name} already exists. Pick another --run.")

    random.seed(args.seed)
    commit, dirty = git_info()
    meta = {"run_id": str(args.run), "stage": args.stage, "target": args.target,
            "started": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "port": args.port, "baud": args.baud, "seed": args.seed,
            "proto_version_host": proto.PROTO_VERSION, "period_s": PERIOD_S,
            "thresholds_mm": {"d_stop": proto.D_STOP_MM, "d_slow": proto.D_SLOW_MM},
            "speeds_mms": {"v_max": proto.V_MAX_MMS, "v_floor": proto.V_FLOOR_MMS},
            "watchdog_ms_expected": 150, "switch_us_limit": SWITCH_US_LIMIT,
            "code_sha1": code_sha1(), "git": commit, "git_dirty": dirty, "note": args.note,
            "host_python": sys.version.split()[0], "host_platform": sys.platform}

    res, verdict = {}, {}
    aborted = None
    with serial.Serial(args.port, args.baud, timeout=proto.PORT_TIMEOUT_S) as ser, \
            open(csv_path, "w", newline="") as f:
        time.sleep(0.2)
        rd = proto.LineReader(ser)
        rd.clear()
        ser.write(proto.VERSION_QUERY)
        fw = None
        t_end = time.perf_counter() + 1.0
        while fw is None and time.perf_counter() < t_end:
            raw = rd.readline(max(0.0, t_end - time.perf_counter()))
            if raw and raw.startswith(b"V,"):
                try:
                    fw = proto.parse_v(raw)
                except ValueError:
                    fw = None
        if fw is None:
            f.close()
            csv_path.unlink()
            sys.exit("no V reply: check port, baud, and that the board is flashed")
        meta["firmware"] = fw
        print(f"firmware: {fw}")

        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        b = Bench(ser, w)
        b.rd = rd
        try:
            for name, fn in (("paced", phase_paced), ("sweep", phase_sweep),
                             ("switch", phase_switch), ("wdog", phase_wdog)):
                print(f"phase {name} ...", flush=True)
                verdict[name] = fn(b, res)
                print(f"  {'PASS' if verdict[name] else 'FAIL'} {res[name]}", flush=True)

            # firmware-side bad line count must not move during the run
            _, c, _ = b.exchange("final", 500, proto.V_MAX_MMS, 0, 0, 0, 0)
            res["bad_lines_delta"] = b.bad_delta() if c is not None else None
            verdict["bad_lines"] = c is not None and b.bad_delta() == 0
        except KeyboardInterrupt:
            aborted = "KeyboardInterrupt"
            print("\ninterrupted; writing meta with pass = false")
        res["stray_lines"] = b.stray

    meta["finished"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    meta["results"] = res
    meta["verdict"] = verdict
    meta["aborted"] = aborted
    meta["pass"] = aborted is None and bool(verdict) and all(verdict.values())
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")
    print(f"\n{args.stage} {'PASS' if meta['pass'] else 'FAIL'}  ->  {csv_path}, {meta_path.name}")
    print(f"register it in WSL: python3 analysis/append_run.py {args.stage} {args.run}")
    sys.exit(0 if meta["pass"] else 1)


if __name__ == "__main__":
    main()
