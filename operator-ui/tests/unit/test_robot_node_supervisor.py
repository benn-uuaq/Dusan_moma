"""로봇 제어 노드를 UI 가 데리고 있는 감독자 검증.

현장에서는 프로그램 하나만 시작하면 노드도 같이 떠야 하고, 이미 떠 있으면
또 띄우면 안 된다(같은 로봇에 두 노드가 붙으면 Modbus 소켓을 서로 뺏는다).
"""

import os
import subprocess
import sys
import time

import pytest

from smr_operator_ui.services import RobotNodeSupervisor


@pytest.fixture(autouse=True)
def no_local_nodes(monkeypatch):
    """이 PC 에 실제로 떠 있는 노드에 시험이 휘둘리지 않게 한다(필요한 시험만 바꾼다)."""
    monkeypatch.setattr(RobotNodeSupervisor, "local_node_pids", staticmethod(lambda: []))


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


def test_a_node_already_running_on_this_pc_is_not_launched_again(monkeypatch) -> None:
    """ROS 탐색 전이라 서비스가 안 보여도, 이 PC 에 노드 프로세스가 있으면 또 띄우지 않는다.

    2026-10-14: 켜자마자 서비스로만 물어 「없다」가 나와 노드가 두 개가 됐고, 둘이
    같은 로봇을 다퉈 연결이 0.01초 간격으로 붙었다 끊겼다 했다.
    """
    supervisor = RobotNodeSupervisor()
    monkeypatch.setattr(RobotNodeSupervisor, "local_node_pids", staticmethod(lambda: [752]))
    monkeypatch.setattr(supervisor, "node_command", lambda: ["true"])

    assert supervisor.start(already_running=False) is False
    assert supervisor.owns_process is False


def test_duplicate_nodes_are_reported(monkeypatch) -> None:
    supervisor = RobotNodeSupervisor()
    monkeypatch.setattr(RobotNodeSupervisor, "local_node_pids",
                        staticmethod(lambda: [752, 2619]))
    notices: list[str] = []
    supervisor.activity.connect(notices.append)

    supervisor.start()

    assert any("2개" in t and "752" in t and "2619" in t for t in notices)


def test_node_is_launched_directly_not_through_the_ros2_wrapper(monkeypatch, tmp_path) -> None:
    """`ros2 run` 래퍼는 죽어도 노드를 남기므로 설치된 실행 파일을 직접 띄운다."""
    exe = tmp_path / "lib" / "elite_robot_controller" / "robot_control_node"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setenv("AMENT_PREFIX_PATH", f"{tmp_path}{os.pathsep}/nonexistent")

    assert RobotNodeSupervisor().node_command() == [str(exe)]


def test_launched_node_dies_with_the_ui(tmp_path) -> None:
    """RCS 가 Ctrl+C · 터미널 닫기로 죽어도 노드가 고아로 남지 않는다(PR_SET_PDEATHSIG)."""
    if not sys.platform.startswith("linux"):
        pytest.skip("Linux 전용")
    pid_file = tmp_path / "child.pid"
    # 가짜 UI: 감독자로 오래 사는 자식을 띄우고 pid 를 적은 뒤 정리 없이 죽는다.
    script = (
        "import os, sys, time\n"
        f"sys.path.insert(0, {str(__import__('pathlib').Path(__file__).resolve().parents[2] / 'src')!r})\n"
        "from PyQt6.QtCore import QCoreApplication\n"
        "app = QCoreApplication([])\n"
        "from smr_operator_ui.services.robot_node_supervisor import RobotNodeSupervisor\n"
        "RobotNodeSupervisor.local_node_pids = staticmethod(lambda: [])\n"
        "s = RobotNodeSupervisor()\n"
        "s.node_command = lambda: [sys.executable, '-c', 'import time; time.sleep(60)']\n"
        "s.start()\n"
        f"open({str(pid_file)!r}, 'w').write(str(s._process.pid))\n"
        "os._exit(0)\n"
    )
    subprocess.run([sys.executable, "-c", script], check=True, timeout=20)
    child = int(pid_file.read_text())
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return                            # 같이 죽었다
        time.sleep(0.1)
    os.kill(child, 9)
    pytest.fail("UI 가 죽었는데 노드가 남았다")

