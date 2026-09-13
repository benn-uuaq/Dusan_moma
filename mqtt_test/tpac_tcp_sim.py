#!/usr/bin/env python3
"""TPAC 쪽에 로봇 TCP 값을 흉내 내서 흘려보내는 독립 실행 시뮬레이터.

실제 로봇/Operator UI 없이 **TPAC이 Modbus TCP로 값을 읽어갈 수 있는지만**
시험하기 위한 도구다. 표준 라이브러리(tkinter, socket, threading)만 쓰고
pymodbus 등 외부 패키지에 의존하지 않는다 — Windows에 Python만 있으면
그대로 실행되고, PyInstaller로도 그대로 exe가 된다.

레지스터 배치는 실제 접속 로그로 확인한 TPAC의 최소 요구사항
(`TPAC 연결 가이드` 참고)과 완전히 동일하게 맞췄다:

    주소 1        Digital Outputs        bit    (항상 0 — 실제 로봇도 그렇다)
    주소 400~402  TCP X, Y, Z            0.1mm  (부호 있는 16bit)
                  X 는 제로점(호 끝) 기준이라 0 ~ -75.8 사이만 움직인다
    주소 410~412  TCP 속도 X, Y, Z       mm/s   (부호 있는 16bit)

값은 이 프로그램이 내부적으로 그리는 ㄹ자(지그재그) 경로를 따라
실시간으로 바뀐다 — 한쪽 방향(Y)으로 폭만큼 갔다가, 한 줄(row) 내려가서
반대 방향으로 돌아오는 것을 반복한다.

실행:
    python3 mqtt_test/tpac_tcp_sim.py

Windows exe로 묶기:
    pip install pyinstaller
    pyinstaller --onefile --windowed --name TPAC-TCP-Sim mqtt_test/tpac_tcp_sim.py
"""

from __future__ import annotations

import bisect
import ctypes
import math
import socket
import struct
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from typing import Optional

# ============================================================================
# Modbus TCP 서버 — FC3(Read Holding Registers)/FC4(Read Input Registers)만
# 지원한다. TPAC은 쓰기(FC6/16)는 하지 않고 읽기만 하므로 이거면 충분하다.
# ============================================================================

_FC_READ_HOLDING = 3
_FC_READ_INPUT = 4
_EXC_ILLEGAL_FUNCTION = 1
_EXC_ILLEGAL_ADDRESS = 2

REG_COUNT = 512  # 주소 0~511, 실제로 쓰는 건 1, 400~402, 410~412 뿐이다.


class RegisterBank:
    """16bit 레지스터 배열. 여러 클라이언트/이동 스레드가 동시에 읽고 쓴다."""

    def __init__(self, count: int = REG_COUNT):
        self._lock = threading.Lock()
        self._regs = [0] * count

    def set(self, addr: int, value: int) -> None:
        with self._lock:
            if 0 <= addr < len(self._regs):
                self._regs[addr] = int(value) & 0xFFFF

    def set_signed(self, addr: int, value: float) -> None:
        """부호 있는 값을 그대로 2의 보수 16bit로 저장한다(음수도 자연스럽게 표현)."""
        v = int(round(value))
        v = -32768 if v < -32768 else (32767 if v > 32767 else v)
        self.set(addr, v & 0xFFFF)

    def read(self, addr: int, qty: int) -> Optional[list]:
        with self._lock:
            if addr < 0 or qty <= 0 or addr + qty > len(self._regs):
                return None
            return list(self._regs[addr:addr + qty])


class ModbusTcpServer:
    """소켓을 직접 다루는 최소 Modbus TCP 서버. Unit ID는 검사하지 않는다

    (실제 TPAC 접속 로그에서도 Unit ID는 특별히 안 가린다 — 가이드 참고).
    """

    def __init__(self, bank: RegisterBank, log_fn=None):
        self.bank = bank
        self._log = log_fn or (lambda msg: None)
        self._sock: Optional[socket.socket] = None
        self._threads: list[threading.Thread] = []
        self._running = False

    def start(self, host: str, port: int) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        # TPAC은 요청마다 접속을 새로 맺고 끊는 경우가 있어서(실제 접속 로그로
        # 확인됨), 대기 큐를 넉넉하게 잡아 접속이 몰려도 거절/리셋되지 않게 한다.
        self._sock.listen(32)
        self._running = True
        t = threading.Thread(target=self._accept_loop, daemon=True)
        t.start()
        self._threads.append(t)

    def stop(self) -> None:
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _accept_loop(self) -> None:
        while self._running and self._sock is not None:
            try:
                conn, addr = self._sock.accept()
            except OSError:
                break
            self._log(f"접속: {addr[0]}:{addr[1]}")
            th = threading.Thread(target=self._client_loop, args=(conn, addr), daemon=True)
            th.start()
            self._threads.append(th)

    def _client_loop(self, conn: socket.socket, addr) -> None:
        conn.settimeout(30.0)
        # Nagle 알고리즘이 응답 패킷을 붙잡아 뒀다가 늦게(최대 수십ms) 보내는
        # 걸 막는다 — 응답 지연은 0이어야 한다(TPAC 연결 가이드의 "UR 수준
        # 빠른 갱신" 요구사항과 같은 이유).
        try:
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        try:
            while self._running:
                header = self._recv_exact(conn, 7)  # MBAP: tid(2) pid(2) len(2) unit(1)
                if header is None:
                    break
                tid, pid, length, unit = struct.unpack(">HHHB", header)
                body = self._recv_exact(conn, length - 1)  # length는 unit 이후 바이트 수
                if body is None:
                    break
                resp_pdu = self._handle_pdu(body)
                resp = struct.pack(">HHHB", tid, pid, len(resp_pdu) + 1, unit) + resp_pdu
                conn.sendall(resp)
        except (OSError, ConnectionError):
            pass
        finally:
            conn.close()
            self._log(f"접속 종료: {addr[0]}:{addr[1]}")

    @staticmethod
    def _recv_exact(conn: socket.socket, n: int) -> Optional[bytes]:
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def _handle_pdu(self, body: bytes) -> bytes:
        if len(body) < 1:
            return bytes([0x80, _EXC_ILLEGAL_FUNCTION])
        func = body[0]
        if func in (_FC_READ_HOLDING, _FC_READ_INPUT):
            if len(body) < 5:
                return bytes([func | 0x80, _EXC_ILLEGAL_ADDRESS])
            addr, qty = struct.unpack(">HH", body[1:5])
            values = self.bank.read(addr, qty)
            if values is None:
                return bytes([func | 0x80, _EXC_ILLEGAL_ADDRESS])
            self._log(f"읽기 FC{func} addr={addr} qty={qty}")
            data = b"".join(struct.pack(">H", v) for v in values)
            return bytes([func, len(data)]) + data
        # 쓰기 등 다른 기능 코드는 안 쓸 거라 그냥 "지원 안 함"으로 응답한다.
        return bytes([func | 0x80, _EXC_ILLEGAL_FUNCTION])


# ============================================================================
# ㄹ자(지그재그) 이동 시뮬레이션
# ============================================================================

class ZigzagMotion:
    """실제 로봇이 그리는 ㄹ자를 그대로 흉내 내는 경로 + 사다리꼴 속도 프로파일.

    한 줄은 이렇게 돈다 (로봇 스크립트 dus_pass_r / dus_pass_l / dus_up 과 같다):

      1. 가로로 한 줄 **쓸고**(Y, 곡면이라 X 가 0 -> -75.8 -> 0 으로 따라 변한다)
      2. 줄 끝에서 한 칸 **올라간다**(Z 만, X·Y 는 그대로)

    **물러나는 동작은 없다.** 실기에서 후퇴는 probe_c/l/r 과 goto_zero 에서만
    일어나는데 그 구간은 state 가 6 이 아니라 280~285 로 나가지 않는다.
    스캔 중(state 6)의 dus_up 은 Z 만 바꾸고 dus_pass_* 는 호만 따라가므로,
    발행되는 X 는 **호의 새그(sag)뿐**이고 -100 같은 값이 나올 수 없다.

    마지막 줄까지 마치면 거기서 한 판이 끝난다. 복귀 동작은 좌표로 내보내지
    않고, 영점에서 쉬었다가 다음 판을 시작한다.

    그래서 Y·Z 는 자기 구간에서 설정 속도(기본 100mm/s)를 온전히 낸다.
    X 는 곡면을 따라 딸려 오는 성분이라 그보다 작다 — 실기와 같다.

    속도는 구간마다 **사다리꼴 프로파일**을 따른다 — 코너에서 0으로 섰다가
    accel_mm_s2(기본 400mm/s²)로 가속해 speed_mm_s까지 올리고, 다음 코너
    앞에서 같은 비율로 감속한다. 실제 로봇처럼 모서리에서 속도가 꺾인다.
    (100mm 구간이면 12.5mm 가속 + 75mm 등속 + 12.5mm 감속이라 최고 속도에
    충분히 도달한다.)

    경로는 촘촘한 점열로 미리 만들어 두고 누적 거리 위를 걸어가는 방식이다.
    담고 있는 것은 **ㄹ자 한 판뿐**이다 — 마지막 줄을 마치고 물러나면 영점
    (0, 0, 0)으로 돌아가 dwell_s 만큼 서 있다가 다시 그린다. 그때 Y 가
    끝값에서 0 으로 **초기화**된다.

    예전에는 두 가지가 더 있었는데 둘 다 뺐다. 끝에 닿으면 경로를 거꾸로
    되짚어 걸어(핑퐁) Z 가 계단 모양으로 도로 내려왔고, 그다음 판에서는
    시작 자리로 돌아오는 복귀 좌표까지 내보냈다. 실제 로봇에 없는 동작인
    데다 평면화 화면에 없는 획이 그려져 헷갈린다.
    """

    # 쓸기 구간을 이 간격(mm)으로 잘라 곡면을 근사한다. 촘촘할수록 곡선이 곱다.
    _SAMPLE_MM = 10.0
    # 코너에서 속도가 정확히 0이 되면 영영 못 출발하므로 아주 작은 하한을 둔다.
    _MIN_SPEED = 2.0

    def __init__(self, width_mm: float, row_step_mm: float, rows: int,
                 radius_mm: float, speed_mm_s: float,
                 accel_mm_s2: float = 400.0, dwell_s: float = 2.0):
        self.width = max(1.0, width_mm)
        self.row_step = row_step_mm
        self.rows = max(1, rows)
        self.radius = max(self.width / 2.0 + 1.0, radius_mm)
        self.speed = max(1.0, speed_mm_s)
        self.accel = max(1.0, accel_mm_s2)
        self.dwell = max(0.0, dwell_s)

        self.points, corner_idx = self._build_path()
        self._cum = [0.0]
        for i in range(1, len(self.points)):
            self._cum.append(self._cum[-1] + _dist(self.points[i - 1], self.points[i]))
        self.total = self._cum[-1]
        # 로봇이 실제로 서는 지점(코너)의 누적 거리. 이 사이가 하나의 가감속 구간이다.
        self.stops = sorted({self._cum[i] for i in corner_idx} | {0.0, self.total})

        self._pos = 0.0
        # 판을 시작하기 전에 영점에서 먼저 쉰다. 매 판 앞에 같은 대기가 붙는다.
        self._hold = self.dwell
        self.x, self.y, self.z = self.points[0]

    def _bulge(self, y: float) -> float:
        """원통면의 볼록한 정도. 쓸기 양 끝에서 0, 가운데서 가장 크다.

        검사면이 로봇 쪽으로 볼록해서, 가운데로 갈수록 로봇은 **뒤로 물러나며**
        훑는다. 그래서 X 좌표에는 이 값을 **빼서** 쓴다 — 자세한 건
        _build_path 의 부호 설명을 보라.
        """
        # width 는 **호 길이**다(현이 아니다). 로봇도 movec 로 호를 따라가므로
        # 현으로 근사하면 가운데에서 5mm 쯤 어긋난다. 호 각으로 바로 푼다.
        #   R844.6 · 호 721 -> 가운데 새그 75.8mm
        half_angle = self.width / (2.0 * self.radius)
        theta = (y - self.width / 2.0) / self.radius
        return self.radius * (math.cos(theta) - math.cos(half_angle))

    def _build_path(self) -> tuple:
        """(점열, 코너인 점의 번호들)을 만든다.

        **X 는 제로점 기준이라 0 이하로만 움직인다.** 실제 로봇이 내보내는
        280~285 는 제로점(호의 끝, 벽에 닿은 자리) 기준 변위이고, 검사면이
        로봇 쪽으로 볼록하므로 호 가운데로 갈수록 로봇은 뒤로 물러난다.

            X =    0     호 양 끝 (= 제로점). 줄이 바뀌는 상승 구간도 여기다.
            X = -75.8    호 가운데 (R844.6 · 호 721 의 새그)

        그 사이를 오갈 뿐, 더 내려가지 않는다. 후퇴(-100 같은 값)는 스캔
        구간이 아니라 probe/goto_zero 에서 일어나고 그때는 발행하지 않는다.

        예전에는 "물러난 면이 0, 벽면이 +retreat" 로 잡아 부호가 반대인 데다
        후퇴까지 섞여 나갔다. TPAC 이 평면화할 때 깊이가 뒤집혀 보이므로
        실기와 맞춘다.
        """
        pts: list = []
        corners: list = []

        def add(p, corner=False):
            if pts and _dist(pts[-1], p) < 1e-9:
                if corner:
                    corners.append(len(pts) - 1)
                return
            pts.append(p)
            if corner:
                corners.append(len(pts) - 1)

        n = max(2, int(round(self.width / self._SAMPLE_MM)))
        # 경로는 **영점에서 시작해 영점에서 끝나지 않는다** — ㄹ자 한 판만
        # 담는다. 마지막 줄을 마치고 물러나면 거기서 경로가 끝나고, 다음 판은
        # 영점(0, 0, 0)에서 다시 시작한다. 그 사이 dwell_s 동안 속도 0 으로
        # 서 있으므로, Y 가 끝값에서 0 으로 되돌아가는 것이 그대로 보인다.
        add((0.0, 0.0, 0.0), corner=True)          # 영점 — 호의 끝, 벽에 닿아 있다
        for row in range(self.rows):
            z = row * self.row_step
            for i in range(1, n + 1):              # 가로로 한 줄 쓸기(곡면)
                t = i / n
                y = t * self.width if row % 2 == 0 else (1.0 - t) * self.width
                add((-self._bulge(y), y, z))
            corners.append(len(pts) - 1)           # 쓸기 끝 = 코너
            if row < self.rows - 1:
                # 한 칸 상승. dus_up 과 같이 **Z 만** 바꾼다 — 줄 끝은 호의
                # 끝이라 X 는 이미 0 이고, Y 도 그대로 둔다.
                y_end = self.width if row % 2 == 0 else 0.0
                add((0.0, y_end, z + self.row_step), corner=True)
        return pts, corners

    def _speed_at(self, pos: float) -> float:
        """사다리꼴 프로파일 — 구간 양 끝에서 0, 가운데서 설정 속도."""
        k = bisect.bisect_right(self.stops, pos) - 1
        k = min(max(k, 0), len(self.stops) - 2)
        s = pos - self.stops[k]
        remain = self.stops[k + 1] - pos
        v = min(self.speed,
                _sqrt_safe(2.0 * self.accel * max(s, 0.0)),
                _sqrt_safe(2.0 * self.accel * max(remain, 0.0)))
        return max(v, self._MIN_SPEED)

    def step(self, dt: float) -> tuple:
        """dt(초)만큼 경로 위를 걷고, (x, y, z), (vx, vy, vz)를 돌려준다.

        속도 성분은 부호 없는 크기다 — 세 성분을 제곱해 더하면 그 순간의 속력이다.
        영점에서 쉬는 동안에는 좌표를 그대로 두고 속도 0 을 낸다.
        """
        if self._hold > 0.0:
            self._hold -= dt
            return (self.x, self.y, self.z), (0.0, 0.0, 0.0)

        v = self._speed_at(self._pos)
        self._pos += v * dt
        if self._pos >= self.total:
            # 한 판이 끝났다. 영점으로 되돌리고 dwell 만큼 선 채로 쉰다.
            # 여기서 Y 는 끝값(0 또는 width)에서 **0 으로 초기화**되는데,
            # 속도 0 으로 서 있는 동안 바뀌므로 받는 쪽에 속도 스파이크로
            # 보이지 않는다. 홈 복귀 좌표는 아예 내보내지 않는다 —
            # ㄹ자만 반복하는 편이 평면화 화면에서 헷갈리지 않는다.
            self._pos = 0.0
            self._hold = self.dwell
            self.x, self.y, self.z = self.points[0]
            return (self.x, self.y, self.z), (0.0, 0.0, 0.0)

        i = self._segment_index(self._pos)
        p0, p1 = self.points[i], self.points[i + 1]
        seg_len = self._cum[i + 1] - self._cum[i]
        t = 0.0 if seg_len <= 0 else (self._pos - self._cum[i]) / seg_len

        self.x = p0[0] + (p1[0] - p0[0]) * t
        self.y = p0[1] + (p1[1] - p0[1]) * t
        self.z = p0[2] + (p1[2] - p0[2]) * t

        if seg_len <= 0:
            vel = (0.0, 0.0, 0.0)
        else:
            vel = tuple(abs(p1[k] - p0[k]) / seg_len * v for k in range(3))
        return (self.x, self.y, self.z), vel

    def _segment_index(self, pos: float) -> int:
        """pos가 들어 있는 구간의 시작점 번호. 마지막 점은 구간의 끝이라 제외한다."""
        i = bisect.bisect_right(self._cum, pos) - 1
        return min(max(i, 0), len(self.points) - 2)


def _dist(a, b) -> float:
    return math.sqrt(sum((a[k] - b[k]) ** 2 for k in range(3)))


def _sqrt_safe(v: float) -> float:
    return math.sqrt(v) if v > 0.0 else 0.0


# ============================================================================
# GUI
# ============================================================================

class _FineTimer:
    """윈도우의 타이머 눈금을 1ms 로 올렸다가 되돌린다.

    기본 눈금은 약 15.6ms 라, `time.sleep(0.03)` 을 걸어도 실제로는 31.2ms
    또는 46.9ms 에 깨어난다. 그러면 좌표가 들쭉날쭉한 덩어리로 갱신되는데,
    TPAC 은 자기 주기로 꼬박꼬박 읽으므로 **좌표 차이로 속도를 계산할 때
    설정값의 1.6~2.3 배까지 튄다**. 레지스터로 내보내는 속도값 자체는 절대
    설정값을 넘지 않지만, 받는 쪽이 미분해서 쓰면 이 눈금이 그대로 보인다.

    timeBeginPeriod(1) 은 프로세스가 아니라 시스템 전역 설정이라 반드시
    짝을 맞춰 되돌려야 한다. 윈도우가 아니면 아무것도 하지 않는다.
    """

    def __init__(self) -> None:
        self._winmm = None

    def __enter__(self):
        try:
            self._winmm = ctypes.WinDLL("winmm")   # 윈도우에만 있다
            self._winmm.timeBeginPeriod(1)
        except (OSError, AttributeError):
            self._winmm = None
        return self

    def __exit__(self, *_exc):
        if self._winmm is not None:
            self._winmm.timeEndPeriod(1)
            self._winmm = None
        return False


def _sleep_until(deadline: float) -> None:
    """deadline(monotonic 초)까지 기다린다. 마지막 1ms 는 스핀으로 메운다.

    sleep 은 항상 요청보다 조금 더 자므로, 눈금을 1ms 로 올려도 30ms 주기가
    미세하게 밀린다. 남은 시간이 1ms 아래로 내려오면 CPU 를 잠깐 돌려
    정확히 그 시각에 깨어난다 — 주기가 고르면 좌표도 고르게 나간다.
    """
    while True:
        remain = deadline - time.monotonic()
        if remain <= 0.0:
            return
        if remain > 0.001:
            time.sleep(remain - 0.001)
        else:
            time.sleep(0)          # 다른 스레드에 양보만 하고 곧바로 되돌아온다


REG_HEARTBEAT = 1
REG_POSE = 400   # X, Y, Z
REG_SPEED = 410  # X, Y, Z


def _local_ip_hint() -> str:
    """TPAC에 알려줄 이 PC의 실제 IP를 추정한다(외부로 실제 나가는 인터페이스 기준)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "확인 불가 (네트워크 연결 없음)"


class App(tk.Tk):
    # 좌표 갱신 주기. TPAC 연결 가이드의 권장 폴링은 20~50ms 인데, 갱신을
    # 그와 비슷한 주기로 하면 **앨리어싱**이 생긴다 — 어떤 폴링 구간은 갱신
    # 한 번을, 어떤 구간은 두 번을 담아 좌표 차이가 들쭉날쭉해지고, 받는 쪽이
    # 그걸 미분하면 설정 속도의 1.5~2.3배가 나온다. 폴링보다 훨씬 촘촘하게
    # 갱신하면 그 오차가 (1 + 갱신주기/폴링주기) 로 줄어든다.
    #   30ms -> 최대 2.34배 | 5ms -> 1.25배 | 2ms -> 1.10배
    # 한 틱 비용이 4.2µs 라 2ms 로 돌려도 CPU 는 0.2 % 만 쓴다.
    UPDATE_SEC = 0.002

    def __init__(self):
        super().__init__()
        self.title("TPAC TCP 이동 시뮬레이터")
        self.geometry("560x560")
        self.resizable(False, False)

        self.bank = RegisterBank()
        self.server: Optional[ModbusTcpServer] = None
        self._motion_stop = threading.Event()
        self._motion_thread: Optional[threading.Thread] = None

        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        conn = ttk.LabelFrame(self, text="Modbus TCP 서버 (TPAC이 여기로 접속)")
        conn.pack(fill="x", **pad)

        ttk.Label(conn, text="바인드 IP").grid(row=0, column=0, sticky="w", **pad)
        self.ip_var = tk.StringVar(value="0.0.0.0")
        self.ip_entry = ttk.Entry(conn, textvariable=self.ip_var, width=18)
        self.ip_entry.grid(row=0, column=1, **pad)

        ttk.Label(conn, text="포트").grid(row=0, column=2, sticky="w", **pad)
        self.port_var = tk.StringVar(value="5020")
        self.port_entry = ttk.Entry(conn, textvariable=self.port_var, width=8)
        self.port_entry.grid(row=0, column=3, **pad)

        hint = _local_ip_hint()
        ttk.Label(conn, text=f"※ TPAC 쪽 설정에는 이 PC의 실제 IP를 알려주세요: {hint}",
                  foreground="#555").grid(row=1, column=0, columnspan=4, sticky="w", **pad)

        conn_btns = ttk.Frame(conn)
        conn_btns.grid(row=2, column=0, columnspan=4, sticky="w", **pad)
        self.open_btn = ttk.Button(conn_btns, text="서버 열기", command=self._on_open)
        self.open_btn.pack(side="left", padx=4)
        self.close_btn = ttk.Button(conn_btns, text="서버 닫기", command=self._on_close,
                                     state="disabled")
        self.close_btn.pack(side="left", padx=4)
        self.server_status_var = tk.StringVar(value="닫힘")
        ttk.Label(conn_btns, textvariable=self.server_status_var,
                  foreground="#0a6").pack(side="left", padx=16)

        motion = ttk.LabelFrame(self, text="ㄹ자 이동 설정 — 서버를 먼저 연 다음 시작하세요")
        motion.pack(fill="x", **pad)

        self.width_var = tk.StringVar(value="800")
        self.row_step_var = tk.StringVar(value="100")
        self.rows_var = tk.StringVar(value="6")
        self.radius_var = tk.StringVar(value="834.6")
        self.speed_var = tk.StringVar(value="100")
        self.accel_var = tk.StringVar(value="400")
        self.dwell_var = tk.StringVar(value="2.0")

        fields = [
            ("가로 폭(Y, mm)", self.width_var),
            ("줄 간격(Z, mm)", self.row_step_var),
            ("줄 수", self.rows_var),
            ("호 반경(mm)", self.radius_var),
            ("속도(mm/s)", self.speed_var),
            ("가속도(mm/s²)", self.accel_var),
            ("영점 대기(s)", self.dwell_var),
        ]
        self._motion_entries: list[ttk.Entry] = []
        for i, (label, var) in enumerate(fields):
            ttk.Label(motion, text=label).grid(row=i // 3, column=(i % 3) * 2,
                                                 sticky="w", **pad)
            entry = ttk.Entry(motion, textvariable=var, width=10)
            entry.grid(row=i // 3, column=(i % 3) * 2 + 1, **pad)
            self._motion_entries.append(entry)

        btns = ttk.Frame(self)
        btns.pack(fill="x", **pad)
        self.start_btn = ttk.Button(btns, text="동작 시작", command=self._on_start_motion,
                                     state="disabled")
        self.start_btn.pack(side="left", padx=8)
        self.stop_btn = ttk.Button(btns, text="정지", command=self._on_stop_motion,
                                    state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        self.status_var = tk.StringVar(value="대기 중")
        ttk.Label(btns, textvariable=self.status_var, foreground="#0a6").pack(side="left", padx=16)

        pose = ttk.LabelFrame(self, text="현재 값 (TPAC이 읽어가는 것과 동일)")
        pose.pack(fill="x", **pad)
        self.pose_var = tk.StringVar(value="X=0.0  Y=0.0  Z=0.0  (mm)")
        self.speed_out_var = tk.StringVar(value="Vx=0.0  Vy=0.0  Vz=0.0  (mm/s)")
        ttk.Label(pose, textvariable=self.pose_var, font=("Consolas", 11)).pack(anchor="w", **pad)
        ttk.Label(pose, textvariable=self.speed_out_var, font=("Consolas", 11)).pack(anchor="w", **pad)

        reg = ttk.LabelFrame(self, text="레지스터 배치 (FC3/FC4, Unit ID 무관)")
        reg.pack(fill="x", **pad)
        reg_text = (
            "주소 1        Digital Outputs   bit     항상 0\n"
            "주소 400~402  TCP X, Y, Z       0.1mm   부호 있는 16bit\n"
            "주소 410~412  TCP 속도 X, Y, Z  mm/s    부호 있는 16bit"
        )
        ttk.Label(reg, text=reg_text, font=("Consolas", 10), justify="left").pack(anchor="w", **pad)

        log_frame = ttk.LabelFrame(self, text="접속 로그")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log = scrolledtext.ScrolledText(log_frame, height=8, state="disabled")
        self.log.pack(fill="both", expand=True, padx=4, pady=4)

    def _log_msg(self, msg: str) -> None:
        def _append():
            self.log.configure(state="normal")
            self.log.insert("end", f"{time.strftime('%H:%M:%S')}  {msg}\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.after(0, _append)

    # ---------------------------------------------------------- 서버 열기/닫기
    def _on_open(self) -> None:
        """서버만 먼저 연다 — TPAC이 접속해서 모니터링을 시작할 수 있게.

        이동은 아직 시작하지 않으므로 값은 전부 0으로 고정돼 있다.
        """
        try:
            host = self.ip_var.get().strip()
            port = int(self.port_var.get().strip())
        except ValueError:
            messagebox.showerror("입력 오류", "포트 숫자를 확인하세요.")
            return

        self.server = ModbusTcpServer(self.bank, log_fn=self._log_msg)
        try:
            self.server.start(host, port)
        except OSError as exc:
            messagebox.showerror("서버 시작 실패", f"{host}:{port} 바인드 실패\n{exc}")
            self.server = None
            return

        self.bank.set(REG_HEARTBEAT, 0)  # 실제 로봇도 이 자리는 항상 0

        self.server_status_var.set(f"열림 ({host}:{port})")
        self.open_btn.configure(state="disabled")
        self.close_btn.configure(state="normal")
        self.ip_entry.configure(state="disabled")
        self.port_entry.configure(state="disabled")
        self.start_btn.configure(state="normal")  # 서버가 열려야 이동을 시작할 수 있다
        self._log_msg(f"서버 열림: {host}:{port}")

    def _on_close(self) -> None:
        if self._motion_thread is not None:
            self._on_stop_motion()
        if self.server is not None:
            self.server.stop()
            self.server = None
        self.server_status_var.set("닫힘")
        self.open_btn.configure(state="normal")
        self.close_btn.configure(state="disabled")
        self.ip_entry.configure(state="normal")
        self.port_entry.configure(state="normal")
        self.start_btn.configure(state="disabled")
        self._log_msg("서버 닫힘")

    # ---------------------------------------------------------------- 이동
    def _on_start_motion(self) -> None:
        try:
            width = float(self.width_var.get())
            row_step = float(self.row_step_var.get())
            rows = int(self.rows_var.get())
            radius = float(self.radius_var.get())
            speed = float(self.speed_var.get())
            accel = float(self.accel_var.get())
            dwell = float(self.dwell_var.get())
        except ValueError:
            messagebox.showerror("입력 오류", "숫자 입력값을 확인하세요.")
            return

        motion = ZigzagMotion(width, row_step, rows, radius, speed, accel, dwell)
        self._motion_stop.clear()
        self._motion_thread = threading.Thread(
            target=self._motion_loop, args=(motion,), daemon=True)
        self._motion_thread.start()

        self.status_var.set("이동 중")
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        for entry in self._motion_entries:
            entry.configure(state="disabled")
        self._log_msg("이동 시작")

    def _on_stop_motion(self) -> None:
        self._motion_stop.set()
        self._motion_thread = None
        self.status_var.set("대기 중")
        self.start_btn.configure(state="normal" if self.server is not None else "disabled")
        self.stop_btn.configure(state="disabled")
        for entry in self._motion_entries:
            entry.configure(state="normal")
        self._log_msg("이동 정지")

    def _motion_loop(self, motion: ZigzagMotion) -> None:
        """실제로 흐른 시간만큼만, 그리고 **고른 주기로** 전진시킨다.

        "한 틱은 30ms일 것이다"라고 가정하고 고정 거리를 더하면, 윈도우의
        time.sleep 오차나 GUI 부하로 한 틱이 40ms 걸렸을 때 레지스터의 속도값은
        100mm/s라고 나가면서 **실제 좌표는 75mm/s로만 움직이는** 거짓말이 된다.
        그래서 매번 monotonic 시계로 진짜 경과 시간을 재서 그만큼만 전진시킨다.

        주기가 고른 것도 그만큼 중요하다. 윈도우 기본 타이머 눈금(15.6ms)
        때문에 갱신이 31.2/46.9ms 로 들쭉날쭉하면, 좌표 차이로 속도를 계산하는
        쪽(TPAC)에서 설정값의 두 배가 넘게 튄다. _FineTimer 로 눈금을 1ms 까지
        내리고 _sleep_until 로 마지막 자투리를 메워 주기를 고르게 유지한다.
        """
        with _FineTimer():
            prev = time.monotonic()
            next_tick = prev
            last_ui = 0.0
            while not self._motion_stop.is_set():
                now = time.monotonic()
                dt = now - prev
                prev = now
                # 창을 오래 끌었거나 스레드가 밀렸을 때 한 번에 확 튀지 않게 막는다.
                dt = min(dt, self.UPDATE_SEC * 5)

                (x, y, z), (vx, vy, vz) = motion.step(dt)
                self.bank.set_signed(REG_POSE, x * 10.0)      # 0.1mm 단위
                self.bank.set_signed(REG_POSE + 1, y * 10.0)
                self.bank.set_signed(REG_POSE + 2, z * 10.0)
                # 속도는 크기만 내보낸다 — 음수 속도는 있을 수 없다.
                self.bank.set_signed(REG_SPEED, vx)
                self.bank.set_signed(REG_SPEED + 1, vy)
                self.bank.set_signed(REG_SPEED + 2, vz)

                # 화면 글자는 10Hz면 충분하다. 레지스터는 위에서 매 틱 갱신되므로
                # TPAC이 보는 값은 그대로고, Tk 이벤트 큐만 한가해진다.
                if now - last_ui >= 0.1:
                    last_ui = now
                    self.after(0, self._update_pose_labels, x, y, z, vx, vy, vz)

                next_tick += self.UPDATE_SEC
                if next_tick < time.monotonic():
                    # 이미 밀렸으면 밀린 만큼 따라잡으려 하지 말고 기준을 다시 잡는다.
                    next_tick = time.monotonic()
                else:
                    _sleep_until(next_tick)

    def _update_pose_labels(self, x: float, y: float, z: float,
                            vx: float, vy: float, vz: float) -> None:
        self.pose_var.set(f"X={x:7.1f}  Y={y:7.1f}  Z={z:7.1f}  (mm)")
        self.speed_out_var.set(f"Vx={vx:6.1f}  Vy={vy:6.1f}  Vz={vz:6.1f}  (mm/s)")

    def destroy(self) -> None:
        self._motion_stop.set()
        if self.server is not None:
            self.server.stop()
        super().destroy()


def main() -> None:
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.destroy)
    app.mainloop()


if __name__ == "__main__":
    main()
