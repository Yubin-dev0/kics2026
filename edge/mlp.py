"""The learned edge controller: a small MLP, evaluated with the standard library only so
the lab PC needs no numpy. Weights come from edge/train_mlp.py as JSON.

Inputs are relative to the current waypoint, never absolute coordinates, so the network
cannot memorise the course and a start that is off the demonstrated path still maps to
states it has seen:

  sin(e), cos(e), min(d, D_SCALE) / D_SCALE

where e is the heading error to WAYPOINTS[wp_i] (wrapped to -pi..pi) and d the distance
to it. min_range is not an input: the demonstrations come from the follower with no
speed rule (D21, rule none), so the controller has no safety layer of its own, which is
the premise of the Simplex split (safety sits on the robot, in N2).

Outputs: v = V_MAX * sigmoid(o0), w = W_MAX * tanh(o1).
"""
import json
import math

FORMAT = 'kics2026-mlp-v1'
D_SCALE = 2.0      # m, distances beyond this read as 1.0 (the course is 2.4 m across)


def features(x, y, yaw, goal):
    dx, dy = goal[0] - x, goal[1] - y
    err = math.atan2(dy, dx) - yaw
    err = math.atan2(math.sin(err), math.cos(err))
    d = math.hypot(dx, dy)
    return [math.sin(err), math.cos(err), min(d, D_SCALE) / D_SCALE]


class MLP:
    def __init__(self, path):
        with open(path) as f:
            spec = json.load(f)
        if spec.get('format') != FORMAT:
            raise ValueError(f'{path}: not a {FORMAT} file')
        self.spec = spec
        self.layers = [(l['W'], l['b'], l['act']) for l in spec['layers']]
        self.v_max = spec['v_max']
        self.w_max = spec['w_max']

    def raw(self, x):
        for W, b, act in self.layers:
            y = [sum(wij * xj for wij, xj in zip(row, x)) + bi for row, bi in zip(W, b)]
            x = [math.tanh(v) for v in y] if act == 'tanh' else y
        return x

    def forward(self, feats):
        """-> (v m/s, w rad/s)"""
        o0, o1 = self.raw(feats)
        # sigmoid written so large |o0| cannot overflow
        v = 1.0 / (1.0 + math.exp(-o0)) if o0 >= 0 else math.exp(o0) / (1.0 + math.exp(o0))
        return self.v_max * v, self.w_max * math.tanh(o1)
