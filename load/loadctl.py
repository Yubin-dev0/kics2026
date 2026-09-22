#!/usr/bin/env python3
"""Hand control of the N5 load agent (load/n5_agent.py) for A8, from N1 or N3:

  python3 load/loadctl.py --n5 192.168.60.20 ping
  python3 load/loadctl.py --n5 192.168.60.20 start L1 --rate 60M --run 1 --out data/a8
  python3 load/loadctl.py --n5 192.168.60.20 start L2 --run 2
  python3 load/loadctl.py --n5 192.168.60.20 start L1 --proto tcp --seconds 10 --run 0   (capacity check)
  python3 load/loadctl.py --n5 192.168.60.20 stop

start records t0 (this machine's wall clock, the moment the trigger left) and waits for the
agent's done datagram, then writes <out>/load_run_N.json with t0, the ack, the achieved
rate and loss. In a sweep the N3 detector sends the same trigger itself (capture/detector.py
--load), so t0 is on N3's clock; this tool is for A8 and for repairs.

A8 (plan v5 7.3): L1 must raise the RTT reproducibly, three repeats within 20% of each
other, measured with net/ping_run.py running alongside. The L1 rate is provisional until
that measurement; the capacity check above (tcp, reverse) gives the link's throughput to
set it from (net/README.md: N1 links at 86.7 Mbit/s on a 20 MHz channel).
"""
import argparse
import json
import os
import socket
import sys
import time
from functools import reduce

LOADS = {'L1': 45.0, 'L2': 2.5}   # seconds; L1 plan v5 5.1, L2 provisional (2 to 3 s)
LOAD_PORT = 47200


def checksum(body):
    return reduce(lambda x, c: x ^ c, body.encode('ascii'), 0)


def with_checksum(body):
    return f'{body}*{checksum(body):02X}\n'.encode('ascii')


def parse(data):
    text = data.decode('ascii', 'replace').strip()
    body, sep, cs = text.rpartition('*')
    if not sep or len(cs) != 2 or int(cs, 16) != checksum(body):
        raise ValueError(text)
    return body.split(',')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--n5', required=True)
    ap.add_argument('--port', type=int, default=LOAD_PORT)
    ap.add_argument('cmd', choices=('ping', 'start', 'stop'))
    ap.add_argument('load', nargs='?', choices=tuple(LOADS))
    ap.add_argument('--run', type=int, default=0)
    ap.add_argument('--seconds', type=float, default=None)
    ap.add_argument('--proto', default='udp', choices=('udp', 'tcp'))
    ap.add_argument('--rate', default='60M', help='iperf3 -b for udp (provisional until A8)')
    ap.add_argument('--server', default='192.168.60.1', help='iperf3 server N5 connects to (N3)')
    ap.add_argument('--out', default=None, help='folder for load_run_N.json (start only)')
    a = ap.parse_args()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2.0)
    dst = (a.n5, a.port)

    if a.cmd == 'ping':
        t = time.time_ns()
        s.sendto(with_checksum('P'), dst)
        f = parse(s.recv(256))
        print(f'N5 answers: its clock {f[1]}, round trip {(time.time_ns() - t) / 1e6:.2f} ms')
        return
    if a.cmd == 'stop':
        s.sendto(with_checksum('S'), dst)
        print(f'N5: {parse(s.recv(256))}')
        return
    if not a.load:
        sys.exit('start needs L1 or L2')
    secs = LOADS[a.load] if a.seconds is None else a.seconds
    body = f'L,{a.load},{secs:g},{a.proto},{a.rate},{a.server},{a.run}'
    t0 = time.time_ns()
    s.sendto(with_checksum(body), dst)
    ack = parse(s.recv(256))
    t_ack = time.time_ns()
    print(f't0 {t0} (this clock), ack {ack} after {(t_ack - t0) / 1e6:.1f} ms')
    rec = {'load': a.load, 'run': a.run, 'seconds': secs, 'proto': a.proto, 'rate': a.rate,
           'server': a.server, 'n5': a.n5, 't0_ns': t0, 'ack': ack, 'ack_rtt_ms': round((t_ack - t0) / 1e6, 3)}
    s.settimeout(secs + 10)
    try:
        done = parse(s.recv(256))
        rec['done'] = done
        print(f'done: {done} (rc {done[4]}, {done[5]} Mbit/s received)')
    except socket.timeout:
        rec['done'] = None
        print('no done datagram (agent still running or reply lost)')
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        p = os.path.join(a.out, f'load_run_{a.run}.json')
        with open(p, 'w') as f:
            json.dump(rec, f, indent=2)
        print(f'-> {p}')


if __name__ == '__main__':
    main()
