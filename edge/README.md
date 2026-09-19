# N4 edge controller (A10)

`server.py` answers the N1 state datagrams of `sim/bridge/edge.py` with commands. It
uses the standard library only and imports `sim/nav.py` (waypoint formulas) and
`sim/bridge/edge.py` (datagram builders) from the same clone, so it runs unchanged in
WSL2 (loopback check) and under native Windows Python on the lab PC.

## Usage

WSL2, loopback check with the fake board (see `sim/bridge/README.md`):

    python3 edge/server.py --listen 127.0.0.1:47000 --log /tmp/a10/edge_run_1.csv

Lab PC (Windows 11, `py` launcher, clone at `C:\dev\kics2026`):

    py edge\server.py --listen 0.0.0.0:47000 --log data\a10\edge_run_1.csv

Windows Defender asks once to allow Python on private networks; UDP 47000 inbound must be
allowed for the N3 side. The N1 bridge then runs with `--edge <N4 address>`.

Options: `--rule a1|none` (edge-side speed rule, below), `--log` (per-datagram CSV),
`--stats-every S`, `--quit-after S`; test delays `--delay-ms`, `--extra-ms`,
`--extra-from` (never in a sweep run: netem on N3 is the real thing).

## Controller

Waypoint follower of `sim/nav.py` (heading gain 2.0, turn in place above 0.4 rad, V_MAX
220 mm/s), steering to `WAYPOINTS[wp_i]` with the `wp_i` N1 sends; N1 advances the index.

| rule | edge speed | reading of policy 2 | status |
|---|---|---|---|
| `a1` (default) | follower speed capped by the A1 rule from the `min_mm` in the state (RUN / SLOW / STOP as on the board) | same controller as policy 1, only remote: any extra collision is stale-command effect alone | provisional (D21) |
| `none` | follower speed, never slows | high-performance controller with no safety layer | provisional (D21) |

The bridge cannot see which rule ran; put it in the bridge run's `--note`.

## Log: `edge_run_N.csv`

One row per datagram, N4 monotonic clock (`time.monotonic_ns()`), unrelated to N1's.

| column | unit | meaning |
|---|---|---|
| n | - | datagram count in this server session |
| t_rx_ns | ns | right after `recvfrom` returned |
| seq, t_send_ns | -, ns | copied from the Q datagram (N1 step and N1 send time) |
| x_mm, y_mm, yaw_mrad, min_mm, wp_i | mm, mm, mrad, mm, - | the state N1 sent |
| v_mm, w_mrad | mm/s, mrad/s | the command returned |
| proc_us | us | `recvfrom` return to `sendto` return (to timer start under a test delay) |

## Pass criteria

In `sim/bridge/README.md` (A10-1 to A10-4); measured from N1 with
`python3 -m bridge.test_edge --probe <N4 address>:47000 --count 1000`.
