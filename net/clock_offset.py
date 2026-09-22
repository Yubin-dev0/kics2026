#!/usr/bin/env python3
"""N1 to N3 clock offset delta (A9). N1 sends 100 UDP exchanges to an echo on N3; each one
gives four wall-clock stamps and the NTP estimate delta = ((t2 - t1) + (t3 - t4)) / 2 where
t1, t4 are N1 send and receive and t2, t3 are N3 receive and send. The median over the
exchanges is delta, and N1 wall + delta = N3 wall. Self-contained (standard library), so
the echo side is piped to N3 over ssh like the other N3 scripts:

  N3   ssh yubin@192.168.60.1 'python3 - --serve' < net/clock_offset.py        (stays up)
  N1   python3 net/clock_offset.py --n3 192.168.60.1 --stage c1 --run 3
  N1   python3 net/clock_offset.py --n3 192.168.60.1 --out /tmp/x --run 0     (A9 itself)

Writes data/<stage>/clock_run_N.json: the samples, delta_ns (median), sd_ms, the round
trips, and the verdict A9-1: sd < 1 ms (plan v5 7.3, A9). The C3 merge
(analysis/sweep_index.py) reads it to put N1's t_det_rtt and t_flag_rx on N3's clock, so
one file per run, taken right before the run (the Pi has no RTC and drifts).

Both sides use time.time_ns(). N3 keeps its wall clock with chrony but has no internet; only
the offset matters, never N3's absolute time.
"""
import argparse
import datetime as dt
import json
import os
import socket
import statistics
import time
from functools import reduce

PORT = 47300            # next to the flag (47100) and load (47200) ports
COUNT = 100             # plan v3 10.1 A9: 100 exchanges
SD_MS = 1.0             # plan v5 7.3: standard deviation under 1 ms


def with_checksum(body):
    cs = reduce(lambda x, c: x ^ c, body.encode('ascii'), 0)
    return f'{body}*{cs:02X}\n'.encode('ascii')


def parse(data):
    text = data.decode('ascii', 'replace').strip()
    body, sep, cs = text.rpartition('*')
    if not sep or len(cs) != 2 or int(cs, 16) != reduce(lambda x, c: x ^ c, body.encode('ascii'), 0):
        raise ValueError(text)
    return body.split(',')


def serve(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('0.0.0.0', port))
    print(f'clock echo on 0.0.0.0:{port}', flush=True)
    n = 0
    while True:
        data, src = s.recvfrom(256)
        t2 = time.time_ns()
        try:
            f = parse(data)
        except ValueError:
            continue
        if f[0] != 'T':
            continue
        n += 1
        s.sendto(with_checksum(f'R,{f[1]},{f[2]},{t2},{time.time_ns()}'), src)
        if n % 100 == 0:
            print(f'{n} exchanges', flush=True)


def measure(n3, port, count, gap_s):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(1.0)
    samples, lost = [], 0
    for i in range(1, count + 1):
        t1 = time.time_ns()
        s.sendto(with_checksum(f'T,{i},{t1}'), (n3, port))
        try:
            while True:
                f = parse(s.recv(256))
                t4 = time.time_ns()
                if f[0] == 'R' and int(f[1]) == i:
                    break
        except (socket.timeout, ValueError):
            lost += 1
            continue
        t2, t3 = int(f[3]), int(f[4])
        samples.append({'i': i, 't1': t1, 't2': t2, 't3': t3, 't4': t4,
                        'delta_ns': ((t2 - t1) + (t3 - t4)) // 2, 'rtt_ns': (t4 - t1) - (t3 - t2)})
        time.sleep(gap_s)
    return samples, lost


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--serve', action='store_true', help='echo side, on N3')
    ap.add_argument('--port', type=int, default=PORT)
    ap.add_argument('--n3', default=None)
    ap.add_argument('--count', type=int, default=COUNT)
    ap.add_argument('--gap-s', type=float, default=0.02)
    ap.add_argument('--stage', default='a9')
    ap.add_argument('--run', type=int, default=None)
    ap.add_argument('--out', default=None, help='folder (default data/<stage>)')
    ap.add_argument('--note', default='')
    a = ap.parse_args()
    if a.serve:
        serve(a.port)
        return
    if not a.n3 or a.run is None:
        ap.error('--n3 and --run are required (or --serve)')
    samples, lost = measure(a.n3, a.port, a.count, a.gap_s)
    if not samples:
        raise SystemExit('no replies from N3')
    deltas = [x['delta_ns'] for x in samples]
    rtts = sorted(x['rtt_ns'] for x in samples)
    delta = int(statistics.median(deltas))
    sd_ms = statistics.pstdev(deltas) / 1e6
    # the exchanges with the shortest round trips bound the offset best (NTP's choice)
    best = sorted(samples, key=lambda x: x['rtt_ns'])[:max(1, len(samples) // 4)]
    delta_best = int(statistics.median(x['delta_ns'] for x in best))
    res = {'count': a.count, 'replied': len(samples), 'lost': lost,
           'delta_ns': delta, 'delta_ms': round(delta / 1e6, 3), 'sd_ms': round(sd_ms, 3),
           'delta_best_quarter_ns': delta_best,
           'spread_ms': round((max(deltas) - min(deltas)) / 1e6, 3),
           'rtt_ms_median': round(statistics.median(rtts) / 1e6, 3),
           'rtt_ms_min': round(rtts[0] / 1e6, 3), 'rtt_ms_max': round(rtts[-1] / 1e6, 3)}
    verdict = {'sd_under_1ms': sd_ms < SD_MS, 'replies': len(samples) >= a.count * 0.95}
    out = a.out or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                'data', a.stage.lower())
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, f'clock_run_{a.run}.json')
    meta = {'run_id': a.run, 'stage': a.stage.upper(), 'harness': 'clock', 'n3': a.n3,
            'started': dt.datetime.now().astimezone().isoformat(timespec='seconds'),
            'results': res, 'verdict': verdict, 'pass': all(verdict.values()),
            'note': a.note, 'samples': samples}
    with open(path, 'w') as f:
        json.dump(meta, f, indent=1)
    print(f"delta (N3 - N1) {res['delta_ms']} ms, sd {res['sd_ms']} ms, spread {res['spread_ms']} ms, "
          f"best quarter {delta_best / 1e6:.3f} ms; round trip median {res['rtt_ms_median']} "
          f"min {res['rtt_ms_min']} max {res['rtt_ms_max']} ms; {len(samples)}/{a.count} replies")
    print(f"{'PASS' if meta['pass'] else 'FAIL'} {verdict} -> {path}")
    raise SystemExit(0 if meta['pass'] else 1)


if __name__ == '__main__':
    main()
