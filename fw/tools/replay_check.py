#!/usr/bin/env python3
r"""Replays A1 run logs through the board and compares with what the sim controller did.

The A1 controller (sim/a1_controller.py) commands
    v = safety speed from min_range (RUN / SLOW / STOP), then v = 0 if |heading error| > 0.4
    w = heading controller output, in every state (the STOP branch touches only v).
The board reproduces this in local mode as v = min(safety speed, local_v), w = local_w, with
N1 sending local_v = 0 while turning in place. Each row of data/a1/run_N.csv
(t,x,y,yaw,min_range,wp_i,mode,v,w) is replayed as such an S line:
    min_mm  = floor(min_range * 1000)
    local_v = 0 on rows where the log shows v == 0 outside STOP (turning in place), else V_MAX
    local_w = logged w in mrad/s
and the reply must satisfy
  1. state equals the float rule and the logged mode;
  2. v_out is never faster than min(float rule, cap) and at most 2.1 mm/s slower
     (1 mm range truncation * 1.1 (mm/s)/mm + 1 mm/s integer division);
  3. v_out matches the logged v within the same tolerance, widened by the rounding of the
     logged min_range (4 decimals = 0.05 mm);
  4. w_out equals the logged w in mrad/s.

Usage (WSL with the fake board, or Windows with the board on COM5):
  python3 fw/tools/replay_check.py --port /tmp/vhost data/a1/run_10.csv data/a1/run_11.csv data/a1/run_12.csv
  python fw\tools\replay_check.py --port COM5 --out-json data\a2\replay_1.json data\a1\run_10.csv ...
"""
import argparse
import csv
import json
import math
import sys
import time
from pathlib import Path

import serial

sys.path.insert(0, str(Path(__file__).resolve().parent))
import proto  # noqa: E402

V_TOL_MMS = 2.1
SLOPE = proto.V_MAX_MMS / (proto.D_SLOW_MM - proto.D_STOP_MM)  # 1.1 (mm/s) per mm
MODE_CODES = {"RUN": 0, "SLOW": 1, "STOP": 2}


def exchange(ser, seq, min_mm, local_v, local_w):
    ser.write(proto.build_s(seq, min_mm, local_v, local_w, 0, 0, 1))
    deadline = time.perf_counter() + 0.2
    while time.perf_counter() < deadline:
        raw = ser.readline()
        if not raw.endswith(b"\n"):
            continue
        try:
            c = proto.parse_c(raw)
        except ValueError:
            continue
        if c.seq == seq and c.state != 3:
            return c
    return None


def decimals(cell):
    return len(cell.split(".", 1)[1]) if "." in cell else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=921600)
    ap.add_argument("--range-col", default="min_range")
    ap.add_argument("--mode-col", default="mode")
    ap.add_argument("--v-col", default="v")
    ap.add_argument("--w-col", default="w")
    ap.add_argument("--out-json", default=None, help="write the summary here")
    args = ap.parse_args()

    summary = {"files": {}, "total": 0, "lost": 0, "mismatches": 0, "turning_rows": 0,
               "worst_dv_mms": 0.0}
    with serial.Serial(args.port, args.baud, timeout=0.05) as ser:
        time.sleep(0.2)
        ser.reset_input_buffer()
        seq = 10_000_000
        for path in args.csv:
            with open(path, newline="") as f:
                rows = list(csv.DictReader(f))
            cols = list(rows[0].keys()) if rows else []
            if args.range_col not in cols:
                sys.exit(f"{path}: no column {args.range_col!r}; columns are {cols}")
            has_mode = args.mode_col in cols
            has_v = args.v_col in cols
            has_w = args.w_col in cols
            fs = {"rows": len(rows), "checked": 0, "lost": 0, "mismatches": 0,
                  "turning_rows": 0, "stop_rows": 0, "stop_rows_with_w": 0}
            for i, row in enumerate(rows):
                cell = row[args.range_col].strip()
                if cell == "":
                    continue
                r = float(cell)
                mm = proto.range_to_mm(r)
                st_ref, v_ref = proto.float_safety(r) if not math.isnan(r) else (2, 0.0)
                logged_mode = row[args.mode_col].strip() if has_mode else ""
                logged_v = float(row[args.v_col]) if has_v and row[args.v_col].strip() else None
                w_mrad = proto.rad_to_mrad(float(row[args.w_col])) \
                    if has_w and row[args.w_col].strip() else 0

                stopped = (logged_mode == "STOP") if logged_mode in MODE_CODES else st_ref == 2
                turning = logged_v is not None and logged_v == 0.0 and not stopped
                cap = 0 if turning else proto.V_MAX_MMS

                seq += 1
                c = exchange(ser, seq, mm, cap, w_mrad)
                fs["checked"] += 1
                if c is None:
                    fs["lost"] += 1
                    continue
                if turning:
                    fs["turning_rows"] += 1

                problems = []
                if c.state != st_ref:
                    problems.append(f"state {c.state} != rule {st_ref}")
                if logged_mode in MODE_CODES and c.state != MODE_CODES[logged_mode]:
                    problems.append(f"state {c.state} != logged {logged_mode}")

                ref = min(v_ref * 1000.0, cap)
                d = ref - c.v_out
                summary["worst_dv_mms"] = max(summary["worst_dv_mms"], abs(d))
                if not (-1e-6 < d < V_TOL_MMS + 1e-6):
                    problems.append(f"v {c.v_out} vs rule {ref:.3f}")

                if logged_v is not None:
                    margin = SLOPE * (10.0 ** -decimals(cell)) * 1000.0 / 2.0 + 1e-6
                    dl = logged_v * 1000.0 - c.v_out
                    if not (-margin < dl < V_TOL_MMS + margin):
                        problems.append(f"v {c.v_out} vs logged {row[args.v_col]}")

                if c.w_out != w_mrad:
                    problems.append(f"w {c.w_out} vs logged {w_mrad}")

                if c.state == 2:
                    fs["stop_rows"] += 1
                    if w_mrad != 0:  # |w| >= 0.5 mrad/s after rounding
                        fs["stop_rows_with_w"] += 1

                if problems:
                    fs["mismatches"] += 1
                    if fs["mismatches"] <= 5:
                        print(f"  {Path(path).name} row {i}: r={cell} mm={mm}: " + "; ".join(problems))
            summary["files"][Path(path).name] = fs
            summary["total"] += fs["checked"]
            summary["lost"] += fs["lost"]
            summary["mismatches"] += fs["mismatches"]
            summary["turning_rows"] += fs["turning_rows"]
            print(f"{Path(path).name}: {fs['checked']} rows checked, {fs['lost']} lost, "
                  f"{fs['mismatches']} mismatches | turning rows {fs['turning_rows']} "
                  f"| STOP rows {fs['stop_rows']} "
                  f"(logged w != 0 in {fs['stop_rows_with_w']})")

    summary["worst_dv_mms"] = round(summary["worst_dv_mms"], 3)
    summary["pass"] = summary["mismatches"] == 0 and summary["lost"] == 0
    print(f"\nreplay {'PASS' if summary['pass'] else 'FAIL'}: {summary['total']} steps, "
          f"{summary['lost']} lost, {summary['mismatches']} mismatches, "
          f"{summary['turning_rows']} turning rows, "
          f"worst |dv| vs rule = {summary['worst_dv_mms']} mm/s")
    if args.out_json:
        out = Path(args.out_json)
        if out.exists():
            sys.exit(f"{out.name} already exists. Pick another name.")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, indent=2) + "\n")
    sys.exit(0 if summary["pass"] else 1)


if __name__ == "__main__":
    main()
