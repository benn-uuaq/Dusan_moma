"""로봇 제어 노드(`robot_control_node`)를 운영 UI 가 직접 띄우고 거둔다.

지금까지는 터미널에서 노드를 따로 실행해 두고 UI 를 켰다. 현장에서는
프로그램 하나만 시작하면 나머지는 알아서 붙어야 하므로, UI 가 노드를
자식 프로세스로 데리고 있는다. 노드가 이미 떠 있으면(따로 실행했거나
UI 를 두 번 켰거나) **다시 띄우지 않는다** — 같은 로봇에 두 노드가 붙으면
Modbus 소켓을 서로 뺏는다.

UI 를 닫으면 자식 노드도 같이 정리한다. 터미널에서 따로 띄운 노드는
우리 자식이 아니므로 건드리지 않는다.

2026-10-14: 노드가 두 개 떠서 로봇 연결이 0.01초 간격으로 붙었다 끊겼다 했다.
  * RCS 를 Ctrl+C · 터미널 닫기로 끄면 노드가 고아로 남았다(창을 닫을 때만 껐다)
    → 자식 노드는 RCS 가 어떻게 죽든 같이 죽게 한다(Linux PR_SET_PDEATHSIG).
    `ros2 run` 래퍼를 거치면 래퍼만 죽고 노드가 남으므로 실행 파일을 직접 띄운다.
  * 켜자마자 ROS 서비스로 「노드가 이미 있나」를 물으면 탐색 전이라 「없다」가
    나와 하나 더 띄웠다 → 이 PC 의 프로세스 목록으로도 본다.

2026-11-24: 노드를 주소 없이 띄워 노드가 자기 기본값(192.168.227.134)에만 붙으려 했다 — 연결 설정의
  IP(예: 192.168.11.30)는 「연결」을 눌러야 넘어가서, RCS 를 켜도 로봇이 「연결 안 됨」이었다.
  → 연결 설정의 IP · Modbus 포트를 띄울 때 파라미터로 넘긴다(robot_ip · modbus_port). 출력은
  ~/.ros/log/smr_robot_node.log 에 남긴다(예전에는 버렸다 — 왜 못 붙는지 볼 곳이 없었다).
"""

from __future__ import annotations

import ctypes
import os
import shutil
import signal
import subprocess
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

#: 노드 실행 파일 이름. colcon 이 install/ 아래에 만들어 둔다.
NODE_EXECUTABLE = "robot_control_node"
#: ros2 run 으로 띄울 때 쓰는 패키지 이름.
NODE_PACKAGE = "elite_robot_controller"
#: 노드 출력(연결 성공 · 실패 줄). 띄울 때마다 새로 쓴다.
LOG_PATH = Path.home() / ".ros" / "log" / "smr_robot_node.log"


class RobotNodeSupervisor(QObject):
    """로봇 제어 노드 프로세스를 켜고 끈다."""

    activity = pyqtSignal(str)
    # 이 PC 에 로봇 제어 노드가 둘 이상 떠 있다 — 알릴 문장.
    duplicate_nodes = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process: subprocess.Popen | None = None
        self._log = None
        #: 붙을 로봇 주소(연결 설정 — 앱이 넣는다). 비면 노드 기본값.
        self.robot_ip = ""
        self.modbus_port = 502

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
        # 워크스페이스를 source 하면 AMENT_PREFIX_PATH 에 install/<패키지> 가 있다.
        # 실행 파일을 직접 띄워야 RCS 와 같이 죽게 걸 수 있다(래퍼는 자식을 남긴다).
        command = self._base_command()
        if command is None or not self.robot_ip:
            return command
        return command + ["--ros-args", "-p", f"robot_ip:={self.robot_ip}",
                          "-p", f"modbus_port:={int(self.modbus_port or 502)}"]

    @staticmethod
    def _base_command() -> list[str] | None:
        for prefix in os.environ.get("AMENT_PREFIX_PATH", "").split(os.pathsep):
            candidate = Path(prefix) / "lib" / NODE_PACKAGE / NODE_EXECUTABLE
            if prefix and candidate.is_file() and os.access(candidate, os.X_OK):
                return [str(candidate)]
        direct = shutil.which(NODE_EXECUTABLE)
        if direct:
            return [direct]
        if shutil.which("ros2"):
            return ["ros2", "run", NODE_PACKAGE, NODE_EXECUTABLE]
        return None

    @staticmethod
    def local_node_pids() -> list[int]:
        """이 PC 에서 돌고 있는 로봇 제어 노드 프로세스 번호들(/proc 을 훑는다).

        ROS 탐색은 켜고 나서 1~2초 걸려 막 켠 RCS 는 이미 떠 있는 노드를 못 본다.
        같은 PC 의 프로세스 목록은 바로 보인다. /proc 이 없으면(Linux 가 아니면) 빈 목록.
        """
        pids: list[int] = []
        me = os.getpid()
        for entry in Path("/proc").glob("[0-9]*"):
            try:
                pid = int(entry.name)
                if pid == me:
                    continue
                args = (entry / "cmdline").read_bytes().split(b"\0")
            except (OSError, ValueError):
                continue
            # 실행 파일 자체(…/robot_control_node)로 뜬 것만 센다 — `ros2 run …
            # robot_control_node` 래퍼나 이 이름을 인자로 받은 셸은 세지 않는다.
            if any(Path(a.decode("utf-8", "replace")).name == NODE_EXECUTABLE
                   for a in args[:2] if a):
                pids.append(pid)
        return sorted(pids)

    # ------------------------------------------------------------------ 실행
    def start(self, already_running: bool = False) -> bool:
        """노드를 띄운다. 이미 떠 있거나 못 찾으면 안 띄우고 False.

        `already_running` 은 "다른 곳에서 띄운 노드가 이미 보인다"는 뜻이다
        (ROS 쪽에서 서비스가 잡히는지로 판단해 넘겨 준다). 그때는 그대로
        쓰고 우리가 또 띄우지 않는다.
        """
        if self.owns_process:
            return False
        running = self.local_node_pids()
        if len(running) > 1:
            text = (f"로봇 제어 노드가 {len(running)}개 떠 있습니다(pid "
                    f"{', '.join(map(str, running))}) — 같은 로봇에 둘이 붙으면 연결이 "
                    "붙었다 끊겼다 합니다. RCS 를 닫고 노드를 모두 끈 뒤 다시 켜세요.")
            self.activity.emit(text)
            self.duplicate_nodes.emit(text)
        if already_running or running:
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
            try:
                LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
                self._log = open(LOG_PATH, "w", encoding="utf-8")    # noqa: SIM115 — 자식이 쓰는 동안 연다
            except OSError:
                self._log = None
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"      # 연결 줄(print)이 바로 기록되게
            self._process = subprocess.Popen(
                command,
                stdout=self._log or subprocess.DEVNULL,
                stderr=subprocess.STDOUT if self._log else subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                # 터미널 신호(Ctrl+C)는 UI 가 받아 정리하게 떼어 두되, UI 가
                # 어떻게 죽든 노드도 같이 죽게 한다 — 고아 노드가 남으면 다음에
                # 켠 RCS 가 노드를 하나 더 띄워 둘이 같은 로봇을 다툰다.
                start_new_session=True,
                preexec_fn=_die_with_parent,
                env=env,
            )
        except OSError as exc:
            self.activity.emit(f"로봇 제어 노드 실행 실패: {exc}")
            self._process = None
            self._close_log()
            return False

        target = f" — 로봇 {self.robot_ip}:{int(self.modbus_port or 502)}" if self.robot_ip else ""
        self.activity.emit(f"로봇 제어 노드를 시작했습니다{target}.")
        return True

    def _close_log(self) -> None:
        if self._log is not None:
            try:
                self._log.close()
            except OSError:
                pass
            self._log = None

    @staticmethod
    def log_tail(lines: int = 3) -> str:
        """노드 기록 끝 몇 줄(연결 실패 원인을 알릴 때)."""
        try:
            text = LOG_PATH.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        except OSError:
            return ""
        return " / ".join(line.strip() for line in text[-lines:] if line.strip())

    def stop(self, timeout: float = 5.0) -> None:
        """우리가 띄운 노드를 정리한다. 남의 노드는 건드리지 않는다."""
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            self._close_log()
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
        self._close_log()


def _die_with_parent() -> None:
    """자식 프로세스에서 exec 직전에 불린다 — 부모(RCS)가 죽으면 SIGTERM 을 받게 한다.

    Linux 의 prctl(PR_SET_PDEATHSIG). 다른 OS 에서는 아무것도 안 한다.
    """
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, signal.SIGTERM)      # 1 = PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass

