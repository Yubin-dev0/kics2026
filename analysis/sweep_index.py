#!/usr/bin/env python3
"""The sweep index: one row per run, the file the figures and table 1 are drawn from.

A run log (data/<stage>/run_N.csv) is a time series of one run. The paper's figures cut
across runs instead: figure 2(a) plots detection delay against base RTT, figure 2(b)
plots collisions per policy against base RTT, and table 1 lists the timing chain at three
conditions. This file is that cut: data/sweep.csv, one row per run, with the condition
the run was executed under and the figures derived from it.

  python3 analysis/sweep_index.py --stages C3 B3 -o data/sweep.csv
  python3 analysis/sweep_index.py --check data/synthetic/sweep.csv

Where the columns come from. The N1 side (everything the bridge already writes) is filled
by this script from run_N_meta.json. The N3 side (a_ms, the clock offset delta_ms, and
therefore d_ms, b_ms and g_ms, which are measured from t0 on N3's clock) is filled by the
C3 merge on 2026-09-26, when the N3 logs exist; until then those cells are empty in real
data. The synthetic set (analysis/make_synthetic.py) fills every column so the figure
scripts can be finished before the sweep runs.

Reading the timing chain (plan 4.5): A is when the metadata detector saw the degradation,
D is when the RTT window saw it, both measured from t0, the moment N3 started the load.
B is the flag's trip to N1, U the USB path to the board (A3 constant), C the board's mode
switch (A2, around 34 us). G = D - (A + B + U + C) is what is left of the head start.
"""
import argparse
import csv
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# The contract. Changing a name here changes the figure scripts, so it is changed once,
# with both owners told (repository ground rules).
SCHEMA = [
    # what the run was
    'run_id', 'stage', 'date', 'policy', 'policy_name', 'rtt_ms', 'load', 'rep',
    'status', 'result',
    # timing chain, milliseconds (plan 4.5); a_ms, d_ms, b_ms, g_ms need the C3 merge
    'a_ms', 'd_ms', 'b_ms', 'u_ms', 'c_ms', 'g_ms',
    # outcome of the run
    'n_col', 'n_sw', 'm_rate', 'hold_rate', 'rtt_ms_median',
    'edge_stop_steps', 'local_steps', 'duration_s', 'min_range_m',
    # provenance
    'delta_ms', 'git', 'note',
]

RTT_STEPS = (10.0, 30.0, 60.0, 100.0, 200.0)   # plan 5.1
LOADS = ('none', 'L1', 'L2')
POLICIES = (1, 2, 3, 4)
U_MS_A3 = 3.5          # A3 p99, idle (fw/NOTES.md); the C3 merge may use 2.8 under load


def row_from_meta(meta):
    """One sweep row from an N1 run meta file. N3 columns are left empty."""
    r = meta.get('results', {})
    cond = meta.get('condition') or {}
    c_us = r.get('switch_us_median')
    return {
        'run_id': meta.get('run_id'),
        'stage': meta.get('stage'),
        'date': (meta.get('started') or '')[:10],
        'policy': meta.get('policy'),
        'policy_name': meta.get('policy_name'),
        'rtt_ms': cond.get('rtt_ms'),
        'load': cond.get('load'),
        'rep': cond.get('rep'),
        'status': ('valid' if meta.get('pass') and not meta.get('git_dirty')
                   else 'check' if meta.get('pass') else 'discarded'),
        'result': r.get('result'),
        'a_ms': None, 'd_ms': None, 'b_ms': None,
        'u_ms': U_MS_A3,
        'c_ms': None if c_us is None else round(c_us / 1000.0, 4),
        'g_ms': None,
        'n_col': r.get('n_col'),
        'n_sw': r.get('n_sw_delta'),
        'm_rate': r.get('miss_rate_steps'),
        'hold_rate': r.get('hold_rate'),
        'rtt_ms_median': r.get('rtt_ms_median'),
        'edge_stop_steps': r.get('edge_stop_steps'),
        'local_steps': r.get('local_steps'),
        'duration_s': r.get('duration_sim_s'),
        'min_range_m': r.get('min_range_m'),
        'delta_ms': None,
        'git': meta.get('git', '') + ('-DIRTY' if meta.get('git_dirty') else ''),
        'note': meta.get('note', ''),
    }


def build(stages, out_path):
    rows = []
    for stage in stages:
        d = REPO / 'data' / stage.lower()
        for meta_path in sorted(d.glob('run_*_meta.json'),
                                key=lambda p: int(p.name.split('_')[1])):
            rows.append(row_from_meta(json.loads(meta_path.read_text())))
    write(rows, out_path)
    print(f'{len(rows)} rows -> {out_path}')
    return rows


def write(rows, out_path):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=SCHEMA, extrasaction='ignore')
        w.writeheader()
        for r in rows:
            w.writerow({k: ('' if r.get(k) is None else r.get(k)) for k in SCHEMA})


def check(path):
    """Validates a sweep file against the contract. Run it on anything a figure script is
    about to read, real or synthetic."""
    with open(path, newline='') as f:
        rdr = csv.DictReader(f)
        header, rows = rdr.fieldnames, list(rdr)
    bad = []
    if header != SCHEMA:
        missing = [c for c in SCHEMA if c not in (header or [])]
        extra = [c for c in (header or []) if c not in SCHEMA]
        bad.append(f'header differs: missing {missing}, extra {extra}')
    seen = set()
    for i, r in enumerate(rows, 2):
        key = (r.get('stage'), r.get('run_id'))
        if key in seen:
            bad.append(f'line {i}: duplicate run {key}')
        seen.add(key)
        if r.get('policy') and int(r['policy']) not in POLICIES:
            bad.append(f"line {i}: policy {r['policy']}")
        if r.get('load') and r['load'] not in LOADS:
            bad.append(f"line {i}: load {r['load']}")
        if r.get('rtt_ms') and float(r['rtt_ms']) not in RTT_STEPS:
            bad.append(f"line {i}: rtt_ms {r['rtt_ms']} is not a sweep step")
        chain = [r.get(k) for k in ('a_ms', 'b_ms', 'u_ms', 'c_ms', 'd_ms', 'g_ms')]
        if all(v not in ('', None) for v in chain):
            a, b, u, c, d, g = (float(v) for v in chain)
            if abs(g - (d - (a + b + u + c))) > 0.2:
                bad.append(f"line {i}: g_ms {g} is not d - (a+b+u+c)")
    print(f'{path}: {len(rows)} rows, {len(bad)} problems')
    for m in bad[:20]:
        print(f'  {m}')
    return not bad


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--stages', nargs='+', default=['C3'])
    ap.add_argument('-o', '--out', default=str(REPO / 'data' / 'sweep.csv'))
    ap.add_argument('--check', metavar='FILE', help='validate a sweep file and exit')
    args = ap.parse_args()
    if args.check:
        raise SystemExit(0 if check(args.check) else 1)
    build(args.stages, args.out)
    raise SystemExit(0 if check(args.out) else 1)


if __name__ == '__main__':
    main()
