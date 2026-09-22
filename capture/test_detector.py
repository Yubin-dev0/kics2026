#!/usr/bin/env python3
"""Self-check of capture/detector.py with a synthetic robot flow (no N3 needed):

  python3 capture/test_detector.py

Cases (a 20 Hz flow up and down, WireGuard-sized, 60 s):
  1. no load: after the baseline q_hat stays inside +/-10% of the 50 ms period and the
     detector never enters (A7 criterion "no-load figures stable, window to window < 10%")
  2. L1 at t0 = 15 s: the downlink queue delay ramps 0 -> 150 ms over 3 s and then jitters
     +/-20 ms around 150 ms. The pair metric (reply minus request on N3) sees the queue
     itself and enters within 1.5 s of t0. The plan's interval median sees only the
     growth rate times the period (150 ms / 3 s x 50 ms = 2.5 ms) and does not enter:
     this case documents that limit, which A0 must weigh with real captures.
  3. L2 at t0: a 2.5 s bump that ends; with the 100 ms interval the pair detector enters
     (the plan's rule does not reject L2 by itself, A0 sets interval_s) and leaves again
  4. pcap round trip: the same flow written as an Ethernet pcap and read back by read_pcap
     gives the same packets (to the microsecond of the pcap format), and a 60 s replay
     takes well under 5 s (A7 criterion)
  5. windows replay with other parameters; tcpdump line parser
"""
import os
import random
import struct
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import detector as D  # noqa: E402

N1, N4 = '192.168.60.23', '192.168.50.4'
T_START = 1_790_000_000_000_000_000   # 2026-09-23 on the wall clock


def flow(seconds=60.0, delay_down=lambda t: 0.0, seed=1):
    """Up packet every 50 ms from N1; the reply 4 ms later plus delay_down(t) (queueing)."""
    rnd = random.Random(seed)
    pk = []
    t = 0.0
    while t < seconds:
        tu = t + rnd.gauss(0, 0.0003)
        pk.append((T_START + int(tu * 1e9), N1, 51820, N4, 51820, 160))
        td = tu + 0.004 + delay_down(t) / 1000.0
        pk.append((T_START + int(td * 1e9), N4, 51820, N1, 51820, 96))
        t += 0.05
    pk.sort()
    return pk


def l1(t, t0=15.0, ramp=3.0, level=150.0, seed=2):
    rnd = random.Random(int(t * 1000) + seed)
    if t < t0:
        return 0.0
    d = level * min(1.0, (t - t0) / ramp)
    return d + (rnd.uniform(-20, 20) if t - t0 > ramp else 0.0)


def l2(t, t0=15.0, length=2.5, level=120.0):
    if t0 <= t < t0 + length:
        return level * min(1.0, (t - t0) / 1.0)
    return 0.0


def args(**over):
    a = D.build_parser().parse_args([])
    for k, v in over.items():
        setattr(a, k, v)
    return a


def write_pcap(path, packets):
    with open(path, 'wb') as f:
        f.write(struct.pack('<IHHiIII', 0xa1b2c3d4, 2, 4, 0, 0, 96, 1))
        for t_ns, src, sp, dst, dp, ulen in packets:
            ip_len = 20 + 8 + ulen
            ip = struct.pack('>BBHHHBBH4s4s', 0x45, 0, ip_len, 0, 0, 64, 17, 0,
                             bytes(int(x) for x in src.split('.')), bytes(int(x) for x in dst.split('.')))
            udp = struct.pack('>HHHH', sp, dp, ulen + 8, 0)
            frame = b'\x00' * 12 + b'\x08\x00' + ip + udp + b'\x00' * min(ulen, 96 - 42)
            frame = frame[:96]
            f.write(struct.pack('<IIII', t_ns // 1_000_000_000, (t_ns % 1_000_000_000) // 1000,
                                len(frame), 14 + ip_len))
            f.write(frame)


def check(cond, msg):
    print(('ok   ' if cond else 'FAIL ') + msg)
    return cond


def main():
    ok = True
    # 1. no load
    det = D.Detector(args())
    rows = D.replay(det, flow())
    after = [r for r in rows if r['baseline_done'] and r['q_hat_ms'] is not None]
    spread = max(abs(r['q_hat_ms']) for r in after)
    ok &= check(det.baseline and abs(det.baseline['up'] - 50) < 1 and abs(det.baseline['down'] - 50) < 1,
                f'no load: baseline {det.baseline} ms is the 50 ms period')
    ok &= check(spread < 5.0, f'no load: |q_hat| max {spread:.2f} ms (< 5 ms = 10% of the period)')
    ok &= check(det.enters == 0, f'no load: enters {det.enters}')
    ok &= check(len(rows) > 550, f'no load: {len(rows)} steps over 60 s')

    # 2. L1
    t0_ns = T_START + 15_000_000_000
    res = {}
    for metric in ('pair', 'med', 'p90', 'mad'):
        det = D.Detector(args(metric=metric))
        D.replay(det, flow(delay_down=l1), t0_ns)
        a_ms = None if det.t_det_meta_ns is None else (det.t_det_meta_ns - t0_ns) / 1e6
        res[metric] = (a_ms, det.enters, det.leaves)
        print(f'     L1 ({metric}): A = {a_ms} ms, enters {det.enters}, leaves {det.leaves}, '
              f'baseline {det.baseline}')
    ok &= check(res['pair'][0] is not None and 0 < res['pair'][0] < 1500 and res['pair'][1] == 1,
                f"L1 (pair): one entry, A = {res['pair'][0]} ms after t0")
    ok &= check(res['med'][1] == 0, 'L1 (med): the interval median does not see a 50 ms/s ramp (documented limit)')
    ok &= check(det.counts['paired'] == det.counts['up'] - det.counts['control'] and det.counts['unpaired_down'] == 0,
                f"pairing: {det.counts['paired']} pairs, unpaired down {det.counts['unpaired_down']}")
    det = D.Detector(args(metric='p90', dir='up'))
    D.replay(det, flow(delay_down=l1), t0_ns)
    ok &= check(det.enters == 0, f'L1 with --dir up: uplink untouched, enters {det.enters}')

    # 3. L2
    det = D.Detector(args(metric='pair'))
    D.replay(det, flow(delay_down=l2), t0_ns)
    ok &= check(det.enters == 1 and det.leaves == 1,
                f'L2 (pair): enters {det.enters}, leaves {det.leaves} (interval 0.1 s does not reject a 2.5 s bump)')
    det = D.Detector(args(metric='pair', interval_s=3.0))
    D.replay(det, flow(delay_down=l2), t0_ns)
    ok &= check(det.enters == 0, f'L2 (pair, interval 3 s): enters {det.enters} (a longer interval rejects it)')
    det = D.Detector(args(metric='pair', interval_s=3.0))
    D.replay(det, flow(delay_down=l1), t0_ns)
    a_ms = None if det.t_det_meta_ns is None else (det.t_det_meta_ns - t0_ns) / 1e6
    ok &= check(det.enters == 1 and a_ms is not None and a_ms < 4500,
                f'L1 (pair, interval 3 s): still enters, A = {a_ms} ms')

    # 4. pcap round trip and speed
    pk = flow(delay_down=l1)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'x.pcap')
        write_pcap(path, pk)
        t = time.perf_counter()
        back = list(D.read_pcap(path))
        det = D.Detector(args(metric='pair'))
        D.replay(det, back, t0_ns)
        took = time.perf_counter() - t
        same = (len(back) == len(pk) and all(b[1:] == q[1:] and abs(b[0] - q[0]) < 1000 for b, q in zip(back, pk)))
        ok &= check(same, f'pcap: {len(back)} packets read back')
        ok &= check(took < 5.0, f'pcap: 60 s replay in {took:.2f} s (< 5 s)')
        # windows replay from the csv
        det2 = D.Detector(args())
        rows = D.replay(det2, back, t0_ns)
        wpath = os.path.join(d, 'w.csv')
        D.write_windows(wpath, rows)
        first, en, le = D.replay_windows(wpath, args(metric='pair', theta_high_ms=20.0))
        ok &= check(first is not None and first[0] == det.t_det_meta_ns,
                    f'windows replay (pair): same first entry as the packet replay, {first}')
        first, en, le = D.replay_windows(wpath, args(metric='med'))
        ok &= check(first is None, 'windows replay (med): no entry, as in the packet replay')

    # 5. line parser
    p = D.parse_line('1758600000.123456 IP 192.168.60.23.51820 > 192.168.50.4.51820: UDP, length 160')
    ok &= check(p == (1758600000123456000, '192.168.60.23', 51820, '192.168.50.4', 51820, 160),
                f'tcpdump line: {p}')
    ok &= check(D.parse_line('1758600000.1 IP 192.168.60.23.47100 > 192.168.60.1.47100: UDP, length 8') is not None
                and D.parse_line('garbage') is None, 'tcpdump line: other lines')
    print('ALL PASS' if ok else 'FAILURES')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
