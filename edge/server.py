#!/usr/bin/env python3
"""N4 edge controller: UDP server answering N1 state datagrams with commands (A10).

Standard library only, no ROS, so it runs under native Windows Python on the lab PC
(`py edge\\server.py`) as well as in WSL2. The datagram format and the parsers are those
of sim/bridge/edge.py, and the controller reuses the waypoint formulas of sim/nav.py, so
the only thing that changes between the loopback check (A10 dry run) and the lab is the
listen address.

  N1 -> N4  Q,<seq>,<t_send_ns>,<x_mm>,<y_mm>,<yaw_mrad>,<min_mm>,<wp_i>*XX
  N4 -> N1  R,<seq>,<t_send_ns>,<v_mm>,<w_mrad>*XX

seq and t_send_ns are copied back unchanged: N1 reads the round trip off the reply.

Controller (decided 9/22). The default is the learned controller, a small MLP
(edge/mlp.py, weights edge/mlp_weights.json from edge/train_mlp.py) that imitates the
waypoint follower with no speed rule. It steers towards WAYPOINTS[wp_i] from the state N1
sends (N1 advances wp_i) and ignores min_range: the edge has no safety layer of its own,
the safety rule runs on the robot (N2), which is the Simplex premise of the paper.

--controller follower runs the follower itself, for comparison and fallback. Its speed
rule is --rule none by default (D21); --rule a1 adds the A1 speed rule from min_range.
Neither controller nor weights are visible to N1, so the startup line prints both, with
the weights SHA-1, for the bridge run's --note.

Log (--log): one row per datagram, N4 monotonic clock. proc_us is the time from recvfrom
returning to sendto returning, the server's own share of the round trip. It is taken
with time.perf_counter_ns, not the monotonic clock: on Windows time.monotonic ticks every
15.6 ms, so a proc figure from it reads 0 or about 16000 us and says nothing about a
controller that takes tens of microseconds.

Test delays (never in a sweep run; netem on N3 is the real thing): --delay-ms holds every
reply back by a fixed time, the loopback stand-in for a base RTT (plan 9.2), and
--extra-ms/--extra-from add more from a given second after the first datagram, a
stand-in for the load at t0 that policy 3 has to notice. Delayed replies go out from a
timer thread, so proc_us then measures only the controller, not the wait.

  python3 edge/server.py --listen 127.0.0.1:47000            (loopback, MLP)
  python3 edge/server.py --listen 127.0.0.1:47000 --controller follower
  py edge\\server.py --listen 0.0.0.0:47000 --log data\\a10\\edge_run_1.csv   (lab PC)
"""
import argparse
import csv
import hashlib
import json
import os
import socket
import sys
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(REPO, 'sim'), os.path.join(REPO, 'fw', 'tools'),
          os.path.join(REPO, 'edge')):
    if p not in sys.path:
        sys.path.insert(0, p)

import nav                                  # noqa: E402
import mlp as mlpmod                        # noqa: E402
from bridge import edge as edgelink         # noqa: E402

WEIGHTS = os.path.join(REPO, 'edge', 'mlp_weights.json')

LISTEN = '0.0.0.0:47000'
LOG_FIELDS = ['n', 't_rx_ns', 'seq', 't_send_ns', 'x_mm', 'y_mm', 'yaw_mrad', 'min_mm',
              'wp_i', 'v_mm', 'w_mrad', 'proc_us']


def speed_rule(name, min_mm, v_mps):
    """Edge-side speed cap. 'a1' is the float rule of A1 (sim/nav.py, mirrored on the board
    in integers); 'none' leaves the follower speed as it is."""
    if name == 'none' or v_mps == 0.0:
        return v_mps
    _, cap = nav.a1_float_speed(min_mm / 1000.0)
    return min(v_mps, cap)


def _wire(v_mps, w_radps):
    return int(round(v_mps * 1000.0)), max(-32768, min(32767, int(round(w_radps * 1000.0))))


class FollowerController:
    """The rule-based waypoint follower of sim/nav.py, with an optional speed rule."""
    name = 'follower'

    def __init__(self, rule='none', waypoints=nav.WAYPOINTS):
        self.rule = rule
        self.waypoints = list(waypoints)

    def describe(self):
        return f'follower, rule {self.rule}'

    def command(self, x_mm, y_mm, yaw_mrad, min_mm, wp_i):
        """-> (v_mm, w_mrad) for the state N1 sent."""
        if wp_i < 0 or wp_i >= len(self.waypoints):
            return 0, 0
        _, _, turning, w = nav.heading(x_mm / 1000.0, y_mm / 1000.0, yaw_mrad / 1000.0,
                                       self.waypoints[wp_i])
        v = 0.0 if turning else nav.V_MAX
        return _wire(speed_rule(self.rule, min_mm, v), w)


class MLPController:
    """The learned controller: edge/mlp.py on the relative state to the current waypoint."""
    name = 'mlp'

    def __init__(self, weights=WEIGHTS, waypoints=nav.WAYPOINTS):
        self.net = mlpmod.MLP(weights)
        self.waypoints = list(waypoints)
        with open(weights, 'rb') as f:
            self.sha1 = hashlib.sha1(f.read()).hexdigest()[:12]

    def describe(self):
        t = self.net.spec.get('train', {})
        return (f"mlp, weights {self.sha1} (seed {t.get('seed')}, "
                f"{t.get('samples')} samples, course {t.get('course_s_mlp')} s)")

    def command(self, x_mm, y_mm, yaw_mrad, min_mm, wp_i):
        if wp_i < 0 or wp_i >= len(self.waypoints):
            return 0, 0
        f = mlpmod.features(x_mm / 1000.0, y_mm / 1000.0, yaw_mrad / 1000.0,
                            self.waypoints[wp_i])
        return _wire(*self.net.forward(f))


def make_controller(kind='mlp', rule='none', weights=WEIGHTS):
    return MLPController(weights) if kind == 'mlp' else FollowerController(rule)


def pct(sorted_xs, q):
    return sorted_xs[min(len(sorted_xs) - 1, int(round(q * (len(sorted_xs) - 1))))]


class Server:
    def __init__(self, host, port, controller, log_path=None, delay_s=0.0, extra_s=0.0,
                 extra_from_s=0.0):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((host, port))
        self.sock.settimeout(0.5)          # lets Ctrl-C through on Windows
        self.ctl = controller
        self.delay_s, self.extra_s, self.extra_from_s = delay_s, extra_s, extra_from_s
        self.t_first = None
        self.timers = []
        self.received = self.answered = self.bad = self.send_errors = 0
        self.proc_us = []
        self.peers = set()
        self.log = None
        if log_path:
            os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
            self.log_f = open(log_path, 'w', newline='')
            self.log = csv.DictWriter(self.log_f, fieldnames=LOG_FIELDS)
            self.log.writeheader()

    def serve_one(self):
        """One datagram in, one out. Returns False on timeout."""
        try:
            data, addr = self.sock.recvfrom(256)
        except socket.timeout:
            return False
        t_rx = time.monotonic_ns()
        p_rx = time.perf_counter_ns()
        try:
            seq, t_send, x_mm, y_mm, yaw_mrad, min_mm, wp_i = edgelink.parse_q(data)
        except ValueError:
            self.bad += 1
            return True
        self.received += 1
        self.peers.add(addr)
        if self.t_first is None:
            self.t_first = time.monotonic()
        v_mm, w_mrad = self.ctl.command(x_mm, y_mm, yaw_mrad, min_mm, wp_i)
        reply = edgelink.build_r(seq, t_send, v_mm, w_mrad)
        delay = self.delay_s
        if self.extra_s and time.monotonic() - self.t_first >= self.extra_from_s:
            delay += self.extra_s
        if delay > 0:
            t = threading.Timer(delay, self._send, (reply, addr))
            t.daemon = True
            self.timers.append(t)
            t.start()
            if len(self.timers) > 200:
                self.timers = [x for x in self.timers if x.is_alive()]
        else:
            self._send(reply, addr)
        proc = (time.perf_counter_ns() - p_rx) // 1000
        self.proc_us.append(proc)
        if self.log:
            self.log.writerow({'n': self.received, 't_rx_ns': t_rx, 'seq': seq,
                               't_send_ns': t_send, 'x_mm': x_mm, 'y_mm': y_mm,
                               'yaw_mrad': yaw_mrad, 'min_mm': min_mm, 'wp_i': wp_i,
                               'v_mm': v_mm, 'w_mrad': w_mrad, 'proc_us': proc})
        return True

    def _send(self, reply, addr):
        try:
            self.sock.sendto(reply, addr)
            self.answered += 1
        except OSError:
            self.send_errors += 1

    def stats(self):
        s = {'received': self.received, 'answered': self.answered, 'bad': self.bad,
             'send_errors': self.send_errors, 'peers': sorted(f'{h}:{p}' for h, p in self.peers)}
        if self.proc_us:
            p = sorted(self.proc_us)
            s.update({'proc_us_median': p[len(p) // 2], 'proc_us_p99': pct(p, 0.99),
                      'proc_us_max': p[-1]})
        return s

    def close(self):
        for t in self.timers:
            t.cancel()
        self.sock.close()
        if self.log:
            self.log_f.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--listen', default=LISTEN, metavar='HOST:PORT')
    ap.add_argument('--controller', default='mlp', choices=('mlp', 'follower'))
    ap.add_argument('--weights', default=WEIGHTS, help='MLP weights (edge/train_mlp.py)')
    ap.add_argument('--rule', default='none', choices=('none', 'a1'),
                    help='follower only: edge-side speed rule (D21: none)')
    ap.add_argument('--log', default=None, help='per-datagram CSV')
    ap.add_argument('--delay-ms', type=float, default=0.0, help='test: hold every reply')
    ap.add_argument('--extra-ms', type=float, default=0.0,
                    help='test: additional delay from --extra-from seconds on')
    ap.add_argument('--extra-from', type=float, default=15.0, metavar='S',
                    help='test: seconds after the first datagram (plan 4.1: t0 = 15 s)')
    ap.add_argument('--stats-every', type=float, default=5.0, metavar='S')
    ap.add_argument('--quit-after', type=float, default=0.0, metavar='S',
                    help='exit after this many seconds (0 = run until Ctrl-C)')
    args = ap.parse_args()
    host, _, port = args.listen.rpartition(':')
    ctl = make_controller(args.controller, args.rule, args.weights)
    srv = Server(host or '0.0.0.0', int(port), ctl, args.log,
                 args.delay_ms / 1000.0, args.extra_ms / 1000.0, args.extra_from)
    print(f'N4 edge controller on {args.listen}, {ctl.describe()}, '
          f'{len(nav.WAYPOINTS)} waypoints, python {sys.version.split()[0]} '
          f'{sys.platform}' + (f', TEST delay {args.delay_ms} ms' if args.delay_ms else '')
          + (f' +{args.extra_ms} ms from {args.extra_from} s' if args.extra_ms else ''),
          flush=True)
    t_start = time.monotonic()
    next_stats = t_start + args.stats_every
    try:
        while True:
            srv.serve_one()
            now = time.monotonic()
            if now >= next_stats:
                s = srv.stats()
                print(f"  got {s['received']} answered {s['answered']} bad {s['bad']} "
                      f"proc p99 {s.get('proc_us_p99')} us", flush=True)
                next_stats = now + args.stats_every
            if args.quit_after and now - t_start >= args.quit_after:
                break
    except KeyboardInterrupt:
        pass
    srv.close()
    print(json.dumps(srv.stats()), flush=True)


if __name__ == '__main__':
    main()
