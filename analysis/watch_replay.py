#!/usr/bin/env python3
"""Replays the policy 3 RTT watcher (sim/bridge/rttwatch.py) over edge probe runs and
prints what it would have done with no load: A5-3b, the revised tunnel-path criterion.

  python3 analysis/watch_replay.py data/a5/run_2.csv
  python3 analysis/watch_replay.py data/a10/run_*.csv data/a5/run_*.csv

Each probe row is one control step: t_send_ns is its time, rtt_us its round trip (empty
when unanswered, which the watcher treats as a held step). The watcher runs with the
values it uses in bridge runs (15 s baseline, 2 s window, theta_high / theta_low from
rttwatch.py), so a probe that makes it enter degraded here would also make it enter in a
no-load bridge run over the same path.

Printed per file: RTT_min of the baseline, the largest excess RTT_win - RTT_min after the
baseline and when it happened, the times at which the watcher entered degraded (none is a
pass), and the replies over 50 ms or missing (a hold beyond the first step).
"""
import csv
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'sim'))
from bridge import rttwatch     # noqa: E402

PERIOD_US = 50000               # 20 Hz control period


def replay(path):
    rows = list(csv.DictReader(open(path)))
    t0 = int(rows[0]['t_send_ns'])
    w = rttwatch.Watcher()
    worst, t_worst, enters, late = 0.0, None, [], 0
    for r in rows:
        t = int(r['t_send_ns'])
        el = (t - t0) / 1e9
        rtt = float(r['rtt_us']) if r['rtt_us'] else None
        if rtt is None or rtt > PERIOD_US:
            late += 1
        was = w.degraded
        w.observe(t, el, rtt)
        if el >= w.baseline_s and w.samples and w.rtt_min_us is not None:
            excess = sum(x for _, x in w.samples) / len(w.samples) - w.rtt_min_us
            if excess > worst:
                worst, t_worst = excess, el
        if w.degraded and not was:
            enters.append(round(el, 1))
    return {'states': len(rows), 'rtt_min_ms': round(w.rtt_min_us / 1000, 3),
            'max_excess_ms': round(worst / 1000, 3),
            'at_s': None if t_worst is None else round(t_worst, 1),
            'enters_at_s': enters, 'over_period_or_missing': late,
            'theta_high_ms': w.theta_high_us / 1000}


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for p in sys.argv[1:]:
        r = replay(p)
        verdict = 'PASS' if not r['enters_at_s'] else 'FAIL'
        print(f"{p}: {verdict} states {r['states']}, RTT_min {r['rtt_min_ms']} ms, "
              f"max excess {r['max_excess_ms']} ms at {r['at_s']} s "
              f"(theta_high {r['theta_high_ms']} ms), enters {r['enters_at_s']}, "
              f"over 50 ms or missing {r['over_period_or_missing']}")


if __name__ == '__main__':
    main()
