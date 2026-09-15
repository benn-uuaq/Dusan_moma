"""실제 차량으로 움직이는 이동 어댑터 (AMR·아웃트리거·리프트).

`DummyMotionAdapter` 와 **같은 모양**이다 — move_to / cancel / reset,
position / target / moving, 시그널 arrived / activity / position_changed.
그래서 시퀀서와 마킹 순회는 고치지 않고 `SwitchableMotion` 으로 더미와
이것을 바꿔 끼운다.

받은 자료에 없는 동작 규칙은 이렇게 가정했다(vehicle_sim 과 같다 — 실제
차량 규칙이 다르면 여기와 모의기를 같이 고친다).

  AMR     : SetJob(set_dist = 이번 이동 거리 m, 부호 = 방향) -> RobotControl("RUNNING")
            -> RUNNING 을 한 번 본 뒤 STOP/HOLD 이고 mv_dist ≈ set_dist 면 도착.
  아웃트리거: ManualCommand(cmd_outrg_set = 2 고정 / 1 해제) -> hold 가 SET/RELEASE 면 도착.
  리프트   : ManualCommand(cmd_mv_lift = 1, lift_height = 목표 m) -> lift_h ≈ 목표면 도착.
            (받은 자료대로 아웃트리거 고정에서만 움직인다.)

차량이 ERROR 가 되거나, 명령이 거절되거나, 제한 시간을 넘기면 도착을 내지
않고 `problem` 으로 알린다 — 시퀀서는 거기서 기다린 채로 멈춘다(정지로 끝낸다).
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


class _VehicleStep(QObject):
    """차량 명령 하나를 보내고 상태로 끝을 판정하는 공통 틀."""

    arrived = pyqtSignal()
    activity = pyqtSignal(str)
    position_changed = pyqtSignal(float)
    #: 도착을 못 낸 이유(오류·거절·시간 초과). 앱이 알람으로 올린다.
    problem = pyqtSignal(str)

    #: 명령 이름 — command_result 에서 내 것을 가려낼 때 쓴다.
    COMMAND = "manual_command"
    TIMEOUT_MS = 120_000

    def __init__(self, name: str, client, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._name = name
        self._client = client
        self._position = 0.0
        self._target = 0.0
        self._moving = False
        self._label = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        client.status_changed.connect(self._on_status)
        client.command_result.connect(self._on_command_result)

    @property
    def position(self) -> float:
        return self._position

    @property
    def target(self) -> float:
        return self._target

    @property
    def moving(self) -> bool:
        return self._moving

    def cancel(self) -> None:
        if self._moving:
            self._finish(False)
            self._client.control("STOP")

    def reset(self, position: float = 0.0) -> None:
        self._finish(False)
        self._position = self._target = float(position)
        self.position_changed.emit(self._position)

    # ---- 공통 흐름 -----------------------------------------------------------
    def _begin(self, label: str) -> None:
        self._moving = True
        self._label = label
        self._timer.start(self.TIMEOUT_MS)
        self.activity.emit(f"[차량] {self._name} → {label}")

    def _finish(self, ok: bool) -> None:
        was = self._moving
        self._moving = False
        self._timer.stop()
        if ok and was:
            self.activity.emit(f"[차량] {self._name} {self._label} 도착")
            self.arrived.emit()

    def _fail(self, why: str) -> None:
        if not self._moving:
            return
        self._finish(False)
        message = f"{self._name} {self._label}: {why}"
        self.activity.emit(f"[차량] {message}")
        self.problem.emit(message)

    def _on_timeout(self) -> None:
        self._fail(f"{self.TIMEOUT_MS // 1000}초 안에 끝나지 않았습니다")

    def _on_command_result(self, name: str, ok: bool, message: str) -> None:
        if self._moving and not ok and name in self._watched_commands():
            self._fail(f"명령이 거절됐습니다 — {message}")

    def _watched_commands(self) -> tuple[str, ...]:
        return (self.COMMAND,)

    def _on_status(self, status: dict) -> None:
        if self._moving and status.get("state") == "ERROR":
            self._fail(f"차량 오류 {status.get('error_code')} {status.get('error_msg', '')}".strip())
            return
        self._track(status)

    def _track(self, status: dict) -> None:  # pragma: no cover - 하위 클래스가 채운다
        raise NotImplementedError


class VehicleAmrAdapter(_VehicleStep):
    """차량 주행. 목표는 작업 시작점부터의 거리 [mm](더미와 같은 단위)."""

    COMMAND = "set_job"
    TOLERANCE_M = 0.002

    def __init__(self, client, speed_mps: float = 0.2, parent: QObject | None = None) -> None:
        super().__init__("AMR", client, parent)
        self.speed_mps = float(speed_mps)
        #: SetJob 의 total_distance / total_height 로 싣는 작업 전체 크기 [m].
        self.totals = (0.0, 0.0)
        self._base = 0.0          # 이번 이동을 시작한 위치 [mm]
        self._set_dist = 0.0      # 이번 이동 거리 [m]
        self._job_id = ""
        self._count = 0
        self._running_seen = False
        self._started = False

    def _watched_commands(self) -> tuple[str, ...]:
        return ("set_job", "robot_control")

    def move_to(self, target: float | int, unit: str = "", label: str = "") -> None:
        self._target = float(target)
        delta_mm = self._target - self._position
        self._begin(label or f"{target:g}{unit}")
        if abs(delta_mm) < 0.5:
            QTimer.singleShot(0, lambda: self._finish(True))
            return
        self._count += 1
        self._job_id = f"RCS-{self._count:04d}"
        self._base = self._position
        self._set_dist = delta_mm / 1000.0
        self._running_seen = False
        self._started = False
        total_d, total_h = self.totals
        if not self._client.set_job(self._job_id, self._set_dist, self.speed_mps,
                                    total_distance=total_d, total_height=total_h):
            self._fail("SetJob 을 보내지 못했습니다")

    def _on_command_result(self, name: str, ok: bool, message: str) -> None:
        super()._on_command_result(name, ok, message)
        # 작업 설정이 받아들여지면 출발시킨다.
        if self._moving and ok and name == "set_job" and not self._started:
            self._started = True
            self._client.control("RUNNING")

    def _track(self, status: dict) -> None:
        if not self._moving or status.get("job_id") != self._job_id:
            return
        mv_m = float(status.get("mv_dist", 0.0))
        self._position = self._base + mv_m * 1000.0
        self.position_changed.emit(self._position)
        state = status.get("state")
        if state == "RUNNING":
            self._running_seen = True
        elif (self._running_seen and state in ("STOP", "HOLD")
              and abs(mv_m - self._set_dist) <= self.TOLERANCE_M):
            self._position = self._target
            self.position_changed.emit(self._position)
            self._finish(True)


class VehicleOutriggerAdapter(_VehicleStep):
    """아웃트리거. move_to(1) = 고정, move_to(0) = 해제 (더미와 같이 1 이 고정)."""

    TIMEOUT_MS = 30_000

    def __init__(self, client, parent: QObject | None = None) -> None:
        super().__init__("아웃트리거", client, parent)
        self._goal = "SET"

    def move_to(self, target: float | int, unit: str = "", label: str = "") -> None:
        self._target = float(target)
        self._goal = "SET" if self._target else "RELEASE"
        self._begin(label or ("고정" if self._goal == "SET" else "해제"))
        if self._client.last_status.get("hold") == self._goal:
            QTimer.singleShot(0, lambda: self._finish(True))
            return
        if not self._client.manual(cmd_outrg_set=2 if self._goal == "SET" else 1):
            self._fail("명령을 보내지 못했습니다")

    def _track(self, status: dict) -> None:
        hold = status.get("hold")
        self._position = 1.0 if hold == "SET" else 0.0
        if self._moving and hold == self._goal:
            self._finish(True)


class VehicleLiftAdapter(_VehicleStep):
    """리프트. 목표·위치는 mm (차량은 m 로 주고받는다)."""

    TOLERANCE_MM = 2.0
    TIMEOUT_MS = 180_000

    def __init__(self, client, parent: QObject | None = None) -> None:
        super().__init__("리프트", client, parent)

    def move_to(self, target: float | int, unit: str = "", label: str = "") -> None:
        self._target = float(target)
        self._begin(label or f"{target:g}{unit}")
        if abs(self._position - self._target) <= self.TOLERANCE_MM and self._client.last_status:
            QTimer.singleShot(0, lambda: self._finish(True))
            return
        if not self._client.manual(cmd_mv_lift=1, lift_height=self._target / 1000.0):
            self._fail("명령을 보내지 못했습니다")

    def _track(self, status: dict) -> None:
        self._position = float(status.get("lift_h", 0.0)) * 1000.0
        self.position_changed.emit(self._position)
        if self._moving and abs(self._position - self._target) <= self.TOLERANCE_MM:
            self._finish(True)
