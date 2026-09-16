#!/usr/bin/env python3
"""Append one finished run to data/runs.csv from its meta and log files.

Usage:
  python3 analysis/append_run.py A1 13
  python3 analysis/append_run.py A2 3
  python3 analysis/append_run.py A2 2 --status discarded --note "firmware before the DMA fix"
  python3 analysis/append_run.py A2 1 --status discarded --date 2026-09-16 --note "aborted, no meta"

Stages:
  A1        simulator runs (sim/a1_controller.py): data/a1/run_N.csv with columns
            t,x,y,yaw,min_range,wp_i,mode,v,w and run_N_meta.json.
  A2, A3    serial bench runs (fw/tools/a2_bench.py): data/a2 or data/a3, run_N.csv with
            one row per line and run_N_meta.json with results and verdict.
            The ledger columns stay the same as for A1; bench figures go into
            'result' (PASS / FAIL / ABORTED) and a compact 'note'.

Status: A1 rows are 'valid', or 'check' when the source was dirty. Bench rows are 'valid'
when the run passed on clean source, 'check' when it passed on dirty source, and
'discarded' otherwise. --status overrides this. A run already in the ledger is refused
unless --force is given.
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(REPO, 'data', 'runs.csv')

FIELDS = ['run_id', 'stage', 'date', 'node', 'course', 'clearance_m',
          'sector_half_deg', 'v_slow_floor', 'git', 'result', 'duration_s',
          'waypoints', 'collisions', 'slow_steps', 'stop_steps',
          'min_range_m', 'status', 'note']

BENCH_STAGES = ('A2', 'A3')
STATUSES = ('valid', 'check', 'discarded', 'partial', 'overwritten')


def summarize(csv_path):
    """A1 log: counts per mode, minimum range, last row."""
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


def git_label(meta):
    commit = meta.get('git') or 'none'
    return commit + ('-DIRTY' if meta.get('git_dirty') else '')


def a1_row(run_id, stage, meta, d):
    modes, min_r, last = summarize(os.path.join(d, f'run_{run_id}.csv'))
    return {
        'run_id': run_id,
        'stage': stage,
        'date': meta['started'][:10],
        'node': 'N1',
        'git': git_label(meta),
        'sector_half_deg': meta.get('sector_half_deg', ''),
        'v_slow_floor': meta.get('v_slow_floor', ''),
        'duration_s': last[0] if last else '',
        'slow_steps': modes.get('SLOW', 0),
        'stop_steps': modes.get('STOP', 0),
        'min_range_m': f'{min_r:.4f}' if min_r is not None else '',
        'status': 'valid' if not meta['git_dirty'] else 'check',
        'note': '' if not meta['git_dirty'] else 'uncommitted source at run time',
    }


def bench_note(meta):
    res = meta.get('results', {})
    paced, sweep, switch, wdog = (res.get(k, {}) for k in ('paced', 'sweep', 'switch', 'wdog'))
    lost = sum(x.get('lost', 0) or 0 for x in (paced, sweep, switch))
    sent = sum(x.get('sent', 0) or 0 for x in (paced, sweep, switch))
    parts = []
    if 'switch_us_median' in switch:
        parts.append(f"C med {switch['switch_us_median']} max {switch['switch_us_max']} us")
    if 'rtt_ms_median' in paced:
        parts.append(f"rtt med {paced['rtt_ms_median']} p99 {paced['rtt_ms_p99']} ms")
    if sent:
        parts.append(f"lost {lost}/{sent}")
    if res.get('bad_lines_delta') is not None:
        parts.append(f"bad +{res['bad_lines_delta']}")
    if wdog.get('gap_ms') is not None:
        parts.append(f"wdog {wdog['gap_ms']} ms")
    fw = meta.get('firmware', {})
    if fw:
        parts.append(f"fw v{fw.get('proto_version')} {fw.get('build_id')}")
    if meta.get('target') and meta['target'] != 'board':
        parts.append(f"target {meta['target']}")
    return '; '.join(parts)


def bench_row(run_id, stage, meta):
    if meta.get('aborted'):
        result = 'ABORTED'
    else:
        result = 'PASS' if meta.get('pass') else 'FAIL'
    duration = ''
    try:
        t0 = datetime.fromisoformat(meta['started'])
        t1 = datetime.fromisoformat(meta['finished'])
        duration = f'{(t1 - t0).total_seconds():.0f}'
    except (KeyError, ValueError, TypeError):
        pass
    if result == 'PASS':
        status = 'check' if meta.get('git_dirty') else 'valid'
    else:
        status = 'discarded'
    return {
        'run_id': run_id,
        'stage': stage,
        'date': meta['started'][:10],
        'node': 'N1+N2' if meta.get('target', 'board') == 'board' else 'N1',
        'git': git_label(meta),
        'result': result,
        'duration_s': duration,
        'status': status,
        'note': bench_note(meta),
    }


def already_listed(stage, run_id):
    if not os.path.exists(LEDGER):
        return False
    with open(LEDGER, newline='') as f:
        return any(r.get('stage') == stage and r.get('run_id') == run_id
                   for r in csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('stage')
    ap.add_argument('run_id')
    ap.add_argument('--status', choices=STATUSES, help='override the derived status')
    ap.add_argument('--note', default='', help='text added in front of the derived note')
    ap.add_argument('--date', help='YYYY-MM-DD, required when the run has no meta file')
    ap.add_argument('--force', action='store_true', help='append even if the run is already listed')
    args = ap.parse_args()

    stage, run_id = args.stage.upper(), args.run_id
    d = os.path.join(REPO, 'data', stage.lower())
    meta_path = os.path.join(d, f'run_{run_id}_meta.json')

    if already_listed(stage, run_id) and not args.force:
        sys.exit(f'{stage} run {run_id} is already in data/runs.csv (use --force to add it again)')

    row = {k: '' for k in FIELDS}
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
        if stage in BENCH_STAGES:
            row.update(bench_row(run_id, stage, meta))
        else:
            row.update(a1_row(run_id, stage, meta, d))
    else:
        # a run that stopped before writing its meta file: record it by hand
        if not (args.status and args.date and args.note):
            sys.exit(f'{meta_path} not found; give --status, --date and --note to record it anyway')
        row.update({'run_id': run_id, 'stage': stage, 'date': args.date,
                    'node': 'N1+N2' if stage in BENCH_STAGES else 'N1',
                    'result': 'ABORTED' if stage in BENCH_STAGES else ''})

    if args.status:
        row['status'] = args.status
    if args.note:
        row['note'] = args.note + ('; ' + row['note'] if row['note'] else '')

    new_file = not os.path.exists(LEDGER)
    with open(LEDGER, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(row)

    if stage in BENCH_STAGES:
        print(f'appended {stage} run {run_id}: {row["result"]} {row["status"]} | {row["note"]}')
    else:
        print(f'appended run {run_id}: SLOW={row["slow_steps"]} '
              f'STOP={row["stop_steps"]} min={row["min_range_m"]}')


if __name__ == '__main__':
    main()
