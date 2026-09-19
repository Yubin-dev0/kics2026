"""ROS 2 side of the N1 bridge: /scan and /odom in, /cmd_vel out.

Run from sim/ with Gazebo (a1_headless.launch.py) already up. No use_sim_time: the bridge
takes time from time.monotonic_ns() and simulation time from each scan's header.

  python3 -m bridge.node --run 1                      (board, policy 1, data/b1)
  python3 -m bridge.node --run 101 --target fake --port /tmp/vhost
"""
import argparse
import sys
import time

import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.utilities import remove_ros_args
from sensor_msgs.msg import LaserScan

from . import run
import nav


class BridgeNode(Node):
    def __init__(self):
        super().__init__('n1_bridge')
        self.runner = None
        self.pub = self.create_publisher(Twist, '/cmd_vel', 10)
        # same subscriptions and depth as sim/a1_controller.py
        self.create_subscription(LaserScan, '/scan', self.on_scan, 10)
        self.create_subscription(Odometry, '/odom', self.on_odom, 10)

    def publish(self, v, w):
        cmd = Twist()
        cmd.linear.x, cmd.angular.z = float(v), float(w)
        try:
            self.pub.publish(cmd)
        except Exception as e:  # context already shut down after Ctrl-C
            print(f'cmd_vel publish failed: {e}', file=sys.stderr)

    def on_scan(self, msg):
        t = time.monotonic_ns()
        if self.runner is None:
            return
        stamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        self.runner.on_scan(msg.ranges, stamp, t)

    def on_odom(self, msg):
        if self.runner is None:
            return
        p, q = msg.pose.pose.position, msg.pose.pose.orientation
        self.runner.set_pose(p.x, p.y, nav.yaw_from_quat(q.x, q.y, q.z, q.w))


def main():
    rclpy.init(args=sys.argv)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    run.add_args(ap)
    args = ap.parse_args(remove_ros_args(sys.argv)[1:])
    node = BridgeNode()
    try:
        session = run.Session(args, node.publish, harness='ros')
    except SystemExit:
        node.destroy_node()
        rclpy.shutdown()
        raise
    node.runner = session.runner
    print('waiting for /scan ...', flush=True)
    try:
        while rclpy.ok() and not session.runner.done_event.is_set():
            rclpy.spin_once(node, timeout_sec=0.05)
            problem = session.startup_problem()
            if problem:
                print(problem, flush=True)
                session.abort(problem)
    except KeyboardInterrupt:
        session.abort('KeyboardInterrupt')
    node.runner = None
    ok = session.finish()
    node.publish(0.0, 0.0)
    node.destroy_node()
    if rclpy.ok():
        rclpy.shutdown()
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
