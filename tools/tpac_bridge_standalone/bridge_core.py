#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bridge_core.py — 단독 TPAC 브릿지의 중계 로직 (UI 없음)

  RobotPoller  : 로봇 Modbus(502)에서 기본 좌표 · CSCAN 좌표 · DO · 상태를 주기적으로 읽는다
  DoPassThrough: 로봇 DO 비트를 TPAC 의 Outputs(레지스터 1 · 코일 16~)로 넘긴다 — 바뀔 때마다
                 latch 시간 이상 붙잡아 TPAC 이 놓치지 않게 한다
  TpacServer   : 이 프로그램이 Modbus TCP 서버가 되어 TPAC 이 UR 로봇처럼 읽어 가게 한다

RCS 의 TPAC 브리지(operator-ui services/tpac_bridge · screens/tpac_bridge_screen.py)와 TPAC 쪽
주소 · 단위는 같다. 다른 점은 DO 신호다 — RCS 는 두산 태스크의 스캔 구간(277) · 진행 상태(290)로
DO[0..2] 를 만들지만, 단독판은 기본으로 **로봇의 실제 DO0** 을 그대로 넘긴다. 그래서 다른 모양이나
로봇에서 직접 짠 동작 프로그램에서도, 그 프로그램이 CSCAN 좌표(280~287)를 쓰고 스캔 동안 DO0 을
켜기만 하면 TPAC 에 스캔 좌표가 간다.

TPAC 이 읽는 자리 (UR 배치, FC3 · FC4 같은 값)
    1        Outputs bits — DO 신호 (코일 16~ 와 같은 값)
    256~258  컨트롤러 버전 · 로봇 모드      260~265 전원 · 정지 상태      450~451 전류
    270~275  관절 [mRad]
    400~405  포즈 — 고른 값(CSCAN 좌표 / 기본 좌표 / 자동) [0.1mm · mRad]
    410~415  TCP 속도 [mm/s · mRad/s] — 로봇이 주는 실제 속도(CSCAN 을 낼 때는 스캔 중에만)
    420~425  작업 평면 = CSCAN 좌표 [0.1mm · mRad]
    430      누적 거리(Odometer) [mm]           431  C-scan 위치(이번 줄 호 위치) [0.1mm]
    코일 16~ DO 신호 (DO0 = 코일 16)
    시작 주소(기본 0) ~ : 관절 6 + 기본 좌표 6 + 상태 8 워드 데이터 블록
"""

from __future__ import annotations

import threading
import time
from collections import deque

from modbus_io import RobotModbus, ServerBridge
from robot_map import (DEFAULT_READ_MAP, OUT_FORMATS, READ_SPEC, RobotData, clamp_int16,
                       encode_payload, to_uint16)
from scan_signals import COIL_BASE, LATCH_MS, STATE_SCAN, STATUS_REGISTER, ScanSignalOutput

# TPAC 이 읽는 UR 자리 — 로봇에서 '읽는' 주소(robot_map)와는 별개다.
MIRROR_UR = {"joint": 270, "pose": 400, "speed": 410, "offset": 420}
PLANE_ADDR = 420
ODOMETER_ADDR = 430
CSCAN_ADDR = 431

#: DO 신호를 만드는 방법 — (키, 화면 이름, 넘길 비트 마스크)
SIGNAL_MODES = (
    ("do0", "로봇 DO0 그대로", 0x0001),
    ("do0_2", "로봇 DO0~DO2 그대로", 0x0007),
    ("do0_7", "로봇 DO0~DO7 그대로", 0x00FF),
    ("task", "두산 태스크 상태(277 · 290)로 만들기", 0),
    ("off", "내보내지 않음", 0),
)
SIGNAL_MASKS = {key: mask for key, _name, mask in SIGNAL_MODES}

#: TPAC 포즈(400) 자리에 낼 값 — (키, 화면 이름)
POSE_SOURCES = (
    ("scan", "CSCAN 좌표 (280~285, 제로점 기준)"),
    ("pose", "기본 좌표 (현재 TCP, 베이스 기준)"),
    ("auto", "자동 (CSCAN 값이 있으면 CSCAN, 없으면 기본 좌표)"),
)


#: 버전 · 모드 · 관절 · 온도(63~110) · 태스크 상태(500) — 표시 · UR 상태 칸용이라 가끔만 읽는다.
SLOW_BLOCKS = [(63, 48), (500, 1)]


def _span(blocks, max_words):
    """붙어 있는 (주소, 개수) 들을 max_words 안이면 한 블록으로, 아니면 그대로."""
    lo = min(a for a, _n in blocks)
    hi = max(a + n for a, n in blocks)
    return [(lo, hi - lo)] if hi - lo <= max_words else sorted(blocks)


def plan_blocks(read_map=None, use_signal=False, max_words=48):
    """매 주기 읽을 블록(빠름)과 가끔 읽을 블록(느림) — 좌표 블록을 **맨 뒤**에 둔다.

    읽은 다음 바로 TPAC 에 내므로, 맨 뒤에 읽은 값이 가장 덜 묵는다(2026-11-18 지연 검토).
    빠름: DO → 기본 좌표 · 실제 속도 → CSCAN 좌표(두산 태스크 방식이면 277~290 과 한 블록).
    느림: 버전 · 상태 · 작업 속도(306/307) — 내보낸 **뒤에** 읽는다.
    """
    m = dict(DEFAULT_READ_MAP)
    if read_map:
        m.update({k: int(v) for k, v in read_map.items() if k in m})
    fast = [(m["do"], READ_SPEC["do"])]
    fast += _span([(m["pose"], READ_SPEC["pose"]), (m["tcp_speed"], READ_SPEC["tcp_speed"])], max_words)
    scan = [(m["scan"], READ_SPEC["scan"])]
    if use_signal:
        scan.append((m["signal"], READ_SPEC["signal"]))
    fast += _span(scan, max_words)
    slow = list(SLOW_BLOCKS) + [(m["speed"], READ_SPEC["speed"])]
    return split_blocks(fast, max_words), split_blocks(slow, max_words)


def split_blocks(blocks, max_words=48):
    """읽기 블록을 max_words 이하로 쪼갠다(컨트롤러가 한 번에 받는 워드 수 제한)."""
    max_words = max(1, int(max_words))
    out = []
    for start, count in blocks:
        off = 0
        while off < count:
            n = min(max_words, count - off)
            out.append((start + off, n))
            off += n
    return out


# ============================================================================
class RobotPoller(threading.Thread):
    """로봇 Modbus 서버에서 주기적으로 읽어 on_data(RobotData) 를 호출한다."""

    #: 읽기 주기 하한 [ms] — 이보다 빨리 물으면 컨트롤러 Modbus 가 버거워한다.
    MIN_INTERVAL_MS = 5

    def __init__(self, host, port=502, unit=1, interval_ms=20,
                 on_data=None, on_log=None, on_conn=None,
                 read_fc="auto", max_words=48, read_map=None, use_signal=False):
        super().__init__(daemon=True, name="robot-poller")
        self.host, self.port, self.unit = host, int(port), int(unit)
        self.interval = max(self.MIN_INTERVAL_MS, int(interval_ms)) / 1000.0
        self.read_fc = read_fc
        self.read_map = read_map or None
        # 좌표 · DO(급함)는 매 주기 — 좌표 블록을 맨 뒤에 읽고 바로 낸다. 버전 · 상태는 가끔,
        # 그것도 낸 **뒤에** 읽는다(예전에는 10 주기마다 그 주기의 좌표가 두 왕복 늦게 나갔다).
        # 277~290(두산 태스크 상태)은 그 방식일 때만 읽는다.
        self.fast_blocks, self.slow_blocks = plan_blocks(self.read_map, use_signal, max_words)
        self.slow_every = 10
        self._regs = {}
        self.cycle_ms = 0.0
        self.on_data = on_data or (lambda d: None)
        self.on_log = on_log or (lambda m: None)
        self.on_conn = on_conn or (lambda ok, m: None)
        self._running = False
        self.mb = None
        self.connected = False
        self.rx_count = 0
        self.err_count = 0

    def stop(self, wait: float = 0.0):
        self._running = False
        if wait and self.is_alive() and threading.current_thread() is not self:
            self.join(wait)

    def run(self):
        self._running = True
        while self._running:
            self.mb = RobotModbus(self.host, self.port, self.unit, timeout=1.0,
                                  read_fc=self.read_fc, on_log=self.on_log)
            if self.mb.connect():
                break
            self.connected = False
            self.on_conn(False, "연결 실패 — 3초 뒤 다시")
            self.on_log(f"[로봇] 연결 실패 {self.host}:{self.port} — 3초 뒤 다시 시도합니다")
            for _ in range(30):
                if not self._running:
                    self.on_conn(False, "미연결")
                    return
                time.sleep(0.1)
        if not self._running:
            return

        self.connected = True
        self.on_conn(True, f"연결됨 {self.host}:{self.port}")
        self.on_log(f"[로봇] 연결 성공 {self.host}:{self.port} (unit {self.unit})")

        fail = 0
        while self._running:
            t0 = time.time()
            try:
                if self.rx_count == 0:
                    self._read(self.slow_blocks)        # 처음 한 번은 상태부터 채운다
                self._read(self.fast_blocks)
                data = RobotData.from_registers(self._regs, self.read_map)
                data.ts = time.time()
                self.cycle_ms = (data.ts - t0) * 1000
                self.rx_count += 1
                if fail:
                    self.on_log("[로봇] 통신 정상 복구")
                    self.connected = True
                    self.on_conn(True, f"연결됨 {self.host}:{self.port}")
                fail = 0
                self.on_data(data)                       # 읽자마자 TPAC 에 낸다
                if self.rx_count % self.slow_every == 0:
                    self._read(self.slow_blocks)         # 상태는 낸 뒤에 — 다음 주기 값에 들어간다
            except Exception as e:                      # noqa: BLE001
                fail += 1
                self.err_count += 1
                if fail in (1, 5) or fail % 50 == 0:
                    self.on_log(f"[로봇] 읽기 오류({fail}): {e}")
                if fail == 3:
                    self.connected = False
                    self.on_conn(False, "통신 오류 — 다시 연결 중")
                if fail >= 3:
                    time.sleep(0.5)
                    self.mb.reconnect()
            dt = self.interval - (time.time() - t0)
            if dt > 0:
                time.sleep(dt)

        self.mb.disconnect()
        self.connected = False
        self.on_conn(False, "미연결")
        self.on_log("[로봇] 읽기 종료")

    def _read(self, blocks):
        for start, count in blocks:
            for i, v in enumerate(self.mb.read_hr(start, count)):
                self._regs[start + i] = v


# ============================================================================
class DoPassThrough:
    """로봇 DO 비트(마스크한 값)를 TPAC Outputs 로 넘긴다.

    로봇 값이 바뀔 때마다 큐에 넣고, 전용 스레드가 앞 값이 latch 시간 이상 유지된 뒤에 다음 값을
    쓴다 — 짧게 켰다 끈 DO 도 TPAC 이 한 번은 본다. write(word) 는 실제로 쓰는 함수다.
    """

    def __init__(self, write, mask: int, latch_ms: int = LATCH_MS, on_change=None) -> None:
        self._write = write
        self.mask = int(mask) & 0xFFFF
        self.latch_s = max(0, int(latch_ms)) / 1000.0
        self._on_change = on_change or (lambda word, reason: None)
        self._queue: deque[int] = deque()
        self._cond = threading.Condition()
        self._current = 0
        self._planned = 0
        self._last_change = 0.0
        self._thread: threading.Thread | None = None
        self._running = False

    @property
    def word(self) -> int:
        return self._current

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._current = self._planned = 0
        self._write(0)
        self._last_change = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="tpac-do", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._running = False
            self._queue.clear()
            self._cond.notify_all()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def observe(self, robot_do: int) -> None:
        word = int(robot_do) & self.mask
        with self._cond:
            if word == self._planned:
                return
            self._queue.append(word)
            self._planned = word
            self._cond.notify_all()

    def _run(self) -> None:
        while True:
            with self._cond:
                while self._running and not self._queue:
                    self._cond.wait(0.5)
                if not self._running:
                    return
                word = self._queue.popleft()
            wait = self.latch_s - (time.monotonic() - self._last_change)
            if wait > 0:
                time.sleep(wait)
            self._write(word)
            self._current = word
            self._last_change = time.monotonic()
            self._on_change(word, "로봇 DO")


# ============================================================================
class TpacServer:
    """TPAC 이 UR 로봇처럼 읽어 가는 Modbus TCP 서버."""

    def __init__(self, on_log=None, on_state=None, on_request=None, on_signal=None):
        self.on_log = on_log or (lambda m: None)
        self.on_state = on_state or (lambda ok, m: None)
        self.on_signal = on_signal or (lambda word, text: None)
        self._on_request = on_request
        self.bridge = ServerBridge(log=self.on_log, on_request=on_request)
        self.start_addr = 0
        self.fmt = "int16_raw"
        self.word_order = "hi_lo"
        self.pose_source = "scan"
        self.signal_mode = "do0"
        self.signal_latch_ms = LATCH_MS
        self.plane_addr = PLANE_ADDR
        self.odometer_addr = ODOMETER_ADDR
        self.cscan_addr = CSCAN_ADDR
        self.pass_through: DoPassThrough | None = None
        self.task_signals: ScanSignalOutput | None = None
        self.alive = 0
        self.update_count = 0
        self._warned_block_overlap = False

    @property
    def running(self):
        return self.bridge.running

    @property
    def n_words(self):
        return OUT_FORMATS[self.fmt][1]

    # ------------------------------------------------------------------
    def start(self, host="0.0.0.0", port=502, start_addr=0, fmt="int16_raw",
              word_order="hi_lo", pose_source="scan", signal_mode="do0",
              signal_latch_ms=LATCH_MS, delay_ms=0, plane_addr=PLANE_ADDR,
              odometer_addr=ODOMETER_ADDR, cscan_addr=CSCAN_ADDR):
        self.bridge = ServerBridge(log=self.on_log, on_request=self._on_request, delay_ms=delay_ms)
        self.start_addr = int(start_addr)
        self.fmt = fmt
        self.word_order = word_order
        self.pose_source = pose_source
        self.signal_mode = signal_mode if signal_mode in SIGNAL_MASKS else "do0"
        self.signal_latch_ms = int(signal_latch_ms)
        self.plane_addr, self.odometer_addr, self.cscan_addr = (
            int(plane_addr), int(odometer_addr), int(cscan_addr))
        self._warned_block_overlap = False
        ok = self.bridge.start(host, int(port))
        if ok:
            if self.signal_mode == "task":
                self.task_signals = ScanSignalOutput(self._write_task_signals, self.signal_latch_ms,
                                                     on_change=self._task_signal_changed)
                self.task_signals.start()
            elif self.signal_mode != "off":
                self.pass_through = DoPassThrough(self._write_do_word, SIGNAL_MASKS[self.signal_mode],
                                                  self.signal_latch_ms,
                                                  on_change=self._do_changed)
                self.pass_through.start()
        self.on_state(ok, (f"동작 중 {host}:{port}" if ok else "시작 실패"))
        return ok

    def stop(self):
        for attr in ("pass_through", "task_signals"):
            worker = getattr(self, attr)
            setattr(self, attr, None)
            if worker is not None:
                worker.stop()
        self.bridge.stop()
        self.on_state(False, "정지")

    # ---------------------------------------------------------- DO 신호
    @property
    def signal_word(self) -> int:
        if self.pass_through is not None:
            return self.pass_through.word
        if self.task_signals is not None:
            return self.task_signals.current.word
        return 0

    def _coil_count(self) -> int:
        mask = SIGNAL_MASKS.get(self.signal_mode, 0)
        return max(3, mask.bit_length()) if mask else 3

    def _write_do_word(self, word: int):
        """DO 워드를 레지스터 1(FC3 · FC4)과 코일 16~(FC1 · FC2)에 같이 쓴다."""
        self.bridge.set_hr(STATUS_REGISTER, [word])
        self.bridge.set_coils(COIL_BASE, [(word >> i) & 1 for i in range(self._coil_count())])

    def _do_changed(self, word: int, reason: str):
        text = " · ".join(f"DO{i} {'1' if (word >> i) & 1 else '0'}"
                          for i in range(self._coil_count()))
        self.on_log(f"[TPAC 신호] {text} — {reason}")
        self.on_signal(word, text)

    def _write_task_signals(self, outputs):
        self.bridge.set_coils(COIL_BASE, outputs.bits)
        self.bridge.set_hr(STATUS_REGISTER, [outputs.word])

    def _task_signal_changed(self, outputs, reason):
        self.on_log(f"[TPAC 신호] {outputs.describe()} (DO={outputs.word:03b}) — {reason}")
        self.on_signal(outputs.word, outputs.describe())

    # ---------------------------------------------------------- 갱신
    def scanning(self, data: RobotData) -> bool:
        """지금 스캔 중인가 — 두산 태스크면 290 = 6, 아니면 DO0 이 켜졌거나 CSCAN 좌표(280~285)에
        값이 있을 때(DO0 으로 기준을 잡는 로봇 스크립트는 켜진 순간 좌표가 0 이다)."""
        if self.signal_mode == "task":
            return data.scan_state == STATE_SCAN
        return bool(data.do_word & 1) or data.scanning

    def pose_for_tpac(self, data: RobotData) -> list[int]:
        if self.pose_source == "scan":
            return list(data.tcp_scan)
        if self.pose_source == "pose":
            return list(data.tcp_raw)
        return list(data.tcp_scan) if data.scanning else list(data.tcp_raw)

    def speed_live(self, data: RobotData) -> bool:
        """속도(410~415)를 내도 되는가 — CSCAN 좌표를 낼 때는 스캔 중에만(아니면 0)."""
        return self.pose_source != "scan" or self.scanning(data)

    def update(self, data: RobotData) -> bool:
        if not self.bridge.running:
            return False
        b = self.bridge
        self.alive = (self.alive + 1) & 0xFFFF
        if self.pass_through is not None:
            self.pass_through.observe(data.do_word)
        elif self.task_signals is not None:
            self.task_signals.observe(data.scan_state, data.scan_segment, data.running_state)

        # 데이터 블록 — 관절 6 + 기본 좌표 6 + 상태 8 (한 번에 통째로 써서 찢어지지 않게)
        block = encode_payload(data, self.fmt, self.word_order) + data.payload_status(self.alive)
        offset = STATUS_REGISTER - self.start_addr
        if 0 <= offset < len(block):
            block[offset] = self.signal_word        # 레지스터 1 은 DO 신호 자리다
            if not self._warned_block_overlap:
                self._warned_block_overlap = True
                self.on_log(f"[TPAC] 데이터 블록(시작 주소 {self.start_addr})이 레지스터 1(DO 신호)과 "
                            "겹칩니다 — 그 칸은 DO 신호로 씁니다. 시작 주소를 옮기기를 권합니다.")
        ok = b.set_hr(self.start_addr, block)

        # UR 자리
        m = MIRROR_UR
        b.set_hr(m["joint"], [to_uint16(v) for v in data.joint_raw])
        b.set_hr(m["pose"], [to_uint16(v) for v in self.pose_for_tpac(data)])
        speed = data.tcp_spd_real if self.speed_live(data) else [0] * 6
        b.set_hr(m["speed"], [to_uint16(v) for v in speed])
        b.set_hr(self.plane_addr, [to_uint16(v) for v in data.tcp_scan])
        b.set_hr(self.odometer_addr, [to_uint16(clamp_int16(data.arc_total))])
        b.set_hr(self.cscan_addr, [to_uint16(clamp_int16(data.arc_pos * 10))])
        self._write_ur_state(data)
        if ok:
            self.update_count += 1
        return ok

    def _write_ur_state(self, data: RobotData):
        """UR 로봇의 상태 자리 — TPAC 이 접속할 때 먼저 확인한다(비어 있으면 거부할 수 있다)."""
        b = self.bridge
        b.set_hr(STATUS_REGISTER, [self.signal_word])
        b.set_hr(256, [data.version[0], data.version[1]])
        b.set_hr(258, [to_uint16(data.robot_mode)])
        estop = 1 if data.emergency_stop else 0
        pstop = 1 if data.protective_stop else 0
        b.set_hr(260, [1 if data.power_on else 0, pstop, estop, 0, 0, 1 if (pstop or estop) else 0])
        b.set_hr(450, [to_uint16(sum(abs(c) for c in data.joint_cur)), 0])
        try:
            bits = [bool(data.power_on), bool(pstop), bool(estop), False, False, bool(pstop or estop)]
            b.bank.set_coils(260, bits)
            b.bank.set_discrete_inputs(260, bits)
        except Exception:                               # noqa: BLE001
            pass

    # ---------------------------------------------------------- 요청 확인
    def filled_ranges(self):
        """지금 값을 채우는 레지스터 구간 [(시작, 끝, 설명)]."""
        n = self.n_words
        m = MIRROR_UR
        return [(self.start_addr, self.start_addr + n - 1, "데이터"),
                (self.start_addr + n, self.start_addr + n + 7, "상태"),
                (STATUS_REGISTER, STATUS_REGISTER, "DO 신호"),
                (256, 258, "UR 버전 · 모드"), (260, 265, "UR 전원 · 정지"), (450, 451, "UR 전류"),
                (m["joint"], m["joint"] + 5, "관절"), (m["pose"], m["pose"] + 5, "포즈"),
                (m["speed"], m["speed"] + 5, "속도"), (self.plane_addr, self.plane_addr + 5, "작업 평면"),
                (self.odometer_addr, self.odometer_addr, "Odometer"),
                (self.cscan_addr, self.cscan_addr, "C-scan")]

    def covers(self, addr, count, fc=3):
        """요청 구간이 채운 범위 안이면 설명, 아니면 None(0 만 읽어 간다)."""
        end = addr + count - 1
        if fc in (1, 2):
            ranges = [(COIL_BASE, COIL_BASE + self._coil_count() - 1, "DO 신호"),
                      (260, 265, "UR 전원 · 정지 비트")]
        else:
            ranges = self.filled_ranges()
        for lo, hi, label in ranges:
            if lo <= addr and end <= hi:
                return label
        return None
