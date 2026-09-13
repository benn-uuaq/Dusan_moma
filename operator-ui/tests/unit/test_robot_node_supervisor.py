"""로봇 제어 노드를 UI 가 데리고 있는 감독자 검증.

현장에서는 프로그램 하나만 시작하면 노드도 같이 떠야 하고, 이미 떠 있으면
또 띄우면 안 된다(같은 로봇에 두 노드가 붙으면 Modbus 소켓을 서로 뺏는다).
"""

import subprocess

from smr_operator_ui.services import RobotNodeSupervisor


def test_does_not_launch_when_a_node_is_already_running() -> None:
    supervisor = RobotNodeSupervisor()
    notices: list[str] = []
    supervisor.activity.connect(notices.append)

    assert supervisor.start(already_running=True) is False
    assert supervisor.owns_process is False
    assert any("이미 실행" in text for text in notices)


def test_reports_when_the_node_cannot_be_found(monkeypatch) -> None:
    """워크스페이스를 source 안 했으면 조용히 실패하면 안 된다."""
    supervisor = RobotNodeSupervisor()
    monkeypatch.setattr(supervisor, "node_command", lambda: None)
    notices: list[str] = []
    supervisor.activity.connect(notices.append)

    assert supervisor.start() is False
    assert any("찾지 못했습니다" in text for text in notices)


def test_stop_only_touches_our_own_process(monkeypatch) -> None:
    """따로 띄운 노드는 남의 것이라 건드리지 않는다."""
    supervisor = RobotNodeSupervisor()
    # 우리가 띄운 게 없으면 stop 은 아무 일도 하지 않는다.
    supervisor.stop()
    assert supervisor.owns_process is False


def test_launches_and_stops_its_own_process(monkeypatch) -> None:
    """띄운 프로세스는 UI 를 닫을 때 같이 거둔다."""
    supervisor = RobotNodeSupervisor()
    monkeypatch.setattr(supervisor, "node_command",
                        lambda: ["python3", "-c", "import time; time.sleep(30)"])

    assert supervisor.start() is True
    assert supervisor.owns_process is True

    supervisor.stop(timeout=5.0)
    assert supervisor.owns_process is False
