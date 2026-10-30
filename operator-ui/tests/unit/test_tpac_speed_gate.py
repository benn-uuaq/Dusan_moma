"""TPAC 속도 자리(410~415)는 스캔 구간(290 = 6)에서만 값이 나간다.

pose 자리(400)에 제로점 기준 스캔 좌표를 내는데, 속도만 홈 -> 대기 위치 이동을
그대로 내보내 TPAC 에 음수 값이 잠깐 떴다가 사라졌다(2026-10-27 전시회).
"""

from smr_operator_ui.services.tpac_bridge.bridge_core import MIRROR_UR, ExternalServer
from smr_operator_ui.services.tpac_bridge.robot_map import RobotData

SPEED = MIRROR_UR["speed"]


class FakeBank:
    def __init__(self):
        self.hr = {}

    def set_hr(self, addr, words):
        self.hr[addr] = list(words)
        return True

    def stop(self):
        pass


def _data(state, scan=(0, 0, 0, 0, 0, 0)):
    d = RobotData()
    d.ok = True
    d.scan_state = state
    d.tcp_scan = list(scan)
    d.tcp_spd_real = [-150, 80, -20, 5, -3, 1]     # 로봇이 움직이는 중
    return d


def _server():
    server = ExternalServer()
    server.bridge = FakeBank()
    return server


def test_speed_is_zero_outside_the_scan_phase() -> None:
    server = _server()
    for state in (0, 2, 3, 5, 7, 8, 14):           # 홈·대기·3점·원점·끝·후퇴
        assert server.speed_live(_data(state)) is False
    assert server.speed_live(_data(6)) is True


def test_estimated_speed_does_not_leak_when_scan_pose_drops_to_zero() -> None:
    """새 작업 시작: 지난 작업의 마지막 좌표(멈춰 둔 값)가 0 으로 떨어지는 순간도 0 이다."""
    server = _server()
    server._write_mirror(_data(5, scan=(0, 1998, 1600, 0, 0, 0)))
    server._write_mirror(_data(0))
    assert server.bridge.hr[SPEED] == [0] * 6


def test_speed_follows_pose_source() -> None:
    """실제 TCP 를 내는 설정(pose · auto)이면 속도도 늘 낸다 — 좌표와 짝이 맞는다."""
    server = _server()
    server.pose_source = "pose"
    assert server.speed_live(_data(0)) is True


def test_screen_writes_real_speed_only_while_scanning(qtbot) -> None:
    from smr_operator_ui.app import OperatorWindow

    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    tpac = window.screens["tpac_bridge"]
    tpac.server.bridge = FakeBank()

    tpac._write_real_speed(_data(3))                # 홈 -> 대기 위치 이동 중
    assert tpac.server.bridge.hr[SPEED] == [0] * 6
    tpac._write_real_speed(_data(6))                # 스캔 중
    assert tpac.server.bridge.hr[SPEED] == [65536 - 150, 80, 65536 - 20, 5, 65536 - 3, 1]
    window.close()
