#!/usr/bin/env python3
"""Append one finished run to data/runs.csv from its meta and log files.

Usage:  python3 analysis/append_run.py A1 13
"""
import csv, json, os, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(REPO, 'data', 'runs.csv')

FIELDS = ['run_id', 'stage', 'date', 'node', 'course', 'clearance_m',
          'sector_half_deg', 'v_slow_floor', 'git', 'result', 'duration_s',
          'waypoints', 'collisions', 'slow_steps', 'stop_steps',
          'min_range_m', 'status', 'note']


def summarize(csv_path):
    modes, min_r, last = {}, None, None
    with open(csv_path) as f:
        for row in csv.reader(f):
            if not row or row[0] == 't':
                continue
            modes[row[6]] = modes.get(row[6], 0) + 1
            try:
                r = float(row[4])
                min_r = r if min_r is None else min(min_r, r)
            except (ValueError, IndexError):
                pass
            last = row
    return modes, min_r, last


def main():
    stage, run_id = sys.argv[1], sys.argv[2]
    d = os.path.join(REPO, 'data', stage.lower())
    meta = json.load(open(os.path.join(d, f'run_{run_id}_meta.json')))
    modes, min_r, last = summarize(os.path.join(d, f'run_{run_id}.csv'))

    row = {k: '' for k in FIELDS}
    row.update({
        'run_id': run_id,
        'stage': stage,
        'date': meta['started'][:10],
        'node': 'N1',
        'git': meta['git'] + ('-DIRTY' if meta['git_dirty'] else ''),
        'sector_half_deg': meta.get('sector_half_deg', ''),
        'v_slow_floor': meta.get('v_slow_floor', ''),
        'duration_s': last[0] if last else '',
        'slow_steps': modes.get('SLOW', 0),
        'stop_steps': modes.get('STOP', 0),
        'min_range_m': f'{min_r:.4f}' if min_r is not None else '',
        'status': 'valid' if not meta['git_dirty'] else 'check',
        'note': '' if not meta['git_dirty'] else 'uncommitted source at run time',
    })

    with open(LEDGER, 'a', newline='') as f:
        csv.DictWriter(f, fieldnames=FIELDS).writerow(row)
    print(f'appended run {run_id}: SLOW={row["slow_steps"]} '
          f'STOP={row["stop_steps"]} min={row["min_range_m"]}')


if __name__ == '__main__':
    main()
