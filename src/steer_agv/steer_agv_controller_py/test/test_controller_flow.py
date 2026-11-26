"""Exercise ROS services, actual TCP framing and PLC feedback on loopback."""
import csv
import socketserver
import struct
import threading
from time import monotonic

import pytest
import rclpy
from nav_msgs.msg import Odometry
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.time import Time
from tf2_ros import Buffer, TransformListener
from vehicle_interfaces.msg import RobotStatus
from vehicle_interfaces.srv import ManualCommand, MeasureLap, ResetOdometry, RobotControl, SetJob, SetMvLift

from steer_agv_controller_py.agv_controller_node import AgvControllerNode


class MockPlc(socketserver.StreamRequestHandler):
    def handle(self):
        registers = self.server.registers
        distance = 0.0
        last = monotonic()
        while True:
            data = bytearray()
            while not data.endswith(b"\r"):
                ch = self.rfile.read(1)
                if not ch:
                    return
                data.extend(ch)
            op, device, *args = data.decode().strip().split()
            address = int(device.split(".")[0][2:])
            now = monotonic()
            speed = registers.get(106, 0)
            if speed > 32767:
                speed -= 65536
            if registers.get(103, 0) != 1:
                speed = 0
            distance += speed * (now-last)
            last = now
            if op == "RDS":
                words = [0]*int(args[0])
                if address == 0:
                    state = registers.get(103, 0)
                    words[:3] = [int(now*1000) % 65536, self.server.mode, state]
                    words[25] = int(state == 2)
                    words[32] = speed & 0xffff
                    words[26:28] = struct.unpack("<HH", struct.pack("<i", registers.get(108, 0)))
                    for i in range(4):
                        words[5+2*i:7+2*i] = struct.unpack("<HH", struct.pack("<i", round(distance)))
                        words[13+2*i:15+2*i] = struct.unpack("<HH", struct.pack("<i", speed))
                else:
                    words[0] = self.server.obstacle
                answer = " ".join(map(str, words))
            else:
                values = list(map(int, args if op == "WR" else args[1:]))
                if address == 104 and self.server.reject_reset:
                    self.server.reset_failures += 1
                    self.wfile.write(b"E1\r\n")
                    continue
                for i, value in enumerate(values):
                    registers[address+i] = value
                    self.server.writes.append((address+i, value))
                answer = "OK"
            self.wfile.write((answer+"\r\n").encode())


def test_reverse_job_hold_lift_and_repeated_reset_via_ros(monkeypatch, tmp_path):
    monkeypatch.setattr("steer_agv_controller_py.plc_client.read_cpu_temperature_word", lambda: 527)
    monkeypatch.setenv("ROS_LOCALHOST_ONLY", "1")
    monkeypatch.setenv("ROS_DOMAIN_ID", "163")
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), MockPlc)
    server.registers = {}
    server.writes = []
    server.mode, server.obstacle = 2, 0
    server.reject_reset, server.reset_failures = False, 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    rclpy.init(args=["--ros-args", "-p", "plc_enabled:=true", "-p", "plc_writes_enabled:=true",
                     "-p", "plc_host:=127.0.0.1", "-p", f"plc_port:={server.server_address[1]}",
                     "-p", f"debug_log_dir:={tmp_path}"])
    controller = AgvControllerNode()
    peer = Node("steer_agv_test_peer")
    executor = SingleThreadedExecutor()
    executor.add_node(controller)
    executor.add_node(peer)
    tf_buffer = Buffer()
    listener = TransformListener(tf_buffer, peer)
    statuses, odom = [], []
    peer.create_subscription(RobotStatus, "/vehicle/robot_status", statuses.append, 10)
    peer.create_subscription(Odometry, "/odom", odom.append, 10)

    def until(check, timeout=6):
        deadline = monotonic() + timeout
        while not check():
            assert monotonic() < deadline, (statuses[-1:] or server.registers)
            executor.spin_once(timeout_sec=0.02)

    def call(kind, name, **fields):
        client = peer.create_client(kind, "/vehicle/"+name)
        until(client.service_is_ready)
        future = client.call_async(kind.Request(**fields))
        until(future.done)
        return future.result()

    try:
        until(lambda: statuses and statuses[-1].state == "STOP" and odom)
        assert call(ResetOdometry, "reset_odometry", x=1.0).success
        assert not call(MeasureLap, "measure_lap", mode="LAP").success
        assert call(SetJob, "set_job", job_id="reverse", set_dist=-0.04,
                    set_speed=0.1, standoff=1.0, radius=1.25).success
        until(lambda: statuses[-1].job_id == "reverse")
        assert controller.jobs.circle_center is None
        assert not tf_buffer.can_transform("odom", "path_circle_center", Time())
        assert server.registers[103] == 0
        assert call(RobotControl, "robot_control", state="RUNNING").success
        until(lambda: tf_buffer.can_transform("odom", "path_circle_center", Time()))
        center = tf_buffer.lookup_transform("odom", "path_circle_center", Time()).transform
        assert (center.translation.x, center.translation.y, center.translation.z) == pytest.approx((1., 2.85, 0.))
        assert center.rotation.w == 1.
        until(lambda: statuses[-1].state == "HOLD")
        assert abs(statuses[-1].mv_dist + 0.04) < 0.006
        assert abs(odom[-1].pose.pose.position.x - 0.96) < 0.006
        assert tf_buffer.lookup_transform("odom", "path_circle_center", Time()).transform == center
        assert server.registers[107] == 65536-40
        assert call(SetMvLift, "lift_control", job_id="lift", set_height=0.15, set_speed=0.05).success
        until(lambda: server.registers.get(108) == 150)
        assert server.registers[112] == 50
        until(lambda: statuses[-1].lift_h == 0.15)
        for _ in range(2):
            assert call(RobotControl, "robot_control", reset=1).success
        assert [v for a,v in server.writes if a == 104] == [1, 1]
        assert not any(a in (102, 105) for a,v in server.writes)
        assert server.registers[101] == 527
        for invalid in ("8", "ERROR"):
            assert not call(RobotControl, "robot_control", state=invalid).success
        assert (103, 8) not in server.writes
        assert call(RobotControl, "robot_control", state="FINISH").success
        until(lambda: statuses[-1].state == "FINISH" and statuses[-1].hold == "RELEASE")
        assert call(SetJob, "set_job", job_id="after_finish", set_dist=0.1,
                    set_speed=0.1, standoff=1., radius=1.).success
        until(lambda: statuses[-1].state == "STOP" and statuses[-1].job_id == "after_finish")
        assert call(RobotControl, "robot_control", state="FINISH").success
        until(lambda: statuses[-1].state == "FINISH")
        assert call(RobotControl, "robot_control", state="STOP", job_cancel=1).success
        until(lambda: statuses[-1].state == "STOP")
        assert call(SetJob, "set_job", job_id="obstacle", set_dist=0.3,
                    set_speed=0.15, standoff=1.0, radius=1.25).success
        assert controller.jobs.circle_center is None
        start_x = controller.odom.pose[0]
        assert call(RobotControl, "robot_control", state="RUNNING").success
        until(lambda: statuses[-1].mv_dist > 0.01)
        def published_center():
            return tf_buffer.lookup_transform("odom", "path_circle_center", Time()).transform

        until(lambda: abs(published_center().translation.x - start_x) < 1e-6)
        second_center = published_center()
        assert second_center != center
        assert call(RobotControl, "robot_control", state="PAUSE").success
        until(lambda: statuses[-1].state == "PAUSE")
        assert controller.odom.pose[0] > start_x + 0.01
        assert call(RobotControl, "robot_control", state="RUNNING").success
        until(lambda: statuses[-1].state == "RUNNING" and 0 < server.registers[106] < 32768)
        assert controller.jobs.circle_center == (second_center.translation.x, second_center.translation.y)
        assert published_center() == second_center
        server.obstacle = 3
        until(lambda: server.registers[103] == 0 and server.registers[106] == 0 and statuses[-1].obstacle_stop)
        assert not call(RobotControl, "robot_control", state="RUNNING").success
        server.obstacle = 0
        until(lambda: not statuses[-1].obstacle_stop)
        assert server.registers[103] == 0
        server.mode = 1
        until(lambda: controller.sample().fast.mode == 1)
        assert call(ManualCommand, "manual_command", cmd_mv_fwd=True).success
        until(lambda: server.registers[120] == 1)
        until(lambda: server.registers[120] == 0)
        assert not call(SetMvLift, "lift_control", job_id="unsecured", set_height=0.2).success
        server.reject_reset = True
        assert not call(RobotControl, "robot_control", reset=1).success
        until(lambda: controller.sample().connected and controller.sample().generation > 1)
        assert server.reset_failures == 1
    finally:
        controller.close()
        listener.unregister()
        executor.shutdown()
        peer.destroy_node()
        controller.destroy_node()
        rclpy.shutdown()
        server.shutdown()
        server.server_close()
        thread.join()

    rcs = "".join(p.read_text() for p in sorted(tmp_path.glob("rcs_*.txt")))
    plc = "".join(p.read_text() for p in sorted(tmp_path.glob("plc_*.txt")))
    assert "SERVICE RX /vehicle/set_job" in rcs and "SERVICE TX /vehicle/set_job" in rcs
    assert "CONNECTED " in plc and "TX b'RDS " in plc and "RX b'OK" in plc
    assert "TX b'WRS DM100.U 2 1 527" in plc

    with (tmp_path / "drive_profile.csv").open(newline="") as stream:
        reverse = [r for r in csv.DictReader(stream) if r["job_id"] == "reverse"]
    assert any(int(r["requested_DM106_mmps"]) < 0 and float(r["FL_speed_mps"]) < 0 for r in reverse)
    assert any(r["encoder_path_mps"] and float(r["encoder_path_mps"]) < 0 for r in reverse)
    assert any(int(r["feedback_DM032_mmps"]) < 0
               and int(r["DM032_raw"]) == 65536 + int(r["feedback_DM032_mmps"])
               and r["feedback_minus_last_tx_mmps"] == "0" for r in reverse)
    assert any(r["last_tx_DM106_ack"] == "OK" and float(r["RR_distance_m"]) < 0 for r in reverse)
    assert any(r["requested_DM103"] == "2" and abs(float(r["remaining_m"])) <= 0.005 for r in reverse)
    assert reverse[-1]["phase"] == "end"
