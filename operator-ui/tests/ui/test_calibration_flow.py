"""ERUT req/calibrate → 로봇 3점 측정 → evt/complete 까지.

로봇이 실제로 도는 대신, 제어 노드가 주는 레지스터 값을 시그널로 흘려
넣는다. 확인할 것은 세 가지다 — 모재 지름이 작업 영역(반지름)으로
내려가는가, 측정이 끝난 순간에만 완료가 나가는가, 접촉점으로 낸 오차가
ERUT 규격 자리(calibration_error_mm)에 실리는가.
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


def test_calibration_runs_the_robot_and_reports_the_measured_error(qtbot):
    window = _window(qtbot)
    events: list[tuple] = []
    window.erut.publish_event = lambda name, req, action, **kw: (
        events.append((name, action, kw)) or True)
    window.erut.publish_res = lambda *a, **kw: True
    started: list[str] = []
    window._start_robot_scan = lambda: started.append("play")
    window._stop_robot_scan = lambda: started.append("stop")
    sent: list[list] = []
    window._push_work_area_to_robot = lambda *values: sent.append(list(values))

    # 로봇이 벽을 누르는 동안 차량이 굳어 있어야 한다 — 아웃트리거 고정이
    # 끝난 뒤에 로봇이 돈다(안전 순서 1단계).
    with qtbot.waitSignal(window.outrigger.arrived, timeout=5000):
        window.erut_session.handle_request(
            "calibrate", {"req_id": "cal-1", "diameter": 1690, "height": 6000})

    # 모재 지름의 절반이 벽 반지름으로 내려가야 한다.
    assert started == ["play"]
    assert window.outrigger.position >= 1
    assert sent and sent[0][4] == 845.0
    assert events == []

    # 로봇이 아직 측정 중 — 완료가 나가면 안 된다.
    window.ros_status.scan_state_changed.emit(_scan_state(2, zero_ok=0))
    assert events == []

    window.ros_status.probe_poses_changed.emit(_arc_poses(855.0))
    window.ros_status.probe_result_changed.emit([0, 0, 0, 0, 3, 3])
    window.ros_status.scan_state_changed.emit(_scan_state(7))

    name, action, payload = events[-1]
    assert (name, action) == ("complete", "calibrate")
    assert payload["origin"] == {"x": 0, "y": 0}
    # 입력 반지름 845 + 두께 10 = 855 로 쟀으니 오차는 1 mm 안쪽이다.
    assert payload["calibration_error_mm"] < 1.0
    # 측정이 끝나면 로봇을 원점에 세워 두지 않는다.
    assert started == ["play", "stop"]
    window.close()


def test_failed_contact_is_reported_as_a_failure(qtbot):
    window = _window(qtbot)
    events: list[tuple] = []
    window.erut.publish_event = lambda name, req, action, **kw: (
        events.append((name, action, kw)) or True)
    window.erut.publish_res = lambda *a, **kw: True
    window._start_robot_scan = lambda: None
    window._stop_robot_scan = lambda: None
    window._push_work_area_to_robot = lambda *values: None

    window.outrigger.reset(1)          # 이미 고정돼 있는 상태에서 시작한다
    window.erut_session.handle_request(
        "calibrate", {"req_id": "cal-2", "diameter": 1690, "height": 6000})
    # 로봇이 벽을 못 찾아 멈췄다(상태 9 · probe_error 1).
    window.ros_status.scan_state_changed.emit(_scan_state(9, zero_ok=0, probe_error=1))

    name, action, payload = events[-1]
    assert (name, action) == ("complete", "calibrate")
    assert payload["code"] == 500
    assert not window.erut_session.calibrating
    window.close()


def test_scan_states_do_not_touch_calibration_when_none_is_running(qtbot):
    window = _window(qtbot)
    events: list[tuple] = []
    window.erut.publish_event = lambda name, req, action, **kw: (
        events.append((name, action, kw)) or True)

    window.ros_status.probe_poses_changed.emit(_arc_poses(845.0))
    window.ros_status.scan_state_changed.emit(_scan_state(7))

    assert events == []
    window.close()


def test_the_robot_starts_at_once_when_the_vehicle_is_already_secured(qtbot) -> None:
    window = _window(qtbot)
    window.erut.publish_event = lambda *a, **kw: True
    window.erut.publish_res = lambda *a, **kw: True
    started: list[str] = []
    window._start_robot_scan = lambda: started.append("play")
    window._push_work_area_to_robot = lambda *values: None
    window.outrigger.reset(1)

    window.erut_session.handle_request(
        "calibrate", {"req_id": "cal-3", "diameter": 1690, "height": 6000})

    assert started == ["play"]
    window.close()
