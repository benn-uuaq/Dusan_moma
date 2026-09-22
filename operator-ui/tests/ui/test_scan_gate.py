"""원점 대기 게이트(레지스터 267) — 지난 허가가 남으면 안 된다.

로봇은 원점에 서서 267 이 1 이 될 때까지 기다리고, 통과하면서 스스로 0 으로
지운다. 그런데 태스크는 **시작할 때 이 칸을 지우지 않는다.** 그래서 허가가
한 번 더 쓰이면(중복 회신·재시도) 다음 셀에서 로봇이 원점을 그냥 지나가
프로브 확인 없이 훑게 된다. 그래서 셀을 시작할 때 RCS 가 0 으로 지운다.
"""

from smr_operator_ui.app import OperatorWindow


def test_starting_a_cell_clears_the_scan_permission(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)
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
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    sent: list[tuple[str, int]] = []
    window.ros_status.send_value = lambda name, value: sent.append((name, value)) or True
    window._origin_waiting = True

    window._release_scan_gate()

    assert ("scan_go", 1) in sent
    window.close()
