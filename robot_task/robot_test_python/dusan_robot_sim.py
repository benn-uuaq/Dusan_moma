#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dusan_robot_sim.py — 로봇 없이 dusan_test_ui.py 를 확인하기 위한 가짜 로봇

dusan_v1.task 와 같은 동작을 흉내낸다.
  * 29999 대시보드 (play/pause/stop, variable -get/-set)
  * 30001 상태 스트림 (robot.py 의 RobotDataConfig 포맷 그대로)
  * Modbus 서버 (범용 레지스터 256~383)

사용:
    python dusan_robot_sim.py                 # 29999 / 30001 / 5502
    python dusan_robot_sim.py --mb-port 502   # 관리자 권한 필요

그다음 dusan_test_ui.py 에서 IP 127.0.0.1, Modbus 포트를 맞춰 연결하면 된다.
"""

import argparse
import math
import socket
import socketserver
import struct
import threading
import time

import dusan_map as M
from robot import RobotDataConfig

CFG = RobotDataConfig()

# --------------------------------------------------------------- 패킷 조립 준비
FLAT_NAMES = list(CFG.names_pre)
for _ in range(6):
    FLAT_NAMES += list(CFG.names_joint)
FLAT_NAMES += list(CFG.names_post)
FMT_CHARS = [c for c in CFG.fmt if c != '>']
assert len(FLAT_NAMES) == len(FMT_CHARS)

_DEFAULT = {'d': 0.0, 'f': 0.0, '?': False}
NAME_IDX = {}
for _i, _n in enumerate(FLAT_NAMES):
    NAME_IDX.setdefault(_n, _i)


def build_packet(fields):
    vals = [_DEFAULT.get(c, 0) for c in FMT_CHARS]
    for k, v in fields.items():
        vals[NAME_IDX[k]] = v
    vals[NAME_IDX['total_message_len']] = struct.calcsize(CFG.fmt)
    vals[NAME_IDX['total_message_type']] = 16
    return struct.pack(CFG.fmt, *vals)


# =============================================================== 로봇 상태 모델
class FakeRobot:
    HOME = [0.33684, -0.00001, 0.31805, 3.14159, 0.0, -1.57079]
    SENSOR_X = 0.150          # 이 X 아래로 내려가면 원점 센서가 켜진다고 가정

    def __init__(self):
        self.lock = threading.Lock()
        self.pose = list(self.HOME)
        self.running = False
        self.paused = False
        self.regs = {a: 0 for a in range(256, 384)}
        self.vars = {
            "app_width": 500, "app_height": 500, "scan_h": 200, "overlap": 10,
            "param_src": 0, "sensor_di": 0, "seek_limit": 400, "stop_comp": 0.005,
            "zero_pose": list(self.HOME), "Home_pose": list(self.HOME),
            "Home_joint": [0.55498, -1.00739, -2.64993, -1.05507, 1.5708, 0.55497],
        }
        self._worker = None

    # ---- 레지스터
    def rget(self, a, signed=False):
        v = self.regs.get(a, 0)
        return M.to_signed(v) if signed else v

    def rset(self, a, v):
        self.regs[a] = M.to_unsigned(v)

    # ---- 대시보드 명령
    def play(self):
        with self.lock:
            if self.running:
                return "already running"
            self.running, self.paused = True, False
        self._worker = threading.Thread(target=self._task, daemon=True)
        self._worker.start()
        return "Starting task"

    def pause(self):
        self.paused = True
        return "Pausing task"

    def stop(self):
        self.running, self.paused = False, False
        return "Stopping task"

    # ---- 이동 (발행하면서 조금씩)
    def _hold(self):
        while self.paused and self.running:
            time.sleep(0.05)
        return self.running

    def _move_axis(self, axis, target, speed, publish):
        dt = 0.02
        while self._hold():
            cur = self.pose[axis]
            d = target - cur
            if abs(d) <= speed * dt:
                self.pose[axis] = target
                if publish:
                    self._pub()
                return True
            self.pose[axis] = cur + math.copysign(speed * dt, d)
            if publish:
                self._pub()
            time.sleep(dt)
        return False

    def _w6(self, base, pose):
        """길이 -> 0.1mm, 회전 -> mRad (로봇 384~389 와 같은 단위)."""
        v = [pose[0] * 10000, pose[1] * 10000, pose[2] * 10000,
             pose[3] * 1000, pose[4] * 1000, pose[5] * 1000]
        for i, x in enumerate(v):
            self.rset(base + i, max(-32000, min(32000, round(x))))

    def _pub(self):
        self._w6(M.R_TCP, self.pose)
        r = M.pose_trans(M.pose_inv(self.vars["zero_pose"]), self.pose)
        self._w6(M.R_REL, r)
        self.rset(M.R_ALIVE, (self.rget(M.R_ALIVE) + 1) % 30001)

    # ---- 태스크 본체 (dusan_v1 과 같은 순서)
    def _task(self):
        try:
            # init
            if self.rget(M.R_IN_SRC, True) == 1:
                for key, addr, *_ in M.INPUT_FIELDS:
                    self.vars[key] = self.rget(addr, True)
            p = M.plan(self.vars["app_width"], self.vars["app_height"],
                       self.vars["scan_h"], self.vars["overlap"])
            rows, pitch = p["rows"], p["pitch"]
            for a, v in ((M.R_STATE, 0), (M.R_ROW, 0), (M.R_ROWS, rows), (M.R_ALIVE, 0),
                         (M.R_PATH, 0), (M.R_PROG, 0), (M.R_ZOK, 0), (M.R_DONE, 0),
                         (M.R_PITCH, pitch)):
                self.rset(a, v)

            # move_home
            if not self._hold():
                return
            self.pose = list(self.HOME)
            time.sleep(0.4)

            # find_zero : X- 로 이동, SENSOR_X 아래로 가면 센서 ON
            self.rset(M.R_STATE, 1)
            if not self._move_axis(0, self.SENSOR_X, 0.05, False):
                return
            self.vars["zero_pose"] = list(self.pose)
            self._w6(M.R_ZERO, self.pose)
            for i in range(6):
                self.rset(M.R_REL + i, 0)
            self.rset(M.R_ZOK, 1)
            self.rset(M.R_STATE, 4)

            # 스캔
            zy, zz = self.pose[1], self.pose[2]
            w = self.vars["app_width"] * 0.001
            path_len, sign = 0.0, 1
            for i in range(rows):
                tgt = zy - w if sign > 0 else zy
                if not self._move_axis(1, tgt, 0.1, True):
                    return
                sign = -sign
                path_len += self.vars["app_width"]
                self.rset(M.R_ROW, i + 1)
                self.rset(M.R_PATH, round(path_len * 0.1))
                self.rset(M.R_PROG, round((i + 1) * 100.0 / rows))
                if i < rows - 1:
                    if not self._move_axis(2, zz + (i + 1) * pitch * 0.001, 0.1, True):
                        return
                    path_len += pitch
                    self.rset(M.R_PATH, round(path_len * 0.1))

            # finish
            self._move_axis(0, self.HOME[0], 0.25, True)
            self.rset(M.R_STATE, 5)
            self.rset(M.R_PROG, 100)
            self.rset(M.R_DONE, 1)
        finally:
            self.running = False

    # ---- 30001 패킷
    def packet(self):
        di = 1 if self.pose[0] <= self.SENSOR_X + 1e-9 else 0
        return build_packet({
            'tcp_x': self.pose[0], 'tcp_y': self.pose[1], 'tcp_z': self.pose[2],
            'rot_x': self.pose[3], 'rot_y': self.pose[4], 'rot_z': self.pose[5],
            'is_robot_power_on': True,
            'is_task_running': bool(self.running and not self.paused),
            'is_task_paused': bool(self.paused),
            'robot_mode': 7 if self.running else 5,
            'digital_input_bits': di,
        })


ROBOT = FakeRobot()


# =============================================================== 29999 대시보드
def fmt_var(v):
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(f"{float(x):.5f}" for x in v) + "]"
    return str(v)


class DashHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"Connected to Fake Elite Robot\n")
        for raw in self.rfile:
            cmd = raw.decode("utf-8", "ignore").strip()
            if not cmd:
                continue
            self.wfile.write((self.dispatch(cmd) + "\n").encode())

    def dispatch(self, cmd):
        c = cmd.lower()
        if c == "play":
            return ROBOT.play()
        if c == "pause":
            return ROBOT.pause()
        if c == "stop":
            return ROBOT.stop()
        if c == "robotmode":
            return "Robotmode: RUNNING" if ROBOT.running else "Robotmode: IDLE"
        if c == "task -s":
            return "Task is running" if ROBOT.running else "Task is stopped"
        if c.startswith("remotecontrol") or c.startswith("robotcontrol") or c == "brakerelease":
            return "ok"
        if c.startswith("speed"):
            return "Speed: 100"
        if cmd.startswith("variable -get"):
            name = cmd.split()[-1]
            if name in ROBOT.vars:
                return fmt_var(ROBOT.vars[name])
            return "variable not found"
        if cmd.startswith("variable -set"):
            parts = cmd.split(None, 3)
            if len(parts) == 4:
                name, val = parts[2], parts[3]
                try:
                    ROBOT.vars[name] = int(val)
                except ValueError:
                    try:
                        ROBOT.vars[name] = float(val)
                    except ValueError:
                        ROBOT.vars[name] = val
                return "ok"
            return "bad args"
        return "unknown command"


class ThreadedTCP(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


# =============================================================== 30001 스트림
def stream_server(port, hz=50):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(5)

    def serve(conn):
        try:
            while True:
                conn.sendall(ROBOT.packet())
                time.sleep(1.0 / hz)
        except Exception:
            pass
        finally:
            conn.close()

    while True:
        conn, _ = srv.accept()
        threading.Thread(target=serve, args=(conn,), daemon=True).start()


# =============================================================== Modbus 서버
def modbus_server(port):
    from pyModbusTCP.server import ModbusServer
    srv = ModbusServer(host="0.0.0.0", port=port, no_block=True)
    srv.start()
    bank = getattr(srv, "data_bank", None)

    def read(addr, n):
        if bank is not None:
            return bank.get_holding_registers(addr, n)
        from pyModbusTCP.server import DataBank
        return DataBank.get_words(addr, n)

    def write(addr, words):
        if bank is not None:
            bank.set_holding_registers(addr, words)
        else:
            from pyModbusTCP.server import DataBank
            DataBank.set_words(addr, words)

    write(256, [0] * 128)
    prev = list(read(256, 128) or [0] * 128)
    while True:
        # 외부가 쓴 값 -> 로봇 상태로
        cur = read(256, 128) or prev
        for i, (a, b) in enumerate(zip(prev, cur)):
            if a != b:
                ROBOT.regs[256 + i] = b
        # 로봇이 쓴 값 -> 서버로
        out = [ROBOT.regs.get(256 + i, 0) for i in range(128)]
        write(256, out)
        prev = out
        time.sleep(0.02)


# =============================================================== main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dash-port", type=int, default=29999)
    ap.add_argument("--stream-port", type=int, default=30001)
    ap.add_argument("--mb-port", type=int, default=5502)
    a = ap.parse_args()

    threading.Thread(target=lambda: ThreadedTCP(("0.0.0.0", a.dash_port), DashHandler).serve_forever(),
                     daemon=True).start()
    threading.Thread(target=stream_server, args=(a.stream_port,), daemon=True).start()
    threading.Thread(target=modbus_server, args=(a.mb_port,), daemon=True).start()
    print(f"가짜 로봇 기동 — 대시보드 {a.dash_port} / 스트림 {a.stream_port} / Modbus {a.mb_port}")
    print("Ctrl+C 로 종료")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
