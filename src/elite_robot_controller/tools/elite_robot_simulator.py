#!/usr/bin/env python3
r"""Elite CS612 없이 elite_robot_controller를 시험하기 위한 가짜 로봇.

29999(Dashboard), 30001(Primary), Modbus TCP 세 채널을 흉내 낸다.
레지스터 주소는 `config/modbus_registers.json`을 그대로 읽으므로 노드와
시뮬레이터가 서로 다른 주소를 쓰는 일이 없다.

## 실행

```bash
python3 tools/elite_robot_simulator.py
```

기본으로 127.0.0.1:29999 / 30001 / 5502 에서 대기한다. 502는 리눅스에서
권한이 필요해 기본값을 5502로 뒀다. 노드는 다음처럼 띄운다.

```bash
ros2 run elite_robot_controller robot_control_node --ros-args \\
    -p robot_ip:=127.0.0.1 -p modbus_port:=5502
```

## 흉내 내는 동작

- **연결**: 세 포트 모두 접속을 받아준다. 여러 번 연결·해제해도 된다.
- **Dashboard 명령**: robotMode/status/robotControl -on|off/brakeRelease/
  play/pause/stop 에 짧은 텍스트로 응답한다. stop 은 조그 속도도 0으로 만든다.
- **조그**: 30001 로 들어오는 `speedj(...)`/`speedl(...)` 스크립트를 읽어
  해당 속도로 관절(73~78)과 TCP(384~389) 레지스터를 계속 갱신한다.
  `stop` 명령이나 연결 해제로 멈춘다. 목표 속도까지 도달하지 않으면
  기존 값을 계속 쓰지 않도록 처리한다.
- **홈 이동**: `movej(...)` 스크립트를 읽으면 짧은 지연 후 관절 레지스터를
  목표값으로 옮긴다.
- **쓰기 레지스터**: UI가 306(작업 속도), 307(속도 비율), 308(pose_src),
  310~321(기준 위치)에 쓴 값을 그대로 저장하고 돌려준다.
- **태스크 진행**: `play` 를 받으면 지금 태스크와 같은 순서로 레지스터를
  채운다 — 차량 고정 대기(309) → 3점 측정(300~305 · 330~355) → 원점 대기
  (267 허가) → 적심 → ㄹ자 스캔(구간 신호 277) → 완료. 홈 플래그(276)와
  태스크 상태(500)도 같이 움직인다.
- **디지털 IO**: 레지스터 2(표준 출력)에 쓴 비트를 그대로 들고 있는다.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import socket
import struct
import sys
import threading
import time

# 소스 트리에서 바로 실행해도 패키지를 찾도록 경로를 보정한다.
_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)

from elite_robot_controller import register_map  # noqa: E402


def to_unsigned16(value: int) -> int:
    return value & 0xFFFF


def to_signed16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value > 0x7FFF else value


class RegisterBank:
    """레지스터 값을 보관하고 잠금으로 여러 스레드의 접근을 보호한다."""

    def __init__(self, registers: register_map.RegisterMap):
        self.registers = registers
        self._lock = threading.Lock()
        self._values: dict[int, int] = {}
        self._seed_defaults()

    def _seed_defaults(self) -> None:
        defaults = {
            "robot_mode": [7],                          # RUNNING
            "control_method": [2],                       # 원격 제어
            "operation_mode": [0],                        # 자동
            "joint_position": [207, -1466, -1875, -1371, 1570, 207],
            "tcp_absolute": [6368, -473, 5810, 3141, 0, -1570],
            "tcp_zero_relative": [0, 0, 1000, 0, 0, 0],
        }
        for name, values in defaults.items():
            entry = self.registers.read_entry(name)
            if not entry.available:
                continue
            for offset, value in enumerate(values):
                self._values[entry.address + offset] = value
        for name in self.registers.write.keys():
            entry = self.registers.write_entry(name)
            for offset in range(entry.count):
                self._values.setdefault(entry.address + offset, 0)

    def read(self, address: int, count: int) -> list[int]:
        with self._lock:
            return [self._values.get(address + i, 0) for i in range(count)]

    def write(self, address: int, value: int) -> None:
        with self._lock:
            self._values[address] = value

    def write_many(self, address: int, values: list[int]) -> None:
        with self._lock:
            for offset, value in enumerate(values):
                self._values[address + offset] = value

    def entry_by_address(self, target: str) -> register_map.RegisterEntry:
        return self.registers.read_entry(target)


class ModbusServer(threading.Thread):
    """FC3(읽기)/FC6·FC16(쓰기)만 지원하는 최소 Modbus TCP 서버."""

    def __init__(self, bank: RegisterBank, host: str, port: int):
        super().__init__(daemon=True)
        self.bank = bank
        self.host, self.port = host, port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(4)

    def run(self) -> None:
        print(f"[Modbus] {self.host}:{self.port} 에서 대기")
        while True:
            conn, addr = self._sock.accept()
            threading.Thread(target=self._handle, args=(conn, addr), daemon=True).start()

    def _handle(self, conn: socket.socket, addr) -> None:
        with conn:
            while True:
                header = self._recv_exact(conn, 7)
                if header is None:
                    return
                tx_id, proto_id, length, unit_id = struct.unpack(">HHHB", header)
                pdu = self._recv_exact(conn, length - 1)
                if pdu is None:
                    return
                response_pdu = self._handle_pdu(pdu)
                resp_header = struct.pack(">HHHB", tx_id, proto_id, len(response_pdu) + 1, unit_id)
                conn.sendall(resp_header + response_pdu)

    @staticmethod
    def _recv_exact(conn: socket.socket, size: int) -> bytes | None:
        buf = b""
        while len(buf) < size:
            chunk = conn.recv(size - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def _handle_pdu(self, pdu: bytes) -> bytes:
        func = pdu[0]
        if func == 0x03:  # Read Holding Registers
            address, count = struct.unpack(">HH", pdu[1:5])
            values = [to_unsigned16(v) for v in self.bank.read(address, count)]
            payload = struct.pack(">B", count * 2) + b"".join(struct.pack(">H", v) for v in values)
            return struct.pack(">B", 0x03) + payload
        if func == 0x06:  # Write Single Register
            address, value = struct.unpack(">HH", pdu[1:5])
            self.bank.write(address, to_signed16(value))
            return pdu[:5]
        if func == 0x10:  # Write Multiple Registers
            address, count, byte_count = struct.unpack(">HHB", pdu[1:6])
            raw = pdu[6:6 + byte_count]
            values = [to_signed16(v) for v, in struct.iter_unpack(">H", raw)]
            self.bank.write_many(address, values)
            return struct.pack(">BHH", 0x10, address, count)
        # 지원하지 않는 기능 코드: 예외 응답(0x01 illegal function)
        return struct.pack(">BB", func | 0x80, 0x01)


class ScanTaskSim(threading.Thread):
    """`play` 를 받으면 지금 로봇 태스크(dusan_v4/v5)의 한 셀을 흉내 낸다.

    실제 태스크가 레지스터에 쓰는 것을 같은 자리·같은 순서로 채운다. 이게
    맞지 않으면 운영 UI 는 첫 셀에서 더 나아가지 못한다.

      290 진행 상태 : 0 대기 · 14 차량 고정 대기 · 2 3점 측정 · 3 원점 복귀 ·
                      7 원점 대기(스캔 허가 기다림) · 8 적심 · 6 스캔 · 5 완료
      277 스캔 구간 : 0 없음 · 1 전진 · 2 줄 바꿈(상승) · 3 후진  (TPAC DO 신호)
      276 홈 위치   : 홈에 있으면 1, 벗어나면 0
      267 스캔 허가 : RCS 가 1 을 쓰면 원점 대기가 풀린다(통과하며 0 으로 지운다)
      309 차량 고정 : 1 이어야 움직인다. 아니면 290 = 14 로 서서 기다린다
      500 태스크    : 1 실행 중 · 3 중지됨
      300~305 / 330~355 : 3점 측정 결과와 접촉 자세(캘리브레이션이 읽는다)

    ㄹ자 경로를 실제로 그리지는 않고 단계·줄 수·구간 신호만 흉내 낸다.
    접촉점은 입력한 반지름(260 + 261)에 `wall_error_mm` 만큼 어긋난 원 위에
    올려 둔다 — 캘리브레이션이 그 차이를 잡아내는지 볼 수 있다.
    """

    # 레지스터 290~299. 로봇 태스크의 dus_*.script 와 같은 뜻이다.
    STATE, ROW_IDX, ROWS, ALIVE = 290, 291, 292, 293
    ZERO_OK, FINISHED, PITCH, PROGRESS = 294, 295, 296, 298
    PROBE_ERROR = 299
    # 작업 영역(256~264). 태스크가 읽어 ㄹ자 줄 수와 호를 계산한다.
    APP_WIDTH, APP_HEIGHT, SCAN_H, OVERLAP = 256, 257, 258, 259
    RADIUS, THICKNESS, PARAM_SRC = 260, 261, 266
    # 오가는 신호들.
    SCAN_GO, SEGMENT, HOME_FLAG, VEHICLE_READY, TASK_STATE = 267, 277, 276, 309, 500
    ARC_POS, ARC_TOTAL = 286, 287
    # 3점 측정 결과.
    PROBE_CX, PROBE_LX, PROBE_RX, PRESS_OFF, N_PROBE, N_HIT = 300, 301, 302, 303, 304, 305
    POSE_CENTER, POSE_RIGHT, POSE_EOAT, POSE_ZERO = 330, 340, 347, 350

    STEP_SECONDS = 0.35
    #: 차량 고정(309)을 기다리는 한도 [s]. 넘으면 태스크가 299 = 8 로 멈춘다.
    VEHICLE_WAIT_S = 15.0
    #: 실제 벽이 입력값보다 이만큼 어긋나 있다고 친다 [mm].
    WALL_ERROR_MM = 0.3
    #: ㄹ자 줄 수 상한. 순회를 확인하는 게 목적이라 줄을 다 그릴 필요는 없다.
    MAX_ROWS = 6

    def __init__(self, bank: RegisterBank, wall_error_mm: float = WALL_ERROR_MM,
                 max_rows: int = MAX_ROWS):
        super().__init__(daemon=True)
        self.bank = bank
        self.wall_error_mm = float(wall_error_mm)
        self.max_rows = max(1, int(max_rows))
        self._start_requested = threading.Event()
        self._abort = threading.Event()
        self._alive_count = 0
        self._set(self.HOME_FLAG, 1)
        self._set(self.TASK_STATE, 3)

    def play(self) -> None:
        """제로점에서 프로그램을 다시 재생한다."""
        self._abort.set()          # 돌고 있으면 먼저 접는다
        self._start_requested.set()

    def stop(self) -> None:
        self._abort.set()
        self._start_requested.clear()
        self._set(self.STATE, 0)
        self._set(self.FINISHED, 0)
        self._set(self.SEGMENT, 0)
        self._set(self.TASK_STATE, 3)
        self._set(self.HOME_FLAG, 1)

    def _set(self, address: int, value: int) -> None:
        self.bank.write(address, int(value))

    def _get(self, address: int) -> int:
        return self.bank.read(address, 1)[0]

    def _tick(self, seconds: float) -> bool:
        """지정한 시간만큼 대기한다. 중단 요청이 오면 False."""
        end = time.time() + seconds
        while time.time() < end:
            if self._abort.is_set():
                return False
            self._alive_count = (self._alive_count + 1) % 30000
            self._set(self.ALIVE, self._alive_count)
            time.sleep(0.05)
        return True

    def _wait_for(self, address: int, value: int, limit_s: float) -> bool:
        """레지스터가 원하는 값이 될 때까지 기다린다. 중단·시간 초과면 False."""
        end = time.time() + limit_s
        while time.time() < end:
            if self._abort.is_set():
                return False
            if self._get(address) == value:
                return True
            self._alive_count = (self._alive_count + 1) % 30000
            self._set(self.ALIVE, self._alive_count)
            time.sleep(0.05)
        return False

    def _planned_rows(self) -> int:
        """작업 영역으로 ㄹ자 줄 수를 센다. 로봇 태스크와 같은 규칙이다."""
        height = self.bank.read(self.APP_HEIGHT, 1)[0]
        scan_h = (self.bank.read(self.SCAN_H, 1)[0] or 1500) / 10.0
        overlap = self.bank.read(self.OVERLAP, 1)[0]
        pitch = max(scan_h - overlap, 1)
        rows = 1
        while (rows - 1) * pitch + scan_h < height:
            rows += 1
            if rows > 200:      # 값이 이상해도 무한 루프에 빠지지 않는다
                break
        return min(rows, self.max_rows)

    def run(self) -> None:
        while True:
            self._start_requested.wait()
            self._start_requested.clear()
            self._abort.clear()
            self._run_once()

    # ---- 단계별 ----------------------------------------------------------
    def _fail(self, code: int, why: str) -> None:
        self._set(self.PROBE_ERROR, code)
        self._set(self.STATE, 9)
        self._set(self.SEGMENT, 0)
        print(f"[Task] 멈춤 — {why} (299 = {code})")

    def _wait_for_vehicle(self) -> bool:
        """차량 고정 확인(309)을 기다린다. 실제 태스크와 같은 인터락이다."""
        if self._get(self.PARAM_SRC) != 1 or self._get(self.VEHICLE_READY) == 1:
            return True
        self._set(self.STATE, 14)
        if self._wait_for(self.VEHICLE_READY, 1, self.VEHICLE_WAIT_S):
            return True
        if self._abort.is_set():
            return False
        self._fail(8, "차량 고정 확인(309)이 오지 않았습니다")
        return False

    def _measure_wall(self) -> None:
        """3점 측정 결과를 레지스터에 남긴다(300~305 · 330~355).

        입력 반지름 + 두께 + wall_error_mm 인 원 위에 세 점을 올린다.
        단위는 실제 태스크와 같은 0.1 mm 다.
        """
        radius_mm = (self._get(self.RADIUS) + self._get(self.THICKNESS)) / 10.0
        radius_mm = (radius_mm or 845.0) + self.wall_error_mm
        arc_mm = self._get(self.APP_WIDTH) or 700
        half = min(arc_mm / (2 * radius_mm), 0.6) if radius_mm else 0.3
        for base, angle in ((self.POSE_CENTER, 0.0), (self.POSE_RIGHT, half),
                            (self.POSE_ZERO, -half)):
            x = radius_mm * math.sin(angle)
            y = radius_mm * math.cos(angle)
            self.bank.write_many(base, [round(x * 10), round(y * 10), 0, 0, 0, 0])
        # 프로브 중심 오프셋(347~349)은 0 — 시뮬레이터는 플랜지가 곧 프로브다.
        self.bank.write_many(self.POSE_EOAT, [0, 0, 0])
        self._set(self.PROBE_CX, round(0.0))
        self._set(self.PROBE_LX, round(-radius_mm * math.sin(half) * 10))
        self._set(self.PROBE_RX, round(radius_mm * math.sin(half) * 10))
        self._set(self.PRESS_OFF, 50)       # 5 mm 더 밀어 벽을 찾았다
        self._set(self.N_PROBE, 3)
        self._set(self.N_HIT, 3)

    def _scan_rows(self, rows: int) -> bool:
        """ㄹ자 스캔. 줄마다 구간 신호(277)를 실제 순서대로 낸다."""
        arc_mm = self._get(self.APP_WIDTH) or 700
        self._set(self.STATE, 6)
        for row in range(rows):
            self._set(self.ROW_IDX, row)
            forward = row % 2 == 0
            self._set(self.SEGMENT, 1 if forward else 3)
            if not self._tick(self.STEP_SECONDS):
                return False
            self._set(self.ARC_POS, round(arc_mm * 10))
            self._set(self.ARC_TOTAL, round(arc_mm * (row + 1)))
            self._set(self.SEGMENT, 0)          # 줄 끝 — 스캔 동결
            self._set(self.PROGRESS, int((row + 1) / rows * 100))
            if row + 1 < rows:
                self._set(self.SEGMENT, 2)      # 줄 바꿈(상승)
                if not self._tick(self.STEP_SECONDS / 2):
                    return False
        self._set(self.SEGMENT, 0)
        return True

    def _run_once(self) -> None:
        rows = self._planned_rows()
        pitch = max(
            self.bank.read(self.SCAN_H, 1)[0] // 10 - self.bank.read(self.OVERLAP, 1)[0], 1
        )
        self._set(self.TASK_STATE, 1)
        self._set(self.PROBE_ERROR, 0)
        self._set(self.FINISHED, 0)
        self._set(self.ROWS, rows)
        self._set(self.PITCH, pitch)
        self._set(self.PROGRESS, 0)
        self._set(self.ZERO_OK, 0)
        self._set(self.SEGMENT, 0)
        self._set(self.STATE, 0)

        if not self._wait_for_vehicle():
            self._set(self.TASK_STATE, 3)
            return
        self._set(self.HOME_FLAG, 0)            # 홈을 벗어난다

        # 3점 측정 → 원점 복귀 (290 = 2, 3)
        self._set(self.STATE, 2)
        if not self._tick(self.STEP_SECONDS):
            return
        self._measure_wall()
        self._set(self.STATE, 3)
        if not self._tick(self.STEP_SECONDS):
            return
        self._set(self.ZERO_OK, 1)

        # 원점 대기 (290 = 7). RCS 가 허가(267)를 줄 때까지 선다.
        self._set(self.STATE, 7)
        if not self._wait_for(self.SCAN_GO, 1, 600.0):
            return
        self._set(self.SCAN_GO, 0)              # 통과하며 지운다

        # 적심 (290 = 8) → 스캔 (290 = 6)
        self._set(self.STATE, 8)
        if not self._tick(self.STEP_SECONDS):
            return
        if not self._scan_rows(rows):
            return

        # 피니시 (290 = 5). 운영 UI 는 이 값으로 셀 완료를 판정한다.
        self._set(self.STATE, 5)
        self._set(self.FINISHED, 1)
        self._set(self.PROGRESS, 100)
        self._set(self.HOME_FLAG, 1)            # 홈으로 돌아갔다
        self._set(self.TASK_STATE, 3)           # 태스크 종료
        print(f"[Task] 셀 스캔 완료 ({rows} 행)")


class DashboardServer(threading.Thread):
    """29999 텍스트 명령 서버."""

    def __init__(self, bank: RegisterBank, motion: "MotionSim",
                 task: "ScanTaskSim", host: str, port: int):
        super().__init__(daemon=True)
        self.bank, self.motion, self.task = bank, motion, task
        self.host, self.port = host, port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(4)

    def run(self) -> None:
        print(f"[Dashboard 29999] {self.host}:{self.port} 에서 대기")
        while True:
            conn, addr = self._sock.accept()
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            try:
                conn.sendall(b"Connected: Elite Robot Dashboard Simulator\n")
                buf = b""
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        self._respond(conn, line.decode("utf-8", "ignore").strip())
            except (ConnectionError, OSError):
                return

    def _respond(self, conn: socket.socket, command: str) -> None:
        replies = {
            "robotMode": "Robotmode: RUNNING",
            "status": "Status: RUNNING",
            "robotControl -on": "Powering on",
            "robotControl -off": "Powering off",
            "brakeRelease": "Brake releasing",
            "pause": "Pausing program",
        }
        if command == "play":
            # 실제 로봇처럼 제로점에서 태스크를 처음부터 다시 돌린다.
            self.task.play()
            reply = "Starting program"
        elif command == "stop":
            self.motion.stop_all()
            self.task.stop()
            reply = "Stopping program"
        else:
            reply = replies.get(command, f"ok: {command}")
        print(f"[Dashboard] {command!r} -> {reply!r}")
        conn.sendall((reply + "\n").encode("utf-8"))


class MotionSim:
    """speedj/speedl 로 받은 속도를 레지스터에 반영하는 물리 루프."""

    RATE_HZ = 20

    def __init__(self, bank: RegisterBank):
        self.bank = bank
        self._lock = threading.Lock()
        self._joint_velocity = [0.0] * 6   # rad/s
        self._tcp_velocity = [0.0] * 6      # [m/s]*3 + [rad/s]*3
        self._joint_entry = bank.registers.read_entry("joint_position")
        self._tcp_entry = bank.registers.read_entry("tcp_absolute")
        self._joint_pos = [float(v) for v in bank.read(self._joint_entry.address, 6)]
        self._tcp_pos = [float(v) for v in bank.read(self._tcp_entry.address, 6)]

    def set_joint_velocity(self, qd: list[float]) -> None:
        with self._lock:
            self._joint_velocity = list(qd)

    def set_tcp_velocity(self, xd: list[float]) -> None:
        with self._lock:
            self._tcp_velocity = list(xd)

    def stop_all(self) -> None:
        with self._lock:
            self._joint_velocity = [0.0] * 6
            self._tcp_velocity = [0.0] * 6

    def move_joint_to(self, joints_rad: list[float]) -> None:
        """목표 관절값(movej)으로 즉시(짧은 지연 후) 옮긴다."""
        def apply():
            time.sleep(0.8)
            with self._lock:
                self._joint_pos = [v * 1000.0 for v in joints_rad]  # rad -> mrad(raw)
            print(f"[Motion] 홈 관절값으로 이동 완료: {joints_rad}")
        threading.Thread(target=apply, daemon=True).start()

    def run_forever(self) -> None:
        dt = 1.0 / self.RATE_HZ
        joint_scale = self.bank.registers.scales_for(self._joint_entry)
        tcp_scale = self.bank.registers.scales_for(self._tcp_entry)
        while True:
            time.sleep(dt)
            with self._lock:
                jv, tv = list(self._joint_velocity), list(self._tcp_velocity)
                for i in range(6):
                    # rad/s 또는 m·rad/s -> milli 단위 -> 레지스터 카운트
                    self._joint_pos[i] += (jv[i] * 1000.0 / joint_scale[i]) * dt
                    self._tcp_pos[i] += (tv[i] * 1000.0 / tcp_scale[i]) * dt
                joint_raw = [int(round(v)) for v in self._joint_pos]
                tcp_raw = [int(round(v)) for v in self._tcp_pos]
            self.bank.write_many(self._joint_entry.address, joint_raw)
            self.bank.write_many(self._tcp_entry.address, tcp_raw)


_SPEEDJ_RE = re.compile(r"speedj\((\[[^\]]*\])")
_SPEEDL_RE = re.compile(r"speedl\((\[[^\]]*\])")
_MOVEJ_RE = re.compile(r"movej\((\[[^\]]*\])")


class PrimaryServer(threading.Thread):
    """30001 스크립트 수신 서버. 조그와 홈 이동 스크립트를 해석한다."""

    def __init__(self, motion: MotionSim, host: str, port: int):
        super().__init__(daemon=True)
        self.motion = motion
        self.host, self.port = host, port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(4)

    def run(self) -> None:
        print(f"[Primary 30001] {self.host}:{self.port} 에서 대기")
        while True:
            conn, addr = self._sock.accept()
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            buf = ""
            try:
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    buf += chunk.decode("utf-8", "ignore")
                    if len(buf) > 8192:
                        buf = buf[-4096:]
                    self._scan(buf)
            except (ConnectionError, OSError):
                self.motion.stop_all()
                return

    def _scan(self, buf: str) -> None:
        m = _SPEEDJ_RE.search(buf)
        if m:
            self.motion.set_joint_velocity(eval(m.group(1)))  # noqa: S307 - 신뢰 가능한 내부 스크립트
        m = _SPEEDL_RE.search(buf)
        if m:
            self.motion.set_tcp_velocity(eval(m.group(1)))  # noqa: S307
        m = _MOVEJ_RE.search(buf)
        if m:
            self.motion.move_joint_to(eval(m.group(1)))  # noqa: S307


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--dash-port", type=int, default=29999)
    parser.add_argument("--primary-port", type=int, default=30001)
    parser.add_argument("--modbus-port", type=int, default=5502)
    parser.add_argument(
        "--wall-error", type=float, default=ScanTaskSim.WALL_ERROR_MM,
        help="실제 벽이 입력 반지름보다 이만큼 어긋나 있다고 친다 [mm]")
    parser.add_argument(
        "--max-rows", type=int, default=ScanTaskSim.MAX_ROWS,
        help="ㄹ자 줄 수 상한 (시험을 빨리 끝내려고 둔다)")
    args = parser.parse_args()

    registers = register_map.load()
    bank = RegisterBank(registers)
    motion = MotionSim(bank)
    task = ScanTaskSim(bank, wall_error_mm=args.wall_error, max_rows=args.max_rows)
    task.start()

    ModbusServer(bank, args.host, args.modbus_port).start()
    DashboardServer(bank, motion, task, args.host, args.dash_port).start()
    PrimaryServer(motion, args.host, args.primary_port).start()

    print(
        f"실행 준비 완료. 다음처럼 노드를 띄우세요:\n"
        f"  ros2 run elite_robot_controller robot_control_node --ros-args "
        f"-p robot_ip:={args.host} -p modbus_port:={args.modbus_port}\n"
        f"Ctrl+C 로 종료합니다."
    )
    try:
        motion.run_forever()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
