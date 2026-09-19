# N1 UART bridge (B1 onward)

Connects the Gazebo robot (N1) to the N2 safety board over the line protocol in
`fw/PROTOCOL.md`, and later to the N4 edge controller and the N3 switching flags. The
same code is used from B1 through the C stage, so every timestamp the timing chain needs
is recorded from the first run.

## Files

| file | contents |
|---|---|
| `core.py` | serial link (send, reader thread, reply matching, bridge watchdog), run logic, CSV log. No ROS |
| `node.py` | ROS 2 node: `/scan`, `/odom` in, `/cmd_vel` out |
| `run.py` | one run from port open to meta file; verdict |
| `summary.py` | run figures computed from `run_N.csv` (also a CLI) |
| `env.py` | environment capture: Windows power mode, WSL and usbipd versions, routes, git, code hashes (also a CLI) |
| `policy.py` | switching policies 1 and 4; 2 and 3 arrive with B2 and B4 |
| `netio.py` | edge stub, N3 flag listener with keepalive, flag-path probe CLI |
| `fake_n3.py` | stand-alone flag sender for tests and the A4 flag-path check |
| `dry_run.py` | kinematic robot on the A1 course, drives the same code without ROS |
| `../nav.py` | waypoint follower shared with the A1 formulas; `../test_nav.py` replays A1 |

## Control step

One S line per `/scan`. N1 computes the heading (`local_w`) and whether the robot may move
forward (`local_v` = 220 or 0, turning in place above 0.4 rad). N2 applies the safety rule
and returns the command, which the bridge publishes when the C line arrives. A step that
reaches a waypoint computes the next heading in the same scan (A1 did the same in its
paired timer call, see `sim/NOTES.md`), so the line cadence stays at 20 Hz.

Before the run, one `settle` line puts the board in the policy's starting mode with zero
speed. Its switch is kept out of the run figures. The run starts at the next scan and ends
on the last waypoint (GOAL) or after 60 s of simulation time (TIMEOUT). After the last
line the board's watchdog fires once; its gap is recorded as `wdog_end_gap_ms`.

A mode change between scans (policy 4) goes out at once as a `flag` line that reuses the
latest scan's seq and sensor values. The firmware echoes seq without checking it
(`fw/core/app.c`), so `line` is the unique key of the log.

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

B inside the bridge is `t_uart_tx_ns - t_flag_rx_ns`. U (UART write to board receive)
cannot be timed one way; `t_c_rx_ns - t_uart_tx_ns` of the same line is its upper bound
(`fw/NOTES.md`, timing chain).

## Log: `run_N.csv`

One row per S line (`kind` = settle, scan, flag) written when its C line arrives or after
200 ms without one (`lost` = 1). Board watchdog lines (`wdog`) and bridge stops
(`bridge_stop`) get rows of their own. Rows are in completion order; sort by `line`.

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
| edge_v, edge_w, edge_ok | mm/s, mrad/s, - | edge command; 0, 0, 0 from the stub until B2 |
| c_seq ... bad_lines | | C line fields (`fw/PROTOCOL.md`) |
| lost | - | 1 when no C line came within 200 ms |

## Usage (from `sim/` in WSL2)

    python3 test_nav.py
    python3 -m bridge.env
    python3 -m bridge.node --run 1
    python3 -m bridge.node --run 101 --target fake --port /tmp/vhost
    python3 -m bridge.summary ../data/b1/run_1.csv

Fake board (socat 1.7.4 on N1 rejects `-d0`):

    make -C ../fw/host fake_stm32
    socat pty,raw,echo=0,link=/tmp/vboard pty,raw,echo=0,link=/tmp/vhost &
    ../fw/host/fake_stm32 /tmp/vboard &
    python3 -m bridge.dry_run --run 1 --target fake --port /tmp/vhost --out /tmp/b1

Dry runs go to `/tmp` and are never registered. Driving runs are registered from the
repository root: `python3 analysis/append_run.py B1 N`.

## B1 pass criteria

| ID | criterion | basis | status |
|---|---|---|---|
| B1-1 | Firmware reports proto 2 and build "Sep 16 2026 18:11:48 dbg" | same board as A2 and A3 | confirmed |
| B1-2 | Policy 1: GOAL, 5/5 waypoints, min_range never below d_col 0.15 m | plan 10.2 | confirmed |
| B1-2a | Scan-driven loop: consecutive scan lines are 0.05 s apart in sim time (median) and no two share a sim time | A1 ran a sim-clock timer that fired twice per /clock tick (sim/NOTES.md); a repeat would mean the same scan was used twice | confirmed |
| B1-3 | Loop jitter: \|interval of S line writes - 50 ms\| p99 < 5 ms, and no interval of 75 ms or more | plan 10.2 bound (10% of the control period). p99 alone passes a run with a few long stalls; a skipped scan means the robot ran one extra period on an old command. A1 measured /scan at sigma 0.4 ms over 2063 intervals, which rules out any skip (one would raise sigma above 1.1 ms) | provisional |
| B1-4 | Link: lost 0, bad_lines +0, no WDOG line and no bridge stop during the run, no stray line | A2/A3 V5 rule carried into driving | provisional |
| B1-5 | U upper bound p99 < 5 ms | A3-7 | provisional |
| B1-6 | Charger connected and Windows power mode Best performance | A3 findings | confirmed |
| B1-7 | B1-1 to B1-6 on 3 consecutive board runs | A2/A3 streak rule | confirmed |
| B1-8 | Fake-board control runs 101-103 pass B1-2 to B1-5 | isolates the USB path and the board: same bridge, same core firmware in WSL2 | provisional |

Comparison with A1 is qualitative (collisions, completion, min_range above d_stop):
A1 updated its command at 10 Hz of simulation time, B1 at 20 Hz. Board runs against the
fake-board runs are the quantitative comparison; the differences in duration and
min_range are reported without a pass bar.

Fake-board control runs are registered (node N1), unlike the fake bench runs in `fw/`,
because they are the reference the board runs are compared with.

## Known constraints

- Policies 2 and 3 are not implemented; the edge command is a stub (0, 0). B2 adds the N4
  UDP client with its 50 ms deadline and miss count.
- The flag datagram format in `netio.py` and port 47100 are provisional until agreed with
  the N3 detector owner. The keepalive path through WSL2 NAT is tested at A4.
- Power facts come from WSL interop (PowerShell). The overlay GUID mapping in `env.py`
  is checked on the first board run; if interop fails, `--power-confirmed` records a manual
  check.
- `t_c_rx_ns` includes the Python read path, so the U bound errs on the long side.
- Jitter in B1 is measured without network traffic in WSL2; B3 and later repeat it under
  load.
- Restart Gazebo before every run so the robot starts at the origin, as in A1.
