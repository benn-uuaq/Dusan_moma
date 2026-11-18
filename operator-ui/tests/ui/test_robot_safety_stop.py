"""로봇 안전정지 — PLC 가 차량 이동 중에 걸고 차량이 서면 안전재설정으로 푼다 (2026-11-17).

비상정지가 아니다. RCS 는 화면(상단 Cobot 램프 · 알림)에만 보이고 ERUT 에는 보내지 않는다.
"""

from smr_operator_ui.app import OperatorWindow


def _window(qtbot):
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    window.ros_status.call_command = lambda name: True
    window.ros_status.send_value = lambda *a: True
    raised: list[dict] = []
    window.erut_session.raise_error = lambda fields: raised.append(fields)
    return window, raised


def test_safety_stop_is_shown_on_the_cobot_lamp_only(qtbot) -> None:
    window, raised = _window(qtbot)
    window.ros_status.connected_changed.emit(True)
    badge = window.top_bar.badges["Cobot"]
    window.ros_status.safety_stop_changed.emit(True)
    assert "안전정지" in badge.state_label.text()
    assert "안전정지" in window.main_screen.activity_label.text()
    window.ros_status.safety_stop_changed.emit(False)
    assert "연결됨" in badge.state_label.text()
    assert raised == [], "안전정지는 ERUT 로 내보내지 않는다"
    window.close()


def test_robot_alarms_during_the_safety_stop_are_not_sent_out(qtbot) -> None:
    window, raised = _window(qtbot)
    window.ros_status.safety_stop_changed.emit(True)
    window._handle_robot_alarm("[ALARM] safeguard stop")
    assert raised == []
    # 한참 뒤(안전정지도 풀리고 차량도 선 뒤)의 알람은 예전처럼 ERUT 경고로 간다.
    window.ros_status.safety_stop_changed.emit(False)
    window._safety_stop_changed_at -= 10.0
    window._handle_robot_alarm("[ALARM] something else")
    assert raised and raised[-1]["code"] == "E9201"
    window.close()


def test_robot_start_waits_for_the_safety_reset(qtbot) -> None:
    window, _raised = _window(qtbot)
    played: list[int] = []
    window.ros_status.safety_stop_changed.emit(True)
    window._schedule_play()
    assert not getattr(window, "_play_timer", None) or not window._play_timer.isActive()
    assert window._play_after_safety
    window.ros_status.call_command = lambda name: played.append(1) or True
    window.ros_status.safety_stop_changed.emit(False)       # 안전재설정
    assert window._play_timer.isActive()
    window._cancel_play()
    window.close()


def test_stop_drops_a_start_waiting_for_the_reset(qtbot) -> None:
    window, _raised = _window(qtbot)
    window.ros_status.safety_stop_changed.emit(True)
    window._schedule_play()
    window._cancel_play()
    window.ros_status.safety_stop_changed.emit(False)
    assert not getattr(window, "_play_timer", None) or not window._play_timer.isActive()
    window.close()


# ---- Config IO: CI0 · CI1 안전정지, CI2 · CI3 안전재설정 ------------------------------------
def test_config_inputs_drive_the_safety_display(qtbot) -> None:
    window, raised = _window(qtbot)
    window.ros_status.connected_changed.emit(True)
    badge = window.top_bar.badges["Cobot"]
    io = window.screens["io"]
    emit = window.ros_status.config_digital_in_changed.emit

    emit(0b0001)                                     # CI0 — 안전정지
    assert "안전정지" in badge.state_label.text() and io.value_items["CI0"].text() == "ON"
    assert io.value_items["SAFETY"].text() == "안전정지"
    emit(0b0000)                                     # 정지 신호가 꺼졌다 — 재설정 전이라 아직 정지
    assert "재설정 대기" in badge.state_label.text()
    assert window._robot_safety_stop
    emit(0b0100)                                     # CI2 — 안전재설정
    assert "연결됨" in badge.state_label.text() and not window._robot_safety_stop
    assert io.value_items["SAFETY"].text() == "정상"
    assert raised == [], "모니터링만 — 밖으로 내보내지 않는다"
    window.close()


def test_reset_input_while_the_stop_is_still_on_does_not_clear(qtbot) -> None:
    window, _raised = _window(qtbot)
    emit = window.ros_status.config_digital_in_changed.emit
    emit(0b0010)                                     # CI1 — 안전정지
    emit(0b1010)                                     # CI3 이 켜져도 CI1 이 켜져 있으면 그대로 정지
    assert window._robot_safety_stop
    emit(0b1000)                                     # 정지 신호가 꺼지고 재설정 입력이 있다 → 풀림
    assert not window._robot_safety_stop
    window.close()
