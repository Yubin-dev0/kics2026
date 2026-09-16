#!/usr/bin/env python3
r"""Replays A1 run logs through the firmware safety rule and compares with the sim controller.

For every row of data/a1/run_N.csv (columns t,x,y,yaw,min_range,wp_i,mode,v,w) the logged
min_range is converted to the wire value (truncated mm), sent to the board or the fake in
local mode, and the reply is checked against:
  1. the float rule the sim controller uses: state must match; speed never faster and at
     most 2.1 mm/s slower (1 mm truncation * 1.1 (mm/s)/mm + 1 mm/s integer division);
  2. the logged mode column (RUN/SLOW/STOP), when present;
  3. the logged v column, when present, with the same tolerance widened by the rounding of
     the logged min_range (a value printed with 4 decimals may be 0.05 mm off).
It also reports the logged w during STOP rows, which tells whether the sim controller
kept turning while stopped (the firmware passes local_w through in every state).

Usage (Windows PowerShell, board on COM5):
  python fw\tools\replay_check.py --port COM5 --out-json <repo>\data\a2\replay_1.json `
      <repo>\data\a1\run_10.csv <repo>\data\a1\run_11.csv <repo>\data\a1\run_12.csv
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


def exchange(ser, seq, min_mm):
    ser.write(proto.build_s(seq, min_mm, 0, 0, 0, 1))
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

    summary = {"files": {}, "total": 0, "lost": 0, "mismatches": 0, "worst_dv_mms": 0.0}
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
                  "stop_rows": 0, "stop_rows_with_w": 0}
            for i, row in enumerate(rows):
                cell = row[args.range_col].strip()
                if cell == "":
                    continue
                r = float(cell)
                mm = proto.range_to_mm(r)
                seq += 1
                c = exchange(ser, seq, mm)
                fs["checked"] += 1
                if c is None:
                    fs["lost"] += 1
                    continue

                st_ref, v_ref = proto.float_safety(r) if not math.isnan(r) else (2, 0.0)
                problems = []
                if c.state != st_ref:
                    problems.append(f"state {c.state} != rule {st_ref}")
                d = v_ref * 1000.0 - c.v_out
                summary["worst_dv_mms"] = max(summary["worst_dv_mms"], abs(d))
                if not (-1e-6 < d < V_TOL_MMS + 1e-6):
                    problems.append(f"v {c.v_out} vs rule {v_ref * 1000.0:.3f}")

                if has_mode and row[args.mode_col].strip() in MODE_CODES:
                    if c.state != MODE_CODES[row[args.mode_col].strip()]:
                        problems.append(f"state {c.state} != logged {row[args.mode_col]}")

                if has_v and row[args.v_col].strip() != "":
                    margin = SLOPE * (10.0 ** -decimals(cell)) * 1000.0 / 2.0 + 1e-6
                    dl = float(row[args.v_col]) * 1000.0 - c.v_out
                    if not (-margin < dl < V_TOL_MMS + margin):
                        problems.append(f"v {c.v_out} vs logged {row[args.v_col]}")

                if c.state == 2:
                    fs["stop_rows"] += 1
                    if has_w and row[args.w_col].strip() not in ("", "0", "0.0") \
                            and abs(float(row[args.w_col])) > 1e-9:
                        fs["stop_rows_with_w"] += 1

                if problems:
                    fs["mismatches"] += 1
                    if fs["mismatches"] <= 5:
                        print(f"  {Path(path).name} row {i}: r={cell} mm={mm}: " + "; ".join(problems))
            summary["files"][Path(path).name] = fs
            summary["total"] += fs["checked"]
            summary["lost"] += fs["lost"]
            summary["mismatches"] += fs["mismatches"]
            print(f"{Path(path).name}: {fs['checked']} rows checked, {fs['lost']} lost, "
                  f"{fs['mismatches']} mismatches, STOP rows {fs['stop_rows']} "
                  f"(logged w != 0 in {fs['stop_rows_with_w']})")

    summary["worst_dv_mms"] = round(summary["worst_dv_mms"], 3)
    summary["pass"] = summary["mismatches"] == 0 and summary["lost"] == 0
    print(f"\nreplay {'PASS' if summary['pass'] else 'FAIL'}: {summary['total']} steps, "
          f"{summary['lost']} lost, {summary['mismatches']} mismatches, "
          f"worst |dv| = {summary['worst_dv_mms']} mm/s")
    if args.out_json:
        out = Path(args.out_json)
        if out.exists():
            sys.exit(f"{out.name} already exists. Pick another name.")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, indent=2) + "\n")
    sys.exit(0 if summary["pass"] else 1)


if __name__ == "__main__":
    main()
