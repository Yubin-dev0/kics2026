"""N1 UART bridge core, free of ROS so that it can be tested against fw/host/fake_stm32.

Every time column is time.monotonic_ns() on N1. The ROS clock is never used; the
simulation time of each scan comes from the LaserScan header and is logged as sim_time.

One S line goes out per /scan (kind=scan). A mode change requested between scans goes
out at once as an extra line (kind=flag) that reuses the latest scan's seq and sensor
values, as fw/PROTOCOL.md specifies. The firmware echoes seq without checking it, so the
per-run column `line` is the unique key; `seq` is the control step shared with the UDP
state and the logs.
"""
import collections
import csv
import threading
import time

from . import paths  # noqa: F401  (puts fw/tools and sim on sys.path)
import nav
import proto

REPLY_TIMEOUT_NS = 200_000_000      # a line without a C line after this is counted lost
BRIDGE_WDOG_NS = 150_000_000        # provisional: same 150 ms as the board watchdog
END_DRAIN_S = 0.40                  # after the last line: board WDOG comes at ~150 ms

FIELDS = [
    'line', 'kind', 'seq', 'flag',
    't_scan_rx_ns', 't_flag_rx_ns', 't_uart_tx_ns', 't_c_rx_ns',
    'sim_time', 'x', 'y', 'yaw', 'wp_i', 'min_range', 'min_mm',
    'local_v', 'local_w', 'edge_v', 'edge_w', 'edge_ok',
    'c_seq', 'v_out', 'w_out', 'mode', 'state', 'switch_us', 'n_sw', 'bad_lines', 'lost',
]
C_FIELDS = ('v_out', 'w_out', 'mode', 'state', 'switch_us', 'n_sw', 'bad_lines')


def now_ns():
    return time.monotonic_ns()


def clock_pair():
    """Wall and monotonic time taken back to back, to place monotonic columns on the wall
    clock later (for N3 alignment with the offset estimated in A9)."""
    m0 = time.monotonic_ns()
    w = time.time_ns()
    m1 = time.monotonic_ns()
    return {'wall_ns': w, 'mono_ns': (m0 + m1) // 2, 'read_ns': m1 - m0}


class RunLog:
    """CSV writer shared by the executor, flag and reader threads."""

    def __init__(self, path):
        self.f = open(path, 'w', newline='')
        self.w = csv.DictWriter(self.f, fieldnames=FIELDS, extrasaction='ignore')
        self.w.writeheader()
        self.lock = threading.Lock()

    def row(self, d):
        with self.lock:
            self.w.writerow({k: ('' if d.get(k) is None else d.get(k)) for k in FIELDS})

    def close(self):
        with self.lock:
            self.f.flush()
            self.f.close()


class Link:
    """Owns the serial port. send() stamps and writes one S line; a reader thread stamps
    every C line, pairs it with the oldest pending line of the same seq, and reports.

    The port timeout is set once at open (fw/tools/proto.py) and never touched again."""

    def __init__(self, ser, log, on_reply, on_wdog, on_silence):
        self.ser = ser
        self.rd = proto.LineReader(ser)
        self.log = log
        self.on_reply = on_reply        # (entry) for answered and lost lines
        self.on_wdog = on_wdog          # (row) for an unsolicited WDOG C line
        self.on_silence = on_silence    # (t_ns) when no C line came for BRIDGE_WDOG_NS
        self.pending = collections.deque()
        self.lock = threading.Lock()
        self.line_no = 0
        self.stray = []
        self.last_c_rx_ns = None
        self.silence_armed_ns = None    # start of the current wait, None = not watching
        self.silenced = False
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._reader, name='uart-reader', daemon=True)

    # -- startup, before the reader thread runs ------------------------------------------
    def query_version(self, timeout_s=1.0):
        self.rd.clear()
        self.ser.write(proto.VERSION_QUERY)
        end = time.perf_counter() + timeout_s
        while time.perf_counter() < end:
            raw = self.rd.readline(max(0.0, end - time.perf_counter()))
            if raw and raw.startswith(b'V,'):
                try:
                    return proto.parse_v(raw)
                except ValueError:
                    pass
        return None

    def start(self):
        self.thread.start()

    def stop(self):
        self._stop.set()
        self.thread.join(timeout=1.0)

    def watch(self, on):
        """Arms or disarms the bridge-side watchdog."""
        with self.lock:
            self.silence_armed_ns = now_ns() if on else None
            self.silenced = False

    # -- sending ---------------------------------------------------------------------------
    def send(self, entry):
        """entry holds the S fields; line, t_uart_tx_ns are filled in here."""
        data = proto.build_s(entry['seq'], entry['min_mm'], entry['local_v'], entry['local_w'],
                             entry['edge_v'], entry['edge_w'], entry['flag'])
        with self.lock:
            self.line_no += 1
            entry['line'] = self.line_no
            entry['t_uart_tx_ns'] = now_ns()
            self.pending.append(entry)
        self.ser.write(data)
        return entry

    def pending_count(self):
        with self.lock:
            return len(self.pending)

    # -- receiving -------------------------------------------------------------------------
    def _reader(self):
        while not self._stop.is_set():
            raw = self.rd.readline(0.02)
            t = now_ns()
            if raw is not None:
                self._handle(raw, t)
            self._expire(t)

    def _handle(self, raw, t):
        try:
            c = proto.parse_c(raw)
        except ValueError:
            self.stray.append({'t_ns': t, 'raw': raw.decode('ascii', 'replace').strip()[:96]})
            return
        cf = {k: getattr(c, k) for k in C_FIELDS}
        if c.state == 3:
            with self.lock:
                self.last_c_rx_ns = t
            row = {'kind': 'wdog', 'c_seq': c.seq, 't_c_rx_ns': t, **cf}
            self.log.row(row)
            self.on_wdog(row)
            return
        matched, lost = None, []
        with self.lock:
            self.last_c_rx_ns = t
            if self.silence_armed_ns is not None:
                self.silence_armed_ns = t
                self.silenced = False
            if any(e['seq'] == c.seq for e in self.pending):
                while self.pending:
                    e = self.pending.popleft()
                    if e['seq'] == c.seq:
                        matched = e
                        break
                    lost.append(e)
        for e in lost:
            self._finish(e, None, None)
        if matched is None:
            self.stray.append({'t_ns': t, 'raw': raw.decode('ascii', 'replace').strip()[:96]})
            return
        self._finish(matched, c, t)

    def _expire(self, t):
        lost, silence = [], False
        with self.lock:
            while self.pending and t - self.pending[0]['t_uart_tx_ns'] > REPLY_TIMEOUT_NS:
                lost.append(self.pending.popleft())
            if (self.silence_armed_ns is not None and not self.silenced
                    and t - self.silence_armed_ns > BRIDGE_WDOG_NS):
                self.silenced = silence = True
        for e in lost:
            self._finish(e, None, None)
        if silence:
            self.log.row({'kind': 'bridge_stop', 't_c_rx_ns': t})
            self.on_silence(t)

    def _finish(self, e, c, t):
        if c is None:
            e['lost'] = 1
        else:
            e.update({'c_seq': c.seq, 't_c_rx_ns': t, 'lost': 0,
                      **{k: getattr(c, k) for k in C_FIELDS}})
        self.log.row(e)
        self.on_reply(e)


class Runner:
    """Stage B control step: waypoint follower on N1, safety rule and mode on N2.

    publish(v_mps, w_radps) sends /cmd_vel (or moves the dry-run robot).
    policy decides the requested mode; edge supplies edge_v, edge_w (stub until B2)."""

    def __init__(self, link, policy, edge, publish, waypoints=nav.WAYPOINTS,
                 run_limit_s=nav.RUN_LIMIT_S):
        self.link = link
        self.policy = policy
        self.edge = edge
        self.publish = publish
        self.follower = nav.Follower(waypoints)
        self.run_limit_s = run_limit_s
        self.lock = threading.RLock()
        self.phase = 'wait'            # wait -> settle -> run -> done
        self.seq = 0
        self.flag = policy.initial_flag
        self.last = None               # latest scan-line entry (sensor values for flag lines)
        self.pose = (0.0, 0.0, 0.0)
        self.collided = False
        self.result = None
        self.settle = None
        self.t0_sim = None
        self.t_start_ns = None
        self.t_end_ns = None
        self.last_sim = None
        self.clock = {}
        self.done_event = threading.Event()
        self.settled_event = threading.Event()
        self.flag_events = 0
        self.bridge_stops = 0
        self.wdog_rows = []

    # -- callbacks from Link -----------------------------------------------------------------
    def on_reply(self, e):
        if e['kind'] == 'settle':
            self.settle = e
            self.settled_event.set()
            return
        with self.lock:
            live = self.phase == 'run'
        if live and not e.get('lost'):
            self.publish(e['v_out'] / 1000.0, e['w_out'] / 1000.0)

    def on_wdog(self, row):
        with self.lock:
            row['phase'] = self.phase
            self.wdog_rows.append(row)
            live = self.phase == 'run'
        if live:
            self.publish(0.0, 0.0)

    def on_silence(self, t):
        with self.lock:
            live = self.phase == 'run'
            if live:
                self.bridge_stops += 1
        if live:
            self.publish(0.0, 0.0)

    # -- inputs ----------------------------------------------------------------------------
    def set_pose(self, x, y, yaw):
        self.pose = (x, y, yaw)

    def on_scan(self, ranges, sim_time, t_scan_rx_ns):
        with self.lock:
            if self.phase == 'wait':
                self._send_settle(ranges, sim_time, t_scan_rx_ns)
                return
            if self.phase == 'settle':
                if not self.settled_event.is_set():
                    return
                self._begin(sim_time)
            if self.phase != 'run':
                return
            self._step(ranges, sim_time, t_scan_rx_ns)

    def on_flag(self, degrade, t_flag_rx_ns):
        """Called from the flag listener thread (policy 4)."""
        with self.lock:
            self.flag_events += 1
            want = self.policy.flag_for_event(degrade)
            if self.phase != 'run' or want is None or want == self.flag or self.last is None:
                return
            self.flag = want
            e = dict(self.last)
            e.update({'kind': 'flag', 'flag': want, 't_scan_rx_ns': None,
                      't_flag_rx_ns': t_flag_rx_ns})
            for k in ('line', 't_uart_tx_ns', 't_c_rx_ns', 'c_seq', 'lost') + C_FIELDS:
                e.pop(k, None)
            self.link.send(e)

    # -- internals -------------------------------------------------------------------------
    def _entry(self, kind, seq, ranges, sim_time, t_scan_rx_ns, local, edge):
        min_range = nav.front_min(ranges)
        x, y, yaw = self.pose
        return {'kind': kind, 'seq': seq, 'flag': self.flag,
                't_scan_rx_ns': t_scan_rx_ns, 'sim_time': f'{sim_time:.3f}',
                'x': f'{x:.4f}', 'y': f'{y:.4f}', 'yaw': f'{yaw:.4f}',
                'wp_i': self.follower.wp_i, 'min_range': f'{min_range:.4f}',
                'min_mm': proto.range_to_mm(min_range),
                'local_v': local[0], 'local_w': local[1],
                'edge_v': edge[0], 'edge_w': edge[1], 'edge_ok': int(edge[2])}, min_range

    def _send_settle(self, ranges, sim_time, t_scan_rx_ns):
        """One line before the run sets the board to the policy's starting mode with zero
        speed. Its switch (if any) is kept out of the run statistics."""
        self.clock['settle'] = clock_pair()
        e, _ = self._entry('settle', 0, ranges, sim_time, t_scan_rx_ns, (0, 0), (0, 0, False))
        self.phase = 'settle'
        self.link.send(e)

    def _begin(self, sim_time):
        self.phase = 'run'
        self.t0_sim = sim_time
        self.t_start_ns = now_ns()
        self.clock['start'] = clock_pair()
        self.link.watch(True)

    def _step(self, ranges, sim_time, t_scan_rx_ns):
        elapsed = sim_time - self.t0_sim
        self.last_sim = elapsed
        min_range = nav.front_min(ranges)
        if min_range < nav.D_COL:
            self.collided = True
        x, y, yaw = self.pose
        cmd = self.follower.step(x, y, yaw)
        # A1 spent one timer call on reaching a waypoint and computed the next heading in
        # the paired call at the same instant (sim/NOTES.md, control rate). Doing both in
        # one scan keeps the 20 Hz line cadence and matches what the robot saw in A1.
        while cmd is None and not self.follower.done:
            cmd = self.follower.step(x, y, yaw)
        if self.follower.done:
            self._finish('GOAL')
            return
        if elapsed > self.run_limit_s:
            self._finish('TIMEOUT')
            return
        v, w = cmd
        local = (int(round(v * 1000)), proto.rad_to_mrad(w))
        self.seq += 1
        self.flag = self.policy.flag_for_scan(self.flag)
        edge = self.edge.command(self.seq)
        e, _ = self._entry('scan', self.seq, ranges, sim_time, t_scan_rx_ns, local, edge)
        self.last = e
        self.link.send(e)

    def _finish(self, why):
        self.result = why
        self.phase = 'done'
        self.t_end_ns = now_ns()
        self.clock['end'] = clock_pair()
        self.link.watch(False)
        self.publish(0.0, 0.0)
        self.done_event.set()

    def abort(self, why):
        with self.lock:
            if self.phase != 'done':
                self._finish(why)
