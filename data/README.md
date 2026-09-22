# Experiment data

## Layout
- `runs.csv` -- the run ledger, one row per run. The index to everything else.
- `sweep.csv` -- the sweep index: one row per run with the condition it ran under and the
  figures the paper draws (built by `analysis/sweep_index.py`; columns below)
- `synthetic/sweep.csv` -- the same schema with invented values, for writing the figure
  scripts before the sweep exists. Never a result; see `synthetic/README.md`
- `a1/run_N.csv` -- per-step log of A1 driving run N
- `a1/run_N_meta.json` -- the exact configuration run N was executed with
- `a2/`, `a3/` -- serial bench runs (see Bench stages)
- `b1/run_N.csv`, `b1/run_N_meta.json` -- bridge driving runs (see Bridge stages)
- `a10/` -- bridge driving runs over loopback with the edge controller (same files as
  `b1/`, plus `edge_run_N.csv` from the N4 server, columns in `edge/README.md`)
- `b3/`, `c1/`, `c3/` -- later bridge stages, same files as `b1/`
- `a10/`, `a5/` also hold edge probe runs (`bridge.test_edge --probe --run N`): run_N.csv
  with one row per datagram (seq, t_send_ns, t_recv_ns, rtt_us, empty when unanswered)
  and run_N_meta.json with the path, figures and verdict (`harness` = probe)
- `a4/`, `a6/` -- N3 round-trip runs (`net/ping_run.py`): run_N.log, the raw `ping -D`
  output, and run_N_meta.json (`harness` = ping); `a4/n3_state_<device>.txt` is the N3
  snapshot the A4 verdict belongs to (`net/n3/state.sh`)
- `<stage>/n3_run_N_windows.csv`, `n3_run_N_flags.csv`, `n3_run_N_meta.json` -- the N3
  detector's files of bridge run N (`capture/fetch.sh`; columns in `capture/README.md`).
  The pcap stays on N3

## runs.csv columns

| column | meaning |
|---|---|
| run_id | run number within the stage; unique per stage |
| stage | A1, A2, B1, ... as defined in the integrated plan |
| node | which machine produced the data (N1 sim, N3 AP, N4 edge, N5 load) |
| course | waypoint layout (4x4 or 3x3) |
| clearance_m | lateral distance from the path to each obstacle centre |
| sector_half_deg | half-angle of the forward LiDAR sector used for min_range |
| v_slow_floor | minimum forward speed held during SLOW, in m/s |
| git | commit the source was at; `-DIRTY` means uncommitted changes existed |
| result | GOAL (all waypoints reached) or TIMEOUT (60 s elapsed) |
| min_range_m | smallest min_range observed over the whole run |
| status | valid / discarded / partial / overwritten / check |

`status` decides what may be cited. Only `valid` rows go into the paper.
Empty cells mean the value was not recorded, not zero.

## sweep.csv columns

One row per run. This is what the figures and table 1 read (`analysis/FIGURES.md`); the
run logs stay the source, and this file is the cut across runs. Empty means the value is
not applicable to that run or has not been merged yet.

| column | unit | meaning |
|---|---|---|
| run_id, stage, date | - | the run this row summarises, as in `runs.csv` |
| policy, policy_name | - | 1 always local, 2 always edge, 3 RTT window, 4 metadata flag |
| rtt_ms | ms | base RTT set with netem on N3: one of 10, 30, 60, 100, 200 |
| load | - | none, L1 (45 s of iperf3 UDP) or L2 (a 2-3 s burst) |
| rep | - | repetition number of this condition, 1 to 3 |
| status | - | valid / check / discarded, as in `runs.csv`; only valid is cited |
| result | - | GOAL or TIMEOUT |
| a_ms | ms | metadata detection after t0 (N3 clock). From the C3 merge |
| d_ms | ms | RTT window detection after t0 (N1 clock plus the A9 offset). From the C3 merge |
| b_ms | ms | flag from N3 detection to the S line on N1. Policy 4 runs only |
| u_ms | ms | USB path to the board, the A3 constant (3.5 ms p99 idle), not per run |
| c_ms | ms | board mode switch, the run's median `switch_us` converted to ms |
| g_ms | ms | D - (A + B + U + C), what is left of the head start. Policy 4 runs only |
| n_col | - | collisions: entries into min_range < 0.15 m, a consecutive stretch counting once |
| n_sw | - | switches during the run (board `n_sw` delta). N_false is this on the L2 rows |
| m_rate | - | share of steps driving on a command older than one period (M of the paper) |
| hold_rate | - | share of steps where no reply arrived at all and the last command was reused |
| rtt_ms_median | ms | measured round trip of the run, a check against the netem setting |
| edge_stop_steps | - | steps in edge mode where the safety rule would have stopped the robot; the fallback y axis of figure 2(b) (D19) |
| local_steps | - | steps the board actually spent in local mode |
| duration_s, min_range_m | s, m | run length in simulation time, closest approach |
| delta_ms | ms | N1 to N3 clock offset used for this run (A9). From the C3 merge |
| git, note | - | commit the run was executed at; free text |

## a1/run_N.csv columns

Header: `t,x,y,yaw,min_range,wp_i,mode,v,w`

| column | unit | meaning |
|---|---|---|
| t | s | elapsed time since the run started, on the simulation clock |
| x, y | m | robot position in the world frame |
| yaw | rad | robot heading in the world frame |
| min_range | m | smallest LiDAR reading in the forward +/-48 deg sector, sensor-referenced |
| wp_i | - | index of the waypoint currently being pursued (0-4) |
| mode | - | RUN (min_range >= d_slow), SLOW (d_stop <= min_range < d_slow), STOP (min_range < d_stop) |
| v | m/s | commanded forward speed |
| w | rad/s | commanded angular speed |

`t` is quantized to 0.1 s because Gazebo publishes `/clock` at 10 Hz, so
each timestamp appears on two consecutive rows. The second row of a pair
repeats the first step on the same scan, so A1 updated the command at 10 Hz
of simulation time and SLOW/STOP counts are twice the number of distinct
steps; see `sim/NOTES.md`.

`min_range` is measured from the LiDAR, which sits 3.2 cm behind the
chassis centre. Distances to the chassis front are 3.2 cm shorter.

## Thresholds
d_col 0.15 m, d_stop 0.20 m, d_slow 0.40 m -- all sensor-referenced.
See `sim/NOTES.md` for their derivation and validation.

## Raw captures
pcap files are excluded from git (see `.gitignore`). They live on N4 with
a backup on an external drive. Only derived metrics belong here.

## Bench stages (A2, A3)

Rows for A2 and A3 come from fw/tools/a2_bench.py and use the same columns as A1.
Columns without a bench meaning (course, clearance_m, sector_half_deg, v_slow_floor,
waypoints, collisions, slow_steps, stop_steps, min_range_m) stay empty.

| column | bench meaning |
|---|---|
| node | N1+N2 for the board, N1 for the fake board |
| result | PASS, FAIL, or ABORTED (stopped before the meta file was complete) |
| duration_s | wall time of the bench run |
| status | valid when PASS on clean fw/ source, check when PASS on dirty source, otherwise discarded |
| note | C median and max (us), host round trip median and p99 (ms), lost/sent lines, bad_lines change, watchdog gap, firmware version and build id |

Per-line data is in data/a2/run_N.csv; full results and the verdict are in run_N_meta.json.
Board replays of A1 logs (data/a2/replay_N.json) are not ledger rows.

## Bridge stages (B1, A10, B3, C1, C3)

Rows for these stages come from sim/bridge/node.py (or sim/bridge/dry_run.py for loopback checks). Driving columns mean the same as for A1, with
these differences:

| column | bridge meaning |
|---|---|
| node | N1+N2 for the board, N1 for the fake-board control runs (run_id 101 and up) |
| course, clearance_m | filled when the world file hash matches A1 runs 10-12 |
| result | GOAL, TIMEOUT, or ABORTED |
| duration_s | simulation time from the first run scan to the finishing scan |
| collisions | 1 if min_range fell below d_col at any step |
| slow_steps, stop_steps | scan lines whose C line reported SLOW or STOP (one per step, not doubled as in A1) |
| status | valid when every verdict passed on clean source, check when it passed on dirty source, otherwise discarded |
| note | PASS/FAIL, policy, loop jitter (p99, max, sd of the deviation from 50 ms; skips), U upper bound, lost/sent, bad_lines change, watchdog lines, end-of-run watchdog gap, power mode, firmware, failed verdicts |

Per-line data and column meanings: `sim/bridge/README.md`. Full results and verdict are
in run_N_meta.json.
