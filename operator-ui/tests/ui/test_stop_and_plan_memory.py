"""정지 · 초기화 뒤 TPAC 스캔 좌표 0 · ERUT 작업 계획 유지 (2026-11-12)."""

import copy

from smr_operator_ui.app import OperatorWindow
from smr_operator_ui.services.tpac_bridge.robot_map import RobotData

SCAN = {"pitch_left": 25, "pitch_right": 35, "speed_mm_s": 40, "speed_pct": 40, "overlap": 10}
JOB = {"job_id": "jb-mem", "surface": "outer", "diameter": 1300, "height": 800,
       "area": {"start": {"x": 0, "y": 0}, "end": {"x": 300, "y": 200}}, "scan": SCAN}


def _window(qtbot, monkeypatch):
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    sent: list = []
    monkeypatch.setattr(window.ros_status, "send_value",
                        lambda name, value: sent.append((name, int(value))) or True)
    monkeypatch.setattr(window.ros_status, "writable", lambda name: True)
    monkeypatch.setattr(window.ros_status, "send_pose", lambda *a: True)
    monkeypatch.setattr(window.ros_status, "call_command", lambda *a: True)
    window.erut.publish_event = lambda *a, **k: True
    window.erut.publish_res = lambda *a, **k: True
    window.erut_session._calibrated = True
    window._robot_link_up = True
    window.main_screen.orbit_view.set_target_dimensions(1.3, 0.8)
    return window, sent


def _scanning_data(state=6):
    data = RobotData()
    data.tcp_scan = [1200, 3500, 400, 0, 0, 0]
    data.arc_pos = 350.0
    data.arc_total = 1050.0
    data.scan_state = state
    return data


# ---- TPAC 스캔 좌표 --------------------------------------------------------------
def test_hold_zeroes_scan_values_until_the_robot_starts_again(qtbot) -> None:
    from smr_operator_ui.screens.tpac_bridge_screen import TpacBridgeScreen
    screen = TpacBridgeScreen()
    qtbot.addWidget(screen)
    frozen = _scanning_data()
    screen._on_data(frozen)
    screen.hold_scan_zero("ERUT 정지")

    masked = screen._masked(copy.copy(frozen))
    assert masked.tcp_scan == [0] * 6 and masked.arc_pos == 0 and masked.arc_total == 0
    assert frozen.tcp_scan[1] == 3500, "원본은 건드리지 않는다"
    assert masked.tcp_raw == frozen.tcp_raw, "스캔 좌표만 비운다"

    # 다음 작업 — dus6_init 이 290 = 0 을 쓰면 놓는다.
    restarted = _scanning_data(state=0)
    assert screen._masked(restarted) is restarted and not screen.scan_zero_held


def test_erut_abort_and_reset_hold_the_tpac_values(qtbot, monkeypatch) -> None:
    window, _sent = _window(qtbot, monkeypatch)
    tpac = window.screens["tpac_bridge"]
    window.erut_session.handle_request("abort", {"req_id": "a-1"})
    assert tpac.scan_zero_held
    tpac.release_scan_zero()
    window.erut_session.handle_request("reset", {"req_id": "r-1"})
    assert tpac.scan_zero_held
    window.close()


def test_normal_completion_keeps_the_last_values(qtbot, monkeypatch) -> None:
    """정상 완료 뒤에는 마지막 값을 일부러 남긴다(TPAC 기록에 0 이 섞이지 않게)."""
    window, _sent = _window(qtbot, monkeypatch)
    window._finish_job()
    assert not window.screens["tpac_bridge"].scan_zero_held
    window.close()


# ---- ERUT 작업 계획 유지 ------------------------------------------------------------
def test_local_start_keeps_the_last_erut_plan(qtbot, monkeypatch) -> None:
    window, sent = _window(qtbot, monkeypatch)
    window.erut_session.handle_request("prepare", {"req_id": "p-mem", **JOB})
    # 구역 값이 작업 영역으로 들어온다(시퀀서가 첫 구간에서 보내는 것과 같다).
    window._send_work_area(300.0, 200.0, 30.0, 10.0, pitch_left=25.0, pitch_right=35.0)
    window.erut_session._forget_work()          # ERUT 작업이 끝났다
    sent.clear()

    started = []
    monkeypatch.setattr(window, "_begin_grid_job",
                        lambda grid, scan_h, source="RCS", job_id="": started.append(grid))
    window._start_inspection()

    grid = started[-1]
    assert (grid.cell_width, grid.cell_height, grid.pitch_y) == (300.0, 200.0, 10.0)
    assert (grid.pitch_left, grid.pitch_right) == (25.0, 35.0), "ERUT 줄 상승이 사라졌다"
    assert window._scan_ratio_now() == (40, "ERUT 마지막")
    assert window.main_screen.orbit_view.target_dimensions() == (1.3, 0.8)
    window.close()


def test_user_input_replaces_the_erut_values(qtbot, monkeypatch) -> None:
    window, _sent = _window(qtbot, monkeypatch)
    window.erut_session.speed_requested.emit(40)
    window._send_work_area(300.0, 200.0, 30.0, 10.0, pitch_left=25.0, pitch_right=35.0)

    window.main_screen.speed_changed.emit(70)                       # 사람이 속도를 바꿨다
    window.main_screen.work_area_changed.emit(300.0, 200.0, 30.0, 10.0, 0.0, 0.0)   # 자동으로

    assert window._scan_ratio_now() == (70, "속도 바")
    assert window._side_pitch is None
    window.close()


def test_plan_survives_a_restart(qtbot, monkeypatch) -> None:
    window, _sent = _window(qtbot, monkeypatch)
    window._apply_stored_settings("scan_speed", {"ratio": 35, "source": "ERUT"})
    window._apply_stored_settings("work_area", {
        "width_mm": 300, "height_mm": 200, "scan_h_mm": 30, "overlap_mm": 10,
        "eoat_w_mm": 0, "eoat_h_mm": 0, "eoat_type": 0,
        "pitch_left_mm": 25.0, "pitch_right_mm": 35.0})
    assert window._scan_ratio_now() == (35, "ERUT 마지막")
    assert window._side_pitch == (25.0, 35.0)
    window.close()
