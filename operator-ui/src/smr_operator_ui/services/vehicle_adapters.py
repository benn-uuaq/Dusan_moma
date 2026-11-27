"""실제 차량으로 움직이는 이동 어댑터 (AMR·아웃트리거·리프트·작업 끝).

`DummyMotionAdapter` 와 **같은 모양**이다 — move_to / cancel / pause / reset,
position / target / moving, 시그널 arrived / activity / position_changed.
그래서 시퀀서와 마킹 순회는 고치지 않고 `SwitchableMotion` 으로 더미와
이것을 바꿔 끼운다.

규칙은 AGV 담당이 만든 제어 노드(src/steer_agv — QUAD_STEER_AGV_CONTROLLER, 2026-11-23)를 따른다.
vehicle_sim 도 같은 규칙으로 돈다.

  AMR     : SetJob(job_id 새 번호, set_dist = 이번 이동 거리 m, 부호 = 방향, radius = 모재 반지름 m)
            -> 차량이 STOP/PAUSE 가 되면 RobotControl("RUNNING") -> RUNNING 을 한 번 본 뒤 HOLD 이고
            mv_dist ≈ set_dist 면 도착. 차량은 도착하면 스스로 HOLD(아웃트리거 고정)한다.
            일시정지 = PAUSE(작업을 둔다) → 재개 = RUNNING(같은 작업을 잇는다). 정지 = STOP + job_cancel.
  아웃트리거: 고정 = 거리 0 SetJob + RUNNING → 도착 HOLD(hold SET). 자동 모드에서는 아웃트리거를 따로 내릴
            명령이 없다 — 도착이 곧 고정이다. 해제 = RobotControl("FINISH") → hold RELEASE.
  리프트   : SetMvLift(set_height = 목표 m, set_speed) 서비스 lift_control -> lift_h ≈ 목표면 도착.
            아웃트리거 고정 · 서 있을 때만 움직인다.
  작업 끝  : RobotControl("FINISH") -> 받아들여지고 hold RELEASE · 서 있으면 끝. 차량이 홀딩을 풀고 아웃트리거를
            올려 이동할 수 있는 상태(state FINISH)가 된다 — 다음 주행 전에 보낸다.

차량이 ERROR 가 되거나, 명령이 거절되거나, 제한 시간을 넘기면 도착을 내지
않고 `problem` 으로 알린다 — 시퀀서는 거기서 기다린 채로 멈춘다(정지로 끝낸다).
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .vehicle_client import FINISH_RESULT

#: 차량이 서 있다고 볼 상태 — 이때만 RUNNING 을 보낸다(FINISH 에서 새 작업을 받으면 곧 STOP 이 된다).
READY_TO_RUN = ("STOP", "PAUSE")


class _VehicleStep(QObject):
    """차량 명령 하나를 보내고 상태로 끝을 판정하는 공통 틀."""

    arrived = pyqtSignal()
    activity = pyqtSignal(str)
    position_changed = pyqtSignal(float)
    #: 도착을 못 낸 이유(오류·거절·시간 초과). 앱이 알람으로 올린다.
    problem = pyqtSignal(str)

    #: 명령 이름 — command_result 에서 내 것을 가려낸다.
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

    def pause(self) -> None:
        """일시정지 — 기본은 정지와 같다(재개는 같은 목표로 move_to 를 다시 부른다)."""
        self.cancel()

    def reset(self, position: float = 0.0) -> None:
        self._finish(False)
        self._position = self._target = float(position)
        self.position_changed.emit(self._position)

    # ---- 공통 흐름 -----------------------------------------------------------
    def _begin(self, label: str, timeout_ms: int | None = None) -> None:
        self._moving = True
        self._label = label
        self._timeout_ms = int(timeout_ms or self.TIMEOUT_MS)
        self._timer.start(self._timeout_ms)
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
        limit_ms = getattr(self, "_timeout_ms", self.TIMEOUT_MS)
        self._fail(f"{limit_ms // 1000}초 안에 끝나지 않았습니다")

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


class _JobStep(_VehicleStep):
    """SetJob → (차량이 STOP/PAUSE 가 되면) RUNNING 을 보내는 공통 틀. AMR 이동과 아웃트리거 고정이 쓴다."""

    def __init__(self, name: str, client, parent: QObject | None = None) -> None:
        super().__init__(name, client, parent)
        self._job_id = ""
        self._run_pending = False     # set_job 이 받아들여졌다 — 차량이 STOP/PAUSE 가 되면 RUNNING
        self._running_seen = False
        self._job_accepted = False    # 보낸 set_job 을 차량이 받아들였다(차량에 작업이 등록됐다)
        self._drop_when_accepted = False   # 답이 오기 전에 접었다 — 받아들여지면 바로 버린다
        #: 이동 작업 값(속도 m/s, 거리 유지 m, 모재 반지름 m)을 주는 함수. 앱이 넣는다.
        self.job_values = lambda: (0.1, 0.0, 0.0)

    def _watched_commands(self) -> tuple[str, ...]:
        return ("set_job", "robot_control")

    def _send_job(self, set_dist_m: float) -> bool:
        speed, standoff, radius = self.job_values()
        self._job_id = self._client.new_job_id()
        self._run_pending = False
        self._running_seen = False
        self._job_accepted = False
        self._drop_when_accepted = False
        return bool(self._client.set_job(self._job_id, set_dist_m, speed,
                                         standoff=standoff, radius=max(float(radius), 0.0)))

    def _on_command_result(self, name: str, ok: bool, message: str) -> None:
        if name == "set_job" and self._job_id:
            if not ok:
                self._job_id = ""                     # 등록되지 않았다 — 버릴 것도 없다
                self._drop_when_accepted = False
            elif not self._job_accepted:
                self._job_accepted = True
                if self._drop_when_accepted:          # 그사이 접었다 — 남지 않게 바로 버린다
                    self._drop_when_accepted = False
                    self._drop_job()
                    return
        super()._on_command_result(name, ok, message)
        if self._moving and ok and name == "set_job" and self._job_id and not self._run_pending \
                and not self._running_seen:
            self._run_pending = True
            self._try_run(self._client.last_status)

    def _drop_job(self) -> None:
        """차량에 등록한 이번 작업을 버린다 — STOP + job_cancel (2026-11-24 검토).

        AGV 노드는 끝나지 않은 작업이 남아 있으면 다음 SetJob 을 「busy, missing or duplicate job_id」로
        거절하고, job_cancel 이 있어야 작업을 지운다. 그래서 RCS 가 작업을 접을 때(정지 · ERUT 정지 · 시간 초과 ·
        출발 거절) 남지 않게 한다.
          · set_job 답이 아직이면 받아들여지는 즉시 버린다.
          · 차량이 HOLD 면 보내지 않는다 — 도착해 끝난 작업이고, job_cancel 의 STOP 이 고정(HOLD)을 흔들 수
            있다. 다음 주행 전 작업 끝(FINISH)이 남은 작업도 끝낸다.
        """
        if not self._job_id:
            return
        if not self._job_accepted:
            self._drop_when_accepted = True
            return
        if self._client.last_status.get("state") != "HOLD":
            self._client.control("STOP", job_cancel=True)
        self._job_id = ""

    def _fail(self, why: str) -> None:
        """실패(시간 초과 · 출발 거절)하면 등록한 작업도 버린다 — 예전에는 그대로 두어 시간 초과 뒤에도 차량이
        계속 가거나, 남은 작업 때문에 다음 SetJob 이 거절될 수 있었다."""
        was_moving = self._moving
        super()._fail(why)
        if was_moving:
            self._run_pending = False
            self._drop_job()

    def _try_run(self, status: dict) -> None:
        """받아들여진 작업을 출발시킨다 — 차량이 STOP/PAUSE 일 때만(FINISH 에서 등록하면 곧 STOP 이 된다)."""
        if not self._run_pending:
            return
        state = status.get("state")
        if state in READY_TO_RUN:
            self._run_pending = False
            self._client.control("RUNNING")
        elif state == "HOLD" and status.get("hold") == "SET":
            self._fail("아웃트리거가 고정돼 있어 출발할 수 없습니다 — 먼저 작업 끝(FINISH)이 필요합니다")


class VehicleAmrAdapter(_JobStep):
    """차량 주행. 목표는 작업 시작점부터의 거리 [mm](더미와 같은 단위)."""

    COMMAND = "set_job"
    #: 도착 허용 오차 [m] — AGV 노드는 5 mm 안에서 서면 도착으로 친다(distance_tolerance 0.005).
    TOLERANCE_M = 0.006

    #: 제한 시간 = TIMEOUT_MS + 달릴 시간 × 이 배수 — 주행 속도가 느리거나(기본 0.1 m/s) 한 바퀴
    #: (캘리브레이션)처럼 먼 이동이 고정 120 초에 걸려 실패하지 않게(2026-11-12).
    TRAVEL_MARGIN = 1.5

    def __init__(self, client, speed_mps: float = 0.1, parent: QObject | None = None) -> None:
        super().__init__("AMR", client, parent)
        self.speed_mps = float(speed_mps)
        #: 전체 속도 비율(메인 화면 속도 바, 0.02~1.0) — 보내는 주행 속도 = speed_mps × 이 값(2026-11-26).
        self.speed_scale = 1.0
        #: 차량이 모재를 돌며 지킬 거리 [m] — SetJob.standoff (RCS 차량 설정 「모재 거리 유지」).
        self.standoff_m = 0.0
        #: 목표 대상(모재) 반지름 [m]을 돌려주는 함수 — SetJob.radius. 보낼 때마다 묻는다. 없으면 0.
        self.radius_source = None
        self.job_values = self._job_values
        self._base = 0.0          # 이번 이동을 시작한 위치 [mm]
        self._set_dist = 0.0      # 이번 이동 거리 [m]
        self._paused = False      # PAUSE 로 세워 둔 작업이 있다 — 같은 목표로 move_to 하면 RUNNING 으로 잇는다

    @property
    def effective_speed_mps(self) -> float:
        """차량에 보내는 주행 속도 [m/s] — 차량 설정 속도 × 전체 속도 비율."""
        return self.speed_mps * max(0.0, min(float(self.speed_scale), 1.0))

    def _job_values(self) -> tuple[float, float, float]:
        radius = float(self.radius_source()) if self.radius_source is not None else 0.0
        return self.effective_speed_mps, self.standoff_m, radius

    @property
    def paused(self) -> bool:
        """PAUSE 로 세워 둔 이동이 남아 있다(재개 전에는 FINISH 를 보내면 안 된다 — 작업이 끝나 버린다)."""
        return self._paused

    def move_to(self, target: float | int, unit: str = "", label: str = "",
                timeout_ms: int | None = None) -> None:
        """`timeout_ms` 를 주면 그 시간을 쓴다(캘리브레이션 한 바퀴 — 차량 설정의 최대 시간)."""
        target = float(target)
        if self._paused and abs(target - self._target) < 0.5:
            self._resume(label or f"{target:g}{unit}")
            return
        if self._paused:
            # 다른 곳으로 간다 — 세워 둔 작업은 버린다.
            self._paused = False
            self._client.control("STOP", job_cancel=True)
        self._target = target
        delta_mm = self._target - self._position
        speed = self.effective_speed_mps
        travel_ms = (abs(delta_mm) / 1000.0 / speed * 1000.0 * self.TRAVEL_MARGIN
                     if speed > 0 else 0.0)
        self._begin(label or f"{target:g}{unit}",
                    int(timeout_ms) if timeout_ms else self.TIMEOUT_MS + int(travel_ms))
        if abs(delta_mm) < 0.5:
            QTimer.singleShot(0, lambda: self._finish(True))
            return
        self._base = self._position
        self._set_dist = delta_mm / 1000.0
        if not self._send_job(self._set_dist):
            self._fail("SetJob 을 보내지 못했습니다")

    def pause(self) -> None:
        """일시정지 — 차량에 PAUSE. 작업(job_id · 간 거리)은 차량에 남는다. 재개는 같은 목표로 move_to."""
        if not self._moving or not self._job_id:
            self.cancel()
            return
        self._moving = False
        self._timer.stop()
        self._paused = True
        self._run_pending = False
        self._client.control("PAUSE")

    def _resume(self, label: str) -> None:
        self._paused = False
        remaining_mm = abs(self._target - self._position)
        speed = self.effective_speed_mps
        travel_ms = (remaining_mm / 1000.0 / speed * 1000.0 * self.TRAVEL_MARGIN
                     if speed > 0 else 0.0)
        self._begin(f"{label} (재개)", self.TIMEOUT_MS + int(travel_ms))
        self._running_seen = False
        self._run_pending = True
        self._try_run(self._client.last_status)

    def cancel(self) -> None:
        """정지 — 차량에 STOP + job_cancel(작업을 버린다). 세워 둔(PAUSE) 작업도 버린다.
        set_job 답이 아직이면 받아들여지는 즉시 버린다(_drop_job)."""
        if self._moving or self._paused:
            self._finish(False)
            self._paused = False
            self._run_pending = False
            self._drop_job()

    def reset(self, position: float = 0.0) -> None:
        self._paused = False
        super().reset(position)

    def _track(self, status: dict) -> None:
        if self._moving:
            self._try_run(status)
        if not self._moving or status.get("job_id") != self._job_id:
            return
        mv_m = float(status.get("mv_dist", 0.0))
        self._position = self._base + mv_m * 1000.0
        self.position_changed.emit(self._position)
        state = status.get("state")
        if state == "RUNNING":
            self._running_seen = True
            self._run_pending = False      # 이미 달린다 — RUNNING 을 또 보낼 일이 없다
        elif (self._running_seen and state == "HOLD"
              and abs(mv_m - self._set_dist) <= self.TOLERANCE_M):
            self._position = self._target
            self.position_changed.emit(self._position)
            self._finish(True)


class VehicleOutriggerAdapter(_JobStep):
    """아웃트리거. move_to(1) = 고정, move_to(0) = 해제 (더미와 같이 1 이 고정).

    자동 모드에서는 아웃트리거만 내리는 명령이 없다 — 차량은 작업이 도착하면 스스로 HOLD 한다. 그래서
    고정은 **거리 0 작업**(SetJob 0 m + RUNNING → 도착 HOLD)으로, 해제는 작업 끝(FINISH)으로 한다.
    """

    TIMEOUT_MS = 30_000

    def __init__(self, client, parent: QObject | None = None) -> None:
        super().__init__("아웃트리거", client, parent)
        self._goal = "SET"

    def _watched_commands(self) -> tuple[str, ...]:
        return ("set_job", "robot_control", FINISH_RESULT)

    def move_to(self, target: float | int, unit: str = "", label: str = "") -> None:
        self._target = float(target)
        self._goal = "SET" if self._target else "RELEASE"
        self._begin(label or ("고정" if self._goal == "SET" else "해제"))
        self._job_id = ""
        status = self._client.last_status
        if status.get("hold") == self._goal:
            QTimer.singleShot(0, lambda: self._finish(True))
            return
        if self._goal == "SET" and status.get("state") == "HOLD":
            # 차량이 스스로 HOLD 하는 중이다(도착 · 정렬 끝) — 작업을 또 보내면 거절된다. 고정을 기다린다.
            return
        if self._goal == "SET":
            if not self._send_job(0.0):
                self._fail("고정 작업(거리 0 SetJob)을 보내지 못했습니다")
        elif not self._client.finish_job():
            self._fail("작업 끝(FINISH)을 보내지 못했습니다")

    def cancel(self) -> None:
        """고정(거리 0 작업) 중이면 그 작업을 버린다(STOP + job_cancel). 해제(FINISH)는 차량이 마저 한다 —
        보내지 않는다(FINISH 상태에 STOP 은 「FINISH waits for a new job or job_cancel」로 거절된다)."""
        if not self._moving:
            return
        goal = self._goal
        self._finish(False)
        self._run_pending = False
        if goal == "SET":
            self._drop_job()

    def _track(self, status: dict) -> None:
        if self._moving and self._goal == "SET":
            self._try_run(status)
        hold = status.get("hold")
        self._position = 1.0 if hold == "SET" else 0.0
        if not self._moving or hold != self._goal:
            return
        if status.get("state") != ("HOLD" if self._goal == "SET" else "FINISH"):
            return
        self._finish(True)


class VehicleFinishAdapter(_VehicleStep):
    """그 자리 작업 끝 — RobotControl(state = "FINISH").

    차량은 작업이 도착하면 HOLD(아웃트리거 고정)하고 RCS 의 끝 신호를 기다린다. FINISH 를 받으면 홀딩을 풀고
    아웃트리거를 올려 이동할 수 있는 상태(state FINISH · hold RELEASE)가 된다. 그래서 받아들여진 뒤 차량이
    **state FINISH · hold RELEASE 를 보고하면** 끝이다 — 받아들였다는 답만으로 끝내면 RCS 가 들고 있던 옛 상태(STOP)로
    다음 작업을 바로 출발시켜 "release outriggers and stop or pause before RUNNING" 으로 거절된다.
    앱은 다음 주행 전 순서(리프트 0 → 이것 → 주행)에 쓴다.
    move_to 의 목표 값은 쓰지 않는다(다른 장비와 같은 모양으로 부르려고 받기만 한다).
    """

    COMMAND = FINISH_RESULT
    TIMEOUT_MS = 30_000          # 아웃트리거를 올리는 시간 포함

    def __init__(self, client, parent: QObject | None = None) -> None:
        super().__init__("작업 끝", client, parent)
        self._accepted = False

    def move_to(self, target: float | int = 0, unit: str = "", label: str = "") -> None:
        self._target = float(target)
        self._accepted = False
        self._align_cleared = False
        self._begin(label or "홀딩 해제 · 아웃트리거 올림")
        if not self._client.finish_job():
            self._fail("작업 끝(FINISH)을 보내지 못했습니다")

    def cancel(self) -> None:
        # 홀딩 해제 · 아웃트리거 올림은 차량이 마저 한다 — 기다리기만 그만둔다(STOP 을 보내지 않는다).
        self._finish(False)

    #: FINISH 가 「ALIGN is active」로 거절되면 남은 정렬을 지우고(STOP + job_cancel) 이만큼 뒤 한 번 더 보낸다.
    ALIGN_CLEAR_RETRY_MS = 1000

    def _on_command_result(self, name: str, ok: bool, message: str) -> None:
        if (self._moving and not ok and name == self.COMMAND and "align is active" in str(message).lower()
                and not getattr(self, "_align_cleared", False)):
            # 지난 캘리브레이션 정렬이 차량에 남았다(실패 · 시간 초과로 접혔는데 지우지 않았다, 2026-11-27 현장).
            self._align_cleared = True
            self.activity.emit("[차량] 남아 있던 QR 정렬을 지우고 작업 끝(FINISH)을 다시 보냅니다.")
            self._client.control("STOP", job_cancel=True)
            QTimer.singleShot(self.ALIGN_CLEAR_RETRY_MS, self._resend)
            return
        super()._on_command_result(name, ok, message)
        if self._moving and ok and name == self.COMMAND:
            self._accepted = True
            self._track(self._client.last_status)

    def _resend(self) -> None:
        if self._moving and not self._accepted and not self._client.finish_job():
            self._fail("작업 끝(FINISH)을 보내지 못했습니다")

    def _track(self, status: dict) -> None:
        if (self._moving and self._accepted and status.get("hold") == "RELEASE"
                and status.get("state") == "FINISH"):
            self._finish(True)


class VehicleLiftAdapter(_VehicleStep):
    """리프트. 목표·위치는 mm (차량은 m 로 주고받는다).

    lift_control(SetMvLift)로 보낸다 — 자동 모드에서 set_speed 는 차량 설정 「리프트 속도」.
    수동 제어 화면은 앱이 ManualCommand(cmd_mv_lift · lift_height)로 따로 보낸다.

    도착 판정(2026-11-24): 차량이 RobotStatus.lift_state(STOP · RUNNING · PAUSE · FINISH — AGV 측과 정함)를
    채우면 **도착 신호(FINISH)를 받은 뒤 높이를 비교**한다
      · FINISH 이고 높이가 목표 ±TOLERANCE_MM 안 → 도착
      · 이번 명령의 RUNNING(PAUSE)을 본 뒤의 FINISH 인데 높이가 밖 → 실패(멈춘 높이 · 목표를 알린다)
      · RUNNING 을 보기 전의 FINISH 는 지난 명령의 것일 수 있다 — 높이가 밖이면 기다린다
      · RUNNING 을 본 뒤 STOP → 목표 전에 멈췄다(실패). 차량 오류는 공통 처리(차량 state ERROR)가 알린다.
    lift_state 가 비어 있으면(안 채우는 AGV · 옛 빌드) 예전처럼 높이만 본다.
    """

    COMMAND = "lift_control"
    #: 도착 허용 오차 [mm] — AGV 노드는 3 mm 안이면 리프트 작업을 끝낸다(lift_tolerance 0.003).
    TOLERANCE_MM = 5.0
    TIMEOUT_MS = 180_000

    def __init__(self, client, speed_mps: float = 0.05, parent: QObject | None = None) -> None:
        super().__init__("리프트", client, parent)
        self.speed_mps = float(speed_mps)    # SetMvLift.set_speed — 차량 설정 「리프트 속도」
        #: 전체 속도 비율(메인 화면 속도 바) — 보내는 리프트 속도 = speed_mps × 이 값(2026-11-26).
        self.speed_scale = 1.0
        self._count = 0

    def move_to(self, target: float | int, unit: str = "", label: str = "") -> None:
        self._target = float(target)
        self._begin(label or f"{target:g}{unit}")
        if abs(self._position - self._target) <= self.TOLERANCE_MM and self._client.last_status:
            QTimer.singleShot(0, lambda: self._finish(True))
            return
        self._count += 1
        self._state_seen = False              # 이번 명령의 RUNNING · PAUSED 를 봤는가
        speed = self.speed_mps * max(0.0, min(float(self.speed_scale), 1.0))
        if not self._client.lift(f"RCS-L{self._count:04d}", self._target / 1000.0, speed):
            self._fail("명령을 보내지 못했습니다")

    def _track(self, status: dict) -> None:
        self._position = float(status.get("lift_h", 0.0)) * 1000.0
        self.position_changed.emit(self._position)
        if not self._moving:
            return
        error = abs(self._position - self._target)
        state = str(status.get("lift_state") or "")
        if not state:                         # 차량이 lift_state 를 안 채운다 — 높이로만
            if error <= self.TOLERANCE_MM:
                self._finish(True)
            return
        where = f"높이 {self._position:,.0f} mm · 목표 {self._target:,.0f} mm"
        if state in ("RUNNING", "PAUSE"):
            self._state_seen = True
        elif state == "FINISH":
            if error <= self.TOLERANCE_MM:
                self._finish(True)
            elif getattr(self, "_state_seen", False):
                self._fail(f"도착 신호(FINISH)를 받았지만 {where} — {error:,.0f} mm 다릅니다"
                           f"(허용 ±{self.TOLERANCE_MM:g} mm)")
        elif state == "STOP" and getattr(self, "_state_seen", False):
            self._fail(f"목표 전에 멈췄습니다(lift_state STOP, {where})")
