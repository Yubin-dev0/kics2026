#!/usr/bin/env python3
"""N3 metadata detector (A7, policy 4): watches the encrypted robot flow on wlan0, computes
the packet-interval features of a sliding window, decides degradation with the CoDel-style
rule of the integrated plan (v3 6.2, v5 4.3) and sends the switching flag to N1. It also
marks the run start (first N1 keepalive) and starts the N5 load at t0, so t0, the
detection time and the flag all sit on one clock: N3's.

Self-contained (standard library only), so it is piped to N3 over ssh like the net/n3
scripts; nothing is cloned onto N3:

  ssh yubin@192.168.60.1 'sudo python3 - --run 1 --stage b3' < capture/detector.py
  ssh yubin@192.168.60.1 'sudo python3 - --run 2 --stage c1 --load L1 --n5 192.168.60.20' < capture/detector.py
  python3 capture/detector.py --replay data/b3/n3_run_1.pcap --run 1 --stage b3 --out /tmp/rep
  python3 capture/detector.py --replay-windows data/c1/n3_run_2_windows.csv --metric p90 --theta-high-ms 15

Files (in --out, default /home/yubin/n3runs/<stage>; fetched to data/<stage>/ with
capture/fetch.sh; the pcap never enters the repository):
  n3_run_N.pcap          tcpdump -s 96, udp port 51820 (WireGuard headers only)
  n3_run_N_windows.csv   one row per 100 ms step (columns in capture/README.md)
  n3_run_N_flags.csv     one row per flag sent
  n3_run_N_meta.json     parameters, baseline, t_run_start_ns, t0_ns, t_det_meta_ns, verdict inputs

Rule (per step of step_s, over the last window_s of packets, after the baseline):
  q_hat      --metric med (the plan's rule): median packet interval of the window in one
             direction minus the no-load baseline of that direction (mean over the first
             baseline_s of the flow); with --dir both, the larger of the two directions.
             p90 and mad are the same with the 90th percentile or the mean absolute
             deviation of the intervals. pair is direction-free: the median of
             (reply seen on wlan0 egress) - (its request seen on wlan0 ingress), i.e. the
             Ethernet round trip to N4 plus the time the reply waited in N3's own wlan0
             queue, minus its baseline. A window records every one of these, so A0 can
             re-decide a run with --replay-windows and choose.
  q_min      smallest q_hat over the last interval_s (RFC 8289 interval)
  degraded   q_min > theta_high enters, q_min < theta_low leaves (double threshold)
  a window with fewer than two packets in a direction has no interval and decides nothing
  (unlike the RTT watcher on N1, an empty window here most often means the run is over)
theta_high / theta_low are provisional until A0; every run records the values it used.
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import select
import signal
import socket
import statistics
import struct
import subprocess
import sys
import time
from collections import deque
from functools import reduce

# ---- parameters (plan v5 4.3, v3 6.2 and 8.3; provisional ones say so) -----------------
WINDOW_S = 1.0          # W_f, plan v3 6.2 step 1
STEP_S = 0.1            # sliding step, plan v3 6.2 step 1
INTERVAL_S = 0.1        # I, RFC 8289 default interval (plan v3 6.2 step 2)
BASELINE_S = 5.0        # no-load reference: first 5 s of the flow (plan v3 8.3)
THETA_HIGH_MS = 20.0    # provisional (A0); same pair as rttwatch.py on N1
THETA_LOW_MS = 10.0     # provisional (A0)
BURST_EPS_MS = 5.0      # provisional: intervals under this are one burst (plan v3, burst_len)
T0_S = 17.0             # plan v5 4.1 says 15 s into the run; the keepalive that marks the run start on N3
                        # precedes N1's first scan by 1-3 s (settle), so 17 s keeps t0 past N1's 15 s RTT
                        # baseline. Provisional: the C3 merge prints t0 on N1's clock for every run
FLAG_PORT = 47100       # sim/bridge/netio.py
LOAD_PORT = 47200       # load/n5_agent.py
ROBOT_FILTER = 'udp port 51820'   # WireGuard, A5-2
AP_NET = '192.168.60.'  # net/README.md topology: wlan0 side addresses
LOADS = {'L1': 45.0, 'L2': 2.5}   # seconds; L1 plan v5 5.1, L2 provisional (2 to 3 s)
WG_CONTROL_LENS = {32, 92, 148}   # WireGuard keepalive, handshake response, handshake initiation
PAIR_MAX_S = 1.5        # provisional: an uplink packet unanswered this long is dropped from pairing

LINE = re.compile(r'^(\d+\.\d+) IP (\d+\.\d+\.\d+\.\d+)\.(\d+) > (\d+\.\d+\.\d+\.\d+)\.(\d+): UDP, length (\d+)')
WINDOW_FIELDS = ['step', 't_ns', 'elapsed_s', 'n_up', 'n_down', 'bytes_up', 'bytes_down',
                 'iat_med_up_ms', 'iat_p90_up_ms', 'iat_max_up_ms', 'iat_mad_up_ms', 'burst_up',
                 'iat_med_down_ms', 'iat_p90_down_ms', 'iat_max_down_ms', 'iat_mad_down_ms', 'burst_down',
                 'updown_ratio', 'pair_n', 'pair_med_ms', 'pair_min_ms', 'pair_max_ms',
                 'q_up_ms', 'q_down_ms', 'q_pair_ms', 'q_hat_ms', 'q_min_ms', 'baseline_done',
                 'degraded', 't_det_meta_ns']
FLAG_FIELDS = ['fseq', 'degrade', 't_det_ns', 't_send_ns', 'peer', 'step']


def with_checksum(body):
    cs = reduce(lambda x, c: x ^ c, body.encode('ascii'), 0)
    return f'{body}*{cs:02X}\n'.encode('ascii')


def pct(xs, q):
    """Nearest rank on a sorted list (the rule of sim/bridge/summary.py)."""
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


# ---- packet sources ---------------------------------------------------------------------
def parse_line(line):
    m = LINE.match(line)
    if not m:
        return None
    return (int(float(m.group(1)) * 1e9), m.group(2), int(m.group(3)),
            m.group(4), int(m.group(5)), int(m.group(6)))


def read_pcap(path):
    """Yields (t_ns, src, sport, dst, dport, udp_payload_len) of the UDP packets of a pcap
    written by tcpdump (link types Ethernet, Linux SLL and SLL2)."""
    with open(path, 'rb') as f:
        head = f.read(24)
        magic = struct.unpack('<I', head[:4])[0]
        if magic in (0xa1b2c3d4, 0xa1b23c4d):
            endian, nano = '<', magic == 0xa1b23c4d
        elif magic in (0xd4c3b2a1, 0x4d3cb2a1):
            endian, nano = '>', magic == 0x4d3cb2a1
        else:
            raise ValueError(f'{path}: not a pcap file')
        linktype = struct.unpack(endian + 'I', head[20:24])[0]
        while True:
            rec = f.read(16)
            if len(rec) < 16:
                return
            ts_s, ts_frac, incl, _orig = struct.unpack(endian + 'IIII', rec)
            data = f.read(incl)
            t_ns = ts_s * 1_000_000_000 + (ts_frac if nano else ts_frac * 1000)
            if linktype == 1:            # Ethernet
                off = 14
                if len(data) >= 14 and data[12:14] == b'\x81\x00':
                    off = 18
                if len(data) < off or data[off - 2:off] != b'\x08\x00':
                    continue
            elif linktype == 113:        # Linux cooked v1
                off = 16
                if len(data) < off or data[14:16] != b'\x08\x00':
                    continue
            elif linktype == 276:        # Linux cooked v2
                off = 20
                if len(data) < off or data[0:2] != b'\x08\x00':
                    continue
            else:
                raise ValueError(f'{path}: link type {linktype} not handled')
            ip = data[off:]
            if len(ip) < 20 or ip[9] != 17:
                continue
            ihl = (ip[0] & 0x0f) * 4
            src = '.'.join(str(b) for b in ip[12:16])
            dst = '.'.join(str(b) for b in ip[16:20])
            udp = ip[ihl:ihl + 8]
            if len(udp) < 8:
                continue
            sport, dport, ulen = struct.unpack('>HHH', udp[:6])
            yield (t_ns, src, sport, dst, dport, max(ulen - 8, 0))


def read_lines(fobj):
    for line in fobj:
        p = parse_line(line)
        if p:
            yield p


# ---- the detector --------------------------------------------------------------------
class Detector:
    def __init__(self, a):
        self.a = a
        self.window_ns = int(a.window_s * 1e9)
        self.interval_ns = int(a.interval_s * 1e9)
        self.step_ns = int(a.step_s * 1e9)
        self.theta_high = a.theta_high_ms
        self.theta_low = a.theta_low_ms
        self.up = deque()       # (t_ns, length) inside the window, uplink (from the AP side)
        self.down = deque()
        self.pending = deque()  # uplink data packets not yet answered (t_ns)
        self.pairs = deque()    # (t_down_ns, delay_ms) inside the window
        self.pair_max_ns = int(PAIR_MAX_S * 1e9)
        self.first_pkt_ns = None
        self.base_samples = {'up': [], 'down': [], 'pair': []}
        self.baseline = None    # {'up': ms, 'down': ms, 'pair': ms}
        self.q_hist = deque()   # (t_ns, q_hat) inside the interval
        self.degraded = False
        self.t_det_meta_ns = None
        self.enters = self.leaves = 0
        self.step = 0
        self.next_step_ns = None
        self.counts = {'up': 0, 'down': 0, 'bytes_up': 0, 'bytes_down': 0, 'other': 0,
                       'control': 0, 'paired': 0, 'unpaired_up': 0, 'unpaired_down': 0}
        self.rows = []

    def add(self, pkt):
        t_ns, src, _sp, dst, _dp, length = pkt
        if src.startswith(self.a.ap_net):
            q, key = self.up, 'up'
        elif dst.startswith(self.a.ap_net):
            q, key = self.down, 'down'
        else:
            self.counts['other'] += 1
            return
        q.append((t_ns, length))
        self.counts[key] += 1
        self.counts['bytes_' + key] += length
        if self.first_pkt_ns is None:
            self.first_pkt_ns = t_ns
            self.next_step_ns = t_ns + self.step_ns
        if length in WG_CONTROL_LENS:
            self.counts['control'] += 1
            return
        # request/reply pairing, in order (the robot flow is one request and one reply per
        # 50 ms period, and N3's queues keep order)
        while self.pending and t_ns - self.pending[0] > self.pair_max_ns:
            self.pending.popleft()
            self.counts['unpaired_up'] += 1
        if key == 'up':
            self.pending.append(t_ns)
        elif self.pending:
            self.pairs.append((t_ns, (t_ns - self.pending.popleft()) / 1e6))
            self.counts['paired'] += 1
        else:
            self.counts['unpaired_down'] += 1

    def due_steps(self, now_ns):
        """Steps whose end time has passed; returns their rows (0 or more)."""
        out = []
        while self.next_step_ns is not None and now_ns >= self.next_step_ns:
            out.append(self.evaluate(self.next_step_ns))
            self.next_step_ns += self.step_ns
        return out

    def _features(self, q, t_end):
        while q and t_end - q[0][0] > self.window_ns:
            q.popleft()
        n = len(q)
        nbytes = sum(b for _, b in q)
        if n < 2:
            return n, nbytes, None, None, None, None, 0
        iat = sorted((q[i][0] - q[i - 1][0]) / 1e6 for i in range(1, n))
        med = statistics.median(iat)
        mad = sum(abs(x - med) for x in iat) / len(iat)
        burst, run = 1, 1
        for i in range(1, n):
            if (q[i][0] - q[i - 1][0]) / 1e6 < self.a.burst_eps_ms:
                run += 1
                burst = max(burst, run)
            else:
                run = 1
        return n, nbytes, med, pct(iat, 0.90), iat[-1], mad, burst

    def _metric(self, med, p90, mad):
        return {'med': med, 'p90': p90, 'mad': mad, 'pair': None}[self.a.metric]

    def _pair_features(self, t_end):
        while self.pairs and t_end - self.pairs[0][0] > self.window_ns:
            self.pairs.popleft()
        if not self.pairs:
            return 0, None, None, None
        d = sorted(x for _, x in self.pairs)
        return len(d), statistics.median(d), d[0], d[-1]

    def evaluate(self, t_end):
        self.step += 1
        elapsed = (t_end - self.first_pkt_ns) / 1e9
        fu = self._features(self.up, t_end)
        fd = self._features(self.down, t_end)
        mu, md = self._metric(fu[2], fu[3], fu[5]), self._metric(fd[2], fd[3], fd[5])
        fp = self._pair_features(t_end)
        row = {'step': self.step, 't_ns': t_end, 'elapsed_s': round(elapsed, 3),
               'n_up': fu[0], 'n_down': fd[0], 'bytes_up': fu[1], 'bytes_down': fd[1],
               'iat_med_up_ms': fu[2], 'iat_p90_up_ms': fu[3], 'iat_max_up_ms': fu[4],
               'iat_mad_up_ms': fu[5], 'burst_up': fu[6],
               'iat_med_down_ms': fd[2], 'iat_p90_down_ms': fd[3], 'iat_max_down_ms': fd[4],
               'iat_mad_down_ms': fd[5], 'burst_down': fd[6],
               'updown_ratio': (round(fu[1] / fd[1], 4) if fd[1] else None),
               'pair_n': fp[0], 'pair_med_ms': fp[1], 'pair_min_ms': fp[2], 'pair_max_ms': fp[3],
               'q_up_ms': None, 'q_down_ms': None, 'q_pair_ms': None, 'q_hat_ms': None, 'q_min_ms': None,
               'baseline_done': 0, 'degraded': int(self.degraded), 't_det_meta_ns': None}
        # baseline: mean of the window metrics over the first baseline_s of the flow
        if self.baseline is None:
            if elapsed <= self.a.baseline_s:
                if mu is not None:
                    self.base_samples['up'].append(mu)
                if md is not None:
                    self.base_samples['down'].append(md)
                if fp[1] is not None:
                    self.base_samples['pair'].append(fp[1])
                return self._finish_row(row)
            self.baseline = {k: (statistics.mean(v) if v else None)
                             for k, v in self.base_samples.items()}
        row['baseline_done'] = 1
        qu = None if (mu is None or self.baseline['up'] is None) else mu - self.baseline['up']
        qd = None if (md is None or self.baseline['down'] is None) else md - self.baseline['down']
        qp = None if (fp[1] is None or self.baseline['pair'] is None) else fp[1] - self.baseline['pair']
        if self.a.metric == 'pair':
            q_hat = qp
        elif self.a.dir == 'up':
            q_hat = qu
        elif self.a.dir == 'down':
            q_hat = qd
        else:
            q_hat = None if (qu is None and qd is None) else max(x for x in (qu, qd) if x is not None)
        self.q_hist.append((t_end, q_hat))
        while self.q_hist and t_end - self.q_hist[0][0] >= self.interval_ns:
            self.q_hist.popleft()
        known = [q for _, q in self.q_hist if q is not None]
        q_min = min(known) if known else None
        row.update({'q_up_ms': qu, 'q_down_ms': qd, 'q_pair_ms': qp,
                    'q_hat_ms': (None if q_hat is None else round(q_hat, 3)),
                    'q_min_ms': (None if q_min is None else round(q_min, 3))})
        if q_min is not None:
            if not self.degraded and q_min > self.theta_high:
                self.degraded = True
                self.enters += 1
                row['enter'] = True
            elif self.degraded and q_min < self.theta_low:
                self.degraded = False
                self.leaves += 1
                row['leave'] = True
        row['degraded'] = int(self.degraded)
        return self._finish_row(row)

    def _finish_row(self, row):
        for k in ('iat_med_up_ms', 'iat_p90_up_ms', 'iat_max_up_ms', 'iat_mad_up_ms',
                  'iat_med_down_ms', 'iat_p90_down_ms', 'iat_max_down_ms', 'iat_mad_down_ms',
                  'pair_med_ms', 'pair_min_ms', 'pair_max_ms', 'q_up_ms', 'q_down_ms', 'q_pair_ms'):
            if row[k] is not None:
                row[k] = round(row[k], 3)
        self.rows.append(row)
        return row

    def stats(self):
        return {'window_s': self.a.window_s, 'step_s': self.a.step_s, 'interval_s': self.a.interval_s,
                'baseline_s': self.a.baseline_s, 'theta_high_ms': self.theta_high,
                'theta_low_ms': self.theta_low, 'metric': self.a.metric, 'dir': self.a.dir,
                'burst_eps_ms': self.a.burst_eps_ms, 'ap_net': self.a.ap_net,
                'baseline_ms': self.baseline, 'first_pkt_ns': self.first_pkt_ns,
                'packets': dict(self.counts), 'steps': self.step,
                'enters': self.enters, 'leaves': self.leaves,
                't_det_meta_ns': self.t_det_meta_ns}


# ---- live run on N3 --------------------------------------------------------------------
class Live:
    """Runs tcpdump, listens for N1 keepalives on the flag port, sends flags, starts the N5
    load at t0, and writes the run files."""

    def __init__(self, a, det):
        self.a, self.det = a, det
        self.out = a.out or f'/home/yubin/n3runs/{a.stage.lower()}'
        os.makedirs(self.out, exist_ok=True)
        self.base = os.path.join(self.out, f'n3_run_{a.run}')
        self.flag_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.flag_sock.bind(('0.0.0.0', a.flag_port))
        self.flag_sock.setblocking(False)
        self.load_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.load_sock.setblocking(False)
        self.peer = None
        self.keepalives = 0
        self.t_run_start_ns = None
        self.t0_ns = None
        self.n5_msgs = []
        self.load_sent = 0
        self.fseq = 0
        self.flags = []
        self.stop = False
        self.tcpdump = None
        self.tcpdump_version = None
        self.started = dt.datetime.now().astimezone().isoformat(timespec='seconds')
        self.t_start_wall_ns = time.time_ns()

    def start_tcpdump(self):
        try:
            self.tcpdump_version = subprocess.run(['tcpdump', '--version'], capture_output=True,
                                                  text=True).stderr.strip().splitlines()[0]
        except (OSError, IndexError):
            self.tcpdump_version = None
        cmd = ['tcpdump', '-i', self.a.iface, '-s', '96', '-n', '-tt', '-l', '-U']
        if not self.a.no_pcap:
            cmd += ['--print', '-w', self.base + '.pcap']
        cmd += self.a.filter.split()
        self.tcpdump = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        stdin=subprocess.DEVNULL, text=True, bufsize=1)
        os.set_blocking(self.tcpdump.stdout.fileno(), False)
        print(f'tcpdump: {" ".join(cmd)}', flush=True)

    def send_flag(self, degrade, step):
        if not self.peer:
            return
        self.fseq += 1
        t_det = time.time_ns()
        self.flag_sock.sendto(with_checksum(f'F,{self.fseq},{degrade},{t_det}'), self.peer)
        t_send = time.time_ns()
        self.flags.append({'fseq': self.fseq, 'degrade': degrade, 't_det_ns': t_det,
                           't_send_ns': t_send, 'peer': f'{self.peer[0]}:{self.peer[1]}', 'step': step})
        print(f'flag {self.fseq} degrade={degrade} -> {self.peer[0]}:{self.peer[1]}', flush=True)
        return t_det

    def start_load(self):
        secs = LOADS[self.a.load] if self.a.load_seconds is None else self.a.load_seconds
        body = (f'L,{self.a.load},{secs:g},{self.a.load_proto},{self.a.load_rate},'
                f'{self.a.load_server},{self.a.run}')
        self.t0_ns = time.time_ns()
        self.load_sock.sendto(with_checksum(body), (self.a.n5, self.a.load_port))
        self.load_sent += 1
        print(f't0 = {self.t0_ns} ({(self.t0_ns - self.t_run_start_ns) / 1e9:.3f} s after run start): '
              f'{body} -> {self.a.n5}:{self.a.load_port}', flush=True)

    def stop_load(self):
        self.load_sock.sendto(with_checksum('S'), (self.a.n5, self.a.load_port))

    def poll_sockets(self):
        for _ in range(64):
            try:
                data, src = self.flag_sock.recvfrom(256)
            except BlockingIOError:
                break
            if data.startswith(b'K,'):
                self.keepalives += 1
                if self.peer != src:
                    print(f'keepalive from {src[0]}:{src[1]}', flush=True)
                    if self.t_run_start_ns is None or self.a.restart_on_new_peer:
                        self.t_run_start_ns = time.time_ns()
                        print(f'run start on N3 clock: {self.t_run_start_ns}', flush=True)
                self.peer = src
        for _ in range(8):
            try:
                data, _src = self.load_sock.recvfrom(256)
            except BlockingIOError:
                break
            msg = data.decode('ascii', 'replace').strip()
            self.n5_msgs.append({'t_ns': time.time_ns(), 'msg': msg})
            print(f'N5: {msg}', flush=True)

    def run(self):
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, 'stop', True))
        signal.signal(signal.SIGHUP, lambda *_: setattr(self, 'stop', True))
        self.start_tcpdump()
        deadline = time.monotonic() + self.a.seconds
        t0_due = None
        buf = ''
        try:
            while not self.stop and time.monotonic() < deadline:
                r, _, _ = select.select([self.tcpdump.stdout, self.flag_sock, self.load_sock], [], [], 0.02)
                if self.tcpdump.stdout in r:
                    chunk = self.tcpdump.stdout.read()
                    if chunk:
                        buf += chunk
                        *lines, buf = buf.split('\n')
                        for ln in lines:
                            p = parse_line(ln)
                            if p:
                                self.det.add(p)
                    elif self.tcpdump.poll() is not None:
                        print('tcpdump exited', flush=True)
                        break
                self.poll_sockets()
                if self.a.load and self.t_run_start_ns and t0_due is None:
                    t0_due = self.t_run_start_ns + int(self.a.t0_s * 1e9)
                if t0_due is not None and self.t0_ns is None and time.time_ns() >= t0_due:
                    self.start_load()
                now_ns = time.time_ns() - int(self.a.lag_ms * 1e6)
                for row in self.det.due_steps(now_ns):
                    if row.pop('enter', False):
                        row['t_det_meta_ns'] = self.send_flag(1, row['step']) or time.time_ns()
                        if self.det.t_det_meta_ns is None:
                            self.det.t_det_meta_ns = row['t_det_meta_ns']
                        print(f"enter degraded at step {row['step']} ({row['elapsed_s']} s), "
                              f"q_min {row['q_min_ms']} ms", flush=True)
                    elif row.pop('leave', False):
                        self.send_flag(0, row['step'])
                        print(f"leave degraded at step {row['step']} ({row['elapsed_s']} s)", flush=True)
        except KeyboardInterrupt:
            pass
        finally:
            if self.tcpdump and self.tcpdump.poll() is None:
                self.tcpdump.terminate()
                try:
                    self.tcpdump.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.tcpdump.kill()
            if self.a.load and self.t0_ns is not None:
                self.stop_load()
            self.write()

    def write(self):
        err = self.tcpdump.stderr.read().strip() if self.tcpdump else ''
        write_windows(self.base + '_windows.csv', self.det.rows)
        with open(self.base + '_flags.csv', 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=FLAG_FIELDS)
            w.writeheader()
            w.writerows(self.flags)
        meta = {
            'run_id': self.a.run, 'stage': self.a.stage.upper(), 'node': 'N3', 'harness': 'detector',
            'started': self.started, 'finished': dt.datetime.now().astimezone().isoformat(timespec='seconds'),
            'iface': self.a.iface, 'filter': self.a.filter, 'tcpdump': self.tcpdump_version,
            'tcpdump_stderr': err, 'pcap': (None if self.a.no_pcap else self.base + '.pcap'),
            'git': self.a.git, 'note': self.a.note,
            'detector': self.det.stats(),
            't_start_wall_ns': self.t_start_wall_ns,
            't_run_start_ns': self.t_run_start_ns, 'run_peer': (f'{self.peer[0]}:{self.peer[1]}' if self.peer else None),
            'keepalives': self.keepalives,
            'load': ({'kind': self.a.load, 'n5': self.a.n5, 'server': self.a.load_server,
                      'proto': self.a.load_proto, 'rate': self.a.load_rate,
                      'seconds': (LOADS[self.a.load] if self.a.load_seconds is None else self.a.load_seconds),
                      't0_s_planned': self.a.t0_s, 'n5_messages': self.n5_msgs} if self.a.load else None),
            't0_ns': self.t0_ns,
            't_det_meta_ns': self.det.t_det_meta_ns,
            'a_ms': (None if (self.t0_ns is None or self.det.t_det_meta_ns is None)
                     else round((self.det.t_det_meta_ns - self.t0_ns) / 1e6, 3)),
            'flags_sent': len(self.flags),
        }
        with open(self.base + '_meta.json', 'w') as f:
            json.dump(meta, f, indent=2)
        d = self.det
        print(f"steps {d.step}, packets up {d.counts['up']} down {d.counts['down']} other {d.counts['other']}, "
              f"baseline {d.baseline}, enters {d.enters}, leaves {d.leaves}, flags {len(self.flags)}, "
              f"t0 {self.t0_ns}, t_det_meta {d.t_det_meta_ns}, A {meta['a_ms']} ms", flush=True)
        if err:
            print(err, flush=True)
        print(f'files: {self.base}_windows.csv, _flags.csv, _meta.json', flush=True)


def write_windows(path, rows):
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=WINDOW_FIELDS, extrasaction='ignore')
        w.writeheader()
        for r in rows:
            w.writerow({k: ('' if r.get(k) is None else r.get(k)) for k in WINDOW_FIELDS})


# ---- replay (A0 threshold study, A7 self-check, tests) ----------------------------------
def replay(det, packets, t0_ns=None):
    """Feeds a finished packet list through the detector; the time of the first flag is the
    end of the step that entered (no send latency). Returns the rows."""
    for p in packets:
        for row in det.due_steps(p[0]):
            _mark(det, row, t0_ns)
        det.add(p)
    return det.rows


def _mark(det, row, t0_ns):
    if row.pop('enter', False):
        row['t_det_meta_ns'] = row['t_ns']
        if det.t_det_meta_ns is None:
            det.t_det_meta_ns = row['t_ns']
    row.pop('leave', None)


def replay_windows(path, a):
    """Re-decides an existing windows file with other thresholds, metric or interval, from
    the per-window features (no packets needed). Prints the first entry time."""
    with open(path, newline='') as f:
        rows = list(csv.DictReader(f))
    col = {'med': 'iat_med_{}_ms', 'p90': 'iat_p90_{}_ms', 'mad': 'iat_mad_{}_ms',
           'pair': 'pair_med_ms'}[a.metric]
    dirs = ('pair',) if a.metric == 'pair' else ('up', 'down')
    base, samples = {}, {d: [] for d in dirs}
    interval_ns = int(a.interval_s * 1e9)
    hist, degraded, first, enters, leaves = deque(), False, None, 0, 0
    for r in rows:
        el = float(r['elapsed_s'])
        vals = {d: (float(r[col.format(d)]) if r[col.format(d)] else None) for d in dirs}
        if el <= a.baseline_s:
            for d in dirs:
                if vals[d] is not None:
                    samples[d].append(vals[d])
            continue
        if not base:
            base = {d: (statistics.mean(v) if v else None) for d, v in samples.items()}
        q = {d: (None if vals[d] is None or base[d] is None else vals[d] - base[d]) for d in dirs}
        if a.metric == 'pair':
            q_hat = q['pair']
        elif a.dir in ('up', 'down'):
            q_hat = q[a.dir]
        else:
            known = [x for x in q.values() if x is not None]
            q_hat = max(known) if known else None
        t = int(r['t_ns'])
        hist.append((t, q_hat))
        while hist and t - hist[0][0] >= interval_ns:
            hist.popleft()
        known = [x for _, x in hist if x is not None]
        q_min = min(known) if known else None
        if q_min is None:
            continue
        if not degraded and q_min > a.theta_high_ms:
            degraded, enters = True, enters + 1
            if first is None:
                first = (t, el)
        elif degraded and q_min < a.theta_low_ms:
            degraded, leaves = False, leaves + 1
    print(f'{path}: metric {a.metric} dir {a.dir} interval {a.interval_s} s theta {a.theta_high_ms}/{a.theta_low_ms} ms '
          f'baseline {base}: first entry {first}, enters {enters}, leaves {leaves}')
    return first, enters, leaves


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--run', type=int, help='run number N -> n3_run_N_*')
    ap.add_argument('--stage', default='c3')
    ap.add_argument('--out', default=None, help='output folder (default /home/yubin/n3runs/<stage>)')
    ap.add_argument('--iface', default='wlan0')
    ap.add_argument('--filter', default=ROBOT_FILTER)
    ap.add_argument('--seconds', type=float, default=90.0, help='stop after this long (a run is 60 s + margin)')
    ap.add_argument('--no-pcap', action='store_true')
    ap.add_argument('--lag-ms', type=float, default=30.0,
                    help='live: evaluate a step this long after its end, so late tcpdump lines land in it')
    ap.add_argument('--ap-net', default=AP_NET, help='address prefix of the wlan0 side (uplink sources)')
    ap.add_argument('--window-s', type=float, default=WINDOW_S)
    ap.add_argument('--step-s', type=float, default=STEP_S)
    ap.add_argument('--interval-s', type=float, default=INTERVAL_S)
    ap.add_argument('--baseline-s', type=float, default=BASELINE_S)
    ap.add_argument('--theta-high-ms', type=float, default=THETA_HIGH_MS)
    ap.add_argument('--theta-low-ms', type=float, default=THETA_LOW_MS)
    ap.add_argument('--metric', default='med', choices=('med', 'p90', 'mad', 'pair'),
                    help='window statistic q_hat is built from (plan: med; A0 decides)')
    ap.add_argument('--dir', default='both', choices=('both', 'up', 'down'))
    ap.add_argument('--burst-eps-ms', type=float, default=BURST_EPS_MS)
    ap.add_argument('--flag-port', type=int, default=FLAG_PORT)
    ap.add_argument('--restart-on-new-peer', action='store_true',
                    help='a keepalive from a new N1 port restarts the run clock (one detector for several runs)')
    ap.add_argument('--load', default=None, choices=tuple(LOADS))
    ap.add_argument('--n5', default=None, help='N5 address running load/n5_agent.py')
    ap.add_argument('--load-port', type=int, default=LOAD_PORT)
    ap.add_argument('--load-server', default='192.168.60.1', help='iperf3 server N5 connects to')
    ap.add_argument('--load-proto', default='udp', choices=('udp', 'tcp'))
    ap.add_argument('--load-rate', default='60M', help='iperf3 -b for udp (provisional until A8)')
    ap.add_argument('--load-seconds', type=float, default=None, help='override the L1/L2 duration')
    ap.add_argument('--t0-s', type=float, default=T0_S, help='load start after the first N1 keepalive')
    ap.add_argument('--git', default='', help='commit of capture/detector.py on N1 (N3 has no clone)')
    ap.add_argument('--note', default='')
    ap.add_argument('--replay', metavar='PCAP', help='offline: decide over a pcap and write the files')
    ap.add_argument('--replay-lines', metavar='TXT', help='offline: tcpdump -n -tt text instead of a pcap')
    ap.add_argument('--replay-windows', metavar='CSV', help='offline: re-decide a windows file')
    ap.add_argument('--t0-ns', type=int, default=None, help='replay: t0 on the capture clock, for A')
    return ap


def main():
    a = build_parser().parse_args()
    if a.load and not a.n5:
        raise SystemExit('--load needs --n5')
    if a.replay_windows:
        replay_windows(a.replay_windows, a)
        return
    if a.replay or a.replay_lines:
        if a.run is None:
            raise SystemExit('--run is needed to name the output files')
        det = Detector(a)
        t_start = time.perf_counter()
        if a.replay:
            packets = list(read_pcap(a.replay))
        else:
            with open(a.replay_lines) as f:
                packets = list(read_lines(f))
        rows = replay(det, packets, a.t0_ns)
        took = time.perf_counter() - t_start
        out = a.out or os.path.dirname(os.path.abspath(a.replay or a.replay_lines))
        os.makedirs(out, exist_ok=True)
        base = os.path.join(out, f'n3_run_{a.run}')
        write_windows(base + '_windows.csv', rows)
        meta = {'run_id': a.run, 'stage': a.stage.upper(), 'node': 'N3', 'harness': 'detector-replay',
                'source': a.replay or a.replay_lines, 'detector': det.stats(), 't0_ns': a.t0_ns,
                't_det_meta_ns': det.t_det_meta_ns,
                'a_ms': (None if a.t0_ns is None or det.t_det_meta_ns is None
                         else round((det.t_det_meta_ns - a.t0_ns) / 1e6, 3)),
                'replay_seconds': round(took, 3), 'git': a.git, 'note': a.note}
        with open(base + '_meta.json', 'w') as f:
            json.dump(meta, f, indent=2)
        d = det
        print(f"{len(packets)} packets in {took:.2f} s: steps {d.step}, baseline {d.baseline}, "
              f"enters {d.enters}, leaves {d.leaves}, t_det_meta {d.t_det_meta_ns}, A {meta['a_ms']} ms")
        print(f'files: {base}_windows.csv, _meta.json')
        return
    if a.run is None:
        raise SystemExit('--run is required')
    Live(a, Detector(a)).run()


if __name__ == '__main__':
    main()
