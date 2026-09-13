"""로봇 제어 노드(`robot_control_node`)를 운영 UI 가 직접 띄우고 거둔다.

지금까지는 터미널에서 노드를 따로 실행해 두고 UI 를 켰다. 현장에서는
프로그램 하나만 시작하면 나머지는 알아서 붙어야 하므로, UI 가 노드를
자식 프로세스로 데리고 있는다. 노드가 이미 떠 있으면(따로 실행했거나
UI 를 두 번 켰거나) **다시 띄우지 않는다** — 같은 로봇에 두 노드가 붙으면
Modbus 소켓을 서로 뺏는다.

UI 를 닫으면 자식 노드도 같이 정리한다. 터미널에서 따로 띄운 노드는
우리 자식이 아니므로 건드리지 않는다.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from PyQt6.QtCore import QObject, pyqtSignal

#: 노드 실행 파일 이름. colcon 이 install/ 아래에 만들어 둔다.
NODE_EXECUTABLE = "robot_control_node"
#: ros2 run 으로 띄울 때 쓰는 패키지 이름.
NODE_PACKAGE = "elite_robot_controller"


class RobotNodeSupervisor(QObject):
    """로봇 제어 노드 프로세스를 켜고 끈다."""

    activity = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process: subprocess.Popen | None = None

    # ------------------------------------------------------------------ 상태
    @property
    def owns_process(self) -> bool:
        """우리가 띄운 노드가 살아 있는가."""
        return self._process is not None and self._process.poll() is None

    def node_command(self) -> list[str] | None:
        """노드를 띄울 명령. 찾지 못하면 None.

        colcon 이 설치한 실행 파일이 PATH 에 있으면 그걸 바로 쓰고,
        없으면 `ros2 run` 으로 돌려 본다(워크스페이스만 source 된 경우).
        """
        direct = shutil.which(NODE_EXECUTABLE)
        if direct:
            return [direct]
        if shutil.which("ros2"):
            return ["ros2", "run", NODE_PACKAGE, NODE_EXECUTABLE]
        return None

    # ------------------------------------------------------------------ 실행
    def start(self, already_running: bool = False) -> bool:
        """노드를 띄운다. 이미 떠 있거나 못 찾으면 안 띄우고 False.

        `already_running` 은 "다른 곳에서 띄운 노드가 이미 보인다"는 뜻이다
        (ROS 쪽에서 서비스가 잡히는지로 판단해 넘겨 준다). 그때는 그대로
        쓰고 우리가 또 띄우지 않는다.
        """
        if self.owns_process:
            return False
        if already_running:
            self.activity.emit("로봇 제어 노드가 이미 실행 중입니다 — 그대로 사용합니다.")
            return False

        command = self.node_command()
        if command is None:
            self.activity.emit(
                "로봇 제어 노드를 찾지 못했습니다. 워크스페이스를 source 했는지"
                " 확인하세요(install/setup.bash)."
            )
            return False

        try:
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # UI 가 죽어도 노드가 터미널 신호를 같이 받지 않도록 떼어 둔다.
                start_new_session=True,
                env=os.environ.copy(),
            )
        except OSError as exc:
            self.activity.emit(f"로봇 제어 노드 실행 실패: {exc}")
            self._process = None
            return False

        self.activity.emit("로봇 제어 노드를 시작했습니다.")
        return True

    def stop(self, timeout: float = 5.0) -> None:
        """우리가 띄운 노드를 정리한다. 남의 노드는 건드리지 않는다."""
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                pass
