#!/usr/bin/env python3
import math, csv, sys
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import LaserScan
from nav_msgs.msg import Odometry
from geometry_msgs.msg import Twist
import json, hashlib, subprocess, os, time

D_COL, D_STOP, D_SLOW = 0.15, 0.20, 0.40
V_MAX, W_MAX = 0.22, 1.0
WP_TOL = 0.15
RMIN, RMAX = 0.12, 3.5
WAYPOINTS = [(1.2, 0.0), (1.2, 1.2), (-1.2, 1.2), (-1.2, -1.2), (0.8, -1.2)]
REPO = os.path.expanduser('~/kics2026')
WORLD = os.path.expanduser(
    '~/tb3_ws/install/turtlebot3_gazebo/share/'
    'turtlebot3_gazebo/worlds/a1_course.world')


def sha1_of(path):
    try:
        with open(path, 'rb') as f:
            return hashlib.sha1(f.read()).hexdigest()[:12]
    except OSError:
        return 'missing'


def dump_meta(run_id):
    meta = {
        'run_id': run_id,
        'started': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'waypoints': WAYPOINTS,
        'd_col': D_COL, 'd_stop': D_STOP, 'd_slow': D_SLOW,
        'v_max': V_MAX, 'v_slow_floor': 0.10,
        'sector_half_deg': 48, 'sector_count': 16,
        'world_sha1': sha1_of(WORLD),
        'code_sha1': sha1_of(os.path.abspath(__file__)),
    }
    try:
        meta['git'] = subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            cwd=REPO, stderr=subprocess.DEVNULL).decode().strip()
        dirty = subprocess.check_output(
            ['git', 'status', '--porcelain', '--',
             'sim', 'fw', 'capture', 'load', 'net'],
            cwd=REPO, stderr=subprocess.DEVNULL).decode().strip()
        meta['git_dirty'] = bool(dirty)
    except Exception:
        meta['git'] = 'unknown'
        meta['git_dirty'] = True
    with open(f'run_{run_id}_meta.json', 'w') as f:
        json.dump(meta, f, indent=2)

class A1Controller(Node):
    def __init__(self, run_id):
        super().__init__('a1_controller')
        self.create_subscription(LaserScan, '/scan', self.on_scan, 10)
        self.create_subscription(Odometry, '/odom', self.on_odom, 10)
        self.pub = self.create_publisher(Twist, '/cmd_vel', 10)

        self.min_range = float('inf')
        self.x = self.y = self.yaw = 0.0
        self.wp_i = 0
        self.mode = 'RUN'
        self.collided = False
        self.t0 = None
        self.done = False

        if os.path.exists(f'run_{run_id}.csv'):
            raise SystemExit(f'run_{run_id}.csv already exists. Pick another run_id.')
        dump_meta(run_id)
        self.f = open(f'run_{run_id}.csv', 'w', newline='')
        self.w = csv.writer(self.f)
        self.w.writerow(['t', 'x', 'y', 'yaw', 'min_range', 'wp_i', 'mode', 'v', 'w'])

        self.create_timer(0.05, self.step)

    def on_scan(self, msg):
        n = len(msg.ranges)
        idx = list(range(0, 48)) + list(range(n - 48, n))
        vals = [msg.ranges[i] for i in idx]
        vals = [r for r in vals if RMIN <= r <= RMAX]
        self.min_range = min(vals) if vals else float('inf')

    def on_odom(self, msg):
        p = msg.pose.pose.position
        q = msg.pose.pose.orientation
        self.x, self.y = p.x, p.y
        self.yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y),
                              1.0 - 2.0 * (q.y * q.y + q.z * q.z))

    def now(self):
        s = self.get_clock().now().nanoseconds * 1e-9
        if self.t0 is None:
            self.t0 = s
        return s - self.t0

    def step(self):
        t = self.now()
        if self.done:
            return

        if self.min_range < D_COL:
            self.collided = True

        if self.wp_i >= len(WAYPOINTS):
            self.finish(t, 'GOAL')
            return
        if t > 60.0:
            self.finish(t, 'TIMEOUT')
            return

        gx, gy = WAYPOINTS[self.wp_i]
        dx, dy = gx - self.x, gy - self.y
        dist = math.hypot(dx, dy)
        if dist < WP_TOL:
            self.wp_i += 1
            return

        err = math.atan2(dy, dx) - self.yaw
        err = math.atan2(math.sin(err), math.cos(err))

        if self.min_range < D_STOP:
            self.mode, v = 'STOP', 0.0
        elif self.min_range < D_SLOW:
            self.mode = 'SLOW'
            v = V_MAX * (self.min_range - D_STOP) / (D_SLOW - D_STOP)
            v = max(v, 0.10)
        else:
            self.mode, v = 'RUN', V_MAX

        if abs(err) > 0.4:
            v = 0.0
        w = max(-W_MAX, min(W_MAX, 2.0 * err))

        cmd = Twist()
        cmd.linear.x, cmd.angular.z = v, w
        self.pub.publish(cmd)

        self.w.writerow([f'{t:.3f}', f'{self.x:.4f}', f'{self.y:.4f}',
                         f'{self.yaw:.4f}', f'{self.min_range:.4f}',
                         self.wp_i, self.mode, f'{v:.4f}', f'{w:.4f}'])

    def finish(self, t, why):
        self.done = True
        self.pub.publish(Twist())
        self.f.close()
        self.get_logger().info(
            f'{why} t={t:.2f}s wp={self.wp_i}/{len(WAYPOINTS)} collided={self.collided}')


def main():
    run_id = sys.argv[1] if len(sys.argv) > 1 else '1'
    rclpy.init()
    node = A1Controller(run_id)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
