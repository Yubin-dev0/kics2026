#!/usr/bin/env python3
"""Writes a sweep file of invented numbers in the real schema, so the figure scripts can
be written and finished before the sweep of 2026-09-26 exists.

  python3 analysis/make_synthetic.py

The values are NOT measurements and may not appear in the paper, in slides, or in any
message that could be mistaken for a result. What is real here is the shape: the column
names, the units, the 66 rows of the design (plan 5.2), which cells are empty for which
policy. A figure script that reads data/synthetic/sweep.csv and draws figure 2(a), figure
2(b) and table 1 will read data/sweep.csv on 9/26 with no change but the path.

The invented numbers follow the mechanism the plan expects, so a wrong axis or a swapped
series is visible as a wrong picture rather than as noise:

  A   metadata detection, near the queue, roughly independent of the base RTT
  D   RTT window detection, later than A by about a second (2 s window, plan 4.3), and
      growing slowly with the base RTT, because the window cannot see a degraded sample
      until one comes back
  B   the flag travels the degraded link, so it grows with the base RTT
  U   A3 constant, C the board's switch (A2), both flat
  G   D - (A + B + U + C), positive across the range here. Whether G rises or falls with
      the base RTT is exactly what table 1 is for and is NOT settled by this file: B grows
      with RTT, which shrinks G, but D grows too, which does the opposite. Plan 2.3
      assumes the first effect wins; the sweep decides
  N_col   0 for policy 1, worst for policy 2, policy 4 below policy 3

Nothing here decides the paper. If the sweep contradicts this shape, the sweep is right.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sweep_index import RTT_STEPS, U_MS_A3, check, write   # noqa: E402

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / 'data' / 'synthetic' / 'sweep.csv'
POLICY_NAMES = {1: 'always_local', 2: 'always_edge', 3: 'rtt_window', 4: 'n3_flag'}
REPS = 3
SEED = 20260920

# invented run outcomes per (policy, base RTT): collisions and edge-mode STOP steps
N_COL = {1: [0, 0, 0, 0, 0], 2: [0, 0, 1, 2, 4], 3: [0, 0, 0, 1, 2], 4: [0, 0, 0, 0, 1]}
EDGE_STOP = {1: [0, 0, 0, 0, 0], 2: [0, 2, 9, 21, 48], 3: [0, 1, 4, 9, 17],
             4: [0, 1, 2, 5, 9]}


def jitter(rng, v, frac=0.08):
    return v * (1.0 + rng.uniform(-frac, frac))


def make_row(rng, run_id, policy, rtt_ms, load, rep):
    i = RTT_STEPS.index(rtt_ms)
    # rounded first, then G from the rounded values, so the file is consistent with
    # itself (sweep_index.check recomputes G from the columns it reads)
    a = round(jitter(rng, 420.0 + 6.0 * i), 1)       # metadata detection after t0
    d = round(jitter(rng, 1080.0 + 25.0 * i), 1)     # RTT window detection after t0
    b = round(jitter(rng, rtt_ms / 2.0 + 4.0), 1) if policy == 4 else None
    c = round(jitter(rng, 0.034, 0.05), 4)
    u = U_MS_A3
    g = None if b is None else round(d - (a + b + u + c), 2)
    n_col = N_COL[policy][i] if load == 'L1' else 0
    if n_col and rng.random() < 0.3:                 # repetitions do not all agree
        n_col += rng.choice([-1, 1])
        n_col = max(n_col, 0)
    if load == 'L2':                                 # short load: false switches only
        n_sw = {3: rng.choice([2, 3, 4]), 4: rng.choice([0, 0, 1])}[policy]
    elif policy == 3:
        n_sw = rng.choice([2, 3, 4]) if i >= 2 else rng.choice([0, 1, 2])
    elif policy == 4:
        n_sw = rng.choice([2, 3]) if i >= 1 else rng.choice([0, 1, 2])
    else:
        n_sw = 0
    m_rate = [0.0, 0.02, 0.83, 1.0, 1.0][i]
    return {
        'run_id': run_id, 'stage': 'C3', 'date': '2026-09-26',
        'policy': policy, 'policy_name': POLICY_NAMES[policy],
        'rtt_ms': rtt_ms, 'load': load, 'rep': rep,
        'status': 'valid', 'result': 'GOAL',
        'a_ms': a, 'd_ms': d, 'b_ms': b, 'u_ms': u, 'c_ms': c, 'g_ms': g,
        'n_col': n_col, 'n_sw': n_sw,
        'm_rate': round(jitter(rng, m_rate, 0.02) if m_rate else 0.0, 4),
        'hold_rate': round(jitter(rng, 0.004 + 0.002 * i, 0.4), 4),
        'rtt_ms_median': round(jitter(rng, rtt_ms + 1.2, 0.03), 3),
        'edge_stop_steps': EDGE_STOP[policy][i] if load == 'L1' else 0,
        'local_steps': {1: 1180, 2: 0}.get(policy, rng.randint(120, 640)),
        'duration_s': round(jitter(rng, 48.5, 0.04), 2),
        'min_range_m': round(jitter(rng, 0.27 if not n_col else 0.14, 0.06), 4),
        'delta_ms': round(jitter(rng, -2.4, 0.3), 3),
        'git': 'synthetic',
        'note': 'SYNTHETIC, not a measurement',
    }


def main():
    rng = random.Random(SEED)
    rows, run_id = [], 0
    for rtt in RTT_STEPS:                            # 5 x 4 x 3 = 60 runs of L1
        for policy in (1, 2, 3, 4):
            for rep in range(1, REPS + 1):
                run_id += 1
                rows.append(make_row(rng, run_id, policy, rtt, 'L1', rep))
    for policy in (3, 4):                            # 1 x 2 x 3 = 6 runs of L2
        for rep in range(1, REPS + 1):
            run_id += 1
            rows.append(make_row(rng, run_id, policy, 60.0, 'L2', rep))
    write(rows, OUT)
    print(f'{len(rows)} synthetic rows -> {OUT.relative_to(REPO)}')
    raise SystemExit(0 if check(OUT) else 1)


if __name__ == '__main__':
    main()
