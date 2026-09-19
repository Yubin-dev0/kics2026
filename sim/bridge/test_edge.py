"""Self-check of the edge link against a fake N4, with no board, no ROS and no network.

The fake server answers every Q after a set delay with v = seq, so the column edge_v of a
step names the datagram whose command the robot is driving on. That makes holding,
staleness and the deadline miss count readable straight off the step log.

  python3 -m bridge.test_edge                 (from sim/)

It also serves as a stand-in N4 for an end-to-end dry run before the real edge controller
exists (A10). In a second terminal:

  python3 -m bridge.test_edge --serve 47000 --delay-ms 0
"""
import argparse
import random
import socket
import threading
import time

from . import edge as edgelink

PERIOD_S = 0.05


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

    print(f"\n{'all checks passed' if ok else 'FAILURES above'}")
    raise SystemExit(0 if ok else 1)


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
    (serve if '--serve' in sys.argv else main)()
