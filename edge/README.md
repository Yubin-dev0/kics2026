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

Options: `--controller mlp|follower` (default mlp), `--weights`, `--rule none|a1` (follower only), `--log` (per-datagram CSV),
`--stats-every S`, `--quit-after S`; test delays `--delay-ms`, `--extra-ms`,
`--extra-from` (never in a sweep run: netem on N3 is the real thing).

## Controller (decided 2026-09-22)

The edge runs a **learned controller**: a small MLP (`mlp.py`, weights `mlp_weights.json`)
trained by imitation of the waypoint follower of `sim/nav.py` with no speed rule. It is the
default of `server.py`.

| item | value | basis |
|---|---|---|
| inputs | sin and cos of the heading error to `WAYPOINTS[wp_i]`, distance to it / 2.0 m (capped at 1) | relative to the current waypoint, never absolute coordinates: a start off the demonstrated path still maps to seen states |
| not an input | `min_mm` | the edge has no safety layer; safety runs on the robot (N2). This is the Simplex premise, and it keeps policy 2 from being pulled towards safety by its own controller (D19) |
| network | 3-24-24-2, tanh hidden layers; v = 0.22 * sigmoid, w = 1.0 * tanh | small enough to evaluate with the standard library on the lab PC (about 45 us per command) |
| labels | follower output, rule none: v 0.22 m/s or 0 while the heading error exceeds 0.4 rad, w = 2 * error clipped to 1 rad/s | D21 |
| demonstrations | kinematic robot of `sim/bridge/dry_run.py` at 20 Hz, command applied one period late as on a healthy link; 60 runs with perturbed starts (course start +/-5 cm and sigma 0.3 rad, +/-30 cm and any heading, every waypoint with any heading) | in edge mode the board passes the edge command unchanged, so the bridge and the fake board add nothing to the (state, command) pairs |
| compounding error | two DAgger rounds: the network drives (with 5% action noise), the follower labels the states it reached, retrain on all | a cloned controller drifts into states no demonstration covered unless it is trained on its own states |
| reproducibility | seed 20260922; the JSON holds the sample counts, fit figures and the SHA-1 of `train_mlp.py`, `mlp.py`, `sim/nav.py` | retraining on N1 (`python3 edge/train_mlp.py`, about 15 s) gives the same network up to float rounding |

Fit of the committed weights (`python3 edge/train_mlp.py --check`): w within 0.073 rad/s
of the follower and v within 0.008 m/s, except next to the two places the follower itself
jumps (v at 0.4 rad, w at +/-pi, where the network takes the long way round up to 0.17 rad
early; both directions reach the heading in about the same time). On the kinematic course
it drives 43.7 s, the same as the follower.

`--controller follower` runs the follower itself, for comparison or as a fallback. Its speed
rule is `--rule none` by default (D21); `--rule a1` adds the A1 rule from `min_mm`.

Neither choice is visible to N1: the startup line prints the controller and the weights
SHA-1, which go into the bridge run's `--note`.

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
