"""차량(AMR·리프트·아웃트리거) 제어 노드 모의기.

실제 차량 제어 노드가 오기 전에 RCS 연동을 끝까지 돌려 보려고 만든다.
차량 담당자가 준 인터페이스(vehicle_interfaces)를 **그대로** 쓴다.

  발행   <ns>/robot_status    RobotStatus   (rate_hz, 기본 10 Hz)
         <ns>/start_request   std_msgs/Empty (차량 「작업 시작」 버튼 — 시험용 <ns>/sim/press_start 로 낸다)
  구독   <ns>/robot_estop     std_msgs/Bool  (로봇 컨트롤러 비상정지 — true 면 차량도 비상정지, 2026-11-18)
  서비스 <ns>/robot_control   RobotControl
         <ns>/set_job         SetJob
         <ns>/manual_command  ManualCommand (조그 · 아웃트리거 전체 · 수동 리프트 · 원점)
         <ns>/set_mv_lift     SetMvLift     (리프트 — 자동 동작 중, set_speed 로)
         <ns>/measure_lap     MeasureLap    (캘리브레이션 — LAP: expected_dist 만큼 돌고 DONE,
                                            ALIGN: 곡판 QR 정렬 — align_s 동안 RUNNING 뒤 DONE)
         <ns>/reset_odometry  ResetOdometry (위치 x · y · yaw 를 받은 값으로 — 2026-11-20 AGV 판)
  (<ns> 기본값 "vehicle" — 실제 이름은 차량 담당자 확인 필요)

받은 자료에 없는 **동작 규칙은 아래처럼 가정**했다(RCS 쪽 vehicle_adapters 와 같다).
실제 차량과 다르면 두 곳을 같이 고친다.

  1. SetJob.set_dist = **이번 이동**에서 갈 거리 [m] (부호 = 방향).
     RobotControl(state="RUNNING") 으로 출발, 다 가면 state "STOP".
     mv_dist 는 이번 이동에서 간 거리(0 -> set_dist).
     아웃트리거가 고정돼 있으면 먼저 풀고(아웃트리거 시간) 출발한다.
  2. 아웃트리거 고정/해제 = ManualCommand.cmd_outrg_set 2/1 — 전체로만(개별은 2026-11-19 뺐다).
     고정되면 hold "SET", state "HOLD".
  3. 리프트 — 자동: SetMvLift(set_height [m] 절대 높이, set_speed [m/s]) · 수동: ManualCommand
     (cmd_mv_lift 1, lift_height [m]) — 수동 속도는 PLC 가 정한다(모의기는 lift_speed 파라미터).
     아웃트리거 고정(hold SET)에서만 움직인다.
  4. ManualCommand · SetMvLift 는 RUNNING·PAUSED·ERROR · 비상정지가 아닐 때만 받는다
     ("수동 모드가 아니면 false").
  5. 전진·후진·회전 조그는 명령이 끊기면(deadman_s) 선다 — RCS 는 누르는
     동안 주기적으로 다시 보낸다.
  6. RobotControl: "PAUSED" 멈춤, "RUNNING" 재개, "STOP" 정지,
     job_cancel=1 작업 취소, reset=1 오류 해제.

  7. MeasureLap(mode LAP 또는 빈 글자) = 원점에서 expected_dist 만큼 돌고 서면 lap_state DONE ·
     lap_path_m · lap_origin_ok. mode ALIGN(곡판) = 주행하지 않고 QR 정렬만(align_s 초 동안
     RUNNING, lap_path_m 0) 하고 DONE · lap_origin_ok. 두 경우 모두 PAUSED · RUNNING 으로 멈추고
     잇고, STOP 이면 lap_state FAILED.

시험용으로 오류를 넣을 수 있다: ros2 param set /vehicle_sim fault_code 21
PLC 끊김 흉내: ros2 param set /vehicle_sim plc_fail true — error_code 701 PLC_COMM_LOST · state ERROR.
  (AGV 판 인터페이스에는 PLC 전용 필드 · 서비스가 없다 — 701 로만 알린다.) plc_retry_s(기본 5 초)마다 스스로
  다시 시도하고, RobotControl reset=1 이면 바로 시도한다. plc_fail false 로 돌린 뒤 다시 시도가
  plc_connect_s(기본 1 초) 지나면 붙고 701 이 풀린다.
비상정지 · 접근 감지 · 배터리도 파라미터로 바꾼다: estop, obstacle_stop, battery_pct
비상정지(차량 estop 파라미터 · 로봇 robot_estop)인 동안은 서고 움직이는 명령을 거절하며 estop = true 로 낸다.
움직이는 모습을 보려면 화면판을 쓴다: ros2 run vehicle_sim vehicle_sim_ui
"""

from __future__ import annotations

import math

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, Empty
from std_srvs.srv import Trigger
from vehicle_interfaces.msg import RobotStatus
from vehicle_interfaces.srv import ManualCommand, MeasureLap, ResetOdometry, RobotControl, SetJob, SetMvLift


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
        self.lift_speed = float(p("lift_speed", 0.05).value)        # m/s
        self.lift_max = float(p("lift_max", 3.0).value)             # m
        self.outrigger_s = float(p("outrigger_s", 1.5).value)       # 고정/해제 걸리는 시간
        self.deadman_s = float(p("deadman_s", 0.5).value)
        self.align_s = float(p("align_s", 3.0).value)               # 곡판 QR 정렬 걸리는 시간
        p("fault_code", 0)                                          # 시험용 오류 주입
        # RCS 와 맞춘 칸(2026-11-17) — 시험용으로 바꿀 수 있다.
        p("battery_pct", 90)
        p("charging", False)
        p("estop", False)
        p("obstacle_stop", False)
        # PLC 연결 흉내(2026-11-20) — plc_fail 이 true 면 PLC 가 응답하지 않는 것으로 친다.
        p("plc_fail", False)
        self.plc_connect_s = float(p("plc_connect_s", 1.0).value)  # 다시 연결에 걸리는 시간
        self.plc_retry_s = float(p("plc_retry_s", 5.0).value)      # FAILED 뒤 스스로 다시 시도하는 간격(0 = 안 함)

        self.state = "STOP"
        self.hold = "RELEASE"
        self.error_code = 0
        self.error_msg = ""
        self.job = None            # (job_id, set_dist, set_speed, ...)
        self.set_dist = 0.0
        self.mv_dist = 0.0
        self.speed = 0.0
        self.odometer = 0.0        # 누적 주행 거리 [m] (position.x 로 낸다)
        self.pos_y = 0.0           # position.y — ResetOdometry 로만 바뀐다
        self.yaw = 0.0
        self.radius = 0.0          # 마지막 SetJob.radius [m] (모재 반지름)
        self.lift_h = 0.0
        self.lift_target = None
        self.outrigger_timer = 0.0
        self.outrigger_goal = None  # "SET" / "RELEASE" 로 가는 중
        # 아웃트리거 다리 3개의 내림 정도 (0 = 올림/해제, 1 = 내려 접지).
        # 화면(vehicle_sim_ui)이 그대로 그린다.
        self.legs = [0.0, 0.0, 0.0]
        self.lift_rate = self.lift_speed   # 이번 리프트 명령의 속도 [m/s] (자동 SetMvLift.set_speed · 수동 PLC 속도)
        self.drive_after_release = False
        self.jog = (0.0, 0.0)       # (전후 속도, 회전 속도)
        self.jog_age = 0.0
        self.standoff = -1.0        # 마지막으로 받은 거리 유지 [m]
        self.lap_state = "IDLE"     # MeasureLap 진행
        self.lap_origin_ok = False
        self.lap_error = ""
        self.align_left = 0.0       # ALIGN(곡판 QR 정렬) 남은 시간 [s]. 0 이면 정렬 중이 아니다
        self.robot_estop = False    # 로봇 컨트롤러 비상정지(RCS 가 <ns>/robot_estop 으로 알린다)
        # PLC 연결(모의기 안쪽 값 — 밖으로는 701 로만 낸다). 처음엔 바로 붙는다(plc_fail 이면 실패로 시작).
        self.plc_state = "CONNECTED"
        self.plc_error = ""
        self.plc_retries = 0
        self.plc_timer = 0.0        # CONNECTING 남은 시간
        self.plc_retry_left = 0.0   # FAILED 에서 다음 자동 재시도까지
        if bool(self.get_parameter("plc_fail").value):
            self._plc_lost("모의 PLC 응답 없음")

        self.pub = self.create_publisher(RobotStatus, f"{ns}/robot_status", 10)
        self.create_service(RobotControl, f"{ns}/robot_control", self.on_control)
        self.create_service(SetJob, f"{ns}/set_job", self.on_set_job)
        self.create_service(ManualCommand, f"{ns}/manual_command", self.on_manual)
        self.create_service(SetMvLift, f"{ns}/set_mv_lift", self.on_lift)
        self.create_service(MeasureLap, f"{ns}/measure_lap", self.on_measure_lap)
        self.create_service(ResetOdometry, f"{ns}/reset_odometry", self.on_reset_odometry)
        self.start_pub = self.create_publisher(Empty, f"{ns}/start_request", 10)
        self.create_subscription(Bool, f"{ns}/robot_estop", self.on_robot_estop, 10)
        self.create_service(Trigger, f"{ns}/sim/press_start", self.on_press_start)
        self.dt = 1.0 / self.rate_hz
        self.create_timer(self.dt, self.tick)
        self.get_logger().info(f"차량 모의기 시작 — {ns}/robot_status, {ns}/robot_control, "
                               f"{ns}/set_job, {ns}/manual_command, {ns}/set_mv_lift, {ns}/reset_odometry")

    # ------------------------------------------------------------ 서비스
    def on_set_job(self, req, res):
        if self.estop_now:
            res.success, res.message = False, "Rejected: estop"
            return res
        if self.plc_state != "CONNECTED":
            res.success, res.message = False, "Rejected: PLC not connected"
            return res
        if self.state in ("RUNNING", "PAUSED"):
            res.success, res.message = False, "Rejected: busy"
            return res
        if self.state == "ERROR":       # 오류 중에는 움직이는 명령을 거절한다(RCS_AGV 인터페이스 차량 오류 규칙)
            res.success, res.message = False, "Rejected: error — reset first"
            return res
        self.job = req
        self.set_dist = float(req.set_dist)
        self.mv_dist = 0.0
        self.standoff = float(getattr(req, "standoff", -1.0))
        self.radius = float(getattr(req, "radius", 0.0))
        self.lap_state = "IDLE"
        res.success, res.message = True, f"Job {req.job_id} set: {req.set_dist:+.3f} m (radius {self.radius:.3f} m)"
        return res

    def on_measure_lap(self, req, res):
        """캘리브레이션 주행 — LAP 은 expected_dist 만큼 바로 출발한다(실물은 원점 QR 을 다시 볼 때까지).

        ALIGN(곡판)은 주행하지 않는다 — 판 가운데 QR 마커에 맞춰 정렬만 하고(align_s 초) 선다.
        곡판은 스캔 때도 차량이 움직이지 않는다(RCS 가 set_job 을 보내지 않는다).
        """
        if self.estop_now:
            res.success, res.message = False, "Rejected: estop"
            return res
        if self.plc_state != "CONNECTED":
            res.success, res.message = False, "Rejected: PLC not connected"
            return res
        if self.state in ("RUNNING", "PAUSED", "ERROR"):
            res.success, res.message = False, f"Rejected: {self.state.lower()}"
            return res
        mode = (getattr(req, "mode", "") or "LAP").strip().upper()
        if mode not in ("LAP", "ALIGN"):
            res.success, res.message = False, f"Rejected: unknown mode {mode}"
            return res
        if mode == "ALIGN":
            self.job = SetJob.Request(job_id=req.job_id, set_dist=0.0, set_speed=float(req.speed))
            self.set_dist = self.mv_dist = 0.0
            self.standoff = float(req.standoff)
            self.lap_state, self.lap_origin_ok, self.lap_error = "RUNNING", False, ""
            self.align_left = max(self.align_s, self.dt)
            if self.hold == "SET":
                self._start_outrigger("RELEASE")
                self.drive_after_release = True
            else:
                self.state = "RUNNING"
            res.success, res.message = True, f"Align {req.job_id}: QR"
            return res
        if float(req.expected_dist) <= 0:
            res.success, res.message = False, "Rejected: expected_dist <= 0"
            return res
        self.job = SetJob.Request(job_id=req.job_id, set_dist=float(req.expected_dist),
                                  set_speed=float(req.speed))
        self.set_dist = float(req.expected_dist)
        self.mv_dist = 0.0
        self.standoff = float(req.standoff)
        self.lap_state, self.lap_origin_ok, self.lap_error = "RUNNING", False, ""
        if self.hold == "SET":
            self._start_outrigger("RELEASE")
            self.drive_after_release = True
        else:
            self.state = "RUNNING"
        res.success, res.message = True, f"Lap {req.job_id}: {req.expected_dist:.3f} m"
        return res

    @property
    def estop_now(self) -> bool:
        """비상정지 중인가 — 차량 버튼(estop 파라미터) 또는 로봇 컨트롤러 비상정지(robot_estop)."""
        return bool(self.get_parameter("estop").value) or self.robot_estop

    # ------------------------------------------------------------ PLC 연결 (2026-11-20)
    # 밖으로는 error_code 701 로만 낸다(AGV 판 인터페이스). 다시 연결은 스스로(plc_retry_s) · 리셋으로.
    def _plc_begin(self) -> None:
        self.plc_state = "CONNECTING"
        self.plc_retries += 1
        self.plc_timer = self.plc_connect_s

    def _plc_lost(self, why: str) -> None:
        """PLC 가 끊겼다 — 차량을 움직일 수 없다: 세우고 701 PLC_COMM_LOST · ERROR."""
        self.plc_state, self.plc_error = "FAILED", why
        self.plc_retry_left = self.plc_retry_s
        self._halt()
        self.state, self.error_code = "ERROR", 701
        self.error_msg = f"701 PLC_COMM_LOST — {why}"

    def _plc_up(self) -> None:
        self.plc_state, self.plc_error, self.plc_retries = "CONNECTED", "", 0
        if self.error_code == 701:                          # 원인이 없어졌다 — 스스로 푼다
            self.error_code, self.error_msg = 0, ""
            if self.state == "ERROR":
                self.state = "HOLD" if self.hold == "SET" else "STOP"

    def _plc_tick(self, dt: float) -> None:
        fail = bool(self.get_parameter("plc_fail").value)
        if self.plc_state == "CONNECTED" and fail:
            self._plc_lost("모의 PLC 응답 없음")
        elif self.plc_state == "CONNECTING":
            self.plc_timer -= dt
            if self.plc_timer <= 0:
                if fail:
                    self._plc_lost(f"모의 PLC 응답 없음 (다시 시도 {self.plc_retries}회)")
                else:
                    self._plc_up()
        elif self.plc_state == "FAILED" and self.plc_retry_s > 0:
            self.plc_retry_left -= dt
            if self.plc_retry_left <= 0:
                self._plc_begin()

    def on_robot_estop(self, msg) -> None:
        """로봇 컨트롤러 비상정지 — 실물은 PLC 비상정지를 건다. 모의기는 서고 estop = true 로 낸다."""
        self.robot_estop = bool(msg.data)
        if self.robot_estop:
            self._halt()

    def on_press_start(self, _req, res):
        """시험용 — 차량 「작업 시작」 버튼을 누른 것처럼 start_request 를 낸다."""
        self.start_pub.publish(Empty())
        res.success, res.message = True, "start_request published"
        return res

    def on_control(self, req, res):
        res.success, res.message = True, "Done"
        if req.reset and self.plc_state != "CONNECTED":
            # PLC 가 안 붙어 있다 — 다시 연결을 시작한다. 붙을 때까지 701 은 그대로(붙으면 스스로 푼다).
            if self.plc_state == "FAILED":
                self._plc_begin()
            res.message = f"PLC reconnecting (try {self.plc_retries})"
        elif req.reset:
            self.error_code, self.error_msg = 0, ""
            if self.state == "ERROR":
                self.state = "HOLD" if self.hold == "SET" else "STOP"
            self.set_parameters([rclpy.parameter.Parameter("fault_code", value=0)])
        if req.job_cancel:
            self._halt()
            self.job = None
            res.message = "Job cancelled"
        state = (req.state or "").upper()
        if not state:
            return res
        if self.state == "ERROR" and state != "STOP":
            res.success, res.message = False, "Rejected: error — reset first"
        elif state == "RUNNING" and self.estop_now:
            res.success, res.message = False, "Rejected: estop"
        elif state == "RUNNING" and self.plc_state != "CONNECTED":
            res.success, res.message = False, "Rejected: PLC not connected"
        elif state == "RUNNING":
            if self.state == "PAUSED":
                self.state = "RUNNING"
            elif self.job is None:
                res.success, res.message = False, "Rejected: no job"
            elif self.hold == "SET":
                self._start_outrigger("RELEASE")
                self.drive_after_release = True
            else:
                self.state = "RUNNING"
        elif state == "PAUSED":
            if self.state == "RUNNING":
                self.state = "PAUSED"
                self.speed = 0.0
            else:
                res.success, res.message = False, "Rejected: not running"
        elif state == "STOP":
            self._halt()
        elif state == "HOLD":
            self._start_outrigger("SET")
        else:
            res.success, res.message = False, f"Rejected: unknown state {req.state!r}"
        return res

    def _lift_refusal(self, height: float) -> str:
        """리프트를 못 움직이는 까닭(없으면 빈 글자) — 자동 · 수동 같다."""
        if self.estop_now:
            return "Rejected: estop"
        if self.plc_state != "CONNECTED":
            return "Rejected: PLC not connected"
        if self.state in ("RUNNING", "PAUSED", "ERROR"):
            return f"Rejected: {self.state.lower()}"
        if self.hold != "SET":
            return "Rejected: outriggers not set"
        if not 0.0 <= height <= self.lift_max:
            return f"Rejected: height {height:.3f} m out of 0~{self.lift_max:.3f}"
        return ""

    def on_lift(self, req, res):
        """자동 동작 중 리프트(SetMvLift) — set_speed 로 움직인다(0 이면 lift_speed)."""
        height = float(req.set_height)
        refusal = self._lift_refusal(height)
        if refusal:
            res.success, res.message = False, refusal
            return res
        speed = float(req.set_speed)
        self.lift_rate = speed if speed > 0 else self.lift_speed
        self.lift_target = height
        res.success, res.message = True, f"Lift {req.job_id}: {height:.3f} m"
        return res

    def on_reset_odometry(self, req, res):
        """위치(position x · y · yaw)를 받은 값으로 다시 맞춘다. 움직이는 중에는 거절한다."""
        if self.state in ("RUNNING", "PAUSED") or any(self.jog):
            res.success, res.message = False, "Rejected: moving"
            return res
        self.odometer, self.pos_y, self.yaw = float(req.x), float(req.y), float(req.yaw)
        res.success, res.message = True, f"Odometry reset: x {req.x:.3f} y {req.y:.3f} yaw {req.yaw:.3f}"
        return res

    def on_manual(self, req, res):
        if self.estop_now:
            res.success, res.message = False, "Rejected: estop"
            return res
        if self.plc_state != "CONNECTED":
            res.success, res.message = False, "Rejected: PLC not connected"
            return res
        if self.state in ("RUNNING", "PAUSED", "ERROR"):
            res.success, res.message = False, "Rejected: Not in manual mode"
            return res
        res.success, res.message = True, "Done"
        fwd = (1.0 if req.cmd_mv_fwd else 0.0) - (1.0 if req.cmd_mv_rear else 0.0)
        turn = (1.0 if req.cmd_trn_left else 0.0) - (1.0 if req.cmd_trn_right else 0.0)
        if fwd or turn:
            if self.hold == "SET":
                res.success, res.message = False, "Rejected: outriggers set"
                return res
            self.jog = (fwd * self.jog_speed, turn * self.turn_rate)
            self.jog_age = 0.0
        else:
            self.jog = (0.0, 0.0)
        if req.cmd_outrg_set == 2:
            self._start_outrigger("SET")
        elif req.cmd_outrg_set == 1:
            self._start_outrigger("RELEASE")
        if req.cmd_mv_lift:
            # 수동 리프트 — 속도는 PLC 가 정한다(모의기는 lift_speed).
            height = float(req.lift_height)
            refusal = self._lift_refusal(height)
            if refusal:
                res.success, res.message = False, refusal
                return res
            self.lift_rate = self.lift_speed
            self.lift_target = height
            res.message = f"Lift manual: {height:.3f} m"
        if req.cmd_init:
            self.odometer = self.mv_dist = 0.0
            self.yaw = 0.0
        return res

    # ------------------------------------------------------------ 동작
    def _halt(self) -> None:
        self.speed = 0.0
        self.jog = (0.0, 0.0)
        self.lift_target = None
        if self.lap_state == "RUNNING":
            self.lap_state, self.lap_error = "FAILED", "stopped"
        self.align_left = 0.0
        if self.state in ("RUNNING", "PAUSED"):
            self.state = "HOLD" if self.hold == "SET" else "STOP"

    def _start_outrigger(self, goal: str) -> None:
        if self.hold == goal and self.outrigger_goal is None:
            return
        self.outrigger_goal = goal
        self.outrigger_timer = self.outrigger_s

    @property
    def outrigger_ratio(self) -> float:
        """아웃트리거 전체 내림 정도 0~1 (다리 3개 평균) — 화면이 쓴다."""
        return sum(self.legs) / len(self.legs)

    @property
    def lift_moving(self) -> bool:
        return self.lift_target is not None and abs(self.lift_target - self.lift_h) > 1e-6

    def snapshot(self) -> dict:
        """화면(vehicle_sim_ui)이 그릴 값 한 벌."""
        return {
            "state": self.state, "hold": self.hold,
            "error_code": self.error_code, "error_msg": self.error_msg,
            "job_id": self.job.job_id if self.job is not None else "",
            "set_dist": self.set_dist, "mv_dist": self.mv_dist, "speed": self.speed,
            "odometer": self.odometer, "y": self.pos_y, "yaw": self.yaw, "radius": self.radius,
            "lift_h": self.lift_h, "lift_max": self.lift_max,
            "lift_moving": self.lift_moving,
            "legs": list(self.legs), "outrigger_moving": self.outrigger_goal is not None,
            "jog": self.jog,
            "plc_state": self.plc_state, "plc_error": self.plc_error, "plc_retries": self.plc_retries,
        }

    def tick(self) -> None:
        dt = self.dt
        if self.estop_now and (self.state in ("RUNNING", "PAUSED") or self.lift_target is not None
                               or any(self.jog)):
            self._halt()
        self._plc_tick(dt)
        fault = int(self.get_parameter("fault_code").value)
        if fault and self.state != "ERROR":
            self.state, self.error_code = "ERROR", fault
            self.error_msg = f"모의 오류 {fault}"
            self.speed = 0.0
            self.lift_target = None

        # 아웃트리거 다리: 전체 명령(고정 · 해제)의 목표로 함께 움직인다(개별 명령은 없다).
        leg_step = dt / max(self.outrigger_s, 1e-6)
        if self.outrigger_goal is not None:
            goal = 1.0 if self.outrigger_goal == "SET" else 0.0
            self.legs = [min(1.0, max(0.0, v + math.copysign(leg_step, goal - v)))
                         if abs(goal - v) > 1e-9 else goal for v in self.legs]

        if self.outrigger_goal is not None:
            self.outrigger_timer -= dt
            if self.outrigger_timer <= 0:
                self.hold = self.outrigger_goal
                self.outrigger_goal = None
                if self.hold == "SET" and self.state == "STOP":
                    self.state = "HOLD"
                elif self.hold == "RELEASE" and self.state == "HOLD":
                    self.state = "STOP"
                if self.drive_after_release and self.hold == "RELEASE":
                    self.drive_after_release = False
                    self.state = "RUNNING"

        if self.state == "RUNNING" and self.align_left > 0:
            # 곡판 QR 정렬 — 제자리에서 맞추기만 한다(주행 거리 0).
            self.speed = 0.0
            self.align_left -= dt
            if self.align_left <= 1e-9:
                self.align_left = 0.0
                self.state = "STOP"
                if self.lap_state == "RUNNING":
                    self.lap_state, self.lap_origin_ok = "DONE", True
        elif self.state == "RUNNING" and self.job is not None:
            target_speed = float(self.job.set_speed) or self.default_speed
            remain = self.set_dist - self.mv_dist
            step = math.copysign(min(abs(remain), abs(target_speed) * dt), remain)
            self.mv_dist += step
            self.odometer += step
            self.speed = abs(step) / dt
            if abs(self.set_dist - self.mv_dist) < 1e-6:
                self.mv_dist = self.set_dist
                self.speed = 0.0
                self.state = "STOP"
                if self.lap_state == "RUNNING":
                    self.lap_state, self.lap_origin_ok = "DONE", True

        if self.state in ("STOP", "HOLD") and any(self.jog):
            self.jog_age += dt
            if self.jog_age > self.deadman_s:
                self.jog = (0.0, 0.0)
            v, w = self.jog
            self.odometer += v * dt
            self.yaw += w * dt
            self.speed = abs(v)
        elif self.state in ("STOP", "HOLD"):
            self.speed = 0.0

        if self.lift_target is not None and self.hold == "SET" and self.state != "ERROR":
            remain = self.lift_target - self.lift_h
            self.lift_h += math.copysign(min(abs(remain), self.lift_rate * dt), remain)
            if abs(self.lift_target - self.lift_h) < 1e-6:
                self.lift_h = self.lift_target
                self.lift_target = None

        msg = RobotStatus()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "vehicle"
        msg.state = self.state
        msg.hold = self.hold
        msg.error_code = self.error_code
        msg.error_msg = self.error_msg
        msg.job_id = self.job.job_id if self.job is not None else ""
        msg.set_dist = self.set_dist
        msg.mv_dist = self.mv_dist
        msg.speed = self.speed
        msg.sen1_dist = 0.5
        msg.sen2_dist = 0.5
        msg.position.x = self.odometer
        msg.position.y = self.pos_y
        msg.position.yaw = self.yaw
        msg.lift_h = self.lift_h
        param = lambda name: self.get_parameter(name).value          # noqa: E731
        msg.battery_pct = int(param("battery_pct"))
        msg.charging = bool(param("charging"))
        msg.estop = self.estop_now
        msg.obstacle_stop = bool(param("obstacle_stop"))
        # 수평은 아웃트리거가 고정돼 있을 때만 맞는다.
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
