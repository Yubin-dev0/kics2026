#!/usr/bin/env python3
"""A0: pick the detector metric and thresholds from the B3 captures (2026-09-23).

    python3 analysis/a0_select.py [--no-load 1 2 3 4] [--load 5 7 8]

Replays every B3 windows file with `capture/detector.py --replay-windows` logic over the
four metrics (med, p90, mad, pair) and a ladder of theta_high values (theta_low =
theta_high / 2), and applies the selection rule fixed before the D19 data (9/23 13:30):

  1. no-load runs: no entry, with theta_high >= 20 ms;
  2. every loaded (D19) run enters;
  3. among the survivors, the smallest median A (first entry minus t0); tie -> med.

Fallback, also fixed in advance: if nothing passes rule 2, relax it to 2 of 3 runs; if
still nothing, keep med 20/10 and report the miss.

Windows files are trimmed to the last window in which the robot flow was live (rule 1 of
the judging window, capture/README.md), so entries in the post-GOAL tail do not count.
A is on N3's clock (t0_ns in the N3 meta), so no offset is involved.

The original script ran from /tmp on 9/23 (output /tmp/a0_result.txt) and was lost with
it; this is the same rule rewritten against the repository files. It reproduces the 9/23
result: p90 20/10, A = 1138 / 2217 / 1720 ms (median 1720).
"""
import argparse
import csv
import io
import json
import os
import statistics
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "capture"))
import detector  # noqa: E402

METRICS = ("med", "p90", "mad", "pair")
THETAS = (10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0)


def trimmed_windows(path):
    """Rows up to the last window with >= FLOW_MIN_PKTS packets each way."""
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
        fields = f.fieldnames if hasattr(f, "fieldnames") else None
    last = None
    for i, r in enumerate(rows):
        if int(r["n_up"] or 0) >= detector.FLOW_MIN_PKTS and int(r["n_down"] or 0) >= detector.FLOW_MIN_PKTS:
            last = i
    rows = rows[: (last + 1) if last is not None else 0]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue(), len(rows)


def replay(csv_text, metric, th, tl):
    tmp = os.path.join(HERE, "out", "_a0_tmp.csv")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", newline="") as f:
        f.write(csv_text)
    a = types.SimpleNamespace(metric=metric, dir="both", interval_s=detector.INTERVAL_S,
                              baseline_s=detector.BASELINE_S, theta_high_ms=th, theta_low_ms=tl)
    out = io.StringIO()
    old = sys.stdout
    sys.stdout = out
    try:
        first, enters, leaves = detector.replay_windows(tmp, a)
    finally:
        sys.stdout = old
    return first, enters, leaves


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", default="b3")
    ap.add_argument("--no-load", type=int, nargs="+", default=[1, 2, 3, 4])
    ap.add_argument("--load", type=int, nargs="+", default=[5, 7, 8])
    args = ap.parse_args()
    d = os.path.join(REPO, "data", args.stage)

    runs = {}
    for r in args.no_load + args.load:
        text, n = trimmed_windows(os.path.join(d, f"n3_run_{r}_windows.csv"))
        meta = json.load(open(os.path.join(d, f"n3_run_{r}_meta.json")))
        runs[r] = (text, n, meta.get("t0_ns"))
        print(f"run {r}: {n} windows while the flow was live, t0 {'set' if meta.get('t0_ns') else 'none'}")

    table = []
    for m in METRICS:
        for th in THETAS:
            tl = th / 2
            false_entries = sum(replay(runs[r][0], m, th, tl)[1] for r in args.no_load)
            a_ms, entered = [], 0
            for r in args.load:
                first, enters, _ = replay(runs[r][0], m, th, tl)
                if first is not None and runs[r][2]:
                    entered += 1
                    a_ms.append((first[0] - runs[r][2]) / 1e6)
            table.append({"metric": m, "theta": th, "false": false_entries, "entered": entered,
                          "a_ms": a_ms, "a_med": statistics.median(a_ms) if a_ms else None})

    print("\nmetric theta_h  no-load entries  loaded runs entered  A (ms)")
    for t in table:
        a = " / ".join(f"{x:.0f}" for x in t["a_ms"]) or "-"
        print(f"{t['metric']:5s} {t['theta']:5.0f}    {t['false']:2d}                {t['entered']}/{len(args.load)}"
              f"                  {a}")

    def pick(need):
        ok = [t for t in table if t["theta"] >= 20 and t["false"] == 0 and t["entered"] >= need]
        if not ok:
            return None
        return sorted(ok, key=lambda t: (t["a_med"], METRICS.index(t["metric"])))[0]

    chosen = pick(len(args.load))
    how = "rule 2 (all loaded runs enter)"
    if chosen is None:
        chosen = pick(len(args.load) - 1)
        how = "fallback: 2 of 3 loaded runs"
    if chosen is None:
        print("\nPICK: nothing passes; keep med 20/10 and report the miss")
        return 1
    print(f"\nPICK: {chosen['metric']} theta {chosen['theta']:.0f}/{chosen['theta']/2:.0f} via {how}, "
          f"A {' / '.join(f'{x:.0f}' for x in chosen['a_ms'])} ms (median {chosen['a_med']:.0f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
