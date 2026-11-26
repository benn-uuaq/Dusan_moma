"""TPAC 브리지 주소 매핑 — ELITE(로봇 자기 주소 그대로) / UR(관절 270 · pose 400 · 속도 410 · offset 420).

TPAC 이 ELITE 협동로봇에 맞춘 판을 새로 만들었다(2026-11-25). ELITE 는 속도가 400 이라 pose 를 400 에
두면 안 된다 — ELITE 매핑은 pose 384 · 속도 400 에 내고, 상태 · 관절은 ELITE 배치가 63~110 · 500 에 낸다.
"""

import socket

from smr_operator_ui.services.tpac_bridge.bridge_core import (
    MAPPING_ELITE, MAPPING_UR, MIRROR_ELITE, MIRROR_UR, ExternalServer,
)
from smr_operator_ui.services.tpac_bridge.robot_map import RobotData

TCP = [5000, -1200, 3300, 3141, -10, 20]        # 로봇 384~389 베이스 TCP
SPEED = [-150, 80, -20, 5, -3, 1]               # 로봇 400~405 실제 TCP 속도
JOINTS = [100, -200, 300, 400, 500, 600]        # 로봇 73~78


class FakeBank:
    def __init__(self):
        self.hr = {}

    def set_hr(self, addr, words):
        self.hr[addr] = list(words)
        return True

    def set_coils(self, addr, bits):
        return True

    def stop(self):
        pass

    @property
    def running(self):
        return True


def _u(values):
    return [v & 0xFFFF for v in values]


def _robot_data(state=0):
    regs = {277: 1, 290: state, 500: 1, 63: 2, 64: 14, 65: 3, 66: 7, 67: 1, 72: 0xFFFF}
    regs.update({384 + i: v & 0xFFFF for i, v in enumerate(TCP)})
    regs.update({400 + i: v & 0xFFFF for i, v in enumerate(SPEED)})
    regs.update({73 + i: v & 0xFFFF for i, v in enumerate(JOINTS)})
    regs.update({306: 120, 307: 80})
    return RobotData.from_registers(regs)


def _elite_server():
    server = ExternalServer()
    server.bridge = FakeBank()
    server.mapping = MAPPING_ELITE
    server.mirror_map.update({"pose": MIRROR_ELITE["pose"], "speed": MIRROR_ELITE["speed"]})
    server.pose_source = "pose"
    server.elite_status = True
    return server


def test_elite_mapping_uses_elite_robot_addresses() -> None:
    server = _elite_server()
    server.update(_robot_data())
    hr = server.bridge.hr
    assert hr[384] == _u(TCP)                       # pose = 로봇 384 그대로
    assert hr[400] == _u(SPEED)                     # 속도 = 로봇 400 그대로 (pose 가 400 을 덮지 않는다)
    assert hr[63] == [2, 14, 3, 7, 1, 0, 0, 0, 0, 0xFFFF] + _u(JOINTS)     # 버전 · 모드 · 전원 … · 관절
    assert hr[306] == [120, 80] and hr[500] == [1]
    # UR 자리(관절 270 · 속도 410 · offset 420)와 UR 상태 자리는 내지 않는다
    for addr in (MIRROR_UR["joint"], MIRROR_UR["speed"], MIRROR_UR["offset"], 256, 260, 450):
        assert addr not in hr


def test_elite_speed_follows_scan_gate_when_pose_is_scan_value() -> None:
    server = _elite_server()
    server.pose_source = "scan"
    server.update(_robot_data(state=3))             # 스캔 아닐 때
    assert server.bridge.hr[400] == [0] * 6
    server.update(_robot_data(state=6))             # 스캔 중
    assert server.bridge.hr[400] == _u(SPEED)


def test_ur_mapping_is_unchanged() -> None:
    server = ExternalServer()
    server.bridge = FakeBank()
    server.update(_robot_data())
    hr = server.bridge.hr
    assert hr[MIRROR_UR["joint"]] == _u(JOINTS)
    assert MIRROR_UR["pose"] in hr and MIRROR_UR["speed"] in hr and MIRROR_UR["offset"] in hr
    assert 384 not in hr and 63 not in hr


def test_covers_follow_mapping_and_extra_ranges() -> None:
    server = _elite_server()
    server.extra_ranges = [(280, 285, "작업 평면"), (286, 286, "Cscan"), (287, 287, "Odometer")]
    assert server.covers(384, 6) == "ELITE pose"
    assert server.covers(400, 6) == "ELITE 속도"
    assert server.covers(280, 6) == "작업 평면" and server.covers(287, 1) == "Odometer"
    assert server.covers(66, 4) == "ELITE 버전·상태·관절" and server.covers(500, 1) == "ELITE 운전 상태"
    assert server.covers(MIRROR_UR["joint"], 6) is None
    server.mapping = MAPPING_UR
    assert server.covers(MIRROR_UR["joint"], 6) == "관절 재현"


def test_unknown_mapping_falls_back_to_ur() -> None:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = ExternalServer()
    assert server.start(host="127.0.0.1", port=port, mapping="robotX", scan_signals=False)
    try:
        assert server.mapping == MAPPING_UR and server.elite_status is False
    finally:
        server.stop()
