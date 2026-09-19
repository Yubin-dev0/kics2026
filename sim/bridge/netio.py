"""Network side of the N1 bridge.

FlagListener receives N3 switching flags. WSL2 runs in NAT mode, so N3 cannot open a
             UDP flow towards N1 (fw/NOTES.md, known constraints). N1 therefore sends a
             keepalive to N3 once per second from the same socket it listens on; N3
             answers flags to the source address of the latest keepalive.

Datagram format (PROVISIONAL, to be agreed with the N3 detector owner), ASCII with the
same XOR checksum as the UART protocol:
  N1 -> N3  K,<n>*XX\\n                      keepalive number n
  N3 -> N1  F,<fseq>,<degrade>,<t_det_ns>*XX\\n
            fseq     flag message number (N3 counter)
            degrade  1 = degraded (switch to local), 0 = healthy
            t_det_ns N3 wall clock (time.time_ns) when the detector decided; N3 is the
                     reference clock of the testbed (integrated plan, A9)

Flag-path probe (A4 add-on check; N3 runs sim/bridge/fake_n3.py), from sim/ in WSL2:
  python3 -m bridge.netio --n3 <N3 address> --seconds 20
"""
import argparse
import socket
import threading
import time

from . import paths  # noqa: F401
import proto

FLAG_PORT = 47100        # provisional
KEEPALIVE_S = 1.0        # plan 3.2: 1 Hz heartbeat


def _checked(data):
    text = data.decode('ascii', 'replace').strip()
    body, sep, cs = text.rpartition('*')
    if not sep or len(cs) != 2 or int(cs, 16) != proto.checksum(body):
        raise ValueError(text)
    return body.split(',')


def parse_flag(data):
    f = _checked(data)
    if f[0] != 'F' or len(f) != 4 or f[2] not in ('0', '1'):
        raise ValueError(data)
    return int(f[1]), int(f[2]), int(f[3])


class FlagListener:
    """on_flag(degrade, t_flag_rx_ns, fseq, t_det_ns) runs on the listener thread.
    t_flag_rx_ns is taken right after recvfrom returns, before parsing."""

    def __init__(self, n3_host, on_flag, port=FLAG_PORT, keepalive_s=KEEPALIVE_S):
        self.addr = (n3_host, port)
        self.on_flag = on_flag
        self.keepalive_s = keepalive_s
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('0.0.0.0', 0))
        self.sock.settimeout(0.05)
        self.sent = 0
        self.received = []       # (t_flag_rx_ns, fseq, degrade, t_det_ns, src)
        self.bad = 0
        self.send_errors = 0
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, name='flag-listener', daemon=True)

    def local_port(self):
        return self.sock.getsockname()[1]

    def start(self):
        self.thread.start()

    def stop(self):
        self._stop.set()
        self.thread.join(timeout=1.0)
        self.sock.close()

    def _keepalive(self):
        self.sent += 1
        try:
            self.sock.sendto(proto.with_checksum(f'K,{self.sent}'), self.addr)
        except OSError:
            self.send_errors += 1

    def _loop(self):
        next_ka = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_ka:
                self._keepalive()
                next_ka = now + self.keepalive_s
            try:
                data, src = self.sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                continue
            t = time.monotonic_ns()
            try:
                fseq, degrade, t_det = parse_flag(data)
            except ValueError:
                self.bad += 1
                continue
            self.received.append((t, fseq, degrade, t_det, f'{src[0]}:{src[1]}'))
            self.on_flag(degrade, t, fseq, t_det)

    def stats(self):
        return {'n3': f'{self.addr[0]}:{self.addr[1]}', 'keepalives': self.sent,
                'send_errors': self.send_errors, 'flags': len(self.received), 'bad': self.bad}


def main():
    ap = argparse.ArgumentParser(description='A4 flag-path probe: keepalive out, flags back.')
    ap.add_argument('--n3', required=True, help='N3 address (the Pi)')
    ap.add_argument('--port', type=int, default=FLAG_PORT)
    ap.add_argument('--seconds', type=float, default=20.0)
    args = ap.parse_args()

    def show(degrade, t, fseq, t_det):
        lag_ms = (time.time_ns() - t_det) / 1e6
        print(f'flag {fseq}: degrade={degrade}  wall lag {lag_ms:.1f} ms (clocks not aligned)')

    fl = FlagListener(args.n3, show, port=args.port)
    fl.start()
    print(f'keepalive from local port {fl.local_port()} to {args.n3}:{args.port} '
          f'for {args.seconds:.0f} s')
    time.sleep(args.seconds)
    fl.stop()
    s = fl.stats()
    print(s)
    raise SystemExit(0 if s['flags'] > 0 and s['bad'] == 0 else 1)


if __name__ == '__main__':
    main()
