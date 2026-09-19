"""N1 <-> N4 edge link: the UDP client the bridge uses to fetch commands from the edge
controller (stages A10 and B3 onward; policies 2, 3 and 4 all need it).

Datagrams are ASCII with the XOR checksum of the UART protocol (fw/PROTOCOL.md), so the
same helpers parse both and a capture shows a constant packet size, which keeps the N3
inter-arrival metric clean:

  N1 -> N4  Q,<seq>,<t_send_ns>,<x_mm>,<y_mm>,<yaw_mrad>,<min_mm>,<wp_i>*XX
  N4 -> N1  R,<seq>,<t_send_ns>,<v_mm>,<w_mrad>*XX

N4 copies seq and t_send_ns back unchanged. t_send_ns is N1's monotonic clock and means
nothing on N4; echoing it lets the round trip be read off a single datagram, and it is
checked against the send table so a wrong echo cannot shorten a measured RTT.

Timing. A step never waits for its own reply: at 200 ms of base RTT the answer to seq k
arrives about four periods later. Each step sends its state and then applies the freshest
command already in hand, which in the healthy case answers seq k-1 and is one period old.

Two different things go wrong on a slow link, and the log keeps them apart:

  held           no reply at all arrived during this period, so the previous command is
                 applied again (zero-order hold, decision D15). Under a steady added
                 delay this is rare even at 200 ms, because replies keep coming one per
                 period, only late. It is loss and jitter that cause holds.
  deadline_miss  the reply to the previous step was not in hand, so the command applied
                 is older than one period. This is the deadline miss of the plan (4.2):
                 the reply to seq k did not arrive within one control period of being
                 sent, seen one step later. Its run average is the M of the paper, and
                 stats() computes the same figure independently from the round trips.

At a steady 200 ms every step is a deadline miss and almost none are holds: the robot
drives continuously on commands four periods stale. That is the effect the paper is
after, and counting holds alone would have reported it as nothing wrong.

There is no upper limit on holding: stopping instead would make policy 2 stand still
above 100 ms of RTT and erase the upper bound the paper compares against. Standing still
is the safety board's job (SLOW/STOP), not the bridge's.

Before the first reply there is nothing to hold, so the command is (0, 0) with edge_ok 0.
At 200 ms of base RTT that covers the first four steps of a run.
"""
import socket
import threading
import time

from . import paths  # noqa: F401  (puts fw/tools on sys.path)
import proto

EDGE_PORT = 47000           # provisional, kept clear of the N3 flag port 47100
DEADLINE_NS = 50_000_000    # one control period (plan 4.2)
RECV_TIMEOUT_S = 0.05

# Columns this module owns in run_N.csv. They describe the command applied at the step
# and the round trip of the datagram that command answered.
FIELDS = ['edge_v', 'edge_w', 'edge_ok', 'edge_seq_used',
          't_send_ns', 't_recv_ns', 'rtt_us', 'held', 'deadline_miss']
BLANK = {k: None for k in FIELDS}


def build_q(seq, t_send_ns, x_mm, y_mm, yaw_mrad, min_mm, wp_i):
    return proto.with_checksum(
        f'Q,{seq},{t_send_ns},{x_mm},{y_mm},{yaw_mrad},{min_mm},{wp_i}')


def build_r(seq, t_send_ns, v_mm, w_mrad):
    return proto.with_checksum(f'R,{seq},{t_send_ns},{v_mm},{w_mrad}')


def _checked(data):
    text = data.decode('ascii', 'replace').strip()
    body, sep, cs = text.rpartition('*')
    if not sep or len(cs) != 2 or int(cs, 16) != proto.checksum(body):
        raise ValueError(text)
    return body.split(',')


def parse_q(data):
    """-> (seq, t_send_ns, x_mm, y_mm, yaw_mrad, min_mm, wp_i). For N4 and the tests."""
    f = _checked(data)
    if f[0] != 'Q' or len(f) != 8:
        raise ValueError(data)
    return tuple(int(v) for v in f[1:])


def parse_r(data):
    """-> (seq, t_send_ns, v_mm, w_mrad)."""
    f = _checked(data)
    if f[0] != 'R' or len(f) != 5:
        raise ValueError(data)
    return tuple(int(v) for v in f[1:])


class NoEdge:
    """No edge controller: policy 1 drives on the safety board alone (B1)."""
    name = 'none'
    configured = False

    def start(self):
        pass

    def stop(self):
        pass

    def step(self, seq, x_mm, y_mm, yaw_mrad, min_mm, wp_i):
        return {**BLANK, 'edge_v': 0, 'edge_w': 0, 'edge_ok': 0}

    def stats(self):
        return {'edge': self.name}


class EdgeClient:
    """One socket, one receiver thread. step() is called from the scan callback and never
    blocks; the thread parks every reply so the next step can pick up the freshest one."""

    name = 'udp'
    configured = True

    def __init__(self, host, port=EDGE_PORT, deadline_ns=DEADLINE_NS):
        self.addr = (host, port)
        self.deadline_ns = deadline_ns
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('0.0.0.0', 0))
        self.sock.settimeout(RECV_TIMEOUT_S)
        # read once: stats() is called after stop(), when the socket is closed
        self.local_port = self.sock.getsockname()[1]
        self.lock = threading.Lock()
        self.sent = {}            # seq -> t_send_ns
        self.replies = []         # (seq, t_send_ns, t_recv_ns) every reply, in arrival order
        self._fresh = None        # newest unused reply: (seq, t_send, t_recv, v, w)
        self._held = (0, 0)       # last command applied, held while nothing newer arrives
        self._used_seq = None
        self.n_sent = 0
        self.bad = 0              # checksum or shape
        self.unknown = 0          # reply to a seq this run never sent
        self.echo_mismatch = 0    # t_send_ns came back altered
        self.out_of_order = 0     # reply no newer than the one already waiting
        self.superseded = 0       # a waiting reply replaced by a newer one before a step
                                  # could take it (two replies inside one period)
        self.send_errors = 0
        self.holds = 0            # steps with no reply at all during the period
        self.misses = 0           # steps whose command was older than one period
        self.steps = 0
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, name='edge-rx', daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        """Safe before start() and on a second call: the run can fail before the link is
        ever used."""
        if self._stop.is_set():
            return
        self._stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=1.0)
        self.sock.close()

    # -- receiving ---------------------------------------------------------------------
    def _loop(self):
        while not self._stop.is_set():
            try:
                data, _ = self.sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                continue
            t_recv = time.monotonic_ns()
            try:
                seq, t_echo, v_mm, w_mrad = parse_r(data)
            except ValueError:
                self.bad += 1
                continue
            with self.lock:
                t_send = self.sent.get(seq)
                if t_send is None:
                    self.unknown += 1
                    continue
                if t_echo != t_send:
                    self.echo_mismatch += 1
                self.replies.append((seq, t_send, t_recv))
                if self._fresh is not None:
                    if self._fresh[0] >= seq:
                        self.out_of_order += 1   # a later step is already answered
                        continue
                    self.superseded += 1
                self._fresh = (seq, t_send, t_recv, v_mm, w_mrad)

    # -- one control step --------------------------------------------------------------
    def step(self, seq, x_mm, y_mm, yaw_mrad, min_mm, wp_i):
        t_send = time.monotonic_ns()
        with self.lock:
            self.sent[seq] = t_send
            self.n_sent += 1
        try:
            self.sock.sendto(build_q(seq, t_send, x_mm, y_mm, yaw_mrad, min_mm, wp_i),
                             self.addr)
        except OSError:
            self.send_errors += 1
        with self.lock:
            self.steps += 1
            fresh, self._fresh = self._fresh, None
            if fresh is None:
                self.holds += 1
                self.misses += 1
                v, w = self._held
                return {**BLANK, 'edge_v': v, 'edge_w': w,
                        'edge_ok': int(self._used_seq is not None),
                        'edge_seq_used': self._used_seq, 'held': 1, 'deadline_miss': 1}
            used, t_q, t_r, v, w = fresh
            self._held = (v, w)
            self._used_seq = used
            # the command answers seq k-1 when it arrived inside one period of being asked
            miss = int(used < seq - 1)
            self.misses += miss
            return {'edge_v': v, 'edge_w': w, 'edge_ok': 1, 'edge_seq_used': used,
                    't_send_ns': t_q, 't_recv_ns': t_r, 'rtt_us': (t_r - t_q) // 1000,
                    'held': 0, 'deadline_miss': miss}

    # -- figures -----------------------------------------------------------------------
    def stats(self):
        with self.lock:
            rtts = sorted((t_r - t_q) / 1e6 for _, t_q, t_r in self.replies)
            late = sum(1 for _, t_q, t_r in self.replies if t_r - t_q > self.deadline_ns)
            unanswered = self.n_sent - len(self.replies)
            s = {'edge': self.name, 'target': f'{self.addr[0]}:{self.addr[1]}',
                 'local_port': self.local_port, 'deadline_ms': self.deadline_ns / 1e6,
                 'sent': self.n_sent, 'replied': len(self.replies),
                 'unanswered': unanswered, 'late': late,
                 'steps': self.steps, 'holds': self.holds, 'misses': self.misses,
                 'bad': self.bad, 'unknown': self.unknown,
                 'echo_mismatch': self.echo_mismatch, 'out_of_order': self.out_of_order,
                 'superseded': self.superseded, 'send_errors': self.send_errors}
        if rtts:
            n = len(rtts)
            s.update({'rtt_ms_median': round(rtts[n // 2], 3),
                      'rtt_ms_p99': round(rtts[min(n - 1, int(round(0.99 * (n - 1))))], 3),
                      'rtt_ms_max': round(rtts[-1], 3), 'rtt_ms_min': round(rtts[0], 3)})
        if self.n_sent:
            # M of the plan, counted twice over: from the round trips of the datagrams
            # (the last few of a run may still have been in flight at stop()), and from
            # the steps that had to drive on a command older than one period. The two
            # differ only at the edges of the run.
            s['miss_rate'] = round((late + unanswered) / self.n_sent, 4)
        if self.steps:
            s['miss_rate_steps'] = round(self.misses / self.steps, 4)
            s['hold_rate'] = round(self.holds / self.steps, 4)
        return s


def make(spec):
    """spec is None (no edge) or 'host[:port]'."""
    if not spec:
        return NoEdge()
    host, _, port = spec.partition(':')
    return EdgeClient(host, int(port) if port else EDGE_PORT)
