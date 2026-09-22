"""One bridge run from port open to meta file. Used by node.py (Gazebo) and dry_run.py."""
import datetime as dt
import json
import sys
import time
from pathlib import Path

import serial

from . import core, edge as edgelink, env, netio, policy as policies, rttwatch, summary
from .paths import REPO
import proto

BOARD_PORT = ('/dev/serial/by-id/'
              'usb-STMicroelectronics_STM32_STLink_0669FF485775495067204114-if02')
BOARD_BUILD = 'Sep 16 2026 18:11:48 dbg'   # 35d6b8b, flashed in A2
FAKE_BUILD = 'host-fake'
FIRST_SCAN_S = 10.0
SETTLE_WAIT_S = 1.0


def add_args(ap):
    ap.add_argument('--run', type=int, required=True, help='run number N -> run_N.csv')
    ap.add_argument('--stage', default='B1')
    ap.add_argument('--policy', type=int, default=1, choices=(1, 2, 3, 4))
    ap.add_argument('--target', default='board', choices=('board', 'fake'))
    ap.add_argument('--port', default=None, help='default: the board by-id path')
    ap.add_argument('--baud', type=int, default=921600)
    ap.add_argument('--busid', default='1-3')
    ap.add_argument('--out', default=None, help='output folder (default data/<stage>)')
    ap.add_argument('--n3', default=None,
                    help='N3 address: keepalive to the detector (every sweep run), flags back (policy 4 acts)')
    ap.add_argument('--edge', default=None, metavar='HOST[:PORT]',
                    help=f'N4 edge controller, default port {edgelink.EDGE_PORT}; '
                         'policies 2 and 4 need it')
    ap.add_argument('--window-s', type=float, default=rttwatch.WINDOW_S,
                    help='RTT watcher window (plan 4.3)')
    ap.add_argument('--baseline-s', type=float, default=rttwatch.BASELINE_S,
                    help='run seconds used for RTT_min (plan 4.1: t0)')
    ap.add_argument('--theta-high-ms', type=float, default=rttwatch.THETA_HIGH_MS,
                    help='enter threshold over RTT_min (provisional until A0)')
    ap.add_argument('--theta-low-ms', type=float, default=rttwatch.THETA_LOW_MS,
                    help='leave threshold over RTT_min (provisional until A0)')
    ap.add_argument('--expect-build', default=None)
    ap.add_argument('--power-confirmed', action='store_true',
                    help='power facts could not be read; you checked charger and mode by hand')
    ap.add_argument('--rtt-ms', type=float, default=None,
                    help='sweep condition: base RTT set with netem on N3')
    ap.add_argument('--load', default=None, choices=('none', 'L1', 'L2'),
                    help='sweep condition: competing load of this run')
    ap.add_argument('--rep', type=int, default=None,
                    help='sweep condition: repetition number of this condition')
    ap.add_argument('--note', default='')


class Session:
    def __init__(self, args, publish, harness):
        self.args = args
        self.harness = harness
        self.policy = policies.make(args.policy)
        if args.policy == 4 and not args.n3:
            raise SystemExit('policy 4 needs --n3')
        if getattr(self.policy, 'needs_edge', False) and not args.edge:
            raise SystemExit(f'policy {args.policy} ({self.policy.name}) needs --edge')
        self.edge = edgelink.make(args.edge)
        # every run with an edge link carries the RTT watcher (plan 5.3: D from every run)
        self.watcher = rttwatch.Watcher(args.window_s, args.baseline_s, args.theta_high_ms,
                                        args.theta_low_ms) if self.edge.configured else None
        self.port = args.port or BOARD_PORT
        self.expect = args.expect_build or (BOARD_BUILD if args.target == 'board' else FAKE_BUILD)
        out = Path(args.out) if args.out else REPO / 'data' / args.stage.lower()
        out.mkdir(parents=True, exist_ok=True)
        self.csv_path = out / f'run_{args.run}.csv'
        self.meta_path = out / f'run_{args.run}_meta.json'
        if self.csv_path.exists() or self.meta_path.exists():
            raise SystemExit(f'{self.csv_path.name} already exists. Pick another --run.')
        self.publish = publish
        self.flags = None
        self.aborted = None

        commit, dirty = env.git_info()
        self.meta = {
            'run_id': str(args.run), 'stage': args.stage, 'harness': harness,
            'target': args.target, 'policy': self.policy.number, 'policy_name': self.policy.name,
            'started': dt.datetime.now().astimezone().isoformat(timespec='seconds'),
            'port': self.port, 'baud': args.baud, 'busid': args.busid,
            'proto_version_host': proto.PROTO_VERSION, 'expected_build': self.expect,
            'waypoints': None, 'git': commit, 'git_dirty': dirty, **env.code_info(),
            'edge': self.edge.stats(),
            'policy_params': self.watcher.params() if self.watcher else None,
            'condition': {'rtt_ms': args.rtt_ms, 'load': args.load, 'rep': args.rep},
            'note': args.note,
            'limits': {'scan_step_s': summary.SCAN_STEP_S,
                       'reply_timeout_ms': core.REPLY_TIMEOUT_NS / 1e6,
                       'bridge_wdog_ms': core.BRIDGE_WDOG_NS / 1e6,
                       'jitter_p99_ms': summary.JITTER_P99_MS, 'skip_ms': summary.SKIP_MS,
                       'u_p99_ms': summary.U_P99_MS,
                       'edge_deadline_ms': edgelink.DEADLINE_NS / 1e6},
        }
        if not Path(self.port).exists():
            raise SystemExit(f'{self.port} not found: attach the board (usbipd attach --wsl '
                             f'--busid {args.busid}) or start the fake board')
        print('reading environment ...', flush=True)
        self.meta['linux'] = env.linux_facts(self.port)
        if args.target == 'board':
            self.meta['windows'], self.meta['env_errors'] = env.windows_facts(args.busid)
        holders = self.meta['linux']['port_holders']
        if holders and args.target == 'board':  # socat holds the fake board's pty by design
            raise SystemExit(f'port is held by: {holders}')

        self.ser = serial.Serial(self.port, args.baud, timeout=proto.PORT_TIMEOUT_S)
        time.sleep(0.2)
        self.log = core.RunLog(self.csv_path)
        runner_box = {}
        self.link = core.Link(
            self.ser, self.log,
            on_reply=lambda e: runner_box['r'].on_reply(e),
            on_wdog=lambda row: runner_box['r'].on_wdog(row),
            on_silence=lambda t: runner_box['r'].on_silence(t))
        fw = self.link.query_version()
        if fw is None:
            self._discard()
            raise SystemExit('no V reply: check the port, usbipd attach, and the firmware')
        self.meta['firmware'] = fw
        print(f'firmware: {fw}', flush=True)
        if fw['build_id'] != self.expect or fw['proto_version'] != proto.PROTO_VERSION:
            self._discard()
            raise SystemExit(f'firmware mismatch: expected {self.expect!r}, got {fw}')

        self.edge.start()
        self.runner = core.Runner(self.link, self.policy, self.edge, publish, self.watcher)
        runner_box['r'] = self.runner
        self.meta['waypoints'] = self.runner.follower.waypoints
        self.link.start()
        self.t_ready = time.monotonic()
        # every run given --n3 keeps the keepalive up: the N3 detector marks the run start
        # on its first keepalive and starts the load at t0 (capture/README.md), and the
        # flags it sends are counted by every policy and acted on by policy 4 only
        if args.n3:
            self.flags = netio.FlagListener(args.n3, self.runner.on_flag)
            self.flags.start()

    def startup_problem(self):
        """Reason to abort while waiting for Gazebo or the settle reply, else None."""
        r, t = self.runner, time.monotonic()
        if r.phase == 'wait' and t - self.t_ready > FIRST_SCAN_S:
            return f'no /scan within {FIRST_SCAN_S:.0f} s: is Gazebo running?'
        if r.phase == 'settle' and not r.settled_event.is_set():
            sent = r.clock.get('settle', {}).get('mono_ns')
            if sent and time.monotonic_ns() - sent > SETTLE_WAIT_S * 1e9:
                return f'no reply to the settle line within {SETTLE_WAIT_S} s'
        return None

    def _discard(self):
        self.log.close()
        self.ser.close()
        self.edge.stop()
        self.csv_path.unlink(missing_ok=True)

    def abort(self, why):
        self.aborted = why
        self.runner.abort('ABORTED')

    def finish(self):
        """Waits for the last replies and the board's end-of-run WDOG line, then writes meta."""
        r = self.runner
        end = time.monotonic() + core.END_DRAIN_S
        while time.monotonic() < end:
            time.sleep(0.02)
        if self.flags:
            self.flags.stop()
            self.meta['flags'] = self.flags.stats()
        self.edge.stop()
        self.meta['edge'] = self.edge.stats()
        self.meta['rtt_watch'] = self.watcher.stats() if self.watcher else None
        self.link.stop()
        self.log.close()
        self.ser.close()

        res = summary.summarize(summary.load(self.csv_path))
        res.update({
            'result': r.result,
            'duration_sim_s': None if r.last_sim is None else round(r.last_sim, 3),
            'duration_mono_s': (None if r.t_start_ns is None or r.t_end_ns is None
                                else round((r.t_end_ns - r.t_start_ns) / 1e9, 3)),
            'waypoints_reached': f'{r.follower.wp_i}/{len(r.follower.waypoints)}',
            'collided': r.collided,
            'stray_lines': len(self.link.stray),
            'stray_samples': self.link.stray[:5],
            'flag_events': r.flag_events,
        })
        res.update(summary.edge_figures(self.meta['edge']))
        # collision and waypoint times on N1's wall clock, through the start clock pair;
        # the C3 merge takes them onto N3's clock with the A9 offset
        cp = r.clock.get('start')
        if cp:
            off = cp['wall_ns'] - cp['mono_ns']
            res['t_col_wall_ns'] = [t + off for t in res.get('t_col_ns', [])]
            res['t_wp_wall_ns'] = [t + off for t in res.get('t_wp_ns', [])]
        self.meta['clock_pairs'] = r.clock
        self.meta['finished'] = dt.datetime.now().astimezone().isoformat(timespec='seconds')
        self.meta['results'] = res
        self.meta['verdict'] = self.verdict(res)
        self.meta['aborted'] = self.aborted
        self.meta['pass'] = (self.aborted is None and
                             all(v for v in self.meta['verdict'].values()))
        self.meta_path.write_text(json.dumps(self.meta, indent=2, ensure_ascii=False) + '\n')
        self.report(res)
        return self.meta['pass']

    def verdict(self, res):
        v = {
            'goal': res['result'] == 'GOAL',
            'no_collision': not res['collided'],
            'jitter': (res.get('tx_dev_p99_ms') is not None
                       and res['tx_dev_p99_ms'] < summary.JITTER_P99_MS
                       and res.get('tx_skips') == 0),
            'link': (res['lost'] == 0 and res.get('bad_lines_delta') == 0
                     and res['wdog_during_run'] == 0 and res['bridge_stops'] == 0
                     and res['stray_lines'] == 0),
            'u_bound': (res.get('u_bound_ms_p99') is not None
                        and res['u_bound_ms_p99'] < summary.U_P99_MS),
            'settled': 'settle' in res,
            'scan_driven': (res.get('sim_step_repeats') == 0
                            and res.get('sim_step_median_s') == summary.SCAN_STEP_S),
        }
        if self.args.target == 'board':
            w = self.meta.get('windows', {})
            auto = (w.get('on_ac_power') is True and w.get('power_mode_ac') == 'best_performance')
            known = w.get('on_ac_power') is not None and w.get('power_mode_ac') is not None
            v['power'] = auto if known else bool(self.args.power_confirmed)
            self.meta['power_check'] = 'auto' if known else (
                'manual' if self.args.power_confirmed else 'unknown')
        return v

    def report(self, res):
        a, m = self.args, self.meta
        print()
        print(f"{a.stage} run {a.run} ({a.target}, policy {self.policy.number}): "
              f"{res['result']} in {res['duration_sim_s']} s sim, "
              f"wp {res['waypoints_reached']}, collided {res['collided']}, "
              f"min {res['min_range_m']} m, states {res['states']}")
        print(f"  times : waypoints at {res.get('t_wp_s')} s, collisions at {res.get('t_col_s')} s "
              f"(from the first scan, N1 clock)")
        print(f"  loop  : tx dev p99 {res.get('tx_dev_p99_ms')} ms, max {res.get('tx_dev_max_ms')}, "
              f"sd {res.get('tx_sd_ms')}, skips {res.get('tx_skips')} "
              f"(scan cb sd {res.get('scan_rx_sd_ms')}, scan->tx p99 {res.get('scan_to_tx_ms_p99')} ms)")
        print(f"  link  : U<= med {res.get('u_bound_ms_median')} p99 {res.get('u_bound_ms_p99')} "
              f"max {res.get('u_bound_ms_max')} ms; lost {res['lost']}/{res['lines']}, "
              f"bad +{res.get('bad_lines_delta')}, wdog in run {res['wdog_during_run']}, "
              f"end wdog {res['wdog_end_gap_ms']} ms, stray {res['stray_lines']}, "
              f"bridge stops {res['bridge_stops']}")
        print(f"  steps : sim step median {res.get('sim_step_median_s')} s, "
              f"repeats {res.get('sim_step_repeats')} (one line per /scan)")
        e = m.get('edge', {})
        if e.get('edge') != 'none':
            print(f"  edge  : rtt med {e.get('rtt_ms_median')} p99 {e.get('rtt_ms_p99')} "
                  f"max {e.get('rtt_ms_max')} ms; sent {e.get('sent')}, "
                  f"late {e.get('late')}, unanswered {e.get('unanswered')}, "
                  f"M {e.get('miss_rate')} (steps {e.get('miss_rate_steps')}); "
                  f"holds {e.get('holds')}/{e.get('steps')}, "
                  f"bad {e.get('bad')}, out of order {e.get('out_of_order')}")
        if self.watcher:
            w = self.meta['rtt_watch']
            print(f"  watch : RTT_min {w.get('rtt_min_us')} us, win {res.get('rtt_win_ms_max')} ms max, "
                  f"degraded steps {res.get('rtt_degraded_steps')}, enters {w.get('enters')}, "
                  f"leaves {w.get('leaves')}, t_det_rtt {res.get('t_det_rtt_s')} s "
                  f"(theta {w.get('theta_high_ms')}/{w.get('theta_low_ms')} ms)")
        print(f"  board : settle {res.get('settle')}, switches in run {res['switches']}, "
              f"flag lines {res['flag_lines']}, flag events {res['flag_events']}")
        if a.target == 'board':
            w = m.get('windows', {})
            print(f"  power : AC {w.get('on_ac_power')}, mode {w.get('power_mode_ac')} "
                  f"({m.get('power_check')})")
        print(f"  verdict {m['verdict']}")
        print(f"\n{'PASS' if m['pass'] else 'FAIL'} -> {self.csv_path}, {self.meta_path.name}")
        if str(self.csv_path).startswith(str(REPO / 'data')):
            print(f'register: python3 analysis/append_run.py {a.stage} {a.run}')
        sys.stdout.flush()
