#!/usr/bin/env python3
"""Append one finished run to data/runs.csv from its meta and log files.

Usage:
  python3 analysis/append_run.py A1 13
  python3 analysis/append_run.py A2 3
  python3 analysis/append_run.py A2 2 --status discarded --note "firmware before the DMA fix"
  python3 analysis/append_run.py A2 1 --status discarded --date 2026-09-16 --note "aborted, no meta"
  python3 analysis/append_run.py B1 1

Stages:
  A1        simulator runs (sim/a1_controller.py): data/a1/run_N.csv with columns
            t,x,y,yaw,min_range,wp_i,mode,v,w and run_N_meta.json.
  A2, A3    serial bench runs (fw/tools/a2_bench.py): data/a2 or data/a3, run_N.csv with
            one row per line and run_N_meta.json with results and verdict.
            The ledger columns stay the same as for A1; bench figures go into
            'result' (PASS / FAIL / ABORTED) and a compact 'note'.
  A4, A6, A8
            N3 round-trip runs (net/ping_run.py): data/a4, a6 or a8, run_N.log (raw ping)
            and run_N_meta.json. Median, p99, loss, gaps and the set base RTT (A6) or the
            load and its rise (A8) go into 'note'; 'result' is PASS / FAIL.
  A10, A5   edge probes (python3 -m bridge.test_edge --probe ... --run N): run_N.csv with
            one row per datagram and run_N_meta.json. A5 is the same probe through the
            WireGuard tunnel. The path, round trip and N4 process time go into 'note'.
  B1, A10, B3, C1, C3
            bridge driving runs (sim/bridge/node.py, sim/bridge/dry_run.py): data/<stage>/
            run_N.csv with one row per UART line and run_N_meta.json with results and
            verdict. Driving columns are filled as for A1; loop, link and board figures go
            into 'note'.

Status: A1 rows are 'valid', or 'check' when the source was dirty. Bench and bridge rows
are 'valid' when the run passed on clean source, 'check' when it passed on dirty source,
and 'discarded' otherwise. --status overrides this. A run already in the ledger is refused
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
BRIDGE_STAGES = ('B1', 'A10', 'B3', 'C1', 'C3')
# world file of A1 runs 10-12: 3x3 course, obstacles 0.42 m from the path
KNOWN_WORLDS = {'6b66d107282a': ('3x3', '0.42')}
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


def bridge_note(meta):
    r = meta.get('results', {})
    parts = [f"policy {meta.get('policy')}"]
    if meta.get('target') != 'board':
        parts.append(f"target {meta.get('target')}")
    e = meta.get('edge') or {}
    if e.get('edge') not in (None, 'none'):
        parts.append(f"edge {e.get('target')} rtt med {e.get('rtt_ms_median')} "
                     f"p99 {e.get('rtt_ms_p99')} ms M {e.get('miss_rate_steps')} "
                     f"holds {e.get('holds')}/{e.get('steps')}")
        parts.append(f"local steps {r.get('local_steps')} edge-STOP steps "
                     f"{r.get('edge_stop_steps')} t_det_rtt {r.get('t_det_rtt_s')} s")
    if r.get('tx_dev_p99_ms') is not None:
        parts.append(f"jitter p99 {r['tx_dev_p99_ms']} max {r['tx_dev_max_ms']} "
                     f"sd {r['tx_sd_ms']} ms skips {r['tx_skips']}")
    if r.get('sim_step_median_s') is not None:
        parts.append(f"sim step {r['sim_step_median_s']} s repeats {r['sim_step_repeats']}")
    if r.get('u_bound_ms_p99') is not None:
        parts.append(f"U<= med {r['u_bound_ms_median']} p99 {r['u_bound_ms_p99']} ms")
    parts.append(f"lost {r.get('lost')}/{r.get('lines')}")
    if r.get('bad_lines_delta') is not None:
        parts.append(f"bad +{r['bad_lines_delta']}")
    parts.append(f"wdog in run {r.get('wdog_during_run')}")
    if r.get('wdog_end_gap_ms') is not None:
        parts.append(f"end wdog {r['wdog_end_gap_ms']} ms")
    if r.get('bridge_stops'):
        parts.append(f"bridge stops {r['bridge_stops']}")
    st = r.get('settle') or {}
    if st.get('switch_us'):
        parts.append(f"settle C {st['switch_us']} us")
    if r.get('switches'):
        parts.append(f"C med {r.get('switch_us_median')} max {r.get('switch_us_max')} us "
                     f"over {r['switches']}")
    w = meta.get('windows') or {}
    if meta.get('target') == 'board':
        parts.append(f"power {w.get('power_mode_ac')} ac {w.get('on_ac_power')} "
                     f"({meta.get('power_check')})")
    fw = meta.get('firmware') or {}
    if fw:
        parts.append(f"fw v{fw.get('proto_version')} {fw.get('build_id')}")
    failed = [k for k, v in (meta.get('verdict') or {}).items() if not v]
    if failed:
        parts.append('failed ' + '/'.join(failed))
    if meta.get('aborted'):
        parts.append(f"aborted: {meta['aborted']}")
    return '; '.join(parts)


def bridge_row(run_id, stage, meta):
    r = meta.get('results', {})
    course, clearance = KNOWN_WORLDS.get(meta.get('world_sha1'), ('', ''))
    passed = bool(meta.get('pass'))
    states = r.get('states') or {}
    dur = r.get('duration_sim_s')
    return {
        'run_id': run_id,
        'stage': stage,
        'date': meta['started'][:10],
        'node': 'N1+N2' if meta.get('target') == 'board' else 'N1',
        'course': course,
        'clearance_m': clearance,
        'sector_half_deg': 48,
        'v_slow_floor': '0.10',
        'git': git_label(meta),
        'result': r.get('result') or ('ABORTED' if meta.get('aborted') else ''),
        'duration_s': '' if dur is None else f'{dur:.2f}',
        'waypoints': r.get('waypoints_reached', ''),
        'collisions': int(bool(r.get('collided'))) if r.get('result') else '',
        'slow_steps': states.get('SLOW', ''),
        'stop_steps': states.get('STOP', ''),
        'min_range_m': '' if r.get('min_range_m') is None else f"{r['min_range_m']:.4f}",
        'status': ('check' if meta.get('git_dirty') else 'valid') if passed else 'discarded',
        'note': ('PASS; ' if passed else 'FAIL; ') + bridge_note(meta),
    }


def _status(meta):
    passed = bool(meta.get('pass'))
    return ('check' if meta.get('git_dirty') else 'valid') if passed else 'discarded'


def ping_row(run_id, stage, meta):
    """A4 / A6: net/ping_run.py."""
    r = meta.get('results', {})
    parts = [f"-> {meta.get('target')}", f"{r.get('received')}/{r.get('sent')} replies",
             f"median {r.get('rtt_ms_median')} p99 {r.get('rtt_ms_p99')} "
             f"max {r.get('rtt_ms_max')} ms", f"loss {r.get('loss_pct')}%",
             f"longest gap {r.get('longest_gap_s')} s", f"gaps>=150ms {r.get('gaps_ge_150ms')}"]
    if meta.get('set_ms') is not None:
        parts.append(f"set {meta['set_ms']} ms" + (
            f" added {r.get('added_ms')} ms ({r.get('added_error_pct'):+}%)"
            if r.get('added_ms') is not None else ' (reference)'))
    if meta.get('load'):
        lr = meta.get('load_run') or {}
        d = r.get('during') or {}
        parts.append(f"load {meta['load']} {lr.get('proto')} {lr.get('rate')} at {meta.get('load_at_s')} s, "
                     f"during median {d.get('rtt_ms_median')} p99 {d.get('rtt_ms_p99')} ms, "
                     f"rise {r.get('rise_ms')} ms over reference {r.get('base_median_ms')} ms")
    failed = [k for k, v in (meta.get('verdict') or {}).items() if not v]
    if failed:
        parts.append('failed ' + '/'.join(failed))
    if meta.get('aborted'):
        parts.append(f"aborted: {meta['aborted']}")
    node = {'A4': 'N1+N3', 'A8': 'N1+N3+N4+N5'}.get(stage, 'N1+N3+N4')
    return {'run_id': run_id, 'stage': stage, 'date': meta['started'][:10],
            'node': node, 'git': git_label(meta),
            'result': 'PASS' if meta.get('pass') else 'FAIL', 'status': _status(meta),
            'note': ('PASS; ' if meta.get('pass') else 'FAIL; ') + '; '.join(parts)}


def probe_row(run_id, stage, meta):
    """A10 / A5: bridge.test_edge --probe."""
    e = meta.get('edge', {})
    parts = [f"path {meta.get('path')} -> {meta.get('target')}",
             f"{e.get('replied')}/{meta.get('count')} answered",
             f"rtt med {e.get('rtt_ms_median')} p99 {e.get('rtt_ms_p99')} "
             f"max {e.get('rtt_ms_max')} ms",
             f"bad {e.get('bad')} echo mismatch {e.get('echo_mismatch')}",
             f"N4 proc p99 {meta.get('server_proc_p99_us')} us"]
    w = meta.get('windows') or {}
    parts.append(f"power {w.get('power_mode_ac')} ac {w.get('on_ac_power')}")
    failed = [k for k, v in (meta.get('verdict') or {}).items() if v is False]
    if failed:
        parts.append('failed ' + '/'.join(failed))
    node = {'direct': 'N1+N4', 'n3': 'N1+N3+N4', 'tunnel': 'N1+N3+N4'}.get(meta.get('path'),
                                                                        'N1+N4')
    return {'run_id': run_id, 'stage': stage, 'date': meta['started'][:10], 'node': node,
            'git': git_label(meta), 'result': 'PASS' if meta.get('pass') else 'FAIL',
            'status': _status(meta),
            'note': ('PASS; ' if meta.get('pass') else 'FAIL; ') + '; '.join(parts)}


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
        if meta.get('harness') == 'ping':
            row.update(ping_row(run_id, stage, meta))
        elif meta.get('harness') == 'probe':
            row.update(probe_row(run_id, stage, meta))
        elif stage in BENCH_STAGES:
            row.update(bench_row(run_id, stage, meta))
        elif stage in BRIDGE_STAGES:
            row.update(bridge_row(run_id, stage, meta))
        else:
            row.update(a1_row(run_id, stage, meta, d))
    else:
        # a run that stopped before writing its meta file: record it by hand
        if not (args.status and args.date and args.note):
            sys.exit(f'{meta_path} not found; give --status, --date and --note to record it anyway')
        row.update({'run_id': run_id, 'stage': stage, 'date': args.date,
                    'node': 'N1+N2' if stage in BENCH_STAGES + BRIDGE_STAGES else 'N1',
                    'result': 'ABORTED' if stage in BENCH_STAGES + BRIDGE_STAGES else ''})

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

    if row.get('result') or stage in BENCH_STAGES + BRIDGE_STAGES:
        print(f'appended {stage} run {run_id}: {row["result"]} {row["status"]} | {row["note"]}')
    else:
        print(f'appended run {run_id}: SLOW={row["slow_steps"]} '
              f'STOP={row["stop_steps"]} min={row["min_range_m"]}')


if __name__ == '__main__':
    main()
