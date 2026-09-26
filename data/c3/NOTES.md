# C3 sweep notes (2026-09-23)

66 planned runs (4 policies x 5 base RTTs x 3 reps at L1, plus policies 3 and 4
x 3 reps at L2/60 ms) were run as run 1-66; run 67-69 are re-runs. `data/sweep.csv`
has all 69 rows. Settings: MET = p90 of the 1 s window, TH 20 / TL 10 ms; N4 left
running (restarted once before the sweep); Gazebo restarted before every run.
Runs were started by hand with a shell helper (`d`, `r`, `f` steps).

## Clock

- clock run 0: first attempt FAIL (sd 6.4 ms, Wi-Fi wobble; that record was
  overwritten), retry PASS, delta = -54174245.459 ms, sd 0.137 ms.
- Runs 19 (FAIL twice), 21, 40 (FAIL once): only the `r` step was repeated while
  the detector kept waiting, so N5 sent L1 once and t0/A are normal. Valid.
- Operating rule from here: on a clock FAIL repeat `r` only. Start again from `d`
  only when N3-DET already printed `files:`.

## Runs excluded from analysis

`analysis/excluded_runs.csv` carries the same list; every script reads it.

| run | sweep.csv status | why | re-run |
|---|---|---|---|
| 29 | discarded (ABORTED) | bridge stopped at 2.8 s (56 steps): `cmd_vel publish failed: publisher's context is invalid` | 67 (policy 2, RTT 60, rep 2) |
| 42 | valid | detector entered about 5 s before t0 (A = -5220 ms), Wi-Fi burst rule | 68 (policy 3, RTT 100, rep 2) |
| 56 | valid | same, A = -5360 ms | 69 (policy 1, RTT 200, rep 2) |

Runs 42 and 56 stay `valid` in `sweep.csv` because every per-run verdict passed;
the exclusion is an analysis decision, taken before the summary was drawn.

## nq limit check (proposal, now measured)

All five base RTTs PASS. Edge RTT median in the first loaded run per RTT:
10 -> 345, 30 -> 364, 60 -> 395, 100 -> 440, 200 -> 546 ms. The rise is 335-345 ms
at every RTT, matching the 340 ms A8 measurement, so the limit does not saturate
across the sweep.

## Other observations

- Loop tx deviation spikes of about 49 ms (jitter verdict True) in run 2, 13, 24, 37
  and others. One skipped scan each; cause not yet traced.
- Pi 5 (N3) temperature 57.1 C after run 37, 56.5 C after run 61.
- Collisions: 0 in every run; min range 0.244-0.308 m. The course cannot show a
  collision difference between policies (policy 2 at 200 ms also 0).

## What the 66 used runs show (analysis/c3_summary.py)

- A vs D by base RTT (median ms, A < D count of 12): 10: 790/822, 8; 30: 1020/859, 4;
  60: 1226/931, 4; 100: 1440/1112, 2; 200: 1350/1310, 5. L2/60 (n = 6): 1077/944, 2.
- Policy 4 G at L1: positive in 3 of 15 runs (16, 19, 55). B 69-367 ms, growing with RTT.
- Hold rate: 7-28 % per run at L1 for every policy, no policy stands apart
  (run 19 7.2 %, run 69 9.9 %, the other 58 runs 13.9-28.1 %); 1.2-3.1 % at L2.
- C: 31-36 us on every switch (A2 said 34).
- L2: policies 3 and 4 both switch exactly twice (enter + leave) in all 6 runs.

## Why A is mostly later than D (analysis/c3_checks.py)

The detector's metric is the p90 of packet inter-arrival time minus its baseline
(`q_hat_ms` in `n3_run_N_windows.csv`). Under L1 the edge RTT climbs by about 340 ms,
but `q_hat` in the first 3 s after t0 stays at a median of 9-34 ms across the 15
policy-4 L1 runs (max 26-84 ms), i.e. around the 20 ms threshold. Inter-arrival
time reacts to the *change* in queueing delay and to burstiness, not to the delay
level; once the queue is full and the delay is steady, spacing returns towards
50 ms. So the metric sits at the threshold, the enter is late and in some runs
(34, 58) it flaps. The edge RTT itself ramps at roughly 100-170 ms per second
from load onset, which the RTT watcher (D) sees directly.

The second cause is B: the flag leaves N3 through the same wlan0 queue as the L1
load (decision D5), so it inherits the queueing delay. C1 run 2 measured B = 343 ms;
in the sweep B is 69-367 ms.

## C1-1 confirmation (analysis/c3_checks.py)

C1 run 2: N3 sent 10 flags, the bridge logged 8. Converting the N3 send times with
the C1 clock offset (-54174235.547 ms) and the run's clock pairs: fseq 8 was sent
3.10 s before the bridge ended (received 0.36 s later, consistent with B), fseq 9
0.80 s and fseq 10 1.10 s *after* the bridge ended. All flags sent while the bridge
was alive were received; C1-1 passes.
