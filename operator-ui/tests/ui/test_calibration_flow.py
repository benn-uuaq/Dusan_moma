"""ERUT req/calibrate = 차량(AGV) 캘리브레이션 — 모재 외벽을 따라 한 바퀴.

로봇의 3점 측정은 캘리브레이션이 아니라 구간 검사(start) 안에서 한다.
확인할 것: 모재 지름이 작업 영역 반지름으로 내려가는가, 차량이 둘레만큼
한 바퀴 도는가(로봇은 홈, 아웃트리거는 풀린 채로), 다 돌아야 완료가
나가고 total_length_mm 이 실리는가.
"""

import math

from smr_operator_ui.app import OperatorWindow
from smr_operator_ui.services import calibration


def _window(qtbot):
    window = OperatorWindow(start_mqtt=False, start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    window._work_area_extra = {"radius_mm": 0.0, "thickness_mm": 10.0,
                               "eoat_w_mm": 0.0, "eoat_h_mm": 0.0, "eoat_type": 0.0}
    return window


def _arc_poses(radius_mm):
    """반지름 위의 세 접촉점을 레지스터 한 벌로."""
    values = [0] * calibration.POSE_LENGTH
    for base, angle in zip((calibration.CENTER, calibration.RIGHT, calibration.ORIGIN),
                           (90, 66, 114)):
        rad = math.radians(angle)
        values[base:base + 6] = [round(radius_mm * math.cos(rad) * 10),
                                 round(radius_mm * math.sin(rad) * 10), 0, 0, 0, 0]
    return values


def _scan_state(state, zero_ok=1, probe_error=0):
    values = [0] * 10
    values[0] = state
    values[4] = zero_ok
    values[9] = probe_error
    return values


def _tap(window):
    events: list[tuple] = []
    window.erut.publish_event = lambda name, req, action, **kw: (
        events.append((name, action, kw)) or True)
    window.erut.publish_res = lambda *a, **kw: True
    window._push_work_area_to_robot = lambda *values: None
    return events


def test_calibration_drives_the_vehicle_once_around(qtbot):
    window = _window(qtbot)
    events = _tap(window)
    robot: list[str] = []
    window._start_robot_scan = lambda: robot.append("play")
    sent: list[list] = []
    window._push_work_area_to_robot = lambda *values: sent.append(list(values))
    window.outrigger.reset(1)                  # 고정돼 있던 차량

    window.erut_session.handle_request(
        "calibrate", {"req_id": "cal-1", "diameter": 1690, "height": 6000})
    assert events == [], "한 바퀴 다 돌기 전에는 완료를 내지 않는다"

    qtbot.waitUntil(lambda: bool(events), timeout=10000)
    name, action, payload = events[-1]
    assert (name, action) == ("complete", "calibrate")
    assert payload["origin"] == {"x": 0, "y": 0}
    # 계획 둘레 π × 1690 ≈ 5309 mm 를 한 바퀴 돌았다.
    assert abs(payload["total_length_mm"] - math.pi * 1690) <= 1
    assert payload["calibration_error_mm"] < 1.0
    # 모재 지름의 절반이 벽 반지름으로 내려간다(로봇이 start 에서 쓴다).
    assert sent and sent[0][4] == 845.0
    # 차량이 달려야 하므로 아웃트리거는 풀고, 로봇은 움직이지 않는다.
    assert window.outrigger.position == 0
    assert robot == []
    # 다 돌면 원점으로 돌아온 것 — 차량 위치를 0 으로 맞춘다.
    assert window.amr.position == 0.0
    window.close()


def test_calibration_waits_for_the_robot_to_be_home(qtbot):
    """차량이 움직이므로 로봇이 홈이 아니면 먼저 홈으로 보낸다(if-0.5)."""
    window = _window(qtbot)
    _tap(window)
    homes: list[int] = []
    window._send_home = lambda: homes.append(1)
    window.outrigger.reset(0)
    window._on_robot_task_state(3)
    window._on_robot_at_home(True)
    window._on_robot_at_home(False)            # 팔이 나와 있다

    window.erut_session.handle_request(
        "calibrate", {"req_id": "cal-2", "diameter": 1690, "height": 6000})

    assert homes == [1]
    assert not window.amr.moving, "홈에 닿기 전엔 차량을 움직이지 않는다"
    window._on_robot_at_home(True)
    assert window.amr.moving
    window.close()


def test_pause_stops_the_lap_and_resume_finishes_it(qtbot):
    window = _window(qtbot)
    events = _tap(window)
    window.outrigger.reset(0)
    window.erut_session.handle_request(
        "calibrate", {"req_id": "cal-3", "diameter": 1000, "height": 6000})
    assert window.amr.moving

    window.erut_session.handle_request("pause", {"req_id": "pz"})
    assert not window.amr.moving
    window.erut_session.handle_request("resume", {"req_id": "rz"})
    assert window.amr.moving

    qtbot.waitUntil(lambda: bool(events), timeout=10000)
    name, action, payload = events[-1]
    assert (name, action) == ("complete", "calibrate")
    assert abs(payload["total_length_mm"] - math.pi * 1000) <= 1
    window.close()


def test_abort_during_the_lap_sends_no_complete(qtbot):
    window = _window(qtbot)
    events = _tap(window)
    window.outrigger.reset(0)
    window.erut_session.handle_request(
        "calibrate", {"req_id": "cal-4", "diameter": 1000, "height": 6000})
    window.erut_session.handle_request("abort", {"req_id": "ab"})

    qtbot.wait(1500)
    assert [e for e in events if e[1] == "calibrate"] == []
    window.close()


def test_three_point_fit_is_logged_when_the_robot_reaches_the_start(qtbot):
    """로봇 3점 측정은 start 안에서 한다 — 시작점에 서면 잰 벽을 알림에 남긴다."""
    window = _window(qtbot)
    _tap(window)
    window._work_area_extra = {"radius_mm": 845.0, "thickness_mm": 10.0,
                               "eoat_w_mm": 0.0, "eoat_h_mm": 0.0, "eoat_type": 0.0}
    said: list[str] = []
    window.main_screen.activity_shown.connect(said.append)
    window.erut_session._start_req_id = "s1"
    window._nosensor = True

    window.ros_status.probe_poses_changed.emit(_arc_poses(855.0))
    window.ros_status.probe_result_changed.emit([0, 0, 0, 0, 3, 0])
    window.ros_status.scan_state_changed.emit(_scan_state(7))

    assert any(text.startswith("3점 측정 — 잰 벽 반지름") for text in said), said
    window.close()


def test_prepared_area_is_placed_on_the_whole_circumference(qtbot):
    """ERUT area 는 모재 둘레를 가로 길이로 자른 자리에 그리고, 차량은 겹침만큼 덜 간다."""
    window = _window(qtbot)
    _tap(window)
    window.erut_session._calibrated = True
    window.main_screen.orbit_view.set_target_dimensions(1.69, 6.0)
    targets: list[float] = []
    window.amr.move_to = lambda distance, unit="", label=None: targets.append(distance)

    window.erut_session.handle_request("prepare", {
        "req_id": "prep-3", "job_id": "jb-3", "surface": "outer",
        "area": {"start": {"x": 1200, "y": 0}, "end": {"x": 1800, "y": 800}},
        "scan": {"pitch": 20, "speed": 100}})

    assert targets == [1160.0], "3번 구간 = 2 × (600 - 20)"
    view = window.main_screen.orbit_view
    # π × 1690 = 5309 mm 를 580 mm 간격으로 덮으려면 10 구간.
    assert view.section_count() == 10
    assert view._sections[3] == 2              # 지금 3번 구간(0 부터 2)
    assert window.main_screen.segment_label.text() == "03"
    window.close()


def test_max_area_width_follows_the_700mm_chord(qtbot):
    """지름 1690 (반지름 845) 이면 현 700 mm 가 되는 호는 약 721 mm."""
    window = _window(qtbot)
    window.main_screen.orbit_view.set_target_dimensions(1.69, 6.0)
    window._work_area_extra["radius_mm"] = 0.0
    window._work_area_extra["thickness_mm"] = 0.0

    assert 715 <= window._max_area_width() <= 725
    # EOAT 를 안 골라도 자른다 — 한계는 팔이 갈 수 있는 거리다.
    assert window._clamp_work_width(1000.0, 845.0, 0.0, 0.0) <= 725
    window.close()


def _record_moves(window) -> list[tuple]:
    """차량·리프트·아웃트리거에 내린 이동을 순서대로 적는다(더미는 그대로 움직인다)."""
    moves: list[tuple] = []
    for name in ("amr", "lift", "outrigger"):
        motion = getattr(window, name)
        original = motion.move_to

        def wrapped(target, unit="", label=None, _name=name, _orig=original):
            moves.append((_name, float(target)))
            return _orig(target, unit, label=label) if label is not None else _orig(target, unit)
        motion.move_to = wrapped
    return moves


def _prepare_area(window, req_id, x0, y0):
    window.erut_session.handle_request("prepare", {
        "req_id": req_id, "job_id": req_id, "surface": "outer",
        "area": {"start": {"x": x0, "y": y0}, "end": {"x": x0 + 600, "y": y0 + 800}},
        "scan": {"pitch": 20, "speed": 100}})


def test_next_column_lowers_the_lift_and_frees_the_outrigger_before_driving(qtbot):
    """옆 세로 줄로: 리프트 0 → 아웃트리거 해제 → 주행 → 고정 → 리프트 그 높이."""
    window = _window(qtbot)
    _tap(window)
    window.erut_session._calibrated = True
    window.main_screen.orbit_view.set_target_dimensions(1.69, 6.0)
    window.lift.reset(780.0)                  # 위 구간을 끝내고 리프트가 올라가 있다
    window.outrigger.reset(1)
    window.amr.reset(0.0)
    moves = _record_moves(window)

    _prepare_area(window, "p-next-col", 600, 0)
    qtbot.waitUntil(lambda: window.sequencer.state.name == "READY", timeout=10000)

    assert moves == [("lift", 0.0), ("outrigger", 0.0), ("amr", 580.0),
                     ("outrigger", 1.0), ("lift", 0.0)]
    window.close()


def test_next_band_up_in_the_same_column_only_moves_the_lift(qtbot):
    """같은 세로 줄의 위 구간: 차량은 그대로, 리프트만 780 으로(겹침 20)."""
    window = _window(qtbot)
    _tap(window)
    window.erut_session._calibrated = True
    window.main_screen.orbit_view.set_target_dimensions(1.69, 6.0)
    window.lift.reset(0.0)
    window.outrigger.reset(1)
    window.amr.reset(0.0)
    moves = _record_moves(window)

    _prepare_area(window, "p-next-band", 0, 800)
    qtbot.waitUntil(lambda: window.sequencer.state.name == "READY", timeout=10000)

    assert ("outrigger", 0.0) not in moves
    assert moves[-1] == ("lift", 780.0)
    window.close()
