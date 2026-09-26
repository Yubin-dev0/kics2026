# N1 UART bridge (B1 onward)

Connects the Gazebo robot (N1) to the N2 safety board over the line protocol in
`fw/PROTOCOL.md`, to the N4 edge controller over UDP, and later to the N3 switching
flags. The same code is used from B1 through the C stage, so every timestamp the timing
chain needs is recorded from the first run.

## Files

| file | contents |
|---|---|
| `core.py` | serial link (send, reader thread, reply matching, bridge watchdog), run logic, CSV log. No ROS |
| `node.py` | ROS 2 node: `/scan`, `/odom` in, `/cmd_vel` out |
| `run.py` | one run from port open to meta file; verdict |
| `summary.py` | run figures computed from `run_N.csv` (also a CLI) |
| `env.py` | environment capture: Windows power mode, WSL and usbipd versions, routes, git, code hashes (also a CLI) |
| `policy.py` | switching policies 1 to 4 |
| `rttwatch.py` | RTT window watcher: RTT_min, 2 s window, double threshold; the verdict policy 3 acts on, logged in every run with an edge link |
| `edge.py` | N4 edge link: state out, command in, round trip, hold of the last command |
| `test_edge.py` | self-check of `edge.py`, of `../../edge/server.py` and of `rttwatch.py`; fake N4 with a test pattern (`--serve`); A10 probe (`--probe`) |
| `../../edge/server.py` | the N4 edge controller (waypoint follower over UDP), standard library only, runs on the lab PC under native Windows Python |
| `netio.py` | N3 flag listener with keepalive, flag-path probe CLI |
| `fake_n3.py` | stand-alone flag sender for tests and the A4 flag-path check |
| `dry_run.py` | kinematic robot on the A1 course, drives the same code without ROS |
| `../nav.py` | waypoint follower shared with the A1 formulas; `../test_nav.py` replays A1 |

## Control step

One S line per `/scan`. N1 computes the heading (`local_w`) and whether the robot may move
forward (`local_v` = 220 or 0, turning in place above 0.4 rad), sends the robot state to
N4 and takes the freshest command that has come back (`edge.py`; none of it happens
without `--edge`). N2 applies the safety rule, picks the command for the mode it is in,
and returns it; the bridge publishes it when the C line arrives. A step that
reaches a waypoint computes the next heading in the same scan (A1 did the same in its
paired timer call, see `sim/NOTES.md`), so the line cadence stays at 20 Hz.

Before the run, one `settle` line puts the board in the policy's starting mode with zero
speed. Its switch is kept out of the run figures. The run starts at the next scan and ends
on the last waypoint (GOAL) or after 60 s of simulation time (TIMEOUT). After the last
line the board's watchdog fires once; its gap is recorded as `wdog_end_gap_ms`.

A mode change between scans (policy 4) goes out at once as a `flag` line that reuses the
latest scan's seq and sensor values. The firmware echoes seq without checking it
(`fw/core/app.c`), so `line` is the unique key of the log.

Policy 3 decides in the scan itself: after the edge step, the RTT watcher (`rttwatch.py`)
sees the round trip of the command just applied, and the flag of this S line follows its
verdict. There is no flag line and B is zero by construction. The watcher runs in every
run with `--edge`, whatever the policy, so a policy 4 run also records when the RTT rule
would have switched (`t_det_rtt_ns`, the D of plan 4.5) and a policy 2 run records how
often it would have (`rtt_degraded`).

Watcher rule (plan 4.3, D9, D10): RTT_min = smallest `rtt_us` while the run is younger
than `--baseline-s` (15 s, plan 4.1: t0); RTT_win = mean of `rtt_us` over the last
`--window-s` (2 s); after the baseline, RTT_win - RTT_min > `--theta-high-ms` enters
local, < `--theta-low-ms` returns to edge. theta 20 / 10 ms were kept at A0 (9/23,
`capture/README.md`), so policies 3 and 4 share one pair; each run records the values it
used in the meta file (`policy_params`). Held
steps add no sample; a window with no sample at all (edge silent) counts as degraded.

Stops: a C line with state 3 (board watchdog) sets `/cmd_vel` to zero at once. If no C
line arrives for 150 ms (provisional, same as the board), the bridge sets `/cmd_vel` to
zero and logs a `bridge_stop` row.

## Time

Every `*_ns` column is `time.monotonic_ns()` on N1. The ROS clock is not used, so no
`use_sim_time` is needed. `sim_time` is the scan header stamp. The meta file holds
(wall, monotonic) pairs taken at settle, start and end, to place the log on the wall
clock for alignment with N3 (offset from A9).

| column | taken |
|---|---|
| `t_scan_rx_ns` | first statement of the `/scan` callback |
| `t_flag_rx_ns` | right after `recvfrom` returns the flag datagram (flag lines) |
| `t_uart_tx_ns` | just before the S line is written |
| `t_c_rx_ns` | when the reader returns the complete C line |
| `t_send_ns` | just before the state datagram of `edge_seq_used` was sent to N4 |
| `t_recv_ns` | right after `recvfrom` returned that datagram's reply |
| `t_det_rtt_ns` | when the watcher's enter condition first held, right after the edge step of that scan |
| `t_det_meta_ns` | N3 wall clock (`time.time_ns()` on N3) copied from the flag datagram; the only column not on N1's clock |

B inside the bridge is `t_uart_tx_ns - t_flag_rx_ns`. U (UART write to board receive)
cannot be timed one way; `t_c_rx_ns - t_uart_tx_ns` of the same line is its upper bound
(`fw/NOTES.md`, timing chain).

## Log: `run_N.csv`

One row per S line (`kind` = settle, scan, flag) written when its C line arrives or after
200 ms without one (`lost` = 1). Board watchdog lines (`wdog`) and bridge stops
(`bridge_stop`) get rows of their own. Rows are in completion order; sort by `line`.

Run figures (`bridge.summary`, meta `results`) also give `t_col_ns` and `t_wp_ns`: the N1
monotonic time of each collision entry and of each waypoint arrival (the scan row where
`wp_i` moves on), with `t_col_s`, `t_wp_s` counted from the first scan and
`t_col_wall_ns`, `t_wp_wall_ns` on N1's wall clock through the start clock pair. They are
taken from `t_scan_rx_ns`, not `sim_time`, which /clock quantises to 0.1 s; the C3 merge
puts them on N3's clock with the A9 offset to compare them with t0 and the G window.

| column | unit | meaning |
|---|---|---|
| line | - | S line number in this run, unique |
| kind | - | settle, scan, flag, wdog, bridge_stop |
| seq | - | control step (scan count), shared with UDP state; flag lines reuse it |
| flag | - | requested mode, 1 local, 0 edge |
| t_* | ns | see Time |
| sim_time | s | scan header stamp |
| x, y, yaw | m, rad | odometry at the step |
| wp_i | - | waypoint being pursued |
| min_range | m | forward +/-48 deg minimum, sensor-referenced (inf = no return) |
| min_mm | mm | wire value, `floor(min_range * 1000)` |
| local_v, local_w | mm/s, mrad/s | follower output |
| edge_v, edge_w, edge_ok | mm/s, mrad/s, - | edge command applied at this step; 0, 0, 0 with no `--edge` and before the first reply |
| edge_seq_used | - | the step whose state that command answers; `seq` minus this is its age in periods |
| t_send_ns, t_recv_ns, rtt_us | ns, ns, us | round trip of that one datagram; empty on a held step |
| held | - | 1 when no reply arrived during this period and the previous command was reused (D15) |
| deadline_miss | - | 1 when the command applied is older than one period, i.e. the reply to the previous step did not make its 50 ms deadline. The run average is M |
| rtt_min_us | us | watcher: smallest rtt_us seen inside the baseline (frozen after it) |
| rtt_win_us | us | watcher: mean rtt_us over the last 2 s window at this step; empty while no reply is inside the window |
| rtt_degraded | - | watcher verdict at this step, 1 = would be local under policy 3 (policy 3 acts on it, the others only record it) |
| t_det_rtt_ns | ns | first step of the run at which the enter condition held (D of plan 4.5); on that row only, else empty |
| flag_seq, t_det_meta_ns | -, ns | flag lines only: N3's flag counter and its detection time on N3's wall clock (`netio.py`); B = (t_flag_rx_ns + offset) - t_det_meta_ns once the A9 offset is known |
| c_seq ... bad_lines | | C line fields (`fw/PROTOCOL.md`) |
| lost | - | 1 when no C line came within 200 ms |

## Usage (from `sim/` in WSL2)

    python3 test_nav.py
    python3 -m bridge.test_edge
    python3 -m bridge.env
    python3 -m bridge.node --run 1
    python3 -m bridge.node --run 101 --target fake --port /tmp/vhost
    python3 -m bridge.node --run 1 --stage b3 --policy 2 --edge 10.0.0.4
    python3 -m bridge.node --run 2 --stage b3 --policy 3 --edge 10.0.0.4 --theta-high-ms 20 --theta-low-ms 10
    python3 -m bridge.node --run 3 --stage c1 --policy 4 --edge 10.0.0.4 --n3 10.0.0.1
    python3 -m bridge.node --run 4 --stage c3 --policy 1 --edge 10.0.0.4 --rtt-ms 60 --load L1 --rep 1
    python3 -m bridge.summary ../data/b1/run_1.csv

Fake board (socat 1.7.4 on N1 rejects `-d0`):

    make -C ../fw/host fake_stm32
    socat pty,raw,echo=0,link=/tmp/vboard pty,raw,echo=0,link=/tmp/vhost &
    ../fw/host/fake_stm32 /tmp/vboard &
    python3 -m bridge.dry_run --run 1 --target fake --port /tmp/vhost --out /tmp/b1

N4 edge controller (A10), from the repository root, in WSL2 for the loopback check and
under native Windows Python on the lab PC (`py edge\server.py --listen 0.0.0.0:47000`):

    python3 edge/server.py --listen 127.0.0.1:47000 --log /tmp/a10/edge_run_1.csv

Test delays for loopback runs, never for the sweep: `--delay-ms 200` holds every reply
(stand-in for a base RTT), `--extra-ms 40 --extra-from 20` adds a rise from a given second
after the first datagram (stand-in for the load at t0). The edge controller is the MLP by
default; `--controller follower` runs the follower, with `--rule none` (D21) by default
(`edge/README.md`).

`python3 -m bridge.test_edge --serve 47000 --delay-ms 60` is the older stand-in: it answers
with a test pattern (v = seq), not control, and stays for link tests only.

Every sweep run takes `--edge`, policy 1 included. Policy 1 ignores the commands, but the
state datagrams have to be on the link all the same: they are the robot flow the N3
detector measures, and their round trips are what the RTT watcher turns into D. A policy 1
run without `--edge` produces neither, and the condition is no longer the same across the
four policies.

`--rtt-ms`, `--load` and `--rep` record the sweep condition in the meta file. They change
nothing in the run; `analysis/sweep_index.py` reads them to build `data/sweep.csv`, which
is what the figures are drawn from (`analysis/FIGURES.md`). Fill them in from B3 on.

Dry runs go to `/tmp` and are never registered. Driving runs are registered from the
repository root: `python3 analysis/append_run.py B1 N` (stages B1, A10, B3, C1, C3 use the
same log and meta format; loopback driving runs go to `data/a10/`).

The verdict printed after a run is the B1 one (goal, no collision, jitter, link, U bound).
From B3 on, a policy 2 run that collides is a result, not a failed run: read `goal` and
`no_collision` as figures there and judge the run by `jitter`, `link` and `u_bound`.

## B1 pass criteria

| ID | criterion | basis | status |
|---|---|---|---|
| B1-1 | Firmware reports proto 2 and build "Sep 16 2026 18:11:48 dbg" | same board as A2 and A3 | confirmed |
| B1-2 | Policy 1: GOAL, 5/5 waypoints, min_range never below d_col 0.15 m | plan 10.2 | confirmed |
| B1-2a | Scan-driven loop: consecutive scan lines are 0.05 s apart in sim time (median) and no two share a sim time | A1 ran a sim-clock timer that fired twice per /clock tick (sim/NOTES.md); a repeat would mean the same scan was used twice | confirmed |
| B1-3 | Loop jitter: \|interval of S line writes - 50 ms\| p99 < 5 ms, and no interval of 75 ms or more | plan 10.2 bound (10% of the control period). p99 alone passes a run with a few long stalls; a skipped scan means the robot ran one extra period on an old command. A1 measured /scan at sigma 0.4 ms over 2063 intervals, which rules out any skip (one would raise sigma above 1.1 ms) | confirmed 9/22 (runs 2-4: p99 0.90-0.93 ms, 0 skips) |
| B1-4 | Link: lost 0, bad_lines +0, no WDOG line and no bridge stop during the run, no stray line | A2/A3 V5 rule carried into driving | confirmed 9/22 (runs 2-4) |
| B1-5 | U upper bound p99 < 5 ms | A3-7 | confirmed 9/22 (runs 2-4: p99 2.74-2.83 ms) |
| B1-6 | Charger connected and Windows power mode Best performance | A3 findings | confirmed |
| B1-7 | B1-1 to B1-6 on 3 consecutive board runs | A2/A3 streak rule | confirmed |
| B1-8 | Fake-board control runs 101-103 pass B1-2 to B1-5 | isolates the USB path and the board: same bridge, same core firmware in WSL2 | not run (dropped 9/22: the comparison with A1 is qualitative and the paper does not use it) |

Comparison with A1 is qualitative (collisions, completion, min_range above d_stop):
A1 updated its command at 10 Hz of simulation time, B1 at 20 Hz. Board runs against the
fake-board runs are the quantitative comparison; the differences in duration and
min_range are reported without a pass bar.

Fake-board control runs are registered (node N1), unlike the fake bench runs in `fw/`,
because they are the reference the board runs are compared with.

## B1 validation (2026-09-22, lab, board)

Policy 1 on the A1 course, NUCLEO-F446RE build "Sep 16 2026 18:11:48 dbg", source 2431ae3,
power mode Best performance on AC (read automatically), usbipd busid 2-3. Gazebo was
restarted before every run. B1 passes on runs 2-4.

| run | result | sim time s | min_range m | SLOW / STOP steps | jitter p99 / max ms | skips | U bound median / p99 / max ms | lost | end wdog ms |
|---|---|---|---|---|---|---|---|---|---|
| 1 | ABORTED | - | - | - | - | - | - | - | - |
| 2 | GOAL | 48.8 | 0.2708 | 198 / 0 | 0.926 / 3.02 | 0 | 1.981 / 2.829 / 6.002 | 0/976 | 150.0 |
| 3 | GOAL | 48.7 | 0.2691 | 195 / 0 | 0.896 / 1.355 | 0 | 1.896 / 2.735 / 4.544 | 0/974 | 150.7 |
| 4 | GOAL | 49.0 | 0.2597 | 204 / 0 | 0.934 / 2.43 | 0 | 1.893 / 2.794 / 3.176 | 0/980 | 150.8 |

Run 1 stopped after 10 s because Gazebo had not been started; it is registered as
discarded. In every passing run bad_lines stayed at +0, no WDOG line or bridge stop
occurred during the run, sim steps were 0.05 s with no repeat, and the jitter sigma was
0.29-0.30 ms against a /scan sigma of 0.26 ms measured before run 2.

Against the references:
- A1 (runs 10-12): 48.7-49.0 s, min_range 0.262-0.277 m, no STOP. B1 falls in the same
  range (run 4 sits 2 mm below the A1 minimum). The comparison stays qualitative, since
  A1 updated its command at 10 Hz of simulation time.
- A3 (run 6, Gazebo running): host round trip p99 2.84 ms. The B1 U bound p99 of
  2.74-2.83 ms is the same path under driving load, so A3 carries over to the timing
  chain.
- The single U maximum of 6.0 ms (run 2) is above the p99 bar but did not repeat
  (4.5 and 3.2 ms in runs 3 and 4).

The settle line switched the board in run 2 only (switch_us 25 us); in runs 3 and 4 the
board was still in local mode from the previous run, so the settle line reported
switch_us 0 and n_sw stayed at 1, as designed. 25 us is below the A2 range of 31-35 us;
the switching delay C used in Table 1 is taken from the C1 flag lines, not from settle
lines.

## A10 pass criteria (edge controller, plan 7.3)

Measured from N1 against the real N4, never on loopback, and registered like any run:

    python3 -m bridge.test_edge --probe 192.168.50.4:47000 --run 1 --path direct
    python3 analysis/append_run.py A10 1

`--path` names how N1 reached N4: `direct` (USB-LAN cable, before A4), `n3` (through the
AP), `tunnel` (WireGuard, stage A5 with `--stage A5`). The probe writes
data/a10/run_N.csv (one row per datagram) and run_N_meta.json, and at the end asks for the
N4 server's `proc p99` figure (A10-3); a run without it is not a pass.

| ID | criterion | basis | status |
|---|---|---|---|
| A10-1 | Round trip p99 < 10 ms over 1000 states at 20 Hz | plan 7.3; a bar for the server alone, on the direct cable | pass 9/22, run 2: 5.031 ms |
| A10-2 | 1000 of 1000 answered, no bad or echo-mismatched reply | plan 7.3 | pass 9/22, run 2 |
| A10-3 | Server process time p99 < 1 ms (`proc_us` in the server log) | the controller must not be a visible share of the 50 ms period | pass 9/22, run 2: 705 us |
| A10-4 | Policy 2 completes the A1 course with the fake board on loopback (GOAL, 5/5) | the controller drives the course before any network is added | confirmed 9/20, dry run (follower) |
| A10-5 | With the MLP controller: A10-4 with no collision and a course time within 5% of the follower's | the learned controller must not change what policy 2 does at zero delay; 5% is provisional | confirmed 9/22, dry run: MLP 44.2 s, follower (rule none) 44.15 s, both min 0.26-0.27 m |

Lab runs of 2026-09-22 (N4 = lab PC, Windows, Python 3.12.10, MLP controller, weights
`fe670ccebaf6`; the server was restarted for every run):

| run | path | round trip median / p99 / max (ms) | answered | N4 proc p99 | verdict |
|---|---|---|---|---|---|
| 1 | direct | 4.578 / 5.726 / 11.411 | 1000/1000 | not usable | void, not registered |
| 2 | direct | 4.145 / 5.031 / 21.875 | 1000/1000 | 705 us | PASS (the A10 verdict) |
| 3 | N1 Wi-Fi, N3, NAT | 6.899 / 9.478 / 13.337 | 1000/1000 | 805 us | PASS |

Run 1 is void because the server then timed `proc_us` with `time.monotonic`, which on
Windows ticks every 15.6 ms: it read 0 or about 16000 us. Since f57fc5b the server uses
`perf_counter_ns`. The same commit marks `edge/mlp_weights.json` as binary in
`.gitattributes`: Git for Windows had checked it out with CRLF, and the server printed the
SHA-1 `aacbf116f8fc` for the same weights. The margins are narrow in two places: run 3
leaves 0.5 ms to the A10-1 bar, and the process time is 70-80% of the A10-3 bar, most of
it the Windows socket calls (the MLP forward pass alone is about 45 us). Run 2's meta file
has no Windows power facts (WSL interop was off; see `net/README.md`). Ping on the direct
cable: 2-3 ms from Windows, 4.08 ms mean from WSL. The tunnel path (stage A5) is judged
in `net/README.md`.

Loopback checks of 9/20 (dry run, fake board, `edge/server.py`, WSL2 loopback; ideal
kinematics, so the distances are not A1 figures): policy 2 GOAL 47.95 s, 959 states,
none unanswered, rtt median 0.31 ms, p99 0.55 ms, server proc p99 0.2 ms; policy 3 with a
test delay of 60 ms and +40 ms from 20 s: RTT_min 60.5 ms, detection at 21.1 s of the
run, one switch, GOAL; policy 4 with `fake_n3.py` every 8 s: 4 flags, 4 flag lines, 4
switches, flag-to-UART 46-65 us, rtt recorded on 952 of 956 steps, GOAL. Policy 2 with a
steady test delay of 200 ms: GOAL, no collision, no STOP step, commands 5 steps old
(both rules) -- see Known constraints.

## B3, D19, C1 and C3 (2026-09-23)

B3 ran the bridge over the tunnel with the board (busid 2-3), the MLP edge controller
and the N3 detector observing. Runs 1-4 (no load): all GOAL 5/5, no collision, min range
0.264-0.268 m, lost 0, edge RTT median 7.5 ms, holds 3 of 907 steps in run 1, watcher
window max 16.83 ms (excess about 13 ms, under theta_high) in run 4. Run 4 is the first
with the detector baseline rule of patch 0016 (`capture/README.md`): live entries 0.
The N1-N2 USB cable was replaced before run 4 (loose connector); run 4 and everything
after use the new cable.

D19 (runs 5, 7, 8: policy 2 at 200 ms under L1, netem limit 1350): all three GOAL with 0
collisions (run 5: min range 0.297 m, edge RTT median 207.8 / p99 751.8 ms, holds 209 of
1016). Run 6 is void (the detector timed out before the bridge started, so L1 never
ran). Decided 9/23 14:09, before C3 (option ga): keep the course, figure 2(b) plots the
hold rate (steps run on a held command / steps sent) per policy, with the minimum
distance as a secondary figure; the collision count is still reported and is 0 in every
run of the sweep. `analysis/FIGURES.md` has the axis.

C1 (RTT 60, L1, p90 20/10, netem limit 830), the flag path:

| ID | criterion | result |
|---|---|---|
| C1-1 | every flag N3 sends while the bridge runs reaches N1 | pass, run 2: 8 of 10 flags logged; fseq 9 and 10 were sent 0.8 and 1.1 s after the bridge ended (`data/c3/NOTES.md`, `analysis/c3_checks.py`) |
| C1-2 | each flag line switches the board once (flag lines = switches) | pass, run 2: 8 flag lines, 8 switches, C median 35.5 us (max 37) |
| C1-3 | policy 3 run reaches GOAL with the watcher switching | pass, run 3: A 21780, D 749 ms, holds 23.8%, C 33 us |
| C1-4 | B recorded | run 2: B 343.5 ms against 30-40 ms expected; the flag leaves N3 through the same wlan0 queue as the L1 load (D5) and inherits its queueing delay, matching the ~340 ms edge RTT rise |
| C1-5 | hold rate of policy 3 against policy 4 | no difference: 23.8% against 24.1% (0.3 points, below the run-to-run spread) |

Run 1 (policy 3) is `check`: Gazebo and N4 were not restarted before it (A 15120, D 921
ms, holds 20.9%); run 3 repeats it. Clock run 0: delta -54174235.547 ms, sd 0.15 ms.

C3, the sweep (runs 1-69, 66 used): results, exclusions and the two checks behind the
interpretation are in `data/c3/NOTES.md`; table 1 and figure 2 come from
`analysis/c3_summary.py`. In short: A is later than D in most runs (median A above median
D at every base RTT but 10 ms), G > 0 in 3 of 15 policy-4 L1 runs, B 69-367 ms growing
with RTT, C 31-36 us on every switch, hold rate 7-28% at L1 with no policy apart,
collisions 0.

## Known constraints

- The board's usbipd busid depends on the laptop USB port and controller numbering: 1-3 at
  home, 2-3 in the lab (2026-09-22). Pass `--busid` to `bridge.node` and `bridge.env`, or the
  meta file records the wrong device.

- On the A1 course a steady 200 ms delay did not make policy 2 collide in the dry run,
  and Gazebo agreed at D19 (section above): the course cannot show a collision
  difference between policies. Figure 2(b) plots the hold rate instead.
- The edge datagram format and port 47000 are confirmed: B3, C1 and C3 ran over the N3
  path and the tunnel with them. N4 must echo `seq` and `t_send_ns` unchanged, or the
  round trip cannot be read.
- The edge controller is the learned MLP (decided 9/22); a follower, if ever used, runs
  with no speed rule (D21). The bridge cannot see either; record the controller line the
  server prints (with the weights SHA-1) in `--note`.
- A steady added delay produces almost no held steps even at 200 ms, because replies keep
  arriving one per period; what grows is their age. Read `deadline_miss` for the paper's
  M and `held` for link outages.
- The flag datagram format in `netio.py` and port 47100 are what the N3 detector
  (`capture/detector.py`) sends and answers; the keepalive goes out in every run given
  `--n3`, whatever the policy, because the detector marks the run start on it and starts
  the load at t0. The keepalive path through WSL2 NAT is tested at A4.
- Power facts come from WSL interop (PowerShell). The overlay GUID mapping in `env.py`
  is checked on the first board run; if interop fails, `--power-confirmed` records a manual
  check.
- `t_c_rx_ns` includes the Python read path, so the U bound errs on the long side.
- Jitter in B1 is measured without network traffic in WSL2. Under the tunnel and load
  (B3, C1, C3) the scan callback's spread is larger than B1's (sd 2.3 ms against 0.29 ms
  in B3 run 1) and some runs show one deviation of about 49 ms (run 2, 13, 24, 37 of C3,
  `jitter` verdict True, one skipped scan each). The cause is not traced; the runs are
  kept because the U bound and the lost count are unaffected.
- Restart Gazebo before every run so the robot starts at the origin, as in A1.
