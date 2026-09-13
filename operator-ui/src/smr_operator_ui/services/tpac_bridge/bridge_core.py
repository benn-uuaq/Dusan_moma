#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bridge_core.py — UI 와 무관한 중계 로직 (pyModbusTCP 기반)
  RobotPoller    : 로봇에서 관절/TCP 값을 주기적으로 읽는 스레드
  ExternalWriter : 외부 Modbus 서버로 값을 write (Client 모드)
  ExternalServer : 이 프로그램이 서버가 되어 외부에서 read (Server 모드)
  Bridge         : 위 3개를 묶은 파사드 (UI 없이도 사용 가능)
"""

import time
import threading

from .modbus_io import RobotModbus, ServerBridge
from .robot_map import (READ_BLOCKS, RobotData, OUT_FORMATS,
                        encode_payload, to_uint16, clamp_int16,
                        build_read_blocks)

# 외부에 '재현' 할 기본 주소 — UR 배치 (TPAC 등 UR 기준 장비가 읽는 자리)
#   로봇에서 '읽는' 주소(robot_map)와는 전혀 별개다.
MIRROR_UR = {"joint": 270, "pose": 400, "speed": 410, "offset": 420}


def split_blocks(blocks, max_words=48):
    """
    읽기 블록을 max_words 이하로 쪼갠다.
    컨트롤러가 한 번에 받을 수 있는 워드 수가 적을 때 사용.
    """
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
    """로봇 Modbus 서버에서 주기적으로 읽어 on_data(RobotData) 를 호출."""

    def __init__(self, host, port=502, unit=1, interval_ms=100,
                 on_data=None, on_log=None, on_conn=None,
                 read_fc="auto", max_words=48, read_map=None):
        super().__init__(daemon=True, name="robot-poller")
        self.host, self.port, self.unit = host, int(port), int(unit)
        self.interval = max(20, int(interval_ms)) / 1000.0
        self.read_fc = read_fc
        self.read_map = read_map or None
        self.blocks = split_blocks(
            build_read_blocks(self.read_map, max_words), max_words)
        # 좌표(급함)와 상태(안 급함)를 나눈다.
        #   63~110 은 48워드짜리 버전·관절·온도 묶음이라 매 주기 읽으면
        #   왕복이 늘어 좌표가 그만큼 늦어진다. 상태는 가끔만 읽는다.
        self.fast_blocks = [b for b in self.blocks if b[0] not in (63, 500)]
        self.slow_blocks = [b for b in self.blocks if b[0] in (63, 500)]
        self.slow_every = 10        # 상태는 N 주기마다 한 번
        self._regs = {}             # 마지막으로 읽은 값 (상태는 유지)
        self.cycle_ms = 0.0         # 한 주기에 실제 걸린 시간
        self.on_data = on_data or (lambda d: None)
        self.on_log = on_log or (lambda m: None)
        self.on_conn = on_conn or (lambda ok, m: None)
        self._running = False
        self.mb = None
        self.connected = False
        self.rx_count = 0
        self.err_count = 0

    # ------------------------------------------------------------------
    def stop(self):
        self._running = False

    def run(self):
        self._running = True
        self.mb = RobotModbus(self.host, self.port, self.unit, timeout=1.0,
                              read_fc=self.read_fc, on_log=self.on_log)

        if not self.mb.connect():
            self.connected = False
            self.on_conn(False, "연결 실패")
            self.on_log(f"[robot] 연결 실패 {self.host}:{self.port}")
            return

        self.connected = True
        self.on_conn(True, f"연결됨 {self.host}:{self.port}")
        self.on_log(f"[robot] 연결 성공 {self.host}:{self.port} (unit {self.unit})")

        fail = 0
        while self._running:
            t0 = time.time()
            try:
                # 좌표는 매 주기, 상태는 slow_every 주기마다
                todo = list(self.fast_blocks)
                if self.rx_count % self.slow_every == 0:
                    todo += self.slow_blocks
                for start, count in todo:
                    for i, v in enumerate(self.mb.read_hr(start, count)):
                        self._regs[start + i] = v

                data = RobotData.from_registers(self._regs, self.read_map)
                data.ts = time.time()
                self.cycle_ms = (data.ts - t0) * 1000
                self.rx_count += 1
                if fail:
                    self.on_log("[robot] 통신 정상 복구")
                    self.connected = True
                    self.on_conn(True, f"연결됨 {self.host}:{self.port}")
                fail = 0
                self.on_data(data)

            except Exception as e:                      # noqa: BLE001
                fail += 1
                self.err_count += 1
                if fail in (1, 5) or fail % 50 == 0:
                    self.on_log(f"[robot] 읽기 오류({fail}): {e}")
                if fail == 3:
                    self.connected = False
                    self.on_conn(False, "통신 오류 - 재연결 시도")
                if fail >= 3:
                    time.sleep(0.5)
                    self.mb.reconnect()

            dt = self.interval - (time.time() - t0)
            if dt > 0:
                time.sleep(dt)

        self.mb.disconnect()
        self.connected = False
        self.on_conn(False, "미연결")
        self.on_log("[robot] 폴링 종료")


# ============================================================================
class ExternalWriter:
    """
    외부 Modbus TCP 서버(PLC 등)에 값을 write 하는 클라이언트.

    데이터 배치 (base = 시작 주소, N = 데이터 타입별 워드 수)
        base+0   ~ base+N-1 : 관절 6 + TCP 6
        base+N   ~ base+N+7 : 상태 8워드 (마지막은 alive 카운터)
    페이로드 + 상태를 한 번의 FC16 으로 연속 전송하므로
    외부에서 읽을 때 값이 찢어지지 않는다.
    """

    def __init__(self, on_log=None, on_conn=None):
        self.on_log = on_log or (lambda m: None)
        self.on_conn = on_conn or (lambda ok, m: None)
        self.mb = None
        self.start_addr = 0
        self.fmt = "int16_raw"
        self.word_order = "hi_lo"
        self.send_status = True
        self._lock = threading.Lock()
        self._fail = 0
        self.tx_count = 0
        self.alive = 0

    @property
    def n_words(self):
        return OUT_FORMATS[self.fmt][1]

    # ------------------------------------------------------------------
    def open(self, host, port=502, unit=1, start_addr=0,
             fmt="int16_raw", word_order="hi_lo", send_status=True):
        self.close(silent=True)
        self.start_addr = int(start_addr)
        self.fmt = fmt
        self.word_order = word_order
        self.send_status = bool(send_status)
        self.mb = RobotModbus(host, port, unit, timeout=1.0)
        ok = self.mb.connect()
        self._fail = 0
        self.on_conn(ok, (f"연결됨 {host}:{port}" if ok else "연결 실패"))
        self.on_log(f"[외부-쓰기] {'연결 성공' if ok else '연결 실패'} "
                    f"{host}:{port} (unit {unit})")
        if not ok:
            self.close(silent=True)
        return ok

    def close(self, silent=False):
        with self._lock:
            if self.mb is not None:
                self.mb.disconnect()
                self.mb = None
        if not silent:
            self.on_conn(False, "미연결")
            self.on_log("[외부-쓰기] 연결 종료")

    def is_open(self):
        return self.mb is not None

    # ------------------------------------------------------------------
    def write(self, data: RobotData):
        if self.mb is None:
            return False
        self.alive = (self.alive + 1) & 0xFFFF
        values = encode_payload(data, self.fmt, self.word_order)
        if self.send_status:
            values = values + data.payload_status(self.alive)
        with self._lock:
            if self.mb is None:
                return False
            try:
                # 페이로드 + 상태를 한 번에 (찢어진 값 방지)
                self.mb.write_hr(self.start_addr, values)
                if self._fail:
                    self.on_log("[외부-쓰기] 전송 정상 복구")
                self._fail = 0
                self.tx_count += 1
                return True
            except Exception as e:                      # noqa: BLE001
                self._fail += 1
                if self._fail in (1, 5) or self._fail % 50 == 0:
                    self.on_log(f"[외부-쓰기] 오류({self._fail}): {e}")
                if self._fail >= 3:
                    self.mb.reconnect()
                return False


# ============================================================================
class ExternalServer:
    """
    이 프로그램이 Modbus TCP 서버가 되어 외부 마스터가 값을 읽어가게 한다.
    Holding Register(FC3) 와 Input Register(FC4) 양쪽에 같은 값이 들어간다.

    레지스터 배치 (base = 시작 주소, N = 데이터 타입별 워드 수)
        base+0   ~ base+N-1 : 관절 6 + TCP 6
        base+N   ~ base+N+7 : 상태 8워드 (mode, running, power, pstop, estop,
                                          control, operation, alive 카운터)
        미러링 사용 시 로봇 원본 주소 73~78 / 384~389 에 INT16 raw 동일 값
    데이터 블록은 한 번의 갱신으로 통째로 쓰므로 값이 찢어지지 않는다.
    """

    def __init__(self, on_log=None, on_state=None, on_request=None):
        self.on_log = on_log or (lambda m: None)
        self.on_state = on_state or (lambda ok, m: None)
        self._on_request = on_request
        self.bridge = ServerBridge(log=self.on_log, on_request=on_request)
        self.start_addr = 0
        self.fmt = "int16_raw"
        self.word_order = "hi_lo"
        self.mirror = True
        self.pos_unit = "raw"       # "raw" = 0.1mm (로봇과 동일) / "mm" = 1mm
        # 외부에 '재현' 할 주소. 로봇에서 '읽는' 주소(robot_map)와 별개다.
        # 외부 장치가 다른 주소를 기대하면 여기만 바꾸면 된다.
        self.mirror_map = dict(MIRROR_UR)
        # 위상 보정: 0 / 1 / 2
        #   응답을 주소로 짝짓지 못하고 도착 순서대로 칸에 채우는 장치가 있다.
        #   접속할 때 보내는 확인용 요청 하나가 칸을 먼저 먹으면 이후 모든 값이
        #   한 칸씩 밀린 자리에 표시된다. 밀린 만큼 미리 돌려서 넣어 상쇄한다.
        self.mirror_shift = 0
        # 좌표만 모드: 세 블록 전부에 좌표(pose)를 넣는다.
        #   응답이 어느 칸에 들어가든 전부 좌표이므로 섞여도 티가 안 나고,
        #   어떤 칸이 갱신을 못 받아도 나머지 칸이 같은 값을 보여준다.
        #   순서를 못 맞추는 장치를 상대할 때 가장 확실한 방법.
        self.pose_only = False
        # UR 호환 모드: UR 로봇이 쓰는 상태 레지스터까지 같이 채운다.
        #   TPAC 처럼 UR 기준으로 만들어진 장비가 접속 확인 단계에서
        #   읽어보는 자리들이라, 비어 있으면 연결을 거부할 수 있다.
        self.ur_mode = False
        # 외부로 내보낼 좌표의 출처: "scan" / "pose" / "auto"
        self.pose_source = "scan"
        self._last_pose = None      # 속도 계산용 (좌표, 시각)
        self.update_count = 0
        self.alive = 0

    @property
    def running(self):
        return self.bridge.running

    @property
    def n_words(self):
        return OUT_FORMATS[self.fmt][1]

    # ------------------------------------------------------------------
    def start(self, host="0.0.0.0", port=502, start_addr=0, mirror=True,
              fmt="int16_raw", word_order="hi_lo", pos_unit="raw",
              mirror_map=None, delay_ms=0, mirror_shift=0, pose_only=False,
              ur_mode=False, pose_source="scan"):
        # 응답 지연: 폴링 속도 제한이 없는 장치가 폭주해 스스로 죽는 것을 막는다
        self.bridge = ServerBridge(log=self.on_log, on_request=self._on_request,
                                   delay_ms=delay_ms)
        self.start_addr = int(start_addr)
        self.mirror = bool(mirror)
        self.fmt = fmt
        self.word_order = word_order
        self.pos_unit = pos_unit
        self.mirror_shift = int(mirror_shift) % 3
        self.pose_only = bool(pose_only)
        self.ur_mode = bool(ur_mode)
        self.pose_source = pose_source
        if mirror_map:
            self.mirror_map.update({k: int(v) for k, v in mirror_map.items()})
        ok = self.bridge.start(host, int(port))
        self.on_state(ok, (f"동작중 {host}:{port}" if ok else "시작 실패"))
        return ok

    def stop(self):
        self.bridge.stop()
        self.on_state(False, "정지")

    # ------------------------------------------------------------------
    def update(self, data: RobotData):
        if not self.bridge.running:
            return False
        self.alive = (self.alive + 1) & 0xFFFF
        block = (encode_payload(data, self.fmt, self.word_order)
                 + data.payload_status(self.alive))
        ok = self.bridge.set_hr(self.start_addr, block)     # 한 번에 통째로
        if self.mirror:
            self._write_mirror(data)
        if self.ur_mode:
            self._write_ur_state(data)
        if ok:
            self.update_count += 1
        return ok

    # ------------------------------------------------------------------
    def filled_ranges(self):
        """
        지금 실제로 값을 채우고 있는 구간 목록 [(시작, 끝, 설명), ...].
        외부 요청이 이 범위를 벗어나면 0 만 읽어가게 된다.
        """
        n = self.n_words
        out = [(self.start_addr, self.start_addr + n - 1, "데이터"),
               (self.start_addr + n, self.start_addr + n + 7, "상태")]
        if self.mirror:
            m = self.mirror_map
            out += [(m["joint"], m["joint"] + 5, "관절 재현"),
                    (m["pose"], m["pose"] + 5, "pose 재현"),
                    (m["speed"], m["speed"] + 5, "속도 재현"),
                    (m["offset"], m["offset"] + 5, "offset 재현")]
        if self.ur_mode:
            out += [(1, 1, "UR Outputs"), (256, 258, "UR 버전·모드"),
                    (260, 265, "UR 전원·정지"), (450, 451, "UR 전류")]
        return out

    def covers(self, addr, count):
        """요청 구간이 채워진 범위 안에 완전히 들어가면 설명을, 아니면 None."""
        end = addr + count - 1
        for lo, hi, label in self.filled_ranges():
            if lo <= addr and end <= hi:
                return label
        return None

    # ------------------------------------------------------------------
    def _calc_speed(self, pose, data):
        """
        로봇이 6워드 속도를 주지 않으므로(306/307 은 작업속도·속도비율 2개)
        연속된 좌표의 차이로 속도를 만든다.
          X,Y,Z   : 0.1mm 차이 → mm/s
          Rx,Ry,Rz: mRad 차이 → mRad/s
        """
        now = time.time()
        prev = self._last_pose
        self._last_pose = (list(pose), now)
        if not prev:
            return [0] * 6
        old, t0 = prev
        dt = now - t0
        if dt <= 0 or dt > 1.0:            # 너무 벌어지면 신뢰할 수 없다
            return [0] * 6
        out = [clamp_int16((pose[i] - old[i]) * 0.1 / dt) for i in range(3)]
        out += [clamp_int16((pose[i] - old[i]) / dt) for i in range(3, 6)]
        return out

    # ------------------------------------------------------------------
    def _write_ur_state(self, data: RobotData):
        """
        UR 로봇이 쓰는 상태 레지스터 자리를 로봇 실제 상태로 채운다.
        UR 기준으로 만들어진 장비(TPAC 등)가 접속할 때 확인하는 자리들이다.

          1        Outputs bits 0-15
          256/257  컨트롤러 버전
          258      Robot mode (7 = Running)
          260~265  전원 / 정지 상태
          450/451  로봇·I/O 전류
        """
        b = self.bridge
        b.set_hr(1, [0])                                   # Outputs bits
        b.set_hr(256, [data.version[0], data.version[1]])
        b.set_hr(258, [to_uint16(data.robot_mode)])
        estop = 1 if data.emergency_stop else 0
        pstop = 1 if data.protective_stop else 0
        b.set_hr(260, [1 if data.power_on else 0,          # isPowerOnRobot
                       pstop,                              # isSecurityStopped
                       estop,                              # isEmergencyStopped
                       0,                                  # isTeachButtonPressed
                       0,                                  # isPowerButtonPressed
                       1 if (pstop or estop) else 0])      # isSafetySignal
        b.set_hr(450, [to_uint16(sum(abs(c) for c in data.joint_cur)), 0])
        try:
            bits = [bool(data.power_on), bool(pstop), bool(estop), False,
                    False, bool(pstop or estop)]
            b.bank.set_coils(260, bits)
            b.bank.set_discrete_inputs(260, bits)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _write_mirror(self, data: RobotData):
        """
        로봇과 동일한 주소·단위로 그대로 재현한다.
          73~78   관절 위치   [mRad]
          384~389 TCP pose    [0.1mm 또는 mm / mRad]
          400~405 TCP 속도    [mm/s / mRad/s]
          410~415 TCP offset  [mm / mRad]
        엔코더 블록처럼 주소가 고정된 장치가 로봇 대신 브리지를 읽으면
        설정을 바꾸지 않고 그대로 동작한다. (브리지는 FC3/FC4 모두 응답)
        """
        pose = data.pose_by_source(self.pose_source)
        if self.pos_unit == "mm":
            pose = [clamp_int16(pose[i] / 10.0) for i in range(3)] + pose[3:]
        m = self.mirror_map
        self.bridge.set_hr(m["joint"], [to_uint16(v) for v in data.joint_raw])
        data.tcp_spd = self._calc_speed(pose, data)

        # 장치가 묻는 순서(pose → speed → offset)에 맞춰 값을 배치한다.
        # mirror_shift 만큼 돌려 넣으면 밀린 표시를 상쇄할 수 있다.
        addrs = [m["pose"], m["speed"], m["offset"]]
        if self.pose_only:
            # 세 블록 모두 좌표를 넣는다. 어느 칸에 들어가도 좌표이므로
            # 섞여도 상관없다. (장치의 세 주소가 각 블록 시작에 맞아야 한다)
            words = [to_uint16(v) for v in pose]
            for a in addrs:
                self.bridge.set_hr(a, words)
            return
        datas = [pose, list(data.tcp_spd), list(data.tcp_off)]
        for i, a in enumerate(addrs):
            vals = datas[(i + self.mirror_shift) % 3]
            self.bridge.set_hr(a, [to_uint16(v) for v in vals])


# ============================================================================
class Bridge:
    """폴러 + 외부 쓰기 + 외부 서버를 묶은 파사드."""

    def __init__(self, on_log=print):
        self.on_log = on_log
        self.poller = None
        self.writer = ExternalWriter(on_log=on_log)
        self.server = ExternalServer(on_log=on_log)
        self.last_data = None
        self.use_writer = True
        self.use_server = True

    def start_robot(self, host, port=502, unit=1, interval_ms=100,
                    on_data=None, on_conn=None):
        self.stop_robot()

        def _cb(d):
            self.last_data = d
            if self.use_writer and self.writer.is_open():
                self.writer.write(d)
            if self.use_server and self.server.running:
                self.server.update(d)
            if on_data:
                on_data(d)

        self.poller = RobotPoller(host, port, unit, interval_ms,
                                  on_data=_cb, on_log=self.on_log,
                                  on_conn=on_conn or (lambda *_: None))
        self.poller.start()
        return self.poller

    def stop_robot(self):
        if self.poller is not None and self.poller.is_alive():
            self.poller.stop()
            self.poller.join(timeout=2)
        self.poller = None

    def shutdown(self):
        self.stop_robot()
        self.writer.close(silent=True)
        self.server.stop()
