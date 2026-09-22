#!/usr/bin/env python3
"""Round-trip runs for the N3 stages, from N1 in WSL2: A4 (the access point) and A6
(netem base RTT). Like every other stage, a run writes data/<stage>/run_N.log (the raw
ping output, one reply per line with its epoch time) and run_N_meta.json (conditions,
figures, verdict), and is registered with analysis/append_run.py.

  python3 net/ping_run.py --stage A4 --run 1 --target 192.168.60.1 --count 12000 --n3 yubin@192.168.60.1
  python3 net/ping_run.py --stage A6 --run 1 --target 192.168.50.4 --set-ms 0 --n3 yubin@192.168.60.1
  python3 net/ping_run.py --stage A6 --run 2 --target 192.168.50.4 --set-ms 10 --base-run 1 --n3 yubin@192.168.60.1
  python3 net/ping_run.py --parse data/a4/run_1.log

Pings go out at 20 Hz (-i 0.05), the robot's control rate, so the figures describe the
cadence the bridge runs at. An interval under 0.2 s needs root; the script calls sudo.

Verdicts (net/README.md):
  A4  median < 5 ms, 10 minutes at 20 Hz (12000 pings), and no gap between replies of
      1 s or more ("no cut"; 1 s is provisional). Gaps of 150 ms or more are counted too:
      that is the board watchdog, so each one would have stopped the robot in B3.
  A6  run with --set-ms 0 is the reference. For a set base RTT, the added delay
      (median minus the reference median) is within +/-10% of the set value (plan 7.3).
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'sim'))
from bridge import env   # noqa: E402

INTERVAL_S = 0.05
A4_COUNT = 12000            # 10 minutes at 20 Hz (plan 7.3: 10 minutes without a cut)
A4_MEDIAN_MS = 5.0          # plan 7.3
A4_CUT_S = 1.0              # provisional: a reply gap this long counts as a cut
WDOG_GAP_S = 0.15           # board watchdog (fw/NOTES.md)
A6_TOL = 0.10               # plan 7.3: +/-10% of the set base RTT
REPLY = re.compile(r'^\[(\d+\.\d+)\].*icmp_seq=(\d+).*time=([\d.]+) ms')


def pct(xs, q):
    """Nearest rank on a sorted list, the rule of sim/bridge/summary.py."""
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def parse(lines, count=None):
    """Figures of one ping log: round trip, loss, and gaps between replies."""
    replies, dups = {}, 0
    for ln in lines:
        m = REPLY.match(ln)
        if not m:
            continue
        seq = int(m.group(2))
        if seq in replies or 'DUP!' in ln:
            dups += 1
            continue
        replies[seq] = (float(m.group(1)), float(m.group(3)))
    if not replies:
        return {'received': 0, 'sent': count or 0}
    seqs = sorted(replies)
    sent = count or seqs[-1]
    rtt = sorted(r for _, r in replies.values())
    stamps = [replies[s][0] for s in seqs]
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    lost_runs, run = [], 0
    have = set(seqs)
    for s in range(1, sent + 1):
        if s in have:
            if run:
                lost_runs.append(run)
            run = 0
        else:
            run += 1
    if run:
        lost_runs.append(run)
    return {
        'sent': sent, 'received': len(seqs), 'duplicates': dups,
        'loss_pct': round(100.0 * (sent - len(seqs)) / sent, 3),
        'rtt_ms_median': round(statistics.median(rtt), 3),
        'rtt_ms_p99': round(pct(rtt, 0.99), 3),
        'rtt_ms_max': round(rtt[-1], 3), 'rtt_ms_min': round(rtt[0], 3),
        'rtt_ms_mean': round(statistics.fmean(rtt), 3),
        'rtt_ms_sd': round(statistics.pstdev(rtt), 3),
        'duration_s': round(stamps[-1] - stamps[0], 1),
        'longest_gap_s': round(max(gaps), 3) if gaps else None,
        'gaps_ge_150ms': sum(g >= WDOG_GAP_S for g in gaps),
        'longest_loss_run': max(lost_runs) if lost_runs else 0,
    }


def verdict(stage, res, set_ms, base_median):
    if stage == 'A4':
        v = {'median': res.get('rtt_ms_median', 1e9) < A4_MEDIAN_MS,
             'ten_minutes': res.get('sent', 0) >= A4_COUNT,
             'no_cut': res.get('longest_gap_s') is not None
                       and res['longest_gap_s'] < A4_CUT_S}
        return v, all(v.values())
    if set_ms == 0:
        return {'reference': True}, res.get('received', 0) > 0
    added = res['rtt_ms_median'] - base_median
    res['added_ms'] = round(added, 3)
    res['added_error_pct'] = round(100.0 * (added - set_ms) / set_ms, 2)
    v = {'within_10pct': abs(added - set_ms) <= A6_TOL * set_ms}
    return v, all(v.values())


def _cmd_text(cmd, timeout=20.0, encoding='utf-8'):
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f'{type(e).__name__}: {e}'
    def dec(b):
        if encoding:
            return b.decode(encoding, 'replace')
        try:
            return b.decode('utf-8')
        except UnicodeDecodeError:
            return b.decode('cp949', 'replace')
    text = dec(out.stdout)
    if out.returncode:
        text += dec(out.stderr)
    return text


def wifi_facts():
    """netsh wlan show interfaces on the Windows side: SSID, band, channel, signal, rates.
    Kept as text lines: the labels are localised (Korean Windows)."""
    netsh = shutil.which('netsh.exe') or '/mnt/c/Windows/System32/netsh.exe'
    if not os.path.exists(netsh) and not shutil.which('netsh.exe'):
        return None
    # netsh prints UTF-8 when Windows runs with the UTF-8 code page and CP949 otherwise;
    # the 9/22 A4 meta file shows UTF-8 read as CP949
    text = _cmd_text([netsh, 'wlan', 'show', 'interfaces'], encoding=None)
    return [ln.strip() for ln in text.splitlines() if ':' in ln]


def n3_facts(n3):
    """qdiscs and the AP's view of its clients, read over ssh (no repository on N3)."""
    # tc lives in /usr/sbin, which a non-login ssh shell on Debian leaves off PATH; without
    # it the 9/22 A6 meta files hold the station lines only
    remote = ('export PATH=$PATH:/usr/sbin:/sbin; '
              'tc -s qdisc show dev wlan0; tc -s qdisc show dev eth0; '
              'sudo -n iw dev wlan0 station dump 2>/dev/null | '
              "grep -E '^Station|signal:|tx bitrate|rx bitrate'")
    text = _cmd_text(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', n3, remote])
    return text.splitlines()


def run_ping(target, count, log_path):
    cmd = ['ping', '-D', '-n', '-i', str(INTERVAL_S), '-c', str(count), '-W', '1', target]
    if os.geteuid() != 0:
        cmd = ['sudo'] + cmd
    lines, got = [], 0
    print(' '.join(cmd), flush=True)
    with open(log_path, 'w') as log:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
        try:
            for ln in p.stdout:
                log.write(ln)
                lines.append(ln)
                if REPLY.match(ln):
                    got += 1
                    if got % 1000 == 0:
                        print(f'  {got}/{count} replies', flush=True)
            p.wait()
            aborted = None
        except KeyboardInterrupt:
            p.terminate()
            p.wait()
            aborted = 'KeyboardInterrupt'
    return lines, aborted


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--stage', choices=('A4', 'A6'))
    ap.add_argument('--run', type=int)
    ap.add_argument('--target', help='A4: N3 (192.168.60.1). A6: N4 (192.168.50.4)')
    ap.add_argument('--count', type=int, default=None,
                    help=f'pings (default A4 {A4_COUNT}, A6 1000)')
    ap.add_argument('--set-ms', type=float, default=None, help='A6: base RTT set on N3')
    ap.add_argument('--base-run', type=int, default=None, help='A6: the --set-ms 0 run')
    ap.add_argument('--n3', default=None, help='user@N3, to record qdiscs and clients')
    ap.add_argument('--out', default=None)
    ap.add_argument('--note', default='')
    ap.add_argument('--parse', metavar='LOG', help='print the figures of a saved log')
    args = ap.parse_args()

    if args.parse:
        print(json.dumps(parse(Path(args.parse).read_text().splitlines()), indent=2))
        return
    if not (args.stage and args.run and args.target):
        ap.error('--stage, --run and --target are required')
    count = args.count or (A4_COUNT if args.stage == 'A4' else 1000)
    out = Path(args.out) if args.out else REPO / 'data' / args.stage.lower()
    out.mkdir(parents=True, exist_ok=True)
    base_median = None
    if args.stage == 'A6':
        if args.set_ms is None:
            ap.error('A6 needs --set-ms (0 for the reference run)')
        if args.set_ms > 0:
            if args.base_run is None:
                ap.error('A6 with --set-ms > 0 needs --base-run (the --set-ms 0 run)')
            bm = out / f'run_{args.base_run}_meta.json'
            base_median = json.loads(bm.read_text())['results']['rtt_ms_median']
    log_path, meta_path = out / f'run_{args.run}.log', out / f'run_{args.run}_meta.json'
    if log_path.exists() or meta_path.exists():
        raise SystemExit(f'{log_path.name} already exists. Pick another --run.')

    commit, dirty = env.git_info()
    meta = {'run_id': str(args.run), 'stage': args.stage, 'harness': 'ping',
            'started': dt.datetime.now().astimezone().isoformat(timespec='seconds'),
            'target': args.target, 'count': count, 'interval_s': INTERVAL_S,
            'set_ms': args.set_ms, 'base_run': args.base_run, 'base_median_ms': base_median,
            'git': commit, 'git_dirty': dirty, 'note': args.note,
            'n1_wifi': wifi_facts(),
            'n3_before': n3_facts(args.n3) if args.n3 else None}
    lines, aborted = run_ping(args.target, count, log_path)
    res = parse(lines, count)
    meta['n3_after'] = n3_facts(args.n3) if args.n3 else None
    meta['finished'] = dt.datetime.now().astimezone().isoformat(timespec='seconds')
    meta['results'] = res
    if res.get('received'):
        meta['verdict'], passed = verdict(args.stage, res, args.set_ms, base_median)
    else:
        meta['verdict'], passed = {'replies': False}, False
    meta['aborted'] = aborted
    meta['pass'] = passed and aborted is None
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + '\n')

    print(f"\n{args.stage} run {args.run} -> {args.target}: "
          f"median {res.get('rtt_ms_median')} p99 {res.get('rtt_ms_p99')} "
          f"max {res.get('rtt_ms_max')} ms, loss {res.get('loss_pct')}% "
          f"({res.get('received')}/{res.get('sent')}), longest gap {res.get('longest_gap_s')} s, "
          f"gaps >= 150 ms {res.get('gaps_ge_150ms')}")
    if 'added_ms' in res:
        print(f"  set {args.set_ms} ms, added {res['added_ms']} ms "
              f"({res['added_error_pct']:+}%) over reference {base_median} ms")
    print(f"  verdict {meta['verdict']}")
    print(f"\n{'PASS' if meta['pass'] else 'FAIL'} -> {log_path}, {meta_path.name}")
    print(f'register: python3 analysis/append_run.py {args.stage} {args.run}')
    raise SystemExit(0 if meta['pass'] else 1)


if __name__ == '__main__':
    main()
