"""Self-check of the edge link against a fake N4, of the N4 controller (edge/server.py)
and of the RTT window watcher, with no board, no ROS and no network.

The fake server answers every Q after a set delay with v = seq, so the column edge_v of a
step names the datagram whose command the robot is driving on. That makes holding,
staleness and the deadline miss count readable straight off the step log.

  python3 -m bridge.test_edge                 (from sim/)

It also serves as a stand-in N4 with a test pattern instead of control, for link tests:

  python3 -m bridge.test_edge --serve 47000 --delay-ms 60

and as the A10 probe against a running edge/server.py (pass: p99 < 10 ms, 1000 requests,
none unanswered; plan 7.3):

  python3 -m bridge.test_edge --probe 127.0.0.1:47000 --count 1000
"""
import argparse
import importlib.util
import random
import socket
import threading
import time

from . import edge as edgelink, rttwatch
from .paths import REPO
import nav

PERIOD_S = 0.05


def load_server():
    """edge/server.py is a script outside the package; import it by path."""
    spec = importlib.util.spec_from_file_location('edge_server', REPO / 'edge' / 'server.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeEdge:
    """Echoes a command for every state datagram after delay_s, dropping a share of them.
    v = seq, w = -seq, so the applied command identifies its datagram."""

    def __init__(self, delay_s=0.0, loss=0.0, corrupt=0, unknown=0, seed=7):
        self.delay_s = delay_s
        self.loss = loss
        self.corrupt = corrupt        # first N replies sent with a broken checksum
        self.unknown = unknown        # extra replies for a seq that was never sent
        self.rng = random.Random(seed)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.settimeout(0.05)
        self.received = 0
        self.sent = 0
        self._timers = []
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    @property
    def port(self):
        return self.sock.getsockname()[1]

    def start(self):
        self.thread.start()

    def stop(self):
        self._stop.set()
        self.thread.join(timeout=1.0)
        for t in self._timers:
            t.cancel()
        self.sock.close()

    def _reply(self, seq, t_send, addr):
        if self.corrupt > 0:
            self.corrupt -= 1
            body = f'R,{seq},{t_send},{seq},{-seq}'
            data = f'{body}*00\n'.encode()          # checksum that will not match
        else:
            data = edgelink.build_r(seq, t_send, seq, -seq)
        try:
            self.sock.sendto(data, addr)
            self.sent += 1
            if self.unknown > 0:
                self.unknown -= 1
                self.sock.sendto(edgelink.build_r(900000 + seq, t_send, 0, 0), addr)
        except OSError:
            pass

    def _loop(self):
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                continue
            try:
                seq, t_send = edgelink.parse_q(data)[:2]
            except ValueError:
                continue
            self.received += 1
            if self.rng.random() < self.loss:
                continue
            if self.delay_s <= 0:
                self._reply(seq, t_send, addr)
            else:
                t = threading.Timer(self.delay_s, self._reply, (seq, t_send, addr))
                t.daemon = True
                self._timers.append(t)
                t.start()


def drive(client, steps, period_s=PERIOD_S):
    """Calls step() on a fixed cadence, the way the scan callback does."""
    rows, t0 = [], time.monotonic()
    for k in range(1, steps + 1):
        due = t0 + (k - 1) * period_s
        time.sleep(max(0.0, due - time.monotonic()))
        r = client.step(k, k, 0, 0, 1000, 0)
        r['seq'] = k
        rows.append(r)
    return rows


def check(name, ok, detail=''):
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{(': ' + detail) if detail else ''}")
    return ok


def scenario(title, steps=40, **kw):
    server = FakeEdge(**kw)
    server.start()
    client = edgelink.EdgeClient('127.0.0.1', server.port)
    client.start()
    rows = drive(client, steps)
    time.sleep(0.3)                      # let the last replies land before the figures
    client.stop()
    server.stop()
    st = client.stats()
    print(f'\n{title}')
    print(f"  server: got {server.received}, answered {server.sent}")
    print(f"  client: {st}")
    return rows, st, server


def applied_matches_seq(rows):
    """v = seq on the fake server, so the command must name the datagram it answered."""
    bad = [r for r in rows if r['edge_ok'] and r['edge_v'] != r['edge_seq_used']]
    return not bad, f'{len(bad)} rows where edge_v != edge_seq_used'


def held_is_previous(rows):
    """Every held step must repeat the command of the last step that got a fresh one."""
    last = None
    for r in rows:
        if not r['held']:
            last = (r['edge_v'], r['edge_w'], r['edge_seq_used'])
        elif last is not None:
            if (r['edge_v'], r['edge_w'], r['edge_seq_used']) != last:
                return False, f"step {r['seq']} held {r['edge_v']} instead of {last[0]}"
        elif r['edge_v'] != 0 or r['edge_ok'] != 0:
            return False, f"step {r['seq']} invented a command before any reply"
    return True, ''


def no_measurement_on_hold(rows):
    bad = [r for r in rows if r['held'] == 1 and r['rtt_us'] is not None]
    return not bad, f'{len(bad)} held steps carry an rtt'


def miss_matches_age(rows):
    """deadline_miss must say exactly: the command is older than one period."""
    for r in rows:
        want = 1 if (r['held'] or r['edge_seq_used'] is None
                     or r['edge_seq_used'] < r['seq'] - 1) else 0
        if r['deadline_miss'] != want:
            return False, f"step {r['seq']}: miss {r['deadline_miss']}, expected {want}"
    return True, ''


def main():
    ok = True

    print('protocol')
    q = edgelink.build_q(7, 123456789, -1200, 340, -1571, 2650, 3)
    ok &= check('Q round trip', edgelink.parse_q(q) == (7, 123456789, -1200, 340, -1571, 2650, 3))
    r = edgelink.build_r(7, 123456789, 220, -150)
    ok &= check('R round trip', edgelink.parse_r(r) == (7, 123456789, 220, -150))
    ok &= check('both end in a newline', q.endswith(b'\n') and r.endswith(b'\n'))
    try:
        edgelink.parse_r(r.replace(b'220', b'221'))
        ok &= check('broken checksum rejected', False)
    except ValueError:
        ok &= check('broken checksum rejected', True)
    try:
        edgelink.parse_r(q)
        ok &= check('a Q is not an R', False)
    except ValueError:
        ok &= check('a Q is not an R', True)
    sizes = {len(edgelink.build_q(s, 1234567890123456789, -1999, 1999, -3141, 65535, 4))
             for s in range(1, 1200)}
    ok &= check('Q size varies by at most a few bytes', max(sizes) - min(sizes) <= 4,
                f'{min(sizes)}..{max(sizes)} bytes')

    rows, st, _ = scenario('healthy link (no added delay)', corrupt=2, unknown=1)
    ok &= check('only the two broken replies went unanswered', st['unanswered'] == 2,
                f"unanswered {st['unanswered']}")
    ok &= check('broken replies counted, not applied', st['bad'] == 2, f"bad={st['bad']}")
    ok &= check('reply to an unsent seq ignored', st['unknown'] == 1)
    ok &= check('echo of t_send_ns intact', st['echo_mismatch'] == 0)
    ok &= check('round trip well under the period', st['rtt_ms_p99'] < 10,
                f"p99 {st['rtt_ms_p99']} ms")
    # the first step cannot have an answer yet, and the two corrupted ones are lost
    ok &= check('almost every step drives on a fresh command', st['hold_rate'] <= 0.15,
                f"hold rate {st['hold_rate']}")
    ok &= check('hardly any deadline missed', st['miss_rate_steps'] <= 0.15,
                f"M by steps {st['miss_rate_steps']}")
    ok &= check('command one step old', st.get('sent') and
                max(r['seq'] - r['edge_seq_used'] for r in rows if r['edge_ok']) <= 3)
    ok &= check('applied command names its datagram', *applied_matches_seq(rows))
    ok &= check('deadline_miss means an over-age command', *miss_matches_age(rows))
    ok &= check('a held step repeats the previous command', *held_is_previous(rows))
    ok &= check('a held step carries no round trip', *no_measurement_on_hold(rows))

    rows, st, _ = scenario('60 ms round trip (just over one period)', delay_s=0.060)
    ok &= check('measured round trip matches the fake delay', 50 <= st['rtt_ms_median'] <= 75,
                f"median {st['rtt_ms_median']} ms")
    ok &= check('a steady delay causes almost no holds', st['hold_rate'] <= 0.15,
                f"hold rate {st['hold_rate']}")
    ok &= check('every datagram missed its deadline', st['miss_rate'] >= 0.9,
                f"M {st['miss_rate']}")
    ok &= check('and every step shows it', st['miss_rate_steps'] >= 0.9,
                f"M by steps {st['miss_rate_steps']}")
    ok &= check('commands are one step staler than healthy',
                max(r['seq'] - r['edge_seq_used'] for r in rows if r['edge_ok']) <= 4)
    ok &= check('a held step repeats the previous command', *held_is_previous(rows))
    ok &= check('applied command names its datagram', *applied_matches_seq(rows))
    ok &= check('deadline_miss means an over-age command', *miss_matches_age(rows))

    rows, st, _ = scenario('200 ms round trip (the worst sweep step)', delay_s=0.200)
    ok &= check('measured round trip matches the fake delay', 180 <= st['rtt_ms_median'] <= 240,
                f"median {st['rtt_ms_median']} ms")
    ok &= check('a steady delay causes almost no holds', st['hold_rate'] <= 0.2,
                f"hold rate {st['hold_rate']}")
    ok &= check('every step drives on a stale command', st['miss_rate_steps'] >= 0.9,
                f"M by steps {st['miss_rate_steps']}")
    ok &= check('the robot never stops for want of a command',
                all(r['edge_v'] != 0 for r in rows[8:]))
    ages = [r['seq'] - r['edge_seq_used'] for r in rows if r['edge_ok']]
    ok &= check('commands are stale by about four steps', 3 <= max(ages) <= 7,
                f'age up to {max(ages)} steps')
    ok &= check('a held step repeats the previous command', *held_is_previous(rows))
    ok &= check('applied command names its datagram', *applied_matches_seq(rows))
    ok &= check('deadline_miss means an over-age command', *miss_matches_age(rows))

    rows, st, _ = scenario('30 percent of replies dropped', delay_s=0.010, loss=0.3)
    ok &= check('losses show up as unanswered', st['unanswered'] > 0,
                f"unanswered {st['unanswered']}")
    ok &= check('and as holds', st['hold_rate'] >= 0.2, f"hold rate {st['hold_rate']}")
    ok &= check('the run continues on held commands', st['steps'] == 40)
    ok &= check('a held step repeats the previous command', *held_is_previous(rows))
    ok &= check('applied command names its datagram', *applied_matches_seq(rows))
    ok &= check('deadline_miss means an over-age command', *miss_matches_age(rows))

    print('\nno server listening')
    client = edgelink.EdgeClient('127.0.0.1', 9)     # discard port, nothing bound
    client.start()
    rows = drive(client, 10)
    time.sleep(0.1)
    client.stop()
    st = client.stats()
    print(f'  client: {st}')
    ok &= check('every step is a hold and a miss',
                st['hold_rate'] == 1.0 and st['miss_rate_steps'] == 1.0)
    ok &= check('command stays zero', all(r['edge_v'] == 0 and r['edge_ok'] == 0 for r in rows))
    ok &= check('no reply invented', st['replied'] == 0)

    ok &= check_server()
    ok &= check_watcher()

    print(f"\n{'all checks passed' if ok else 'FAILURES above'}")
    raise SystemExit(0 if ok else 1)


def check_server():
    """edge/server.py: commands equal the nav formulas, the echo is intact, the process
    time is small, and the client sees it as a healthy link."""
    ok = True
    print('\nN4 controller (edge/server.py)')
    srvmod = load_server()
    ctl = srvmod.Controller('a1')
    # straight ahead towards waypoint 0 from the origin: full speed, no turn
    ok &= check('aligned: V_MAX straight', ctl.command(0, 0, 0, 2000, 0) == (220, 0))
    # 90 deg off: turn in place at W_MAX
    v, w = ctl.command(1200, 0, 0, 2000, 1)
    ok &= check('90 deg off: turn in place', v == 0 and w == 1000, f'({v}, {w})')
    # small error: w = W_GAIN * err, clipped
    _, err, _, wf = nav.heading(0.0, 0.0, 0.1, nav.WAYPOINTS[0])
    v, w = ctl.command(0, 0, 100, 2000, 0)
    ok &= check('small error: proportional w', v == 220 and w == int(round(wf * 1000)),
                f'({v}, {w}) vs {wf:.3f} rad/s')
    ok &= check('a1 rule slows at 300 mm', ctl.command(0, 0, 0, 300, 0) == (110, 0),
                str(ctl.command(0, 0, 0, 300, 0)))
    ok &= check('a1 rule stops at 150 mm', ctl.command(0, 0, 0, 150, 0)[0] == 0)
    ok &= check('rule none never slows', srvmod.Controller('none').command(0, 0, 0, 150, 0)[0] == 220)
    ok &= check('past the last waypoint: zero', ctl.command(0, 0, 0, 2000, 5) == (0, 0))

    srv = srvmod.Server('127.0.0.1', 0, ctl)
    port = srv.sock.getsockname()[1]
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            srv.serve_one()
    th = threading.Thread(target=loop, daemon=True)
    th.start()
    client = edgelink.EdgeClient('127.0.0.1', port)
    client.start()
    rows = drive(client, 40)
    time.sleep(0.2)
    client.stop()
    stop.set()
    th.join(timeout=1.0)
    st, ss = client.stats(), srv.stats()
    srv.close()
    print(f'  client: {st}')
    print(f'  server: {ss}')
    ok &= check('every state answered', ss['received'] == 40 and ss['answered'] == 40 and
                st['replied'] == 40, f"got {ss['received']} answered {ss['answered']}")
    ok &= check('echo intact, nothing bad', st['echo_mismatch'] == 0 and st['bad'] == 0
                and ss['bad'] == 0)
    ok &= check('server process time p99 < 1 ms', ss['proc_us_p99'] < 1000,
                f"p99 {ss['proc_us_p99']} us")
    ok &= check('round trip p99 < 10 ms (A10 bar, loopback)', st['rtt_ms_p99'] < 10,
                f"p99 {st['rtt_ms_p99']} ms")
    # drive() sends x = seq mm, y = 0, yaw = 0, min 1000, wp 0: straight at full speed
    ok &= check('commands are the follower output', all(
        (r['edge_v'], r['edge_w']) == (220, 0) for r in rows if r['edge_ok']))
    return ok


def check_watcher():
    """rttwatch.Watcher against a scripted RTT series: baseline, a rise, a fall."""
    ok = True
    print('\nRTT window watcher (policy 3)')
    w = rttwatch.Watcher(window_s=2.0, baseline_s=15.0, theta_high_ms=20.0, theta_low_ms=10.0)
    t0 = 1_000_000_000
    rows = []
    for k in range(0, 800):                  # 40 s at 20 Hz
        el = k * PERIOD_S
        if el < 20.0:
            rtt = 60_000 + (k % 3) * 500     # 60 ms base with a little jitter
        elif el < 30.0:
            rtt = 60_000 + 40_000            # +40 ms of queueing
        else:
            rtt = 60_000
        if k == 100:
            rtt = None                       # one held step in the baseline
        rows.append((el, w.observe(t0 + int(el * 1e9), el, rtt)))
    ok &= check('RTT_min from the baseline only', w.rtt_min_us == 60_000,
                f'{w.rtt_min_us}')
    ok &= check('no verdict inside the baseline',
                all(r['rtt_degraded'] == 0 and r['t_det_rtt_ns'] is None
                    for el, r in rows if el < 15.0))
    ok &= check('a held step carries the window, not a sample',
                rows[100][1]['rtt_win_us'] is not None)
    det = [el for el, r in rows if r['t_det_rtt_ns'] is not None]
    ok &= check('exactly one detection', len(det) == 1, f'{det}')
    # a 2 s mean of a +40 ms step crosses +20 ms after about 1 s
    ok &= check('detected about 1 s after the rise', det and 20.9 <= det[0] <= 21.2,
                f'{det[0] if det else None} s')
    left = [el for (el, r), (el2, r2) in zip(rows, rows[1:])
            if r['rtt_degraded'] == 1 and r2['rtt_degraded'] == 0]
    ok &= check('left once, about 1.5 s after the fall (theta_low)',
                len(left) == 1 and 31.3 <= left[0] <= 31.7, f'{left}')
    ok &= check('enters 1, leaves 1', w.enters == 1 and w.leaves == 1)

    w2 = rttwatch.Watcher(baseline_s=1.0)
    for k in range(80):                      # answers for 1 s, then silence for 3 s
        r = w2.observe(t0 + k * 50_000_000, k * PERIOD_S, 1000 if k < 20 else None)
    ok &= check('an edge that stops answering is degraded once the window is empty',
                r['rtt_degraded'] == 1 and w2.t_det_rtt_ns is not None)
    return ok


def probe():
    """A10 measurement from N1 against a running edge/server.py: 20 Hz states for --count
    steps, then the round-trip figures and the unanswered count."""
    ap = argparse.ArgumentParser(description='A10 probe of the N4 controller.')
    ap.add_argument('--probe', required=True, metavar='HOST[:PORT]')
    ap.add_argument('--count', type=int, default=1000)
    args = ap.parse_args()
    client = edgelink.make(args.probe)
    client.start()
    t = time.monotonic()
    drive(client, args.count)
    time.sleep(0.3)
    client.stop()
    st = client.stats()
    print(f"{args.count} states in {time.monotonic() - t:.1f} s -> {st['target']}")
    print(f"  rtt med {st.get('rtt_ms_median')} p99 {st.get('rtt_ms_p99')} "
          f"max {st.get('rtt_ms_max')} ms; replied {st['replied']}/{st['sent']}, "
          f"unanswered {st['unanswered']}, bad {st['bad']}, echo mismatch {st['echo_mismatch']}, "
          f"holds {st['holds']}, M {st['miss_rate_steps']}")
    passed = (st['replied'] == args.count and st['unanswered'] == 0 and st['bad'] == 0
              and st.get('rtt_ms_p99', 1e9) < 10.0)
    print('A10 PASS' if passed else 'A10 FAIL', '(p99 < 10 ms, none unanswered)')
    raise SystemExit(0 if passed else 1)


def serve():
    ap = argparse.ArgumentParser(description='Stand-in N4 for an end-to-end dry run.')
    ap.add_argument('--serve', type=int, required=True, metavar='PORT')
    ap.add_argument('--delay-ms', type=float, default=0.0)
    ap.add_argument('--loss', type=float, default=0.0)
    args = ap.parse_args()
    srv = FakeEdge(delay_s=args.delay_ms / 1000.0, loss=args.loss)
    srv.sock.close()
    srv.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.sock.bind(('0.0.0.0', args.serve))
    srv.sock.settimeout(0.05)
    srv.start()
    print(f'fake N4 on port {args.serve}, delay {args.delay_ms} ms, loss {args.loss} '
          f'(v = seq, w = -seq). Ctrl-C to stop.')
    try:
        while True:
            time.sleep(1.0)
            print(f'  got {srv.received}, answered {srv.sent}', flush=True)
    except KeyboardInterrupt:
        srv.stop()


if __name__ == '__main__':
    import sys
    (serve if '--serve' in sys.argv else probe if '--probe' in sys.argv else main)()
