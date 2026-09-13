#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
robot_map.py — S-series Modbus 레지스터 맵 및 값 변환
(출처: S_series_UserManual_Modbus_Server_Data.xlsx)
"""

import struct

RAD2DEG = 57.29577951308232

# ---------------------------------------------------------------- 레지스터
REG_VERSION         = 63     # 63,64,65 : major, minor, bugfix
REG_ROBOT_MODE      = 66
REG_POWER_ON        = 67
REG_PROTECTIVE_STOP = 68
REG_EMERGENCY_STOP  = 69
REG_REDUCED_MODE    = 70
REG_CONTROL_METHOD  = 71
REG_OPERATION_MODE  = 72

REG_JOINT_POS       = 73     # 73~78  Base,Shoulder,Elbow,Wrist1,Wrist2,Wrist3  [mRad]
REG_JOINT_SPD       = 84     # 84~89   [mRad/s]
REG_JOINT_CUR       = 95     # 95~100  [mA]
REG_JOINT_TMP       = 105    # 105~110 [℃]

# ---------------------------------------------------------------------------
# TCP 관련 주소  (로봇 팀 config/modbus_registers.json 기준)
#
#   280~285  제로점 기준 상대좌표 — 스캔 중에만 값이 들어온다
#            절대좌표가 아니라 (현재 TCP - 제로점) 이며, 베이스 좌표계에서
#            그냥 뺀 값이라 Z 는 위로 갈수록 + 다.
#            로봇 태스크가 state==4 (ㄹ자 스캔 중) 일 때만 실제 값을 쓰고
#            그 외에는 [0,0,0,0,0,0] 을 쓴다. 게이팅이 로봇 쪽에 이미
#            들어가 있으므로 브리지에서 따로 판단하지 않는다.
#   384~389  현재 TCP (베이스 프레임, 컨트롤러가 채우는 원래 자리)
#   306/307  작업 속도 / 속도 비율  (6워드 블록이 아니라 낱개 2개)
#
# 단위는 280~285 도 300~305(pose) 와 같다. (X,Y,Z 0.1mm / Rx,Ry,Rz mRad)
# ---------------------------------------------------------------------------
REG_TCP_SCAN        = 280    # 280~282 X,Y,Z / 283~285 Rx,Ry,Rz (제로점 기준)
REG_TCP_POSE        = 384    # 384~386 X,Y,Z / 387~389 Rx,Ry,Rz (베이스 프레임)
REG_WORK_SPEED      = 306    # 306 작업 속도 / 307 속도 비율
# 로봇이 직접 주는 실제 TCP 속도(베이스 프레임). X,Y,Z는 mm/s, Rx,Ry,Rz는
# mRad/s로 이미 실제 단위라 배율 변환이 필요 없다. 원래는 좌표 변화량으로
# 속도를 추정했는데(ExternalServer._calc_speed), 로봇이 직접 주는 값이
# 있으므로 이 쪽을 쓴다 — 폴링 주기에 따라 추정값이 흔들리는 문제가 없다.
REG_TCP_SPEED       = 400    # 400~402 X,Y,Z speed(mm/s) / 403~405 Rx,Ry,Rz speed(mRad/s)
REG_RUNNING_STATE   = 500    # 1=Running 2=Pause 3=Stopped

# 각 항목이 차지하는 워드 수
# scan 은 8 워드다: 280~285 제로점 기준 pose + 286 호 위치[0.1mm] +
# 287 누적 호 길이[mm]. 한 블록으로 읽어야 좌표와 호 위치가 같은
# 시점의 값이 된다 — 나눠 읽으면 그 사이 로봇이 움직여 어긋난다.
READ_SPEC = {"scan": 8, "pose": 6, "speed": 2, "tcp_speed": 6}

# 위 주소는 UI "로봇에서 읽기"의 '읽기 주소' 칸에서 바꿀 수 있다.
# 여기 값은 프로그램을 처음 켤 때의 기본값일 뿐이다.
DEFAULT_READ_MAP = {"scan": REG_TCP_SCAN, "pose": REG_TCP_POSE,
                    "speed": REG_WORK_SPEED, "tcp_speed": REG_TCP_SPEED}


def build_read_blocks(amap=None, max_words=48):
    """
    읽기 주소 설정에 맞춰 읽을 블록 목록을 만든다.
    가까이 붙은 구간은 한 번에 묶어 읽는다. 나눠 읽으면 그 사이에 로봇이
    값을 갱신해 스캔좌표와 pose 가 서로 다른 시점의 값으로 섞일 수 있다.
    """
    m = dict(DEFAULT_READ_MAP)
    if amap:
        m.update({k: int(v) for k, v in amap.items() if k in m})

    spans = sorted((m[k], READ_SPEC.get(k, 6)) for k in m)
    merged = []
    for addr, cnt in spans:
        if merged:
            start, count = merged[-1]
            need = addr + cnt - start
            if addr >= start and need <= max_words:      # 붙여서 한 번에
                merged[-1] = (start, max(count, need))
                continue
        merged.append((addr, cnt))
    return [(63, 48)] + merged + [(500, 1)]


READ_BLOCKS = build_read_blocks()

JOINT_NAMES = ["Base (J1)", "Shoulder (J2)", "Elbow (J3)",
               "Wrist1 (J4)", "Wrist2 (J5)", "Wrist3 (J6)"]
TCP_NAMES = ["TCP X", "TCP Y", "TCP Z", "TCP Rx", "TCP Ry", "TCP Rz"]

ROBOT_MODE_TXT = {
    0: "DISCONNECTED", 1: "CONFIRM_SAFETY", 2: "BOOTING", 3: "POWER_OFF",
    4: "POWER_ON", 5: "IDLE", 6: "BACKDRIVE", 7: "RUNNING",
    8: "UPDATING_FW", 9: "WAIT_CALIB",
}
RUNNING_TXT = {0: "-", 1: "Running", 2: "Pause", 3: "Stopped"}
CONTROL_TXT = {0: "원격 미개방", 1: "Local", 2: "Remote"}
OPERATION_TXT = {-1: "None", 0: "Automatic", 1: "Manual"}


# ---------------------------------------------------------------- 변환
def to_int16(v):
    v = int(v) & 0xFFFF
    return v - 0x10000 if v >= 0x8000 else v


def to_uint16(v):
    return int(round(v)) & 0xFFFF


def clamp_int16(v):
    """16bit signed 범위를 넘지 않게 잘라낸다 (랩어라운드 방지)."""
    v = int(round(v))
    return -32768 if v < -32768 else (32767 if v > 32767 else v)


def clamp_int32(v):
    v = int(round(v))
    return -2147483648 if v < -2147483648 else (2147483647 if v > 2147483647 else v)


# ============================================================================
class RobotData:
    """한 번의 폴링으로 읽은 로봇 상태 스냅샷."""

    def __init__(self):
        self.ok = False
        self.ts = 0.0
        self.joint_raw = [0] * 6
        self.joint_spd = [0] * 6
        self.joint_cur = [0] * 6
        self.joint_tmp = [0] * 6
        self.tcp_raw = [0] * 6
        self.tcp_spd = [0] * 6
        self.tcp_off = [0] * 6
        self.tcp_scan = [0] * 6
        # TPAC 스캔 축. 좌표(현)가 아니라 **호 길이** 기준이라야 C-scan 이
        # 눌리거나 비선형으로 밀리지 않는다. 로봇이 계산해서 준다.
        self.arc_pos = 0.0      # 이번 줄의 호 위 거리 [mm]
        self.arc_total = 0.0    # 완료된 줄까지 더한 누적 [mm]
        # 로봇이 레지스터 400~405로 직접 주는 실제 TCP 속도(베이스 프레임).
        # tcp_spd(위)는 ExternalServer._calc_speed 가 좌표 변화량으로
        # 추정한 값이라 이것과는 다르다 — 실제 값이 있으면 이쪽을 쓴다.
        self.tcp_spd_real = [0] * 6
        self.work_speed = 0        # 306 작업 속도
        self.speed_ratio = 0       # 307 속도 비율
        self.version = (0, 0, 0)
        self.robot_mode = -1
        self.running_state = 0
        self.power_on = 0
        self.protective_stop = 0
        self.emergency_stop = 0
        self.reduced_mode = 0
        self.control_method = 0
        self.operation_mode = -1

    # ------------------------------------------------------------------
    @classmethod
    def from_registers(cls, regs, amap=None):
        """regs: {주소: 원시값} 딕셔너리, amap: 읽기 주소 설정"""
        m = dict(DEFAULT_READ_MAP)
        if amap:
            m.update({k: int(v) for k, v in amap.items() if k in m})
        d = cls()
        g = lambda a: regs.get(a, 0)                                  # noqa: E731
        s = lambda a: to_int16(regs.get(a, 0))                        # noqa: E731

        d.version = (g(63), g(64), g(65))
        d.robot_mode = g(REG_ROBOT_MODE)
        d.power_on = g(REG_POWER_ON)
        d.protective_stop = g(REG_PROTECTIVE_STOP)
        d.emergency_stop = g(REG_EMERGENCY_STOP)
        d.reduced_mode = g(REG_REDUCED_MODE)
        d.control_method = g(REG_CONTROL_METHOD)
        d.operation_mode = s(REG_OPERATION_MODE)
        d.running_state = g(REG_RUNNING_STATE)

        d.joint_raw = [s(REG_JOINT_POS + i) for i in range(6)]
        d.joint_spd = [s(REG_JOINT_SPD + i) for i in range(6)]
        d.joint_cur = [s(REG_JOINT_CUR + i) for i in range(6)]
        d.joint_tmp = [s(REG_JOINT_TMP + i) for i in range(6)]
        d.tcp_raw = [s(m["pose"] + i) for i in range(6)]
        d.tcp_scan = [s(m["scan"] + i) for i in range(6)]
        d.arc_pos = s(m["scan"] + 6) * 0.1      # 286: 0.1mm -> mm
        d.arc_total = s(m["scan"] + 7)          # 287: mm
        # mm/s, mRad/s 로 이미 실제 단위라 배율 변환이 필요 없다(0.1mm 아님).
        d.tcp_spd_real = [s(m["tcp_speed"] + i) for i in range(6)]
        d.work_speed = s(m["speed"])
        d.speed_ratio = s(m["speed"] + 1)
        d.ok = True
        return d

    # ---------------- 사람이 읽는 단위 --------------------------------
    @property
    def joint_deg(self):
        """관절 각도 [deg]  (raw mRad → deg)"""
        return [v / 1000.0 * RAD2DEG for v in self.joint_raw]

    @property
    def joint_spd_deg(self):
        return [v / 1000.0 * RAD2DEG for v in self.joint_spd]

    @property
    def tcp_conv(self):
        """X,Y,Z → mm  /  Rx,Ry,Rz → deg"""
        out = [self.tcp_raw[i] / 10.0 for i in range(3)]
        out += [self.tcp_raw[i] / 1000.0 * RAD2DEG for i in range(3, 6)]
        return out

    @property
    def tcp_spd_conv(self):
        """XYZ 속도는 이미 mm/s, 회전 속도는 mRad/s → deg/s"""
        out = [float(self.tcp_spd[i]) for i in range(3)]
        out += [self.tcp_spd[i] / 1000.0 * RAD2DEG for i in range(3, 6)]
        return out

    # ---------------- 외부 전송 페이로드 ------------------------------
    def payload_raw(self):
        """12 워드 : 관절 6 (mRad) + TCP 6 (0.1mm / mRad), signed→unsigned"""
        return [to_uint16(v) for v in (self.joint_raw + self.tcp_raw)]

    def payload_scaled(self):
        """
        12 워드 (모두 signed 16bit, 범위 초과 시 클램핑)
          관절 6       : 각도 deg × 100      (±180.00° → ±18000)
          TCP X,Y,Z    : 0.1 mm             (raw 그대로. mm×100 은 16bit 초과)
          TCP Rx,Ry,Rz : 각도 deg × 100
        """
        vals = [v * 100 for v in self.joint_deg]        # deg*100
        t = self.tcp_conv
        vals += [t[i] * 10 for i in range(3)]           # mm → 0.1mm
        vals += [t[i] * 100 for i in range(3, 6)]       # deg*100
        return [to_uint16(clamp_int16(v)) for v in vals]

    # scaled 블록의 단위 문자열 (UI 표기용)
    SCALED_UNITS = ["deg×100"] * 6 + ["0.1mm"] * 3 + ["deg×100"] * 3

    def payload_status(self, alive=1):
        """
        8 워드 : 상태 정보
        마지막 워드는 alive 카운터. 매 갱신마다 1씩 증가하므로 외부에서
        '값이 갱신되고 있는지' 를 판단할 수 있다 (0~65535 순환).
        """
        return [to_uint16(v) for v in (
            self.robot_mode, self.running_state, self.power_on,
            self.protective_stop, self.emergency_stop,
            self.control_method, self.operation_mode, alive)]

    @property
    def scanning(self):
        """스캔 좌표에 값이 들어와 있으면 스캔 중으로 본다."""
        return any(v != 0 for v in self.tcp_scan)

    def pose_by_source(self, source="scan"):
        """
        외부로 내보낼 좌표를 고른다.
          "scan" : 항상 스캔 좌표 (제로점 기준, 스캔 아닐 때는 0)  ← 기본
          "pose" : 항상 현재 TCP (베이스 프레임)
          "auto" : 스캔 중이면 스캔 좌표, 아니면 현재 TCP
        게이팅은 로봇 태스크에서 하므로 보통 "scan" 을 그대로 쓰면 된다.
        """
        if source == "scan":
            return list(self.tcp_scan)
        if source == "pose":
            return list(self.tcp_raw)
        return list(self.tcp_scan) if self.scanning else list(self.tcp_raw)

    # ---------------- 공학 단위 12개 ----------------------------------
    def eng_values(self):
        """관절 6 [deg] + TCP X,Y,Z [mm] + Rx,Ry,Rz [deg]"""
        return list(self.joint_deg) + list(self.tcp_conv)


# ============================================================================
#  외부 전송 데이터 타입
# ============================================================================
# key : (표시 이름, 워드 수, 설명)
OUT_FORMATS = {
    "int16_raw": ("INT16 raw", 12,
                  "관절 mRad, TCP XYZ 0.1mm, R mRad — 로봇 레지스터 원본"),
    "int16_scaled": ("INT16 scaled", 12,
                     "관절 deg×100, TCP XYZ 0.1mm, R deg×100"),
    "int32_scaled": ("INT32 scaled", 24,
                     "관절 deg×1000, TCP XYZ mm×1000, R deg×1000 (2워드/값)"),
    "float32": ("FLOAT32", 24,
                "관절 deg, TCP XYZ mm, R deg — IEEE754 실수 (2워드/값)"),
}
WORD_ORDERS = {
    "hi_lo": "상위워드 먼저 (Big-endian, 표준 Modbus)",
    "lo_hi": "하위워드 먼저 (Word swap, 다수 PLC)",
}


def encode_payload(data: "RobotData", fmt="int16_raw", word_order="hi_lo"):
    """
    선택한 데이터 타입으로 12개 값(관절 6 + TCP 6)을 레지스터 리스트로 변환.
    INT16 계열은 12워드, INT32/FLOAT32 는 24워드를 반환한다.
    """
    if fmt == "int16_raw":
        return data.payload_raw()
    if fmt == "int16_scaled":
        return data.payload_scaled()

    out = []
    for v in data.eng_values():
        if fmt == "int32_scaled":
            raw = struct.pack(">i", clamp_int32(v * 1000))
        elif fmt == "float32":
            raw = struct.pack(">f", float(v))
        else:
            raise ValueError(f"알 수 없는 형식: {fmt}")
        hi, lo = struct.unpack(">HH", raw)
        out += [hi, lo] if word_order == "hi_lo" else [lo, hi]
    return out


def decode_payload(words, fmt="int16_raw", word_order="hi_lo"):
    """
    encode_payload 의 역변환. 외부에서 어떻게 보이는지 확인하거나
    테스트에서 왕복 검증할 때 사용한다.
    """
    if fmt == "int16_raw":
        vals = [to_int16(w) for w in words[:12]]
        joints = [v / 1000.0 * RAD2DEG for v in vals[:6]]
        tcp = [vals[6 + i] / 10.0 for i in range(3)]
        tcp += [vals[9 + i] / 1000.0 * RAD2DEG for i in range(3)]
        return joints + tcp
    if fmt == "int16_scaled":
        vals = [to_int16(w) for w in words[:12]]
        return [v / 100.0 for v in vals[:6]] + \
               [vals[6 + i] / 10.0 for i in range(3)] + \
               [vals[9 + i] / 100.0 for i in range(3)]

    out = []
    for i in range(0, 24, 2):
        a, b = words[i], words[i + 1]
        hi, lo = (a, b) if word_order == "hi_lo" else (b, a)
        raw = struct.pack(">HH", hi & 0xFFFF, lo & 0xFFFF)
        if fmt == "int32_scaled":
            out.append(struct.unpack(">i", raw)[0] / 1000.0)
        else:
            out.append(struct.unpack(">f", raw)[0])
    return out
