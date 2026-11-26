"""AGV 제어 노드(AGV 담당 steer_agv — QUAD_STEER_AGV_CONTROLLER)를 RCS 가 직접 띄우고 거둔다 (2026-11-24).

로봇 제어 노드(robot_node_supervisor)와 같은 방식이다 — RCS 의 자식 프로세스라 RCS 와 같은 ROS 도메인 ·
환경으로 뜨고(따로 띄울 때 ROS_DOMAIN_ID 가 달라 「차량 서비스가 없습니다」가 나던 일을 막는다), RCS 가
어떻게 죽든 같이 죽는다(PR_SET_PDEATHSIG).

  실행   <install>/lib/steer_agv_controller_py/agv_controller --ros-args
           --params-file <share>/steer_agv_bringup/config/controller.yaml
           -p plc_enabled:=true -p plc_host:=<PLC 주소> -p plc_port:=<포트>
           -p plc_writes_enabled:=<쓰기> -p debug_ui:=<조작 패널> -p debug_log_dir:=~/.ros/log/steer_agv_debug
           -p max_lift_height:=<차량 설정 「리프트 한계 높이」 m>
         AGV 담당 실행 커맨드(src/steer_agv/command — ros2 launch steer_agv_bringup controller.launch.py …)의
         agv_controller 와 같다. 런치가 같이 띄우는 robot_state_publisher(TF · RViz 용)는 RCS 에 필요 없어 안 띄운다.
  안 띄움 - 차량 제어가 「ROS 차량 노드 고정」이 아니거나 AGV 제어 노드가 「외부」일 때
         - 이 PC 에 agv_controller 가 이미 돌거나, 다른 노드(다른 PC 의 AGV 노드 · vehicle_sim)가
           vehicle/robot_status 를 이미 내고 있을 때 — /vehicle/* 를 둘이 열면 명령이 엇갈린다
  기록   ~/.ros/log/smr_agv_controller.log (띄울 때마다 새로). 바로 죽으면 마지막 줄을 알린다.

외부 PC 에서 시험할 때: 이 노드는 RCS 와 같은 ROS_DOMAIN_ID 로 네트워크에 열린다(ROS_LOCALHOST_ONLY 가 1 이
아니면). 다른 PC 에서 같은 도메인으로 `ros2 topic echo /vehicle/robot_status` · AGV 담당 조작 패널
(`ros2 run steer_agv_operator_ui operator_ui`)로 붙어 본다.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from smr_operator_ui.services.robot_node_supervisor import _die_with_parent

EXECUTABLE = "agv_controller"
PACKAGE = "steer_agv_controller_py"
BRINGUP = "steer_agv_bringup"
LOG_PATH = Path.home() / ".ros" / "log" / "smr_agv_controller.log"
#: AGV 노드의 일별 PLC · 주행 기록 폴더(debug_log_dir). 노드 기본값은 AGV 개발자 PC 경로
#: (/home/rnd08/…)라 이 PC 에서는 「로그 저장 실패 … Permission denied」가 쏟아진다 — 띄울 때 바꿔 준다.
DEBUG_LOG_DIR = Path.home() / ".ros" / "log" / "steer_agv_debug"


class AgvNodeSupervisor(QObject):
    """AGV 제어 노드 프로세스를 켜고 끈다."""

    activity = pyqtSignal(str)
    #: 상태 글(연결 설정 화면에 보인다) — 바뀔 때마다.
    state_changed = pyqtSignal(str)

    #: 띄운 뒤 이만큼 안에 죽으면 「바로 죽었다」로 보고 기록 끝줄을 알린다 [ms].
    WATCH_MS = 2000

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process: subprocess.Popen | None = None
        self._log = None
        self.plc_host = "192.168.11.10"
        self.plc_port = 8501
        self.writes = True
        self.panel = False
        #: 리프트 상한 [m] — AGV 노드 max_lift_height(기본 1.5 m, 읽기 전용이라 띄울 때만). RCS 차량 설정
        #: 「리프트 한계 높이」를 넘긴다(2026-11-24 — 실제 리프트는 5000 mm). None 이면 노드 기본값.
        self.max_lift_m: float | None = None
        #: 다른 노드가 이미 차량 상태를 내는가(앱이 넣는다 — vehicle/robot_status 발행자 수 · 수신).
        self.external_present: Callable[[], bool] = lambda: False
        self._state = "꺼짐"
        self._watch = QTimer(self)
        self._watch.setInterval(self.WATCH_MS)
        self._watch.timeout.connect(self._check)

    # ------------------------------------------------------------------ 상태
    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def state(self) -> str:
        return self._state

    @property
    def pid(self) -> int | None:
        """우리가 띄운 노드의 프로세스 번호(안 돌면 None)."""
        process = self._process
        return process.pid if process is not None and process.poll() is None else None

    def _set_state(self, text: str) -> None:
        if text != self._state:
            self._state = text
            self.state_changed.emit(text)

    def endpoint(self) -> str:
        return f"{self.plc_host}:{self.plc_port}"

    @staticmethod
    def _find(relative: str) -> Path | None:
        for prefix in os.environ.get("AMENT_PREFIX_PATH", "").split(os.pathsep):
            if prefix:
                candidate = Path(prefix) / relative
                if candidate.is_file():
                    return candidate
        return None

    def command(self) -> list[str] | None:
        """띄울 명령. 실행 파일을 못 찾으면 None(워크스페이스를 소싱하지 않았거나 빌드 전)."""
        exe = self._find(f"lib/{PACKAGE}/{EXECUTABLE}")
        if exe is None or not os.access(exe, os.X_OK):
            return None
        args = [str(exe), "--ros-args"]
        params = self._find(f"share/{BRINGUP}/config/controller.yaml")
        if params is not None:
            args += ["--params-file", str(params)]

        def flag(value: bool) -> str:
            return "true" if value else "false"

        for name, value in (("plc_enabled", "true"), ("plc_host", self.plc_host),
                            ("plc_port", str(int(self.plc_port))),
                            ("plc_writes_enabled", flag(self.writes)), ("debug_ui", flag(self.panel)),
                            ("debug_log_dir", str(DEBUG_LOG_DIR))):
            args += ["-p", f"{name}:={value}"]
        if self.max_lift_m is not None and self.max_lift_m > 0:
            # 실수로 넘겨야 한다 — 노드가 1.5(float)로 선언해 "5" 처럼 정수로 주면 타입이 달라 뜨지 않는다.
            args += ["-p", f"max_lift_height:={float(self.max_lift_m):.3f}"]
        return args

    @staticmethod
    def local_pids(exclude: int | None = None) -> list[int]:
        """이 PC 에서 도는 agv_controller 프로세스 번호(/proc). 우리 자식은 빼고 셀 수 있다."""
        pids: list[int] = []
        proc = Path("/proc")
        if not proc.is_dir():
            return pids
        for entry in proc.glob("[0-9]*"):
            try:
                pid = int(entry.name)
                if pid in (os.getpid(), exclude):
                    continue
                args = (entry / "cmdline").read_bytes().split(b"\0")
            except (OSError, ValueError):
                continue
            # 실행 파일은 파이썬 스크립트라 python3 …/agv_controller 로도 뜬다 — 앞 두 칸을 본다.
            if any(Path(a.decode("utf-8", "replace")).name == EXECUTABLE for a in args[:2] if a):
                pids.append(pid)
        return sorted(pids)

    #: 이 PC 에서 차량 상태를 낼 수 있는 실행 파일 — AGV 제어 노드 · 3S 차량 모의기.
    VEHICLE_EXECUTABLES = (EXECUTABLE, "vehicle_sim_node")

    @classmethod
    def local_vehicle_nodes(cls, exclude: int | None = None) -> list[tuple[int, str]]:
        """이 PC 에서 도는 차량 노드(프로세스 번호, 이름) — 받는 노드가 이 PC 인지 외부 PC 인지 가린다."""
        found: list[tuple[int, str]] = []
        proc = Path("/proc")
        if not proc.is_dir():
            return found
        for entry in proc.glob("[0-9]*"):
            try:
                pid = int(entry.name)
                if pid in (os.getpid(), exclude):
                    continue
                args = (entry / "cmdline").read_bytes().split(b"\0")
            except (OSError, ValueError):
                continue
            for arg in args[:2]:
                name = Path(arg.decode("utf-8", "replace")).name if arg else ""
                if name in cls.VEHICLE_EXECUTABLES:
                    found.append((pid, name))
                    break
        return sorted(found)

    # ------------------------------------------------------------------ 실행
    def start(self) -> bool:
        """띄운다. 이미 우리 것이 돌거나, 다른 AGV 노드가 있거나, 못 찾으면 안 띄우고 False."""
        if self.running:
            return False
        others = self.local_pids()
        if others:
            self._set_state(f"이 PC 의 다른 AGV 제어 노드 사용 중 (pid {', '.join(map(str, others))})")
            self.activity.emit("AGV 제어 노드가 이 PC 에서 이미 돌고 있습니다 — RCS 는 따로 띄우지 않고 그대로 씁니다.")
            return False
        if self.external_present():
            self._set_state("외부 차량 노드 사용 중 (vehicle/robot_status 가 이미 들어옴)")
            self.activity.emit("차량 상태가 이미 들어옵니다(다른 PC 의 AGV 노드 · 차량 모의기) — RCS 는 AGV 제어 "
                               "노드를 따로 띄우지 않습니다.")
            return False
        command = self.command()
        if command is None:
            self._set_state("못 띄움 — 실행 파일 없음")
            self.activity.emit("AGV 제어 노드(steer_agv_controller_py)를 찾지 못했습니다 — 워크스페이스를 빌드 · "
                               "소싱했는지 확인하세요(colcon build · source install/setup.bash).")
            return False
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            DEBUG_LOG_DIR.mkdir(parents=True, exist_ok=True)
            self._log = open(LOG_PATH, "w", encoding="utf-8")      # noqa: SIM115 — 자식이 쓰는 동안 연다
            self._process = subprocess.Popen(
                command, stdout=self._log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                start_new_session=True, preexec_fn=_die_with_parent, env=os.environ.copy())
        except OSError as exc:
            self._process = None
            self._set_state(f"못 띄움 — {exc}")
            self.activity.emit(f"AGV 제어 노드 실행 실패: {exc}")
            return False
        writes = "읽기 · 쓰기" if self.writes else "읽기만"
        self._set_state(f"RCS 가 실행 중 — PLC {self.endpoint()} ({writes})")
        self.activity.emit(f"AGV 제어 노드를 시작했습니다 — PLC {self.endpoint()} ({writes}), "
                           f"ROS 도메인 {os.environ.get('ROS_DOMAIN_ID', '0') or '0'}.")
        self._watch.start()
        return True

    def stop(self, timeout: float = 5.0) -> None:
        """우리가 띄운 노드만 거둔다."""
        self._watch.stop()
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    pass
        if self._log is not None:
            self._log.close()
            self._log = None
        if process is not None:
            self._set_state("꺼짐")

    def restart(self) -> bool:
        self.stop()
        return self.start()

    def _check(self) -> None:
        """우리 노드가 죽었으면 알린다(기록 끝줄과 함께)."""
        process = self._process
        if process is None or process.poll() is None:
            return
        self._watch.stop()
        code = process.returncode
        self._process = None
        if self._log is not None:
            self._log.close()
            self._log = None
        tail = self.log_tail()
        self._set_state(f"멈춤 (종료 코드 {code})")
        self.activity.emit(f"AGV 제어 노드가 멈췄습니다(종료 코드 {code}) — {tail or LOG_PATH}")

    @staticmethod
    def log_tail(lines: int = 2) -> str:
        try:
            text = LOG_PATH.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        except OSError:
            return ""
        return " / ".join(line.strip() for line in text[-lines:] if line.strip())
