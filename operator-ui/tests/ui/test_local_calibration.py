"""주요 제어 「계측 시작」 — 캘리브레이션(차량 한 바퀴)만 RCS 에서 따로 한다 (2026-11-12)."""

from smr_operator_ui.app import OperatorWindow


def _window(qtbot):
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    events: list[tuple] = []
    window.erut.publish_event = lambda name, req, action, status_code=200, error=None, **kw: (
        events.append((name, action, status_code)) or True)
    responses: list[dict] = []
    window.erut.publish_res = lambda req_id, action, status_code=200, error=None, **kw: (
        responses.append({"action": action, "status_code": status_code, "error": error}) or True)
    window._start_robot_scan = lambda: None
    window.outrigger.reset(0)
    window.main_screen.orbit_view.set_target_dimensions(1.3, 0.8)
    return window, events, responses


def test_measure_button_sits_under_the_start_button(qtbot) -> None:
    window, *_ = _window(qtbot)
    rail = window.main_screen.start_button.parentWidget().layout()
    order = [rail.itemAt(i).widget() for i in range(rail.count())]
    assert order.index(window.main_screen.measure_button) == order.index(
        window.main_screen.start_button) + 1
    assert window.main_screen.measure_button.text() == "계측 시작"
    window.close()


def test_measure_runs_only_the_calibration_lap(qtbot) -> None:
    window, events, responses = _window(qtbot)
    targets: list[float] = []
    original = window.amr.move_to

    def record(target, unit="", label=None, timeout_ms=None):
        targets.append(float(target))
        return original(target, unit, label=label) if label is not None else original(target, unit)
    window.amr.move_to = record

    window.main_screen.measure_button.click()
    # ERUT 에는 calibrating — 끝날 때까지 이동 명령은 받지 않는다.
    assert window.erut_session.activity_state() == "calibrating"
    window.erut_session.handle_request("calibrate", {"req_id": "c-busy", "geometry": {"shape": "cylinder", "diameter": 1300, "height": 800}})
    assert responses[-1]["status_code"] == 409

    qtbot.waitUntil(lambda: not window.erut_session.local_calibrating, timeout=15000)
    assert targets, "차량이 한 바퀴 돌지 않았다"
    assert "계측 완료" in window.main_screen.activity_label.text()
    # ERUT 가 시킨 일이 아니다 — 결과를 내지 않고 원점(calibrated)도 그대로.
    assert not any(name == "complete" and action == "calibrate" for name, action, _ in events)
    assert window.erut_session._calibrated is False
    assert window.erut_session.activity_state() == "idle"
    # 캘리브레이션 최대 시간도 이때 계산해 넣는다.
    assert window.screens["vehicle"].calibration_time_s() > 0
    window.close()


def test_stop_ends_the_measurement(qtbot) -> None:
    window, *_ = _window(qtbot)
    window.main_screen.measure_button.click()
    assert window.erut_session.local_calibrating
    window.main_screen.stop_button.click()
    assert not window.erut_session.local_calibrating
    assert not window.amr.moving
    assert window.erut_session.activity_state() == "idle"
    window.close()


def test_measure_is_refused_during_other_work(qtbot) -> None:
    window, *_ = _window(qtbot)
    window.erut_session._prepare_req_id = "p-1"           # ERUT 작업이 걸려 있다
    window.main_screen.measure_button.click()
    assert not window.erut_session.local_calibrating
    assert "계측 시작 거절" in window.main_screen.activity_label.text()
    window.close()


# ---- 일시정지 · 정지는 캘리브레이션 중 차량, 검사 중 로봇 팔 (2026-11-12) -----------------------
def test_measure_and_start_buttons_are_the_same_size(qtbot) -> None:
    from smr_operator_ui.styles import load_stylesheet
    qss = load_stylesheet()
    import re
    primary = re.search(r"QPushButton#PrimaryButton \{([^}]*)\}", qss).group(1)
    measure = re.search(r"QPushButton#MeasureButton \{([^}]*)\}", qss).group(1)
    for prop in ("min-height", "font-size"):
        pick = lambda block: re.search(prop + r":\s*([^;]+);", block).group(1)
        assert pick(primary) == pick(measure), prop


def test_pause_and_stop_drive_the_vehicle_during_measurement(qtbot) -> None:
    window, *_ = _window(qtbot)
    robot: list[str] = []
    window.ros_status.call_command = lambda name: robot.append(name) or True
    pause = window.main_screen.pause_button
    window.main_screen.measure_button.click()
    assert pause.isEnabled() and pause.text() == "일시정지"

    pause.click()                                   # 차량을 세운다 — 로봇 명령은 없다
    assert not window.amr.moving and pause.text() == "재개"
    assert window.erut_session.local_calibrating
    assert "pause" not in robot

    pause.click()                                   # 남은 거리를 이어 간다
    assert pause.text() == "일시정지"
    window.main_screen.stop_button.click()          # 차량을 세우고 계측을 접는다
    assert not window.erut_session.local_calibrating and not window.amr.moving
    assert not pause.isEnabled()
    assert "pause" not in robot
    window.close()


def test_rcs_pause_of_an_erut_calibration_shows_paused_to_erut(qtbot) -> None:
    window, events, _responses = _window(qtbot)
    window.erut_session._calibrated = False
    window.erut_session.handle_request("calibrate", {"req_id": "cal-p", "geometry": {"shape": "cylinder", "diameter": 1300,
                                                     "height": 800}})
    pause = window.main_screen.pause_button
    assert pause.isEnabled()
    pause.click()
    assert window.erut_session.calibration_paused and not window.amr.moving
    assert window.erut_session.activity_state() == "paused"
    # ERUT 의 resume 로도 잇는다.
    window.erut_session.handle_request("resume", {"req_id": "res-1"})
    assert not window.erut_session.calibration_paused
    assert pause.text() == "일시정지"
    window.close()

