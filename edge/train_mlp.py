#!/usr/bin/env python3
"""Trains the learned edge controller (edge/mlp.py) by imitating the waypoint follower of
sim/nav.py with no speed rule (D21: rule none), and writes edge/mlp_weights.json.

  python3 edge/train_mlp.py                 (N1, WSL2; numpy only, about a minute)
  python3 edge/train_mlp.py --check         (figures of the current weights, no training)

Demonstrations. The follower drives the A1 course on the kinematic robot of
sim/bridge/dry_run.py (unicycle, 20 Hz), with the command applied one period late as on
a healthy edge link, so the states are the ones the loopback dry runs visit. The bridge,
the fake board and Gazebo add nothing to the (state, command) pairs: in edge mode the
board passes the edge command unchanged. Starts are perturbed so that the network sees
headings far off the path:
  - 20 runs from the course start, position +/-5 cm, heading sigma 0.3 rad
  - 20 runs from the course start, position +/-30 cm, heading uniform
  - 4 runs from every waypoint with a uniform heading (large errors on every leg)
Then two rounds of DAgger: the network drives, the follower labels the states the network
reached, and the network is retrained on everything. This is what keeps a learned
controller from drifting into states no demonstration covered.

Labels are the follower outputs: v = V_MAX, or 0 while the heading error exceeds 0.4 rad
(turn in place); w = W_GAIN * error clipped to W_MAX. min_range is not used.

Everything is seeded; the JSON records the seed, the sample counts, the fit figures, the
closed-loop course time against the follower, and the SHA-1 of this file and sim/nav.py.
"""
import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'sim'))
sys.path.insert(0, str(REPO / 'edge'))
import nav                      # noqa: E402
import mlp as mlpmod            # noqa: E402

OUT = REPO / 'edge' / 'mlp_weights.json'
SEED = 20260922
DT = 0.05
HIDDEN = (24, 24)
RUN_LIMIT_STEPS = 1400          # 70 s
FLIP_BAND = 0.35                # rad next to +/-pi left out of the w fit figure


def follower_label(x, y, yaw, goal):
    """rule none: the follower's command with no speed rule."""
    _, _, turning, w = nav.heading(x, y, yaw, goal)
    return (0.0 if turning else nav.V_MAX), w


def drive(start, policy, rng=None, noise=0.0):
    """Runs the course from start = (x, y, yaw, wp_i) with the command applied one period
    late. policy(x, y, yaw, goal) -> (v, w). Returns (features, labels, steps, done)."""
    x, y, yaw, wp = start
    wps = nav.WAYPOINTS
    applied = (0.0, 0.0)
    feats, labels = [], []
    for k in range(RUN_LIMIT_STEPS):
        while wp < len(wps) and math.hypot(wps[wp][0] - x, wps[wp][1] - y) < nav.WP_TOL:
            wp += 1
        if wp >= len(wps):
            return feats, labels, k, True
        goal = wps[wp]
        feats.append(mlpmod.features(x, y, yaw, goal))
        labels.append(follower_label(x, y, yaw, goal))
        cmd = policy(x, y, yaw, goal)
        if noise and rng is not None:
            cmd = (max(0.0, cmd[0] + rng.normal(0, noise * nav.V_MAX)),
                   cmd[1] + rng.normal(0, noise * nav.W_MAX))
        v, w = applied
        x += v * math.cos(yaw) * DT
        y += v * math.sin(yaw) * DT
        yaw = math.atan2(math.sin(yaw + w * DT), math.cos(yaw + w * DT))
        applied = cmd
    return feats, labels, RUN_LIMIT_STEPS, False


def starts(rng):
    s = []
    for _ in range(20):
        s.append((rng.uniform(-.05, .05), rng.uniform(-.05, .05), rng.normal(0, .3), 0))
    for _ in range(20):
        s.append((rng.uniform(-.3, .3), rng.uniform(-.3, .3), rng.uniform(-math.pi, math.pi), 0))
    prev = [(0.0, 0.0)] + nav.WAYPOINTS[:-1]
    for i, p in enumerate(prev):
        for _ in range(4):
            s.append((p[0] + rng.uniform(-.1, .1), p[1] + rng.uniform(-.1, .1),
                      rng.uniform(-math.pi, math.pi), i))
    return s


# ---------------------------------------------------------------- network (numpy)
def init(rng, sizes):
    return [(rng.normal(0, 1.0 / math.sqrt(a), (b, a)), np.zeros(b))
            for a, b in zip(sizes, sizes[1:])]


def fwd(params, X):
    hs = [X]
    h = X
    for i, (W, b) in enumerate(params):
        z = h @ W.T + b
        h = np.tanh(z) if i < len(params) - 1 else z
        hs.append(h)
    return hs


def outputs(o):
    return 1.0 / (1.0 + np.exp(-o[:, 0])), np.tanh(o[:, 1])


def loss_grad(params, X, T):
    hs = fwd(params, X)
    sv, tw = outputs(hs[-1])
    ev, ew = sv - T[:, 0], tw - T[:, 1]
    n = len(X)
    loss = float((ev ** 2).mean() + (ew ** 2).mean())
    d = np.stack([2 * ev * sv * (1 - sv), 2 * ew * (1 - tw ** 2)], axis=1) / n
    grads = [None] * len(params)
    for i in range(len(params) - 1, -1, -1):
        W, _ = params[i]
        grads[i] = (d.T @ hs[i], d.sum(0))
        if i:
            d = (d @ W) * (1 - hs[i] ** 2)
    return loss, grads


def train(params, X, T, rng, epochs, lr=3e-3, batch=256):
    m = [(np.zeros_like(W), np.zeros_like(b)) for W, b in params]
    v = [(np.zeros_like(W), np.zeros_like(b)) for W, b in params]
    b1, b2, eps, t = 0.9, 0.999, 1e-8, 0
    for _ in range(epochs):
        idx = rng.permutation(len(X))
        for s in range(0, len(X), batch):
            j = idx[s:s + batch]
            _, g = loss_grad(params, X[j], T[j])
            t += 1
            for k, ((W, b), (gW, gb)) in enumerate(zip(params, g)):
                (mW, mb), (vW, vb) = m[k], v[k]
                mW[:] = b1 * mW + (1 - b1) * gW
                mb[:] = b1 * mb + (1 - b1) * gb
                vW[:] = b2 * vW + (1 - b2) * gW ** 2
                vb[:] = b2 * vb + (1 - b2) * gb ** 2
                c1, c2 = 1 - b1 ** t, 1 - b2 ** t
                W -= lr * (mW / c1) / (np.sqrt(vW / c2) + eps)
                b -= lr * (mb / c1) / (np.sqrt(vb / c2) + eps)
    return loss_grad(params, X, T)[0]


def to_json(params, meta):
    layers = [{'W': np.round(W, 6).tolist(), 'b': np.round(b, 6).tolist(),
               'act': 'tanh' if i < len(params) - 1 else 'linear'}
              for i, (W, b) in enumerate(params)]
    return {'format': mlpmod.FORMAT, 'inputs': ['sin_err', 'cos_err', 'dist_norm'],
            'd_scale': mlpmod.D_SCALE, 'v_max': nav.V_MAX, 'w_max': nav.W_MAX,
            'outputs': {'v': 'v_max * sigmoid(o0)', 'w': 'w_max * tanh(o1)'},
            'layers': layers, 'train': meta}


# ---------------------------------------------------------------- figures
def figures(net):
    """Fit on a heading-error grid, and the course driven by the network vs the follower."""
    worst_w = worst_v = 0.0
    flip = 0.0      # how far before +/-pi the network already turns the other way
    for d in (0.2, 1.0, 2.0):
        for i in range(721):
            e = -math.pi + 2 * math.pi * i / 720
            f = [math.sin(e), math.cos(e), min(d, mlpmod.D_SCALE) / mlpmod.D_SCALE]
            v, w = net.forward(f)
            fv = 0.0 if abs(e) > nav.TURN_IN_PLACE else nav.V_MAX
            fw = max(-nav.W_MAX, min(nav.W_MAX, nav.W_GAIN * e))
            if abs(e) > 0.5 and w * e < 0:           # turns away from the short way
                flip = max(flip, math.pi - abs(e))
            elif abs(abs(e) - math.pi) > FLIP_BAND:
                worst_w = max(worst_w, abs(w - fw))
            if abs(abs(e) - nav.TURN_IN_PLACE) > 0.05:  # v is a step at 0.4 rad
                worst_v = max(worst_v, abs(v - fv))
    net_policy = lambda x, y, yaw, g: net.forward(mlpmod.features(x, y, yaw, g))
    _, _, k_net, done_net = drive((0.0, 0.0, 0.0, 0), net_policy)
    _, _, k_fol, done_fol = drive((0.0, 0.0, 0.0, 0), follower_label)
    return {'grid_max_abs_w_err': round(worst_w, 4), 'grid_max_abs_v_err': round(worst_v, 4),
            'grid_flip_before_pi_rad': round(flip, 3),
            'grid_note': f'w excluding {FLIP_BAND} rad next to +/-pi, v excluding 0.05 rad '
                         'around its step at 0.4 rad. Near +/-pi both turn directions reach '
                         'the heading in about the same time; the flip figure says how early '
                         'the network already takes the long way',
            'course_s_mlp': round(k_net * DT, 2) if done_net else None,
            'course_s_follower': round(k_fol * DT, 2) if done_fol else None}


def sha1(path):
    return hashlib.sha1(Path(path).read_bytes()).hexdigest()[:12]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--out', default=str(OUT))
    args = ap.parse_args()
    if args.check:
        net = mlpmod.MLP(args.out)
        print(json.dumps({**net.spec['train'], 'now': figures(net)}, indent=2))
        return

    t_start = time.time()
    rng = np.random.default_rng(SEED)
    X, T, runs = [], [], 0
    for s in starts(rng):
        f, l, _, _ = drive(s, follower_label)
        X += f
        T += l
        runs += 1
    demo = len(X)
    params = init(rng, (3,) + HIDDEN + (2,))

    def arrays():
        Xa = np.array(X)
        Ta = np.array(T) / np.array([nav.V_MAX, nav.W_MAX])
        return Xa, Ta

    loss = train(params, *arrays(), rng, epochs=60)
    dagger = []
    for rnd in range(2):
        net = _wrap(params)
        pol = lambda x, y, yaw, g: net.forward(mlpmod.features(x, y, yaw, g))
        added = 0
        for s in starts(rng)[:24]:
            f, l, _, _ = drive(s, pol, rng=rng, noise=0.05)
            X += f
            T += l
            added += len(f)
        loss = train(params, *arrays(), rng, epochs=40)
        dagger.append({'round': rnd + 1, 'added': added, 'loss': round(loss, 6)})

    net = _wrap(params)
    meta = {'seed': SEED, 'hidden': list(HIDDEN), 'demo_runs': runs, 'demo_samples': demo,
            'dagger': dagger, 'samples': len(X), 'final_loss': round(loss, 6),
            'labels': 'sim/nav.py follower, rule none (no speed rule)',
            'train_sha1': sha1(__file__), 'nav_sha1': sha1(REPO / 'sim' / 'nav.py'),
            'mlp_sha1': sha1(REPO / 'edge' / 'mlp.py'),
            'seconds': round(time.time() - t_start, 1), **figures(net)}
    Path(args.out).write_text(json.dumps(to_json(params, meta)) + '\n')
    print(json.dumps(meta, indent=2))
    print(f'-> {args.out}  (weights sha1 {sha1(args.out)})')


def _wrap(params):
    """numpy params -> an mlp.MLP-compatible object without writing a file."""
    net = mlpmod.MLP.__new__(mlpmod.MLP)
    net.layers = [(W.tolist(), b.tolist(), 'tanh' if i < len(params) - 1 else 'linear')
                  for i, (W, b) in enumerate(params)]
    net.v_max, net.w_max = nav.V_MAX, nav.W_MAX
    return net


if __name__ == '__main__':
    main()
