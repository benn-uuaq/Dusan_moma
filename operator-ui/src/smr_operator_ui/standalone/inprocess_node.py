"""단독판 — 로봇 제어 노드를 RCS 프로세스 안의 스레드로 돌린다.

리눅스판의 RobotNodeSupervisor 는 노드를 별도 프로세스(ros2)로 띄운다. 단독판은
ROS 가 없으므로 같은 노드 코드(elite_robot_controller.robot_control_node)를 그대로
쓰되, liteross 가 잇는 한 프로세스 안에서 돌린다. 화면 쪽(RosStatusClient)은
리눅스판과 똑같이 토픽 · 서비스로 노드와 이야기한다.
"""

from __future__ import annotations

import threading
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from smr_operator_ui.standalone import liteross

NODE_NAME = "robot_control_node"


class InProcessRobotNode(QObject):
    """RobotNodeSupervisor 와 같은 모양(activity · duplicate_nodes · start · stop)."""

    activity = pyqtSignal(str)
    duplicate_nodes = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None,
                 endpoint: Callable[[], tuple[str, int]] | None = None) -> None:
        super().__init__(parent)
        self._endpoint = endpoint
        self._node = None
        self._executor = None
        self._thread: threading.Thread | None = None

    @property
    def owns_process(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, already_running: bool = False) -> bool:
        """노드를 스레드에서 만들고 돌린다. 만들 때 바로 로봇에 붙어 보므로(수 초)
        화면이 멈추지 않게 그 일도 스레드에서 한다."""
        if self.owns_process or already_running:
            return False
        overrides: dict[str, object] = {}
        if self._endpoint is not None:
            ip, port = self._endpoint()
            if ip:
                overrides["robot_ip"] = str(ip)
            if port:
                overrides["modbus_port"] = int(port)
        liteross.set_parameter_overrides(NODE_NAME, overrides)
        self._thread = threading.Thread(target=self._run, name="robot-node", daemon=True)
        self._thread.start()
        target = overrides.get("robot_ip", "기본 주소")
        self.activity.emit(f"로봇 제어를 시작했습니다 — {target} 에 붙어 봅니다.")
        return True

    def _run(self) -> None:
        try:
            from elite_robot_controller.robot.robot_control_node import RobotControlNode
            self._node = RobotControlNode()
        except Exception as exc:  # pragma: no cover - 현장 진단용
            self.activity.emit(f"로봇 제어를 시작하지 못했습니다: {exc}")
            return
        self._executor = liteross.SingleThreadedExecutor()
        self._executor.add_node(self._node)
        self._executor.spin()

    def stop(self, timeout: float = 5.0) -> None:
        executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        node, self._node = self._node, None
        if node is not None:
            try:
                node.destroy_node()
            except Exception:  # pragma: no cover - 종료 중
                pass
