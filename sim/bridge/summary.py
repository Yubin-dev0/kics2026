"""Figures of one bridge run, computed from its run_N.csv.

The bridge calls this on the file it has just written, and analysis scripts can call it
again later, so the meta file never holds a number the CSV cannot reproduce.

  python3 -m bridge.summary ../data/b1/run_1.csv      (from sim/)
"""
import csv
import json
import statistics
import sys

PERIOD_MS = 50.0
JITTER_P99_MS = 5.0     # B1 criterion (integrated plan 10.2): loop period jitter < 5 ms
SKIP_MS = 75.0          # an interval this long means a whole scan went missing
U_P99_MS = 5.0          # same bound as A3-7 (fw/NOTES.md)
EDGE_DEADLINE_MS = 50.0  # one control period: a reply later than this missed its deadline
SCAN_STEP_S = 0.05      # /scan header stamps advance by one LiDAR period (20 Hz)
STATE_NAMES = {0: 'RUN', 1: 'SLOW', 2: 'STOP'}


def _i(v):
    return None if v in ('', None) else int(v)


def _f(v):
    return None if v in ('', None) else float(v)


def pct(sorted_xs, q):
    """Nearest-rank on the sorted list, the same rule as fw/tools/a2_bench.py."""
    return sorted_xs[min(len(sorted_xs) - 1, int(round(q * (len(sorted_xs) - 1))))]


def dist(xs, name, nd=3):
    if not xs:
        return {f'{name}_n': 0}
    s = sorted(xs)
    return {f'{name}_n': len(s), f'{name}_median': round(statistics.median(s), nd),
            f'{name}_p99': round(pct(s, 0.99), nd), f'{name}_max': round(s[-1], nd),
            f'{name}_min': round(s[0], nd)}


def interval_stats(ts_ns, name):
    """Intervals between consecutive stamps: mean, sigma, deviation from 50 ms, skips."""
    iv = [(b - a) / 1e6 for a, b in zip(ts_ns, ts_ns[1:])]
    if len(iv) < 2:
        return {f'{name}_n': len(iv)}
    dev = sorted(abs(x - PERIOD_MS) for x in iv)
    return {f'{name}_n': len(iv),
            f'{name}_mean_ms': round(statistics.fmean(iv), 3),
            f'{name}_sd_ms': round(statistics.stdev(iv), 3),
            f'{name}_dev_p99_ms': round(pct(dev, 0.99), 3),
            f'{name}_dev_max_ms': round(dev[-1], 3),
            f'{name}_skips': sum(x >= SKIP_MS for x in iv)}


def load(path):
    with open(path, newline='') as f:
        return list(csv.DictReader(f))


def summarize(rows):
    scan = [r for r in rows if r['kind'] == 'scan']
    flag = [r for r in rows if r['kind'] == 'flag']
    settle = [r for r in rows if r['kind'] == 'settle']
    run_lines = sorted(scan + flag, key=lambda r: int(r['line']))
    scan.sort(key=lambda r: int(r['line']))
    answered = [r for r in run_lines if r['lost'] == '0']
    last_tx = max((int(r['t_uart_tx_ns']) for r in run_lines), default=None)

    out = {'lines': len(run_lines), 'scan_lines': len(scan), 'flag_lines': len(flag),
           'lost': sum(r['lost'] == '1' for r in run_lines)}

    # loop period: S line writes (the criterion) and scan callback entries (for diagnosis)
    out.update(interval_stats([int(r['t_uart_tx_ns']) for r in scan], 'tx'))
    out.update(interval_stats([int(r['t_scan_rx_ns']) for r in scan], 'scan_rx'))
    # one line per scan: consecutive scan lines must be one LiDAR period apart in sim time.
    # A1 ran a sim-clock timer that fired twice per /clock tick, which shows up as step 0.
    steps = [round(float(b['sim_time']) - float(a['sim_time']), 3) for a, b in zip(scan, scan[1:])]
    out['sim_step_median_s'] = round(statistics.median(steps), 3) if steps else None
    out['sim_step_repeats'] = sum(x <= 0 for x in steps)
    lat = [(int(r['t_uart_tx_ns']) - int(r['t_scan_rx_ns'])) / 1e6 for r in scan]
    out.update(dist(lat, 'scan_to_tx_ms'))

    # U upper bound per line: the round trip of that same line
    out.update(dist([(int(r['t_c_rx_ns']) - int(r['t_uart_tx_ns'])) / 1e6 for r in answered],
                    'u_bound_ms'))

    # B inside the bridge for flag lines: flag received -> UART write
    out.update(dist([(int(r['t_uart_tx_ns']) - int(r['t_flag_rx_ns'])) / 1e6 for r in flag],
                    'flag_to_tx_ms', nd=4))
    sw = [int(r['switch_us']) for r in answered if _i(r['switch_us'])]
    out['switches'] = len(sw)
    out.update(dist(sw, 'switch_us', nd=1))

    states = {n: 0 for n in STATE_NAMES.values()}
    for r in scan:
        if r['lost'] == '0':
            states[STATE_NAMES[int(r['state'])]] += 1
    out['states'] = states
    mr = [_f(r['min_range']) for r in scan]
    out['min_range_m'] = round(min(mr), 4) if mr else None
    out['sim_time_first'] = _f(scan[0]['sim_time']) if scan else None
    out['sim_time_last'] = _f(scan[-1]['sim_time']) if scan else None
    out['last_wp_i'] = _i(scan[-1]['wp_i']) if scan else None

    if settle and settle[0]['lost'] == '0':
        s = settle[0]
        out['settle'] = {'mode': _i(s['mode']), 'switch_us': _i(s['switch_us']),
                         'n_sw': _i(s['n_sw']), 'bad_lines': _i(s['bad_lines'])}
        if answered:
            out['bad_lines_delta'] = _i(answered[-1]['bad_lines']) - _i(s['bad_lines'])
            out['n_sw_delta'] = _i(answered[-1]['n_sw']) - _i(s['n_sw'])

    # edge link, from the CSV alone: the round trip of each command the robot applied,
    # and the steps that had none and reused the previous one (plan 4.2, decision D15).
    rtt = [_f(r['rtt_us']) / 1000.0 for r in scan if r.get('rtt_us') not in ('', None)]
    out.update(dist(rtt, 'rtt_ms'))
    misses = sum(r.get('deadline_miss') == '1' for r in scan)
    holds = sum(r.get('held') == '1' for r in scan)
    out['deadline_misses'] = misses
    out['holds'] = holds
    out['miss_rate_steps'] = round(misses / len(scan), 4) if scan else None
    out['hold_rate'] = round(holds / len(scan), 4) if scan else None
    out['rtt_late'] = sum(x > EDGE_DEADLINE_MS for x in rtt)
    ages = [int(r['seq']) - _i(r['edge_seq_used']) for r in scan
            if r.get('edge_seq_used') not in ('', None)]
    out.update(dist(ages, 'edge_age_steps', nd=1))

    # WDOG lines: during the run they are faults; the one after the last line is expected
    wd = [r for r in rows if r['kind'] == 'wdog']
    run_start = min((int(r['t_uart_tx_ns']) for r in run_lines), default=None)
    during = [r for r in wd if run_start is not None
              and run_start <= int(r['t_c_rx_ns']) <= last_tx]
    after = [r for r in wd if last_tx is not None and int(r['t_c_rx_ns']) > last_tx]
    out['wdog_during_run'] = len(during)
    out['wdog_end_gap_ms'] = (round((int(after[0]['t_c_rx_ns']) - last_tx) / 1e6, 1)
                              if after else None)
    out['bridge_stops'] = sum(r['kind'] == 'bridge_stop' for r in rows)
    return out


def edge_figures(stats):
    """The few edge-link numbers that belong next to the run figures. The full stats stay
    in the meta file under 'edge'."""
    if not stats or stats.get('edge') == 'none':
        return {}
    keep = ('sent', 'replied', 'late', 'unanswered', 'miss_rate', 'miss_rate_steps',
            'holds', 'misses', 'rtt_ms_median', 'rtt_ms_p99', 'rtt_ms_max')
    return {f'edge_{k}': stats.get(k) for k in keep}


def main():
    print(json.dumps(summarize(load(sys.argv[1])), indent=2))


if __name__ == '__main__':
    main()
