"""RCS 알람 목록 번호 — docs/RCS_알람_목록_20261119.xlsx 의 코드가 알람 목록 · 운영 기록에 붙는다."""

from smr_operator_ui.app import OperatorWindow


def _window(qtbot):
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    window.erut.publish_event = lambda *a, **k: True
    window.erut.publish_res = lambda *a, **k: True
    window.erut.publish_error = lambda *a, **k: True
    return window


def _alarms(window) -> list[str]:
    lst = window.cobot_manual_screen.alarm_list
    return [lst.item(i).text() for i in range(lst.count())]


def test_alarm_list_shows_the_code_and_the_log_keeps_it(qtbot) -> None:
    window = _window(qtbot)
    logged: list[list] = []
    window.data_recorder.event_logged.connect(logged.append)
    window._on_vehicle_problem("리프트 1000 mm: 180초 안에 끝나지 않았습니다")
    assert "[E2001] 차량: 리프트 1000 mm" in _alarms(window)[0]
    row = [f for f in logged if f[1] == "알람"][-1]
    assert row[2] == "E2001" and row[4].startswith("차량: 리프트")       # 코드 칸 · 글에는 번호 없음
    window.close()


def test_robot_controller_alarm_stream_is_e9201(qtbot) -> None:
    window = _window(qtbot)
    window.ros_status.alarm_received.emit("[ALARM] joint 3 over speed")
    assert "[E9201] [ALARM] joint 3 over speed" in _alarms(window)[0]
    window.close()


def test_robot_task_code_uses_its_own_number(qtbot) -> None:
    """레지스터 299 = 8 → E9108, ERUT detail 에는 번호를 붙이지 않는다."""
    window = _window(qtbot)
    raised: list[dict] = []
    window.erut_session.raise_error = raised.append
    window._handle_probe_error([0] * 9 + [8])
    assert _alarms(window)[0].split("  ", 1)[1].startswith("[E9108] 차량 고정 확인을 못 받아")
    assert raised[-1]["code"] == "E9108" and not raised[-1]["detail"].startswith("[")
    window.close()


def test_plain_add_alarm_still_works_without_a_code(qtbot) -> None:
    window = _window(qtbot)
    window.cobot_manual_screen.add_alarm("번호 없는 알람")
    assert _alarms(window)[0].endswith("  번호 없는 알람")
    window.close()
