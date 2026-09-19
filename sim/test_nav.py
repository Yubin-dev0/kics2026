#!/usr/bin/env python3
"""Replays the A1 logs through sim/nav.py and requires the logged command on every row.

Usage: python3 sim/test_nav.py            (reads data/a1/run_6.csv .. run_12.csv)
"""
import csv
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nav  # noqa: E402

RUNS = range(6, 13)  # runs 1-5 used an earlier controller (see data/runs.csv)
Q = 5e-5              # the log rounds every value to 4 decimals


def tolerances(dist):
    """Largest error that rounding of the logged inputs and outputs can explain.
    w = 2 * err: err moves with yaw (Q) and with position (up to sqrt(2) * Q / dist).
    v on the SLOW slope moves 1.1 m/s per metre of min_range (Q)."""
    tol_w = nav.W_GAIN * (Q + math.sqrt(2) * Q / dist) + Q
    tol_v = (nav.V_MAX / 0.20) * Q + Q
    return tol_v + 1e-9, tol_w + 1e-9


def check(path):
    rows = mismatches = 0
    with open(path, newline='') as f:
        for r in csv.DictReader(f):
            x, y, yaw = float(r['x']), float(r['y']), float(r['yaw'])
            wp_i, mr = int(r['wp_i']), float(r['min_range'])
            dist, _, turning, w = nav.heading(x, y, yaw, nav.WAYPOINTS[wp_i])
            mode, v = nav.a1_float_speed(mr)
            if turning:
                v = 0.0
            tol_v, tol_w = tolerances(dist)
            ok = (mode == r['mode'] and abs(v - float(r['v'])) <= tol_v
                  and abs(w - float(r['w'])) <= tol_w)
            rows += 1
            mismatches += not ok
            if not ok and mismatches <= 3:
                print(f'  {path.name}: t={r["t"]} logged v={r["v"]} w={r["w"]} {r["mode"]}, '
                      f'nav v={v:.4f} w={w:.4f} {mode}')
    return rows, mismatches


def main():
    data = Path(__file__).resolve().parents[1] / 'data' / 'a1'
    total = bad = 0
    for n in RUNS:
        rows, mm = check(data / f'run_{n}.csv')
        print(f'run_{n}: {rows} rows, {mm} mismatches')
        total += rows
        bad += mm
    ranges = [float('inf')] * 360
    ranges[3], ranges[200], ranges[358] = 0.5, 0.05, 0.3   # 200 is outside, 0.05 is invalid
    assert nav.front_min(ranges) == 0.3
    assert math.isinf(nav.front_min([float('inf')] * 360))
    print(f'total {total} rows, {bad} mismatches')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
