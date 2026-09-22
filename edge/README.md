# N4 edge controller (A10)

`server.py` answers the N1 state datagrams of `sim/bridge/edge.py` with commands. It
uses the standard library only and imports `sim/nav.py` (waypoint formulas) and
`sim/bridge/edge.py` (datagram builders) from the same clone, so it runs unchanged in
WSL2 (loopback check) and under native Windows Python on the lab PC.

## Usage

WSL2, loopback check with the fake board (see `sim/bridge/README.md`):

    python3 edge/server.py --listen 127.0.0.1:47000 --log /tmp/a10/edge_run_1.csv

Lab PC (Windows 11, `py` launcher). The lab PC only reads the repository, so it clones
over HTTPS without a key, and `git log -1` there names the commit the server ran:

    git clone https://github.com/Yubin-dev0/kics2026 C:\dev\kics2026
    py edge\server.py --listen 0.0.0.0:47000

Before the first run, in an administrator PowerShell, allow the edge port (and ping, for
A6) on every network profile, since the USB-LAN adapter and the tunnel come up as
unidentified public networks:

    New-NetFirewallRule -DisplayName "kics2026 edge UDP 47000" -Direction Inbound -Protocol UDP -LocalPort 47000 -Action Allow -Profile Any
    New-NetFirewallRule -DisplayName "kics2026 ping" -Direction Inbound -Protocol ICMPv4 -IcmpType 8 -Action Allow -Profile Any

If Windows also shows its own prompt for Python, allow it. Cancelling it creates a block
rule for python.exe, and a block rule wins over the port rule.

Restart the server before each A10 probe run: its periodic line (`proc p99 ... us`) then
covers exactly that run's datagrams, and the probe asks for that figure at the end (A10-3).
The N1 bridge then runs with `--edge <N4 address>`; the addresses are in `net/README.md`.

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
