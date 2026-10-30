"""ERUT req/calibrate = 차량(AGV) 캘리브레이션 — 모재 외벽을 따라 한 바퀴.

로봇의 3점 측정은 캘리브레이션이 아니라 구간 검사(start) 안에서 한다.
확인할 것: 모재 지름이 작업 영역 반지름으로 내려가는가, 차량이 둘레만큼
한 바퀴 도는가(로봇은 홈, 아웃트리거는 풀린 채로), 다 돌아야 완료가
나가고 total_length_mm 이 실리는가.
"""

import math

import pytest

from smr_operator_ui.app import OperatorWindow
from smr_operator_ui.services import calibration


def _window(qtbot):
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    window._work_area_extra = {"eoat_w_mm": 0.0, "eoat_h_mm": 0.0, "eoat_type": 0.0}
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
    window.erut.publish_event = lambda name, req, action, status_code=200, error=None, **kw: (
        events.append((name, action, {"status_code": status_code,
                                      **({"error": error} if error else {}), **kw})) or True)
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
    # 벽 바깥 반지름 855 = 바깥 지름 1.71 m 의 절반(두께는 쓰지 않는다).
    window.main_screen.orbit_view.set_target_dimensions(1.71, 6.0)
    window._work_area_extra = {"eoat_w_mm": 0.0, "eoat_h_mm": 0.0, "eoat_type": 0.0}
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
    """ERUT area 는 좌표 그대로 — 차량은 구역 가운데로 간다(2026-10-13).

    차량 위치는 로봇 중심의 x 라, 1160~1760 구역이면 1460 에 서야 로봇이 좌우로
    300 씩 훑어 구역을 덮는다. 겹침은 ERUT 가 좌표에 넣어 보낸다(구역 시작
    0 · 580 · 1160 …). 원주 표시는 (가로 - 겹침) 간격으로 센 구간 번호로 그린다.
    """
    window = _window(qtbot)
    _tap(window)
    window.erut_session._calibrated = True
    window.main_screen.orbit_view.set_target_dimensions(1.69, 6.0)
    targets: list[float] = []
    window.amr.move_to = lambda distance, unit="", label=None: targets.append(distance)

    window.erut_session.handle_request("start", {
        "req_id": "prep-3", "job_id": "jb-3", "surface": "outer",
        "area": {"start": {"x": 1160, "y": 0}, "end": {"x": 1760, "y": 800}},
        "scan": {"pitch": 200, "speed": 100, "overlap": 20}})

    assert targets == [1460.0], "구역 가운데(start.x + 가로/2)"
    view = window.main_screen.orbit_view
    # π × 1690 = 5309 mm 를 580 mm 간격으로 덮으려면 10 구간.
    assert view.section_count() == 10
    assert view._sections[3] == 2              # 지금 3번 구간(0 부터 2)
    assert window.main_screen.segment_label.text() == "03"
    window.close()


def test_vehicle_goes_to_the_same_angle_on_its_own_track(qtbot):
    """차량은 모재에서 떨어진 바깥 궤적을 돈다 — x 에 한 바퀴 배율을 곱한다.

    ERUT 의 x 는 검사면 둘레(π × 지름) 위 거리다. 캘리브레이션 한 바퀴를 실제로
    5840 mm 달렸으면(검사면 5309 mm 의 1.1 배) 구역 1000~1600 의 가운데 1300 은
    차량 1430 mm 다.
    """
    window = _window(qtbot)
    _tap(window)
    window.erut_session._calibrated = True
    window.main_screen.orbit_view.set_target_dimensions(1.69, 6.0)
    window._vehicle_lap_scale = 1.1
    targets: list[float] = []
    window.amr.move_to = lambda distance, unit="", label=None: targets.append(distance)

    window.erut_session.handle_request("start", {
        "req_id": "prep-scale", "job_id": "jb-s", "surface": "outer",
        "area": {"start": {"x": 1000, "y": 0}, "end": {"x": 1600, "y": 800}},
        "scan": {"pitch": 200, "speed": 100, "overlap": 20}})

    assert targets == [pytest.approx(1430.0)]
    window.close()


def test_vehicle_puts_the_robot_centre_on_the_section_centre(qtbot):
    """QR 영점에서 0~150 구역이면 차량은 75 — 로봇이 중심에서 좌우로 75 씩 훑는다.

    이음매를 넘어 가운데가 둘레 밖이면(5250~5450, 둘레 약 5309) 감긴 자리로 간다 —
    한 바퀴를 더 돌지 않는다.
    """
    window = _window(qtbot)
    _tap(window)
    window.erut_session._calibrated = True
    window.main_screen.orbit_view.set_target_dimensions(1.69, 6.0)
    targets: list[float] = []
    window.amr.move_to = lambda distance, unit="", label=None: targets.append(distance)

    window.erut_session.handle_request("start", {
        "req_id": "p-150", "job_id": "jb-150", "surface": "outer",
        "area": {"start": {"x": 0, "y": 0}, "end": {"x": 150, "y": 140}},
        "scan": {"pitch": 200, "speed": 100, "overlap": 20}})
    assert targets == [75.0]

    window.sequencer.stop()
    window.erut_session._forget_work()
    window.erut_session.handle_request("start", {
        "req_id": "p-seam", "job_id": "jb-seam", "surface": "outer",
        "area": {"start": {"x": 5250, "y": 0}, "end": {"x": 5450, "y": 140}},
        "scan": {"pitch": 200, "speed": 100, "overlap": 20}})
    assert targets[-1] == pytest.approx(5350.0 - 3.141592653589793 * 1690.0)
    window.close()


def test_max_area_width_follows_the_700mm_chord(qtbot):
    """지름 1690 (반지름 845) 이면 현 700 mm 가 되는 호는 약 721 mm."""
    window = _window(qtbot)
    window.main_screen.orbit_view.set_target_dimensions(1.69, 6.0)

    assert 715 <= window._max_area_width() <= 725
    # EOAT 를 안 골라도 자른다 — 한계는 팔이 갈 수 있는 거리다.
    assert window._clamp_work_width(1000.0, 845.0, 0.0) <= 725
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


def _start_area(window, req_id, x0, y0):
    """ERUT start — 차량 이동 → 아웃트리거 고정 → 리프트 → 로봇(prepare 는 값만 본다)."""
    window.erut_session.handle_request("start", {
        "req_id": req_id, "job_id": req_id, "surface": "outer",
        "area": {"start": {"x": x0, "y": y0}, "end": {"x": x0 + 600, "y": y0 + 800}},
        "scan": {"pitch": 200, "speed": 100, "overlap": 20}})


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

    _start_area(window, "s-next-col", 580, 0)
    qtbot.waitUntil(lambda: window.sequencer.state.name == "SCANNING", timeout=10000)

    assert moves == [("lift", 0.0), ("outrigger", 0.0), ("amr", 880.0),
                     ("outrigger", 1.0), ("lift", 0.0)]
    window.close()


def test_next_band_up_in_the_same_column_only_moves_the_lift(qtbot):
    """같은 세로 줄의 위 구간(y 780 — ERUT 가 겹침 20 을 넣었다): 리프트만 780 으로."""
    window = _window(qtbot)
    _tap(window)
    window.erut_session._calibrated = True
    window.main_screen.orbit_view.set_target_dimensions(1.69, 6.0)
    window.lift.reset(0.0)
    window.outrigger.reset(1)
    window.amr.reset(300.0)                   # 아래 구간(0~600)을 끝내고 가운데에 서 있다
    moves = _record_moves(window)

    _start_area(window, "s-next-band", 0, 780)
    qtbot.waitUntil(lambda: window.sequencer.state.name == "SCANNING", timeout=10000)

    assert ("outrigger", 0.0) not in moves
    assert moves[-1] == ("lift", 780.0)
    window.close()


def test_section_taller_than_1200mm_is_refused_and_area_limit_carries_it(qtbot):
    """한 구간 세로 최대 1200 mm — 400 + E6003 AREA_HEIGHT, 자기소개에도 싣는다."""
    window = _window(qtbot)
    events = _tap(window)
    responses: list[dict] = []
    window.erut_session.client.publish_res = lambda req, action, status, error=None, **kw: (
        responses.append({"status_code": status, "error": error, **kw}) or True)
    infos: list[dict] = []
    window.erut_session.client.publish_info = lambda content: infos.append(content) or True
    window.erut_session._calibrated = True
    window.main_screen.orbit_view.set_target_dimensions(1.69, 6.0)

    window.erut_session.handle_request("prepare", {
        "req_id": "p-tall", "job_id": "jb-tall", "surface": "outer",
        "area": {"start": {"x": 0, "y": 0}, "end": {"x": 600, "y": 1300}},
        "scan": {"pitch": 200, "speed": 100, "overlap": 20}})

    assert responses[-1]["status_code"] == 400
    assert (responses[-1]["error"]["code"], responses[-1]["error"]["max_height"]) == (
        "E6003", 1200)
    window.erut_session.publish_info()
    assert infos[-1]["area_limit"]["max_height"] == 1200
    assert infos[-1]["area_limit"]["diameter"] == 1690
    assert events == []
    window.close()


def test_vehicle_drives_at_0_3_m_per_s(qtbot):
    window = _window(qtbot)
    assert window.VEHICLE_SPEED_MPS == 0.3
    window.close()


def test_home_within_ms_follows_the_speed_ratio(qtbot):
    """접는 시간은 속도 비율을 따른다 — 100 % 20초, 40 % 50초. 바뀌면 evt/info 를 다시 낸다."""
    window = _window(qtbot)
    infos: list[dict] = []
    window.erut_session.client.publish_info = lambda content: infos.append(content) or True

    assert window._home_within_ms() == 20_000
    window._show_speed_scale(40)

    assert window._home_within_ms() == 50_000
    assert infos[-1]["timings"]["home_within_ms"] == 50_000
    window.close()


def test_scan_rise_limit_per_eoat_comes_from_cobot_settings(qtbot):
    """스캔 상승 높이 제한(5축 · 8축)은 Cobot 설정에서 넣고, 작업에 그대로 쓴다."""
    from smr_operator_ui.services import GridPlan

    window = _window(qtbot)
    cobot = window.screens["cobot"]
    assert cobot.scan_rise_mm(5) == 30.0 and cobot.scan_rise_mm(8) == 167.5
    cobot.field("스캔 상승 높이 제한 8축").setValue(120.0)
    window._work_area_extra = {"eoat_w_mm": 75.0, "eoat_h_mm": 167.5, "eoat_type": 8.0}

    plan = window._fill_from_local_setup(GridPlan(column_count=1, row_count=1,
                                                  cell_width=600.0, cell_height=800.0))

    assert plan.eoat_probes == 8 and plan.eoat_size[1] == 120.0
    assert window._scan_band_mm(plan) == 120.0             # 로봇 258 · 263 으로 가는 값
    window.close()


def test_saving_the_scan_rise_limit_sends_it_to_the_robot_when_idle(qtbot):
    window = _window(qtbot)
    pushed: list[tuple] = []
    window._push_work_area_to_robot = lambda *values: pushed.append(values)
    window._work_area_extra = {"eoat_w_mm": 30.0, "eoat_h_mm": 30.0, "eoat_type": 5.0}
    cobot = window.screens["cobot"]
    cobot.field("스캔 상승 높이 제한 5축").setValue(28.0)

    cobot.save_requested.emit("cobot", cobot.values())

    assert pushed, "저장했는데 로봇에 보내지 않았다"
    # (가로, 세로, 스캐너 밴드, 겹침, 바깥 반지름, eoat_w, eoat_h, eoat_type)
    assert pushed[-1][2] == 28.0 and pushed[-1][6] == 28.0
    window.close()


def test_eoat_choice_in_cobot_settings_reaches_the_robot(qtbot):
    """장착 EOAT(5축 · 8축)는 Cobot 설정에서 고른다 — ERUT 규격에는 칸이 없다.

    고르면 바로 로봇 264 와 유효 커버(262 · 263)·스캐너 밴드(258)로 가고, 다음 작업도
    그 값으로 채운다(_fill_from_local_setup).
    """
    from smr_operator_ui.services import GridPlan

    window = _window(qtbot)
    pushed: list[tuple] = []
    window._push_work_area_to_robot = lambda *values: pushed.append(values)
    cobot = window.screens["cobot"]
    assert cobot.eoat_type() == 0

    cobot.eoat_combo.setCurrentIndex(2)
    cobot.eoat_combo.activated.emit(2)                     # 8축 직사각

    assert window._work_area_extra["eoat_type"] == 8.0
    # (가로, 세로, 스캐너 밴드, 겹침, 바깥 반지름, eoat_w, eoat_h, eoat_type)
    assert pushed and pushed[-1][7] == 8.0
    assert pushed[-1][5] == 75.0 and pushed[-1][6] == 167.5 and pushed[-1][2] == 167.5
    plan = window._fill_from_local_setup(GridPlan(column_count=1, row_count=1,
                                                  cell_width=600.0, cell_height=800.0))
    assert plan.eoat_probes == 8

    cobot.eoat_combo.setCurrentIndex(1)
    cobot.eoat_combo.activated.emit(1)                     # 5축 십자형 — 상승 제한 30
    assert pushed[-1][7] == 5.0 and pushed[-1][6] == 30.0
    window.close()


def test_eoat_choice_is_refused_while_a_job_runs(qtbot):
    window = _window(qtbot)
    pushed: list[tuple] = []
    window._push_work_area_to_robot = lambda *values: pushed.append(values)
    window._job_running = lambda: True
    cobot = window.screens["cobot"]

    cobot.eoat_combo.setCurrentIndex(1)
    cobot.eoat_combo.activated.emit(1)

    assert not pushed and window._work_area_extra["eoat_type"] == 0.0
    assert cobot.eoat_type() == 0, "받지 않았으면 목록도 되돌린다"
    window.close()


def test_saved_eoat_type_shows_in_cobot_settings(qtbot):
    window = _window(qtbot)
    window._push_work_area_to_robot = lambda *values: None
    window._send_work_area(600.0, 800.0, 30.0, 20.0, radius_mm=845.0,
                           eoat_w_mm=30.0, eoat_h_mm=30.0, eoat_type=5.0)
    assert window.screens["cobot"].eoat_type() == 5
    window.close()


def test_prepare_moves_nothing_and_start_runs_vehicle_outrigger_lift_robot(qtbot):
    """2026-10-14: 캘리브레이션 뒤 prepare 는 값만 확인한다 — 차량 · 아웃트리거 ·
    리프트 · 로봇 모두 그대로다. start 를 받으면 그때 차량 이동 → 아웃트리거 고정
    → 리프트 → 로봇 순으로 간다.
    """
    window = _window(qtbot)
    events = _tap(window)
    window.erut_session._calibrated = True
    window.main_screen.orbit_view.set_target_dimensions(1.69, 6.0)
    window.lift.reset(0.0)
    window.outrigger.reset(0)                 # 캘리브레이션 뒤 영점 — 아웃트리거는 풀려 있다
    window.amr.reset(0.0)
    moves = _record_moves(window)

    window.erut_session.handle_request("prepare", {
        "req_id": "p-150", "job_id": "jb-150", "surface": "outer",
        "area": {"start": {"x": 0, "y": 0}, "end": {"x": 150, "y": 140}},
        "scan": {"pitch": 200, "speed": 100, "overlap": 20}})
    qtbot.wait(300)

    assert moves == [], "prepare 에서 장비가 움직였다"
    assert window.sequencer.state.name == "IDLE", "prepare 에서 순회를 시작했다"
    assert [e[0] for e in events] == ["ready"]
    assert window.erut_session.activity_state() == "ready"

    window.erut_session.handle_request("start", {
        "req_id": "s-150", "job_id": "jb-150", "surface": "outer",
        "area": {"start": {"x": 0, "y": 0}, "end": {"x": 150, "y": 140}},
        "scan": {"pitch": 200, "speed": 100, "overlap": 20}})
    # 로봇 단계(스캔)까지 간다.
    qtbot.waitUntil(lambda: window.sequencer.state.name == "SCANNING", timeout=10000)

    assert moves == [("amr", 75.0), ("outrigger", 1.0), ("lift", 0.0)]
    window.close()


def test_scan_pitch_caps_the_line_spacing(qtbot):
    """ERUT scan.pitch(검사 줄 간격)는 줄 간격의 최대값이다(if-0.10 Q-39 가안).

    탐촉자 유효 높이(5축 30)로 정하되 pitch 를 넘지 않게 줄을 늘린다.
    """
    from smr_operator_ui.services import GridPlan

    window = _window(qtbot)
    window._work_area_extra = {"eoat_w_mm": 30.0, "eoat_h_mm": 30.0, "eoat_type": 5.0}
    plan = window._fill_from_local_setup(GridPlan(
        column_count=1, row_count=1, cell_width=140.0, cell_height=140.0,
        line_pitch_max=10.0))
    assert plan.eoat_size[1] == 10.0 and window._scan_band_mm(plan) == 10.0

    wide = window._fill_from_local_setup(GridPlan(
        column_count=1, row_count=1, cell_width=140.0, cell_height=140.0,
        line_pitch_max=200.0))
    assert wide.eoat_size[1] == 30.0               # 탐촉자 커버보다 크면 커버대로
    window.close()


def test_scan_errors_use_the_standard_probe_codes() -> None:
    """스캔 중 접촉을 잃음 E3002 · 마킹 자리 접촉 실패 E3003 (if-0.10 Q-33)."""
    messages = OperatorWindow._PROBE_ERROR_MESSAGES
    assert messages[5][:2] == ("E3003", "MARKER_CONTACT_FAILED")
    assert messages[6][:2] == ("E3002", "PROBE_CONTACT_LOST")
    assert messages[5][3]["probe"] == messages[6][3]["probe"] == "center"


def test_short_press_loss_is_ignored_but_a_long_one_raises_e3002(qtbot):
    """스캔 중 눌림 센서가 잠깐 떨어지면 로봇이 다시 눌러 잡는다 — 장애가 아니다.

    1초(PRESS_LOST_MS) 넘게 계속 떨어져 있을 때만 E3002 PROBE_CONTACT_LOST 를 낸다
    (2026-10-19). 288 은 scan_arc(286~289)의 3번째 값이다.
    """
    window = _window(qtbot)
    window.PRESS_LOST_MS = 150
    raised: list[dict] = []
    window.erut_session.raise_error = lambda fields: raised.append(dict(fields))

    window.ros_status.scan_arc_changed.emit([100, 0, 2, 0])      # 떨어짐
    qtbot.wait(60)
    window.ros_status.scan_arc_changed.emit([120, 0, 1, 0])      # 다시 붙음
    qtbot.wait(250)
    assert raised == [], "잠깐 떨어진 것은 장애가 아니다"

    window.ros_status.scan_arc_changed.emit([140, 0, 2, 0])      # 다시 떨어져
    qtbot.waitUntil(lambda: bool(raised), timeout=2000)          # 오래 이어지면
    error = raised[-1]
    assert (error["code"], error["name"], error["level"]) == ("E3002", "PROBE_CONTACT_LOST", "stop")
    assert error["probe"] == "center"
    qtbot.wait(300)
    assert len(raised) == 1, "떨어진 채로 있으면 한 번만 낸다"
    window.close()
