"""Waypoint follower and forward-sector minimum shared by the N1 bridge.

The formulas are the ones sim/a1_controller.py ran in A1 (runs 10-12). That file is left
untouched because the A1 runs record its code_sha1; sim/test_nav.py replays the A1 logs
through this module and requires the same v and w on every row.

Split of work in stage B: this module decides the heading (local_w) and whether the robot
may move forward at all (local_v = V_MAX or 0). The speed rule (RUN/SLOW/STOP) runs on the
N2 board, which outputs min(safety speed, local_v).
"""
import math

D_COL = 0.15           # m, collision threshold (sensor-referenced), confirmed in A1
V_MAX = 0.22           # m/s
W_MAX = 1.0            # rad/s
W_GAIN = 2.0           # w = W_GAIN * heading error, clipped to W_MAX
TURN_IN_PLACE = 0.4    # rad, heading error above which v = 0
WP_TOL = 0.15          # m, waypoint reached radius
RMIN, RMAX = 0.12, 3.5 # m, valid LiDAR range
SECTOR_RAYS = 48       # rays on each side of index 0 (+/-48 deg at 1 deg per ray)
WAYPOINTS = [(1.2, 0.0), (1.2, 1.2), (-1.2, 1.2), (-1.2, -1.2), (0.8, -1.2)]
RUN_LIMIT_S = 60.0     # simulation seconds, same timeout as A1


def front_min(ranges):
    """Smallest valid reading in the forward +/-48 deg sector; inf when none is valid.

    The minimum over 16 sectors of 6 rays equals the minimum over all 96 rays, so the
    sector split does not need to be computed here."""
    n = len(ranges)
    idx = list(range(0, SECTOR_RAYS)) + list(range(n - SECTOR_RAYS, n))
    vals = [ranges[i] for i in idx if RMIN <= ranges[i] <= RMAX]
    return min(vals) if vals else float('inf')


def yaw_from_quat(qx, qy, qz, qw):
    return math.atan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz))


def heading(x, y, yaw, goal):
    """Returns (dist, err, turning, w) towards goal. turning means v must be 0."""
    gx, gy = goal
    dx, dy = gx - x, gy - y
    dist = math.hypot(dx, dy)
    err = math.atan2(dy, dx) - yaw
    err = math.atan2(math.sin(err), math.cos(err))
    w = max(-W_MAX, min(W_MAX, W_GAIN * err))
    return dist, err, abs(err) > TURN_IN_PLACE, w


class Follower:
    """Waypoint sequencing exactly as in A1: a step that reaches a waypoint only advances
    the index and issues no new command."""

    def __init__(self, waypoints=WAYPOINTS):
        self.waypoints = list(waypoints)
        self.wp_i = 0

    @property
    def done(self):
        return self.wp_i >= len(self.waypoints)

    def step(self, x, y, yaw):
        """Returns None when the step only advanced the waypoint (or the course is done),
        otherwise (local_v in m/s: V_MAX or 0, w in rad/s)."""
        if self.done:
            return None
        dist, _, turning, w = heading(x, y, yaw, self.waypoints[self.wp_i])
        if dist < WP_TOL:
            self.wp_i += 1
            return None
        return (0.0 if turning else V_MAX), w


def a1_float_speed(min_range):
    """The A1 float safety rule, for replay tests only (the board runs the integer port)."""
    if min_range < 0.20:
        return 'STOP', 0.0
    if min_range < 0.40:
        return 'SLOW', max(V_MAX * (min_range - 0.20) / 0.20, 0.10)
    return 'RUN', V_MAX
