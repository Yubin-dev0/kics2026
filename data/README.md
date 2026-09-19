# Experiment data

## Layout
- `runs.csv` -- the run ledger, one row per run. The index to everything else.
- `a1/run_N.csv` -- per-step log of A1 driving run N
- `a1/run_N_meta.json` -- the exact configuration run N was executed with
- `a2/`, `a3/` -- serial bench runs (see Bench stages)
- `b1/run_N.csv`, `b1/run_N_meta.json` -- bridge driving runs (see Bridge stages)
- `a10/` -- bridge driving runs over loopback with the edge controller (same files as
  `b1/`, plus `edge_run_N.csv` from the N4 server, columns in `edge/README.md`)
- `b3/`, `c1/`, `c3/` -- later bridge stages, same files as `b1/`

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
