"""RTT window watcher: the baseline detector of policy 3 (integrated plan 4.3, D9, D10).

Runs on every step of every run that has an edge link, whatever the policy: the plan
(5.3) wants D, the RTT detection delay, from policy 4 runs as well, so the watcher only
observes here and policy 3 is the one that acts on its verdict.

Rule, per control step:
  RTT_min   smallest rtt_us seen while the run is younger than baseline_s (plan 4.1: the
            load starts at t0 = 15 s, and the run start .. t0 window is the no-load
            reference). Frozen at the end of the baseline.
  RTT_win   mean of rtt_us over the steps of the last window_s (2 s window, Tran 2026 as
            cited in the plan). Held steps carry no rtt and contribute nothing.
  degraded  after the baseline: RTT_win - RTT_min > theta_high enters, < theta_low leaves
            (double threshold, TCP Vegas). A window with no round trip at all, which only
            happens when the edge has stopped answering, counts as degraded.
  t_det_rtt_ns  monotonic time of the first step of the run at which the enter condition
            held. Written once; it is the D of the timing chain (plan 4.5).

theta_high and theta_low are provisional until A0 (9/24) produces candidates; the run
records the values it used in the meta file (policy_params).
"""
import collections
import time

WINDOW_S = 2.0          # plan 4.3
BASELINE_S = 15.0       # plan 4.1: t0 = 15 s
THETA_HIGH_MS = 20.0    # provisional (A0)
THETA_LOW_MS = 10.0     # provisional (A0)

FIELDS = ['rtt_min_us', 'rtt_win_us', 'rtt_degraded', 't_det_rtt_ns']
BLANK = {k: None for k in FIELDS}


class Watcher:
    def __init__(self, window_s=WINDOW_S, baseline_s=BASELINE_S,
                 theta_high_ms=THETA_HIGH_MS, theta_low_ms=THETA_LOW_MS):
        self.window_ns = int(window_s * 1e9)
        self.baseline_s = baseline_s
        self.theta_high_us = theta_high_ms * 1000.0
        self.theta_low_us = theta_low_ms * 1000.0
        self.samples = collections.deque()   # (t_ns, rtt_us) inside the window
        self.rtt_min_us = None
        self.degraded = False
        self.t_det_rtt_ns = None
        self.enters = 0
        self.leaves = 0

    def params(self):
        return {'window_s': self.window_ns / 1e9, 'baseline_s': self.baseline_s,
                'theta_high_ms': self.theta_high_us / 1000.0,
                'theta_low_ms': self.theta_low_us / 1000.0}

    def observe(self, t_ns, elapsed_s, rtt_us):
        """One control step: t_ns its monotonic time, elapsed_s its age in the run, rtt_us
        the round trip of the command applied (None on a held step). Returns the log
        columns of this step."""
        if rtt_us is not None:
            self.samples.append((t_ns, rtt_us))
            if elapsed_s < self.baseline_s:
                self.rtt_min_us = (rtt_us if self.rtt_min_us is None
                                   else min(self.rtt_min_us, rtt_us))
        while self.samples and t_ns - self.samples[0][0] > self.window_ns:
            self.samples.popleft()
        win = (sum(r for _, r in self.samples) / len(self.samples)) if self.samples else None
        row = {'rtt_min_us': self.rtt_min_us,
               'rtt_win_us': None if win is None else int(round(win)),
               'rtt_degraded': int(self.degraded), 't_det_rtt_ns': None}
        if elapsed_s < self.baseline_s:
            return row
        if win is None or self.rtt_min_us is None:
            excess = float('inf')
        else:
            excess = win - self.rtt_min_us
        if not self.degraded and excess > self.theta_high_us:
            self.degraded = True
            self.enters += 1
            if self.t_det_rtt_ns is None:
                self.t_det_rtt_ns = time.monotonic_ns()
                row['t_det_rtt_ns'] = self.t_det_rtt_ns
        elif self.degraded and excess < self.theta_low_us:
            self.degraded = False
            self.leaves += 1
        row['rtt_degraded'] = int(self.degraded)
        return row

    def stats(self):
        return {**self.params(), 'rtt_min_us': self.rtt_min_us, 'enters': self.enters,
                'leaves': self.leaves, 't_det_rtt_ns': self.t_det_rtt_ns}
