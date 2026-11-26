#!/usr/bin/env python3
"""Run a Twist/odometry acceptance test against the installed Gazebo plugin."""
import argparse
from contextlib import contextmanager, nullcontext
import math
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

from ament_index_python.packages import get_package_prefix, get_package_share_directory
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
import rclpy
from rclpy.node import Node
from rclpy.signals import SignalHandlerOptions
import xacro


@contextmanager
def gazebo():
    # Limit the test's ROS traffic and Gazebo transport to a separate local simulation.
    domain = str(5 + secrets.randbelow(90))
    if domain == os.environ.get("ROS_DOMAIN_ID", "0"):
        domain = "95"
    os.environ.update(ROS_LOCALHOST_ONLY="1", ROS_DOMAIN_ID=domain)
    env = dict(os.environ)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env.update(GAZEBO_MASTER_URI=f"http://127.0.0.1:{port}", GAZEBO_MODEL_DATABASE_URI="")
    plugin_lib = Path(get_package_prefix("four_steer_amr_plugin")) / "lib"
    env["GAZEBO_PLUGIN_PATH"] = os.pathsep.join([str(plugin_lib), env.get("GAZEBO_PLUGIN_PATH", "")])
    share = Path(get_package_share_directory("steer_agv_sim_test"))
    with tempfile.TemporaryDirectory(prefix="steer-agv-sim-") as temp:
        folder = Path(temp)
        urdf = folder / "agv.urdf"
        urdf.write_text(xacro.process_file(str(share/"plugin_test.xacro"), mappings={"prefix":"test_"}).toxml())
        converted = subprocess.check_output(["gz", "sdf", "-p", str(urdf)], env=env, timeout=15)
        model = ET.fromstring(converted).find("model")
        if model is None or model.find("plugin[@filename='libfour_steer_amr_plugin.so']") is None:
            raise RuntimeError("Gazebo model conversion lost the drive plugin")
        world = ET.fromstring('<sdf version="1.7"><world name="test"><include><uri>model://ground_plane</uri></include></world></sdf>')
        world.find("world").append(model)
        path = folder / "test.world"
        ET.ElementTree(world).write(path)
        with (folder/"gazebo.log").open("w+") as log:
            server = subprocess.Popen(["gzserver", "--verbose", "-s", "libgazebo_ros_init.so", str(path)],
                                      env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                print(f"Gazebo Classic | ROS_DOMAIN_ID={domain} | {plugin_lib}", flush=True)
                yield server
            except Exception:
                log.seek(0)
                print(log.read()[-3000:], file=sys.stderr)
                raise
            finally:
                if server.poll() is None:
                    server.terminate()
                    try:
                        server.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait()


def yaw(msg):
    q = msg.pose.pose.orientation
    return math.atan2(2*(q.w*q.z + q.x*q.y), 1-2*(q.y*q.y + q.z*q.z))


def stamp(msg):
    t = msg.header.stamp
    return t.sec + t.nanosec * 1e-9


class MotionTest(Node):
    def __init__(self, server):
        super().__init__("four_steer_motion_test")
        self.server = server
        self.cmd = self.create_publisher(Twist, "/cmd_vel", 10)
        self.odom = None
        self.received = 0.0
        self.create_subscription(Odometry, "/odom", self.feedback, 1)

    def feedback(self, msg):
        if self.server is not None and msg.child_frame_id != "test_base_link":
            return
        if self.odom is None or stamp(msg) > stamp(self.odom):
            self.odom = msg
            self.received = time.monotonic()

    def connected(self):
        subs = self.get_subscriptions_info_by_topic("/cmd_vel")
        pubs = self.get_publishers_info_by_topic("/odom")
        senders = self.get_publishers_info_by_topic("/cmd_vel")
        return ([e.node_name for e in subs] == ["four_steer_amr_plugin"]
                and [e.node_name for e in pubs] == ["four_steer_amr_plugin"]
                and [e.node_name for e in senders] == [self.get_name()])

    def ready(self):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            rclpy.spin_once(self, timeout_sec=0.1)
            if self.server and self.server.poll() is not None:
                raise RuntimeError("gzserver exited before the plugin became ready")
            if self.connected() and self.odom and time.monotonic()-self.received < 0.5:
                return
        raise RuntimeError("expected one plugin on /cmd_vel and /odom, a fresh odometry sample, and no competing commander")

    def phase(self, vx, wz, seconds):
        msg = Twist()
        msg.linear.x, msg.angular.z = vx, wz
        first = self.odom
        end = stamp(first) + seconds
        deadline = time.monotonic() + max(8, 4*seconds)
        next_cmd = 0.0
        stopped_since = None
        while stamp(self.odom) < end:
            now = time.monotonic()
            if now >= deadline or now-self.received > 0.75:
                raise RuntimeError("odometry stalled or simulation timed out")
            if now >= next_cmd:
                if not self.connected():
                    raise RuntimeError("topic endpoints changed during test")
                self.cmd.publish(msg)
                next_cmd = now + 0.05
            rclpy.spin_once(self, timeout_sec=min(0.05, max(0.0, next_cmd-time.monotonic())))
            v = self.odom.twist.twist
            if math.hypot(v.linear.x, v.linear.y) < 0.025 and abs(v.angular.z) < 0.035:
                if stopped_since is None:
                    stopped_since = stamp(self.odom)
            else:
                stopped_since = None
        if vx == wz == 0 and (stopped_since is None or stamp(self.odom)-stopped_since < 0.3):
            raise RuntimeError("vehicle did not hold still for 0.3 s")
        a, b = first.pose.pose.position, self.odom.pose.pose.position
        heading = yaw(first)
        dx, dy = b.x-a.x, b.y-a.y
        angle = yaw(self.odom)-heading
        return (math.cos(heading)*dx + math.sin(heading)*dy,
                -math.sin(heading)*dx + math.cos(heading)*dy, math.atan2(math.sin(angle), math.cos(angle)))

    def run(self):
        self.ready()
        try:
            self.phase(0., 0., 1.0)
            for name, vx, wz in [("forward", 0.20, 0.), ("reverse", -0.20, 0.), ("turn left", 0., 0.25)]:
                dx, dy, angle = self.phase(vx, wz, 2.0)
                print(f"{name:10s} dx={dx:+.3f} m  dy={dy:+.3f} m  yaw={math.degrees(angle):+.1f} deg", flush=True)
                if vx:
                    passed = 0.12 < dx * math.copysign(1, vx) < 0.55 and abs(dy) < 0.12 and abs(angle) < 0.2
                else:
                    passed = 0.10 < angle < 0.75 and math.hypot(dx, dy) < 0.15
                if not passed:
                    raise RuntimeError(f"{name}: observed odometry outside motion tolerance")
                self.phase(0., 0., 1.5)
        finally:
            if self.connected():
                for _ in range(3):
                    self.cmd.publish(Twist())
                    time.sleep(0.05)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--existing", action="store_true", help="command an already running Gazebo plugin")
    options = parser.parse_args()
    context = nullcontext(None) if options.existing else gazebo()
    try:
        with context as server:
            rclpy.init(args=[], signal_handler_options=SignalHandlerOptions.NO)
            node = MotionTest(server)
            try:
                node.run()
            finally:
                node.destroy_node()
                rclpy.try_shutdown()
        print("PASS: forward, reverse, left rotation and stops")
        return 0
    except KeyboardInterrupt:
        return 130
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
