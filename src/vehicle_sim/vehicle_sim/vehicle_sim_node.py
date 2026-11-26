"""차량(AMR·리프트·아웃트리거) 제어 노드 모의기.

AGV 담당이 만든 실제 제어 노드(src/steer_agv — QUAD_STEER_AGV_CONTROLLER, 2026-11-23 받음)와
**같은 규칙**으로 돈다. RCS 는 이 모의기와 AGV 제어 노드 + 테스트 PLC 어느 쪽에 붙어도 똑같이 움직여야 한다.
인터페이스는 vehicle_interfaces 를 그대로 쓴다.

  발행   <ns>/robot_status    RobotStatus   (rate_hz, 기본 10 Hz)
         <ns>/start_request   std_msgs/Empty (차량 「작업 시작」 버튼 — 시험용 <ns>/sim/press_start 로 낸다)
         <ns>/robot_estop     std_msgs/Bool  PLC(차량) 비상정지 상태 — AGV 가 내고 RCS 가 본다
  서비스 <ns>/robot_control   RobotControl   state "" · STOP · RUNNING · PAUSE · FINISH, reset, job_cancel
         <ns>/set_job         SetJob
         <ns>/manual_command  ManualCommand  (수동 모드에서만 — 조그 · 아웃트리거 · 수동 리프트)
         <ns>/lift_control    SetMvLift      (리프트 — 자동 모드는 set_speed, 수동 모드는 PLC 속도)
         <ns>/measure_lap     MeasureLap     (캘리브레이션 — AGV 노드는 아직 거절한다: lap_supported 로 흉내)
         <ns>/reset_odometry  ResetOdometry
  (<ns> 기본값 "vehicle" — AGV 노드는 /vehicle 로 고정)

동작 규칙 (AGV 노드 job_manager · agv_controller_node 와 같다)

  운전 모드  mode 파라미터 = PLC DM001. 0 코봇 수동 · 1 주행 수동 · 2 자동(기본). 자동 명령(set_job · RUNNING ·
             FINISH)은 2 에서만, 수동 명령(manual_command)은 0 · 1 에서만, 수동 주행 조그는 1 에서만 받는다.
  1. set_job  등록만 한다(출발하지 않는다). 받는 상태: STOP · HOLD · FINISH. 앞 작업이 끝나지 않았거나(도착 ·
              취소 전) job_id 가 비었거나 **이 노드가 켜진 뒤 한 번이라도 쓴 job_id** 면 거절한다. radius > 0,
              0 ≤ set_speed ≤ 0.98, standoff ≥ 0. FINISH 에서 받으면 곧 STOP 으로 바뀐다.
  2. RUNNING  등록한 작업을 출발 · PAUSE 에서 재개한다. 상태가 STOP · PAUSE 이고 아웃트리거가 풀려 있어야 한다.
  3. 도착     set_dist 만큼 가서 서면 스스로 HOLD — 아웃트리거를 내려 고정하고 state HOLD · hold SET.
              거리 0 작업도 같다(차를 움직이지 않고 고정할 때 RCS 가 쓴다).
  4. PAUSE    작업을 그대로 두고 선다(state PAUSE) — RUNNING 으로 잇는다. STOP 도 작업을 두고 선다(state STOP).
              job_cancel = 1 이면 작업을 버린다. FINISH 상태에서는 STOP · PAUSE 를 거절한다(새 작업 · 취소만).
  5. FINISH   그 자리 일이 끝났다 — 홀딩을 풀고 아웃트리거를 올려 state FINISH · hold RELEASE. 자동 모드에서
              서 있고 리프트가 쉬고 있을 때만 받는다(STOP · HOLD · PAUSE · FINISH).
  6. 리프트   lift_control: 아웃트리거 고정 · STOP/HOLD 에서만. 높이 0 ~ lift_max, 자동 모드 속도 0 < v ≤
              max_lift_speed. 리프트가 움직이는 동안 status.job_id 는 리프트 job_id 다.
              status.lift_state(STOP · RUNNING · PAUSE · FINISH, 2026-11-24): 받으면 RUNNING → 닿으면 FINISH(다음
              명령까지) · PAUSE 면 PAUSE · STOP · 오류면 STOP. lift_stop_error [m] 를 주면 그만큼 모자라게 서고 FINISH 를 낸다.
  7. 수동     manual_command: 수동 모드 · STOP/HOLD 에서만. 조그는 주행 수동(1) · 아웃트리거 해제에서만,
              cmd_init 은 거절(DM105 규약 미정). 조그는 deadman_s 동안 다시 오지 않으면 선다.
  8. 오류     fault_code(PLC 오류) · PLC 끊김(701)은 걸어 둔다(latched) — 원인이 없어진 뒤 reset = 1 로 푼다.
              원인이 남아 있으면 reset 은 "reset cause still active" 로 거절. PLC 는 끊기면 plc_retry_s 마다
              스스로 다시 붙어 본다(plc_connect_s 걸림).
  9. 비상정지 estop 파라미터 = PLC 비상정지(DM030). 서고, 움직이는 명령을 거절하고, robot_estop 으로 낸다.
 10. measure_lap  lap_supported 가 false 면 AGV 노드처럼 거절한다. true(기본)면 LAP: expected_dist 만큼 돌고,
              ALIGN: align_s 동안 정렬만 한 뒤 DONE · lap_origin_ok 로 STOP 에 선다 — 스스로 HOLD 하지 않는다.
              RCS 가 이어서 HOLD(거리 0 작업 → 도착 HOLD)로 아웃트리거를 고정한다(2026-11-24 캘리브레이션 =
              ALIGN → HOLD → prepare). RCS 는 LAP 을 더 쓰지 않는다(한 바퀴는 작업자가 수동으로 돈다).
              PAUSE → lap_state PAUSED · RUNNING 으로 잇고, STOP → FAILED.

화면판: ros2 run vehicle_sim vehicle_sim_ui (모드 바꾸기 · 오류 · 비상정지 버튼)
시험용 파라미터: ros2 param set /vehicle_sim fault_code 21 · plc_fail true · estop true · obstacle_stop true · mode 1
"""

from __future__ import annotations

import math

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, Empty
from std_srvs.srv import Trigger
from vehicle_interfaces.msg import RobotStatus
from vehicle_interfaces.srv import ManualCommand, MeasureLap, ResetOdometry, RobotControl, SetJob, SetMvLift

AUTO, DRIVE_MANUAL, COBOT_MANUAL = 2, 1, 0
MODE_NAMES = {COBOT_MANUAL: "코봇 수동", DRIVE_MANUAL: "주행 수동", AUTO: "자동"}


class VehicleSim(Node):
    def __init__(self, **node_kwargs) -> None:
        # node_kwargs: 시험에서 parameter_overrides 등을 넘긴다.
        super().__init__("vehicle_sim", **node_kwargs)
        p = self.declare_parameter
        ns = p("namespace", "vehicle").value.strip("/")
        self.rate_hz = float(p("rate_hz", 10.0).value)
        self.default_speed = float(p("drive_speed", 0.2).value)     # m/s
        self.jog_speed = float(p("jog_speed", 0.1).value)           # m/s
        self.turn_rate = float(p("turn_rate", 0.2).value)           # rad/s
        self.lift_speed = float(p("lift_speed", 0.05).value)        # m/s — 수동(PLC) 리프트 속도
        self.lift_max = float(p("lift_max", 3.0).value)             # m (AGV 노드 기본 max_lift_height 1.5)
        self.max_lift_speed = float(p("max_lift_speed", 0.2).value)  # m/s — 자동 리프트 속도 상한
        # PLC 가 리프트를 목표에서 이만큼 [m] 모자라게 세우고 FINISH 를 내는 것을 흉내(시험용, 2026-11-24). 0 = 정확히.
        p("lift_stop_error", 0.0)
        self.outrigger_s = float(p("outrigger_s", 1.5).value)       # 고정/해제 걸리는 시간
        self.deadman_s = float(p("deadman_s", 0.4).value)           # AGV 노드 manual_timeout
        self.align_s = float(p("align_s", 3.0).value)               # 곡판 QR 정렬 걸리는 시간
        p("mode", AUTO)                                             # PLC 운전 모드(DM001)
        p("lap_supported", True)                                    # false = AGV 노드처럼 measure_lap 거절
        p("fault_code", 0)                                          # 시험용 PLC 오류 주입
        p("battery_pct", 90)
        p("charging", False)
        p("estop", False)
        p("obstacle_stop", False)
        p("plc_fail", False)                                        # true = PLC 응답 없음 → 701
        self.plc_connect_s = float(p("plc_connect_s", 1.0).value)  # 다시 붙는 데 걸리는 시간
        self.plc_retry_s = float(p("plc_retry_s", 1.0).value)      # 끊긴 뒤 다시 붙어 보는 간격(AGV retry 1 s)

        self.state = "STOP"
        self.hold = "RELEASE"
        self.error_code = 0
        self.error_msg = ""
        self.job = None            # SetJob.Request (캘리브레이션 주행도 이 꼴로 둔다)
        self.job_done = False      # 작업이 도착했거나 FINISH 로 끝났다 — 새 작업을 받는다
        self.used_ids: set[str] = set()
        self.set_dist = 0.0
        self.mv_dist = 0.0
        self.speed = 0.0
        self.odometer = 0.0        # 누적 주행 거리 [m] (position.x 로 낸다)
        self.pos_y = 0.0           # position.y — ResetOdometry 로만 바뀐다
        self.yaw = 0.0
        self.radius = 0.0          # 마지막 SetJob.radius [m] (모재 반지름)
        self.lift_h = 0.0
        self.lift_target = None
        self.lift_job_id = ""
        self.lift_rate = self.lift_speed
        # 리프트 상태(RobotStatus.lift_state, 2026-11-24 AGV 측과 정함): STOP · RUNNING · PAUSE · FINISH.
        # 명령을 받으면 RUNNING, 목표에 닿으면 FINISH(다음 명령까지), 일시정지 PAUSE, 정지 · 오류 STOP.
        self.lift_state = "STOP"
        self.outrigger_timer = 0.0
        self.outrigger_goal = None  # "SET" / "RELEASE" 로 가는 중
        self.after_outrigger = None  # 아웃트리거가 다 움직이면 바꿀 state ("HOLD" 도착 · "FINISH" 작업 끝)
        self.leave_finish = False   # FINISH 에서 새 작업을 받았다 — 다음 주기에 STOP
        # 아웃트리거 다리 3개의 내림 정도 (0 = 올림/해제, 1 = 내려 접지). 화면(vehicle_sim_ui)이 그대로 그린다.
        self.legs = [0.0, 0.0, 0.0]
        self.jog = (0.0, 0.0)       # (전후 속도, 회전 속도)
        self.jog_age = 0.0
        self.standoff = -1.0        # 마지막으로 받은 거리 유지 [m]
        self.lap_state = "IDLE"     # MeasureLap 진행
        self.lap_origin_ok = False
        self.lap_error = ""
        self.align_left = 0.0       # ALIGN(곡판 QR 정렬) 남은 시간 [s]
        # PLC 연결(모의기 안쪽 값 — 밖으로는 701 로만 낸다).
        self.plc_state = "CONNECTED"
        self.plc_error = ""
        self.plc_retries = 0
        self.plc_timer = 0.0        # CONNECTING 남은 시간
        self.plc_retry_left = 0.0   # FAILED 에서 다음 재시도까지
        if bool(self.get_parameter("plc_fail").value):
            self._plc_lost("모의 PLC 응답 없음")

        self.pub = self.create_publisher(RobotStatus, f"{ns}/robot_status", 10)
        self.create_service(RobotControl, f"{ns}/robot_control", self.on_control)
        self.create_service(SetJob, f"{ns}/set_job", self.on_set_job)
        self.create_service(ManualCommand, f"{ns}/manual_command", self.on_manual)
        self.create_service(SetMvLift, f"{ns}/lift_control", self.on_lift)
        self.create_service(MeasureLap, f"{ns}/measure_lap", self.on_measure_lap)
        self.create_service(ResetOdometry, f"{ns}/reset_odometry", self.on_reset_odometry)
        self.start_pub = self.create_publisher(Empty, f"{ns}/start_request", 10)
        self.estop_pub = self.create_publisher(Bool, f"{ns}/robot_estop", 10)
        self.create_service(Trigger, f"{ns}/sim/press_start", self.on_press_start)
        self.dt = 1.0 / self.rate_hz
        self.create_timer(self.dt, self.tick)
        self.get_logger().info(f"차량 모의기 시작 — {ns}/robot_status, {ns}/robot_control, {ns}/set_job, "
                               f"{ns}/manual_command, {ns}/lift_control, {ns}/measure_lap, {ns}/reset_odometry")

    # ------------------------------------------------------------ 공통 판정
    def param(self, name: str):
        return self.get_parameter(name).value

    @property
    def mode(self) -> int:
        return int(self.param("mode"))

    @property
    def estop_now(self) -> bool:
        """PLC(차량) 비상정지 — estop 파라미터."""
        return bool(self.param("estop"))

    @property
    def moving(self) -> bool:
        return self.state == "RUNNING" or any(self.jog)

    @property
    def lift_moving(self) -> bool:
        return self.lift_target is not None and abs(self.lift_target - self.lift_h) > 1e-6

    @property
    def job_pending(self) -> bool:
        """등록했지만 아직 도착 · 끝 · 취소되지 않은 작업이 있다."""
        return self.job is not None and not self.job_done

    def _blocked(self) -> str:
        """움직이는 명령을 받을 수 없는 까닭(AGV 노드 permit · check_motion 과 같은 글) — 없으면 빈 글자."""
        if self.error_code:
            return f"vehicle fault {self.error_code}"
        if self.estop_now or bool(self.param("obstacle_stop")):
            return "motion blocked by E-STOP, obstacle or initialization"
        return ""

    def _cause_active(self) -> bool:
        """리셋해도 풀 수 없는 원인이 남아 있다."""
        return (self.plc_state != "CONNECTED" or bool(int(self.param("fault_code")))
                or self.estop_now or bool(self.param("obstacle_stop")))

    # ------------------------------------------------------------ 서비스
    def on_set_job(self, req, res):
        res.success, res.message = False, self._blocked()
        if res.message:
            return res
        if (self.mode != AUTO or self.state not in ("STOP", "HOLD", "FINISH") or self.moving
                or self.lift_moving):
            res.message = "set_job requires idle automatic mode"
        elif self.job_pending or not req.job_id.strip() or req.job_id in self.used_ids:
            res.message = "busy, missing or duplicate job_id"
        elif not (0 <= req.set_speed <= 0.98 and req.radius > 0 and req.standoff >= 0):
            res.message = "invalid speed, radius or standoff"
        else:
            self.used_ids.add(req.job_id)
            self.job, self.job_done = req, False
            self.set_dist, self.mv_dist = float(req.set_dist), 0.0
            self.standoff = float(req.standoff)
            self.radius = float(req.radius)
            self.lap_state = "IDLE"
            if self.state == "FINISH":
                self.leave_finish = True
            res.success, res.message = True, "accepted"
        return res

    def on_measure_lap(self, req, res):
        """캘리브레이션 주행 — LAP 은 expected_dist 만큼 바로 출발, ALIGN 은 align_s 동안 정렬만 한다."""
        res.success = False
        mode = (getattr(req, "mode", "") or "LAP").strip().upper()
        if mode not in ("LAP", "ALIGN"):
            res.message = "unknown calibration mode"
            return res
        if not bool(self.param("lap_supported")):
            res.message = "QR origin and alignment completion feedback unavailable"
            return res
        res.message = self._blocked()
        if res.message:
            return res
        if (self.mode != AUTO or self.state not in ("STOP", "FINISH") or self.hold != "RELEASE"
                or self.job_pending or self.moving or self.lift_moving):
            res.message = "measure_lap requires idle automatic mode with released outriggers"
            return res
        if mode == "LAP" and float(req.expected_dist) <= 0:
            res.message = "expected_dist must be positive for LAP"
            return res
        dist = float(req.expected_dist) if mode == "LAP" else 0.0
        self.job = SetJob.Request(job_id=req.job_id, set_dist=dist, set_speed=float(req.speed),
                                  standoff=float(req.standoff), radius=max(self.radius, 0.0))
        self.job_done = False
        self.set_dist, self.mv_dist = dist, 0.0
        self.standoff = float(req.standoff)
        self.lap_state, self.lap_origin_ok, self.lap_error = "RUNNING", False, ""
        self.align_left = max(self.align_s, self.dt) if mode == "ALIGN" else 0.0
        self.leave_finish = False
        self.state = "RUNNING"
        res.success, res.message = True, "accepted"
        return res

    def on_control(self, req, res):
        res.success, res.message = False, ""
        state = req.state or ""
        if state not in ("", "STOP", "RUNNING", "PAUSE", "FINISH") or req.reset > 1 or req.job_cancel > 1:
            res.message = "invalid robot_control request"
            return res
        if (req.reset or req.job_cancel) and state in ("RUNNING", "FINISH"):
            res.message = f"{state} conflicts with reset or cancel"
            return res
        if state == "FINISH":
            return self._finish(res)
        if not req.job_cancel and self.state == "FINISH" and state in ("STOP", "PAUSE"):
            res.message = "FINISH waits for a new job or job_cancel"
            return res
        if req.job_cancel or state in ("STOP", "PAUSE"):
            self._stop(pause=state == "PAUSE" and not req.job_cancel, discard=bool(req.job_cancel))
        if req.reset:
            if self._cause_active():
                res.message = "reset cause still active"
                return res
            if self.moving or self.lift_moving or self.state not in ("STOP", "HOLD", "PAUSE", "ERROR"):
                res.message = "reset requires stationary state"
                return res
            self.error_code, self.error_msg = 0, ""
            if self.state == "ERROR":
                self.state = "HOLD" if self.hold == "SET" and self.job_done else "STOP"
        if state == "RUNNING":
            blocked = self._blocked()
            if blocked:
                res.message = blocked
                return res
            if self.mode != AUTO or self.job is None or self.job_done or self.lift_moving:
                res.message = "no resumable automatic drive job"
                return res
            if self.job.set_dist != 0 and self.job.set_speed == 0:
                res.message = "a nonzero drive distance requires positive speed"
                return res
            if self.state not in ("STOP", "PAUSE") or self.hold == "SET" or self.outrigger_goal:
                res.message = "release outriggers and stop or pause before RUNNING"
                return res
            self.state = "RUNNING"
            if self.lap_state == "PAUSED":
                self.lap_state = "RUNNING"
        res.success, res.message = True, "accepted"
        return res

    def _stop(self, pause: bool, discard: bool) -> None:
        """STOP · PAUSE · job_cancel — 움직임을 멈춘다. 작업은 discard 일 때만 버린다."""
        was_running = self.state == "RUNNING"
        self.speed = 0.0
        self.jog = (0.0, 0.0)
        if self.lift_target is not None:
            self.lift_state = "PAUSE" if pause else "STOP"
        self.lift_target = None
        if self.lap_state in ("RUNNING", "PAUSED"):
            if pause:
                self.lap_state = "PAUSED"
            else:
                self.lap_state, self.lap_error = "FAILED", "stopped"
                discard = True
        if discard:
            self.job, self.job_done = None, False
            self.set_dist = self.mv_dist = 0.0
            self.align_left = 0.0
        if self.state in ("RUNNING", "PAUSE", "FINISH", "HOLD", "STOP"):
            if pause and (was_running or self.state == "PAUSE"):
                self.state = "PAUSE"
            elif self.state != "ERROR":
                self.state = "STOP"

    def _finish(self, res):
        res.message = self._blocked()
        if res.message:
            return res
        if (self.mode != AUTO or self.state not in ("STOP", "HOLD", "PAUSE", "FINISH")
                or self.moving or self.lift_moving or abs(self.speed) > 1e-6):
            res.message = "FINISH requires stationary automatic mode and idle lift"
            return res
        if self.job is not None:
            self.job_done = True
        if self.lap_state in ("RUNNING", "PAUSED"):
            self.lap_state, self.lap_error = "FAILED", "finished"
        self.leave_finish = False
        if self.hold == "SET" or self.outrigger_goal == "SET":
            self._start_outrigger("RELEASE")
            self.after_outrigger = "FINISH"
        else:
            self.state = "FINISH"
        res.success, res.message = True, "accepted"
        return res

    def _lift_goal(self, height: float) -> float:
        """리프트가 실제로 설 높이 — lift_stop_error 만큼 모자라게(PLC 가 목표 옆에 세우는 것 흉내)."""
        error = float(self.param("lift_stop_error"))
        if error <= 0 or abs(height - self.lift_h) <= error:
            return height
        return height - math.copysign(error, height - self.lift_h)

    def _lift_refusal(self, height: float, speed: float, manual: bool) -> str:
        """리프트를 못 움직이는 까닭(없으면 빈 글자) — AGV 노드 set_lift 와 같다."""
        blocked = self._blocked()
        if blocked:
            return blocked
        if self.hold != "SET" or self.state not in ("STOP", "HOLD") or self.moving:
            return "lift requires stopped and secured vehicle"
        if any(self.jog):
            return "manual switches are active"
        if not (math.isfinite(height) and 0.0 <= height <= self.lift_max):
            return "lift height out of range"
        if not manual and self.mode == AUTO and not (0.0 < speed <= self.max_lift_speed):
            return "automatic lift speed out of range"
        return ""

    def on_lift(self, req, res):
        """lift_control(SetMvLift) — 자동 모드는 set_speed, 수동 모드는 PLC 속도(lift_speed)."""
        height, speed = float(req.set_height), float(req.set_speed)
        refusal = "lift requires stopped and secured vehicle" if not req.job_id else ""
        refusal = refusal or self._lift_refusal(height, speed, manual=False)
        if refusal:
            res.success, res.message = False, refusal
            return res
        self.lift_rate = speed if self.mode == AUTO else self.lift_speed
        self.lift_target = self._lift_goal(height)
        self.lift_job_id = req.job_id
        self.lift_state = "RUNNING"
        res.success, res.message = True, "accepted"
        return res

    def on_reset_odometry(self, req, res):
        """위치(position x · y · yaw)를 받은 값으로 다시 맞춘다 — 서 있고 끝나지 않은 작업이 없을 때만."""
        res.success = False
        if (self.error_code or self.state not in ("STOP", "HOLD") or self.job_pending
                or self.moving or self.lift_moving):
            res.message = "odometry reset requires a stationary vehicle without a pending drive"
            return res
        if not all(map(math.isfinite, (req.x, req.y, req.yaw))):
            res.message = "nonfinite pose"
            return res
        self.odometer, self.pos_y, self.yaw = float(req.x), float(req.y), float(req.yaw)
        res.success, res.message = True, "accepted"
        return res

    def on_manual(self, req, res):
        res.success, res.message = False, self._blocked()
        if res.message:
            return res
        if self.mode not in (COBOT_MANUAL, DRIVE_MANUAL) or self.state not in ("STOP", "HOLD"):
            res.message = "manual mode required"
            return res
        if req.cmd_init:
            res.message = "DM105 initialize trigger protocol is undefined"
            return res
        if req.cmd_outrg_set not in (0, 1, 2) or req.cmd_mv_lift not in (0, 1):
            res.message = "invalid manual selector"
            return res
        if (req.cmd_mv_fwd and req.cmd_mv_rear) or (req.cmd_trn_left and req.cmd_trn_right):
            res.message = "opposing manual commands"
            return res
        travel = any((req.cmd_mv_fwd, req.cmd_mv_rear, req.cmd_trn_left, req.cmd_trn_right))
        if travel and (self.mode != DRIVE_MANUAL or self.hold == "SET" or req.cmd_outrg_set
                       or req.cmd_mv_lift or self.lift_moving):
            res.message = "drive requires DRIVE Manual mode and released outriggers"
            return res
        if req.cmd_outrg_set and (req.cmd_mv_lift or self.lift_moving or abs(self.speed) > 1e-6):
            res.message = "outrigger requires stationary vehicle and lift"
            return res
        if req.cmd_mv_lift:
            refusal = self._lift_refusal(float(req.lift_height), 0.0, manual=True)
            if refusal:
                res.message = refusal
                return res
            self.lift_rate = self.lift_speed          # 수동 속도는 PLC 가 정한다
            self.lift_target = self._lift_goal(float(req.lift_height))
            self.lift_job_id = "manual"
            self.lift_state = "RUNNING"
        fwd = (1.0 if req.cmd_mv_fwd else 0.0) - (1.0 if req.cmd_mv_rear else 0.0)
        turn = (1.0 if req.cmd_trn_left else 0.0) - (1.0 if req.cmd_trn_right else 0.0)
        self.jog = (fwd * self.jog_speed, turn * self.turn_rate)
        self.jog_age = 0.0
        if req.cmd_outrg_set == 2:
            self._start_outrigger("SET")
        elif req.cmd_outrg_set == 1:
            self._start_outrigger("RELEASE")
        res.success, res.message = True, "accepted"
        return res

    def on_press_start(self, _req, res):
        """시험용 — 차량 「작업 시작」 버튼을 누른 것처럼 start_request 를 낸다."""
        self.start_pub.publish(Empty())
        res.success, res.message = True, "start_request published"
        return res

    # ------------------------------------------------------------ PLC 연결 · 오류
    def _plc_begin(self) -> None:
        self.plc_state = "CONNECTING"
        self.plc_retries += 1
        self.plc_timer = self.plc_connect_s

    def _plc_lost(self, why: str) -> None:
        """PLC 가 끊겼다 — 세우고 701 PLC_COMM_LOST 를 건다(다시 붙어도 reset 전까지 남는다)."""
        self.plc_state, self.plc_error = "FAILED", why
        self.plc_retry_left = self.plc_retry_s
        self._latch(701, f"701 PLC_COMM_LOST — {why}")

    def _plc_tick(self, dt: float) -> None:
        fail = bool(self.param("plc_fail"))
        if self.plc_state == "CONNECTED" and fail:
            self._plc_lost("모의 PLC 응답 없음")
        elif self.plc_state == "CONNECTING":
            self.plc_timer -= dt
            if self.plc_timer <= 0:
                if fail:
                    self._plc_lost(f"모의 PLC 응답 없음 (다시 시도 {self.plc_retries}회)")
                else:
                    self.plc_state, self.plc_error, self.plc_retries = "CONNECTED", "", 0
        elif self.plc_state == "FAILED" and self.plc_retry_s > 0:
            self.plc_retry_left -= dt
            if self.plc_retry_left <= 0:
                self._plc_begin()

    def _latch(self, code: int, text: str) -> None:
        if self.error_code == code:
            return
        self._stop(pause=False, discard=False)
        self.state, self.error_code, self.error_msg = "ERROR", code, text

    # ------------------------------------------------------------ 동작
    def _halt(self) -> None:
        """화면판 「정지」 — STOP 과 같다(작업은 둔다)."""
        self._stop(pause=False, discard=False)

    def _start_outrigger(self, goal: str) -> None:
        if self.hold == goal and self.outrigger_goal is None:
            return
        self.outrigger_goal = goal
        self.outrigger_timer = self.outrigger_s

    def _arrived(self) -> None:
        """작업이 끝까지 가서 섰다 — 스스로 HOLD(아웃트리거 고정)."""
        self.speed = 0.0
        self.job_done = True
        if self.lap_state == "RUNNING":
            self.lap_state, self.lap_origin_ok = "DONE", True
        self.state = "STOP"
        if self.hold == "SET":
            self.state = "HOLD"
        else:
            self._start_outrigger("SET")
            self.after_outrigger = "HOLD"

    def _aligned(self) -> None:
        """QR 정렬(ALIGN)이 끝났다 — STOP 에 선다. 아웃트리거 고정(HOLD)은 RCS 가 이어서 시킨다."""
        self.speed = 0.0
        self.job_done = True
        self.lap_state, self.lap_origin_ok = "DONE", True
        self.state = "STOP"

    @property
    def outrigger_ratio(self) -> float:
        """아웃트리거 전체 내림 정도 0~1 (다리 3개 평균) — 화면이 쓴다."""
        return sum(self.legs) / len(self.legs)

    def snapshot(self) -> dict:
        """화면(vehicle_sim_ui)이 그릴 값 한 벌."""
        return {
            "state": self.state, "hold": self.hold, "mode": self.mode,
            "error_code": self.error_code, "error_msg": self.error_msg,
            "job_id": self.job.job_id if self.job is not None else "", "job_done": self.job_done,
            "set_dist": self.set_dist, "mv_dist": self.mv_dist, "speed": self.speed,
            "odometer": self.odometer, "y": self.pos_y, "yaw": self.yaw, "radius": self.radius,
            "lift_h": self.lift_h, "lift_max": self.lift_max,
            "lift_moving": self.lift_moving,
            "legs": list(self.legs), "outrigger_moving": self.outrigger_goal is not None,
            "jog": self.jog, "estop": self.estop_now,
            "plc_state": self.plc_state, "plc_error": self.plc_error, "plc_retries": self.plc_retries,
        }

    def tick(self) -> None:
        dt = self.dt
        self._plc_tick(dt)
        fault = int(self.param("fault_code"))
        if fault and self.error_code != fault and self.error_code != 701:
            self._latch(fault, f"{fault} 모의 PLC 오류 {fault}")
        if (self.estop_now or bool(self.param("obstacle_stop"))) and (
                self.moving or self.lift_target is not None):
            self._stop(pause=False, discard=False)
        if self.leave_finish and self.state == "FINISH":
            self.state = "STOP"
        self.leave_finish = False

        # 아웃트리거 다리: 전체 명령(고정 · 해제)의 목표로 함께 움직인다.
        leg_step = dt / max(self.outrigger_s, 1e-6)
        if self.outrigger_goal is not None:
            goal = 1.0 if self.outrigger_goal == "SET" else 0.0
            self.legs = [min(1.0, max(0.0, v + math.copysign(leg_step, goal - v)))
                         if abs(goal - v) > 1e-9 else goal for v in self.legs]
            self.outrigger_timer -= dt
            if self.outrigger_timer <= 0:
                self.hold = self.outrigger_goal
                self.outrigger_goal = None
                if self.after_outrigger and self.state != "ERROR":
                    self.state = self.after_outrigger
                self.after_outrigger = None

        if self.state == "RUNNING" and self.align_left > 0:
            # 곡판 QR 정렬 — 제자리에서 맞추기만 한다(주행 거리 0).
            self.speed = 0.0
            self.align_left -= dt
            if self.align_left <= 1e-9:
                self.align_left = 0.0
                self._aligned()
        elif self.state == "RUNNING" and self.job is not None:
            target_speed = float(self.job.set_speed) or self.default_speed
            remain = self.set_dist - self.mv_dist
            step = math.copysign(min(abs(remain), abs(target_speed) * dt), remain)
            self.mv_dist += step
            self.odometer += step
            self.speed = abs(step) / dt
            if abs(self.set_dist - self.mv_dist) < 1e-6:
                self.mv_dist = self.set_dist
                self._arrived()

        if self.state in ("STOP", "HOLD") and any(self.jog):
            self.jog_age += dt
            if self.jog_age > self.deadman_s:
                self.jog = (0.0, 0.0)
            v, w = self.jog
            self.odometer += v * dt
            self.yaw += w * dt
            self.speed = abs(v)
        elif self.state != "RUNNING":
            self.speed = 0.0

        if self.lift_target is not None and self.hold == "SET" and self.state != "ERROR":
            remain = self.lift_target - self.lift_h
            self.lift_h += math.copysign(min(abs(remain), self.lift_rate * dt), remain)
            if abs(self.lift_target - self.lift_h) < 1e-6:
                self.lift_h = self.lift_target
                self.lift_target = None
                self.lift_state = "FINISH"
        if self.state == "ERROR" and self.lift_state in ("RUNNING", "PAUSE"):
            self.lift_state = "STOP"               # 오류로 섰다 — 차량 state ERROR 가 까닭을 알린다

        self.estop_pub.publish(Bool(data=self.estop_now))
        msg = RobotStatus()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "odom"
        msg.state = self.state
        msg.hold = self.hold
        msg.error_code = self.error_code
        msg.error_msg = self.error_msg
        msg.job_id = self.job.job_id if self.job is not None else ""
        if self.lift_target is not None and self.lift_job_id:
            msg.job_id = self.lift_job_id
        msg.set_dist = self.set_dist
        msg.mv_dist = self.mv_dist
        msg.speed = self.speed
        msg.sen1_dist = 0.5
        msg.sen2_dist = 0.5
        msg.position.x = self.odometer
        msg.position.y = self.pos_y
        msg.position.yaw = self.yaw
        msg.lift_h = self.lift_h
        if hasattr(msg, "lift_state"):             # 옛 vehicle_interfaces 빌드에는 없다
            msg.lift_state = self.lift_state
        msg.battery_pct = int(self.param("battery_pct"))
        msg.charging = bool(self.param("charging"))
        msg.estop = self.estop_now
        msg.obstacle_stop = bool(self.param("obstacle_stop"))
        # 수평은 아웃트리거가 고정돼 있을 때만 맞는다(AGV 노드는 아직 -1 모름으로 낸다).
        msg.level_state = 1 if self.hold == "SET" else 0
        msg.standoff_meas = self.standoff
        msg.lap_state = self.lap_state
        msg.lap_path_m = self.mv_dist if self.lap_state != "IDLE" else 0.0
        msg.lap_origin_ok = self.lap_origin_ok
        msg.lap_error = self.lap_error
        self.pub.publish(msg)


def main() -> None:
    rclpy.init()
    node = VehicleSim()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
