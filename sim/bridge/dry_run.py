"""Dry run of the bridge without ROS or Gazebo.

A unicycle robot on the A1 course (sim/worlds/a1_course.world geometry) with a 360-ray
LiDAR, stepped at 20 Hz in wall time, drives the same Runner, Link and meta code as the
ROS node. It checks the bridge end to end against fw/host/fake_stm32 (or the board), not
the robot: the kinematics are ideal, so its times and distances are not A1 figures.

From sim/, with the fake board on /tmp/vhost (see sim/bridge/README.md):
  python3 -m bridge.dry_run --run 1 --target fake --port /tmp/vhost --out /tmp/b1
"""
import argparse
import math
import random
import threading
import time

from . import run

DT = 0.05
OBSTACLES = [(0.6, -0.42), (0.0, 0.78), (-0.78, 0.4), (-0.4, -0.78), (0.78, 0.6)]
OBS_R = 0.15
WALL = 1.95            # inner face of the 0.1 m thick walls at +/-2.0
LIDAR_BACK = 0.032     # LDS-01 behind the chassis centre
NOISE_SD = 0.01        # LDS-01 model noise in the TurtleBot3 SDF
RMIN, RMAX = 0.12, 3.5


def cast(px, py, ang):
    dx, dy = math.cos(ang), math.sin(ang)
    best = RMAX + 1.0
    for ox, oy in OBSTACLES:
        fx, fy = px - ox, py - oy
        b = fx * dx + fy * dy
        c = fx * fx + fy * fy - OBS_R * OBS_R
        disc = b * b - c
        if disc >= 0:
            t = -b - math.sqrt(disc)
            if t > 0:
                best = min(best, t)
    for d, p in ((dx, px), (dy, py)):
        if d > 1e-9:
            best = min(best, (WALL - p) / d)
        elif d < -1e-9:
            best = min(best, (-WALL - p) / d)
    return best


def scan(x, y, yaw, rng):
    px, py = x - LIDAR_BACK * math.cos(yaw), y - LIDAR_BACK * math.sin(yaw)
    out = []
    for i in range(360):
        r = cast(px, py, yaw + math.radians(i)) + rng.gauss(0.0, NOISE_SD)
        out.append(r if RMIN <= r <= RMAX else float('inf'))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    run.add_args(ap)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--a1-timer', action='store_true',
                    help='test: imitate A1, two steps on the same scan every 0.1 s')
    ap.add_argument('--stall-at', type=float, default=None,
                    help='test: pause the scan loop for 0.3 s at this sim time')
    args = ap.parse_args()

    cmd = {'v': 0.0, 'w': 0.0}
    lock = threading.Lock()

    def publish(v, w):
        with lock:
            cmd['v'], cmd['w'] = v, w

    s = run.Session(args, publish, harness='dry_run')
    s.meta['dry_run'] = {'seed': args.seed, 'noise_sd': NOISE_SD, 'stall_at': args.stall_at,
                         'a1_timer': args.a1_timer}
    rng = random.Random(args.seed)
    x = y = yaw = 0.0
    start = time.monotonic()
    k = 0
    try:
        while not s.runner.done_event.is_set():
            target = start + k * DT
            delay = target - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            sim_t = k * DT
            if args.stall_at is not None and abs(sim_t - args.stall_at) < DT / 2:
                time.sleep(0.3)
                start += 0.3
            s.runner.set_pose(x, y, yaw)
            if not args.a1_timer:
                s.runner.on_scan(scan(x, y, yaw, rng), sim_t, time.monotonic_ns())
            elif k % 2 == 0:
                ranges = scan(x, y, yaw, rng)
                for _ in range(2):
                    s.runner.on_scan(ranges, sim_t, time.monotonic_ns())
            with lock:
                v, w = cmd['v'], cmd['w']
            x += v * math.cos(yaw) * DT
            y += v * math.sin(yaw) * DT
            yaw = math.atan2(math.sin(yaw + w * DT), math.cos(yaw + w * DT))
            k += 1
            if k > 20 * 90:
                s.abort('dry run exceeded 90 s')
                break
    except KeyboardInterrupt:
        s.abort('KeyboardInterrupt')
    raise SystemExit(0 if s.finish() else 1)


if __name__ == '__main__':
    main()
