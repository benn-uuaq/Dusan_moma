"""원점 대기 게이트(레지스터 267) — 지난 허가가 남으면 안 된다.

로봇은 원점에 서서 267 이 1 이 될 때까지 기다리고, 통과하면서 스스로 0 으로
지운다. 그런데 태스크는 **시작할 때 이 칸을 지우지 않는다.** 그래서 허가가
한 번 더 쓰이면(중복 회신·재시도) 다음 셀에서 로봇이 원점을 그냥 지나가
프로브 확인 없이 훑게 된다. 그래서 셀을 시작할 때 RCS 가 0 으로 지운다.
"""

from smr_operator_ui.app import OperatorWindow


def test_starting_a_cell_clears_the_scan_permission(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    sent: list[tuple[str, int]] = []
    commands: list[str] = []
    window.ros_status.send_value = lambda name, value: sent.append((name, value)) or True
    window.ros_status.call_command = lambda name: commands.append(name) or True

    window._start_robot_scan()

    assert sent[0] == ("scan_go", 0), sent
    # 허가를 지운 뒤에 stop → play 순서로 간다.
    assert commands[:2] == ["remote_control_on", "stop"]
    window.close()


def test_releasing_the_gate_sends_one(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    sent: list[tuple[str, int]] = []
    window.ros_status.send_value = lambda name, value: sent.append((name, value)) or True
    window._origin_waiting = True

    window._release_scan_gate()

    assert ("scan_go", 1) in sent
    window.close()


def test_zero_radius_is_refused_before_play(qtbot) -> None:
    """반지름(260)이 0 이면 로봇이 "ARC IS ZERO" 로 멈춘다 — 틀기 전에 막는다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    commands: list[str] = []
    errors: list[dict] = []
    window.ros_status.send_value = lambda name, value: True
    window.ros_status.send_pose = lambda name, values: True
    window.ros_status.call_command = lambda name: commands.append(name) or True
    window.erut_session.raise_error = errors.append

    window._push_work_area_to_robot(600, 800, 30, 20, 0, 10, 0, 0, 0)
    window._start_robot_scan()

    assert commands == []
    assert errors and errors[0]["code"] == "E9304"

    window._push_work_area_to_robot(600, 800, 30, 20, 845, 10, 0, 0, 0)
    window._start_robot_scan()
    assert commands[:2] == ["remote_control_on", "stop"]
    window.close()


# ---- 일시정지 = 29999 pause, 재개 = play ------------------------------------
def _scanning_window(qtbot):
    window = OperatorWindow(start_mqtt=False, start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    commands: list[str] = []
    window.ros_status.send_value = lambda name, value: True
    window.ros_status.call_command = lambda name: commands.append(name) or True
    return window, commands


def test_pause_sends_pause_and_resume_sends_play_only(qtbot) -> None:
    """줄 한가운데서도 그 자리에 서고(pause), 그 자리에서 잇는다(play)."""
    window, commands = _scanning_window(qtbot)
    window._on_robot_task_state(1)

    window._pause_robot_scan()
    assert commands == ["remote_control_on", "pause"]

    window._on_robot_task_state(2)              # 로봇이 일시정지로 섰다
    commands.clear()
    window._resume_robot_scan()
    assert commands == ["remote_control_on", "play"], "재개가 stop 을 섞었다"
    window.close()


def test_resume_after_the_task_was_stopped_restarts_the_section(qtbot) -> None:
    """그 사이 태스크가 중지됐으면 이을 자리가 없다 — 알리고 처음부터 튼다."""
    window, commands = _scanning_window(qtbot)
    window._on_robot_task_state(1)
    window._pause_robot_scan()
    window._on_robot_task_state(3)              # 펜던트에서 stop 등
    commands.clear()

    window._resume_robot_scan()

    assert commands[:2] == ["remote_control_on", "stop"]
    assert window._play_timer.isActive()
    window.close()


def test_refused_pause_falls_back_to_stop(qtbot) -> None:
    """pause 가 거절되면 로봇이 계속 훑는다 — 세우기만은 stop 으로 반드시 한다."""
    window, commands = _scanning_window(qtbot)
    window._on_robot_task_state(1)
    window._pause_robot_scan()
    commands.clear()

    window._on_pause_command_result("pause", False, "not supported in local control mode")

    assert "stop" in commands
    commands.clear()
    window._resume_robot_scan()                 # 태스크가 끝났으니 처음부터
    assert commands[:2] == ["remote_control_on", "stop"]
    window.close()


def test_pause_before_play_went_out_cancels_the_play(qtbot) -> None:
    """시작 직후(play 대기 중) 멈추면 play 를 거두고, 재개 때 처음부터 튼다."""
    window, commands = _scanning_window(qtbot)
    window._start_robot_scan()
    assert window._play_timer.isActive()
    commands.clear()

    window._pause_robot_scan()
    assert not window._play_timer.isActive()
    assert "pause" not in commands

    window._resume_robot_scan()
    assert window._play_timer.isActive()
    window.close()


def test_paused_task_blocks_vehicle_and_lift(qtbot) -> None:
    """일시정지한 팔은 벽에 붙은 채다 — 실행 중과 같이 차량·리프트를 막는다."""
    window, _commands = _scanning_window(qtbot)
    window._on_robot_task_state(2)

    assert window._robot_motion_block_reason()
    window.close()
