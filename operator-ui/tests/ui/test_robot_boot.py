"""설비 전원 ON — 로봇 부팅을 기다렸다 전원 ON · 브레이크 해제, 끝나면 창을 스스로 닫는다 (2026-11-27)."""

from smr_operator_ui.robot_boot import RobotBootAssist


def _assist(qtbot, ready_modes=True):
    """ready_modes — 이미 원격 모드 · 자동 모드다."""
    sent = []
    boot = RobotBootAssist(sent.append)
    qtbot.addWidget(boot.dialog)
    boot.DONE_CLOSE_MS = 50
    boot.start()
    if ready_modes:
        boot.on_control_method(2)
        boot.on_operation_mode(0)
    return boot, sent


def test_waits_for_boot_then_powers_on_and_releases_the_brake(qtbot) -> None:
    boot, sent = _assist(qtbot)
    assert boot.dialog.isVisible() and "부팅을 기다립니다" in boot.dialog.text.text()
    boot.on_connected(True)
    boot.on_mode(2)                                   # BOOTING
    assert sent == []
    boot.on_mode(3)                                   # POWER_OFF
    assert sent == ["power_on"]
    boot.on_mode(3)                                   # 바로 다시 와도 또 보내지 않는다
    assert sent == ["power_on"]
    boot.on_mode(4)                                   # POWER_ON
    boot.on_mode(5)                                   # IDLE
    assert sent[-1] == "brake_release"
    done = []
    boot.finished.connect(done.append)
    boot.on_mode(7)                                   # RUNNING
    assert done == [True] and boot.dialog.title.text() == "로봇 부팅 완료"
    qtbot.waitUntil(lambda: not boot.dialog.isVisible(), timeout=2000)


def test_already_running_finishes_without_commands(qtbot) -> None:
    boot, sent = _assist(qtbot)
    boot.on_connected(True)
    boot.on_mode(7)
    assert sent == [] and not boot.active


def test_safety_confirmation_is_left_to_the_operator(qtbot) -> None:
    boot, sent = _assist(qtbot)
    boot.on_connected(True)
    boot.on_mode(1)                                   # CONFIRM_SAFETY
    assert sent == [] and "펜던트" in boot.dialog.text.text()


def test_rejected_command_is_retried_later(qtbot) -> None:
    boot, sent = _assist(qtbot)
    boot.RETRY_S = 0.0
    boot.on_connected(True)
    boot.on_mode(3)
    boot.on_command_result("power_on", False, "not supported in local control mode")
    assert "거절" in boot.dialog.text.text()
    assert sent.count("power_on") == 2, "다시 보낸다"


def test_closing_the_window_stops_the_auto_sequence(qtbot) -> None:
    boot, sent = _assist(qtbot)
    done = []
    boot.finished.connect(done.append)
    boot.dialog.close_button.click()
    boot.on_connected(True)
    boot.on_mode(3)
    assert done == [False] and sent == [] and not boot.dialog.isVisible()


def test_controller_already_up_at_rcs_start_is_powered_on(qtbot) -> None:
    """RCS 는 비상정지로 꺼지지 않는다 — RCS 를 켤 때 컨트롤러가 이미 떠 있어도 팔을 켠다(2026-11-27)."""
    boot, sent = _assist(qtbot)
    boot.on_connected(True)                           # 켜자마자 붙었다
    boot.on_mode(3)                                   # POWER_OFF
    assert sent == ["power_on"]


def test_estop_stops_the_sequence(qtbot) -> None:
    boot, sent = _assist(qtbot)
    boot.on_connected(True)
    boot.on_fault(True)
    boot.on_mode(3)
    assert sent == [] and not boot.active


def test_after_boot_the_arm_is_never_powered_on_again(qtbot) -> None:
    """부팅을 끝낸 뒤 충돌 · 비상정지로 팔이 꺼져도(컨트롤러는 켜진 채) 켜지 않는다."""
    boot, sent = _assist(qtbot)
    boot.on_connected(True)
    boot.on_mode(7)
    boot.on_connected(False)
    boot.on_connected(True)
    boot.on_mode(3)
    boot.on_mode(5)
    assert sent == []


def test_remote_mode_is_turned_on_before_power_and_brake(qtbot) -> None:
    """원격 모드여야 30001 을 받는다 — 전원 ON 전에 remoteControl -on, Remote 가 되기를 기다린다(2026-11-27)."""
    boot, sent = _assist(qtbot, ready_modes=False)
    boot.on_operation_mode(0)
    boot.on_connected(True)
    boot.on_mode(3)                                   # POWER_OFF — 아직 Local
    assert sent == ["remote_control_on"] and "원격 모드" in boot.dialog.text.text()
    boot.on_control_method(2)                         # Remote
    assert sent == ["remote_control_on", "power_on"]


def test_manual_operation_mode_waits_for_automatic(qtbot) -> None:
    """자동 모드여야 안전정지 · 안전 리셋이 먹는다 — 29999 에 바꾸는 명령이 없어 펜던트에서 바꾸기를 기다린다."""
    boot, sent = _assist(qtbot, ready_modes=False)
    boot.on_control_method(2)
    boot.on_operation_mode(1)                         # 수동
    boot.on_connected(True)
    boot.on_mode(3)
    assert sent == [] and "자동 모드로 바꾸세요" in boot.dialog.text.text()
    boot.on_operation_mode(0)                         # 펜던트에서 자동으로
    assert sent == ["power_on"]
    boot.on_mode(5)
    assert sent[-1] == "brake_release"
