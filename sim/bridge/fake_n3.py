#!/usr/bin/env python3
"""Stand-in for the N3 flag sender. Self-contained (no repository imports), so it can be
copied to the Pi on its own.

Waits for N1 keepalives, then alternates degrade 1 / 0 every --period seconds and sends
each flag to the source address of the latest keepalive (WSL2 NAT, see netio.py).

  python3 fake_n3.py                     # on the Pi, A4 flag-path check
  python3 fake_n3.py --host 127.0.0.1    # local tests
"""
import argparse
import socket
import time
from functools import reduce


def with_checksum(body):
    cs = reduce(lambda x, c: x ^ c, body.encode('ascii'), 0)
    return f'{body}*{cs:02X}\n'.encode('ascii')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--host', default='0.0.0.0')
    ap.add_argument('--port', type=int, default=47100)
    ap.add_argument('--period', type=float, default=2.0)
    ap.add_argument('--count', type=int, default=0, help='stop after this many flags (0 = never)')
    ap.add_argument('--start-degrade', type=int, default=1, choices=(0, 1))
    args = ap.parse_args()

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((args.host, args.port))
    s.settimeout(0.05)
    peer, kas, fseq = None, 0, 0
    degrade = args.start_degrade
    next_flag = None
    print(f'listening on {args.host}:{args.port}', flush=True)
    try:
        while not (args.count and fseq >= args.count):
            try:
                data, src = s.recvfrom(256)
                if data.startswith(b'K,'):
                    kas += 1
                    if peer != src:
                        print(f'keepalive from {src[0]}:{src[1]}', flush=True)
                    peer = src
                    if next_flag is None:
                        next_flag = time.monotonic() + args.period
            except socket.timeout:
                pass
            if peer and next_flag is not None and time.monotonic() >= next_flag:
                fseq += 1
                s.sendto(with_checksum(f'F,{fseq},{degrade},{time.time_ns()}'), peer)
                print(f'flag {fseq} degrade={degrade} -> {peer[0]}:{peer[1]}', flush=True)
                degrade ^= 1
                next_flag += args.period
    except KeyboardInterrupt:
        pass
    print(f'keepalives {kas}, flags sent {fseq}')


if __name__ == '__main__':
    main()
