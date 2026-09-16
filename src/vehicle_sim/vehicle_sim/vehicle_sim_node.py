"""차량(AMR·리프트·아웃트리거) 제어 노드 모의기.

실제 차량 제어 노드가 오기 전에 RCS 연동을 끝까지 돌려 보려고 만든다.
차량 담당자가 준 인터페이스(vehicle_interfaces)를 **그대로** 쓴다.

  발행   <ns>/robot_status    RobotStatus   (rate_hz, 기본 10 Hz)
  서비스 <ns>/robot_control   RobotControl
         <ns>/set_job         SetJob
         <ns>/manual_command  ManualCommand
  (<ns> 기본값 "vehicle" — 실제 이름은 차량 담당자 확인 필요)

받은 자료에 없는 **동작 규칙은 아래처럼 가정**했다(RCS 쪽 vehicle_adapters 와 같다).
실제 차량과 다르면 두 곳을 같이 고친다.

  1. SetJob.set_dist = **이번 이동**에서 갈 거리 [m] (부호 = 방향).
     RobotControl(state="RUNNING") 으로 출발, 다 가면 state "STOP".
     mv_dist 는 이번 이동에서 간 거리(0 -> set_dist).
     아웃트리거가 고정돼 있으면 먼저 풀고(아웃트리거 시간) 출발한다.
  2. 아웃트리거 고정/해제 = ManualCommand.cmd_outrg_set 2/1.
     고정되면 hold "SET", state "HOLD".
  3. 리프트 = ManualCommand.cmd_mv_lift=1 + lift_height [m] (절대 높이).
     아웃트리거 고정(hold SET)에서만 움직인다(받은 자료 그대로).
  4. ManualCommand 는 RUNNING·PAUSED·ERROR 가 아닐 때만 받는다
     ("수동 모드가 아니면 false").
  5. 전진·후진·회전 조그는 명령이 끊기면(deadman_s) 선다 — RCS 는 누르는
     동안 주기적으로 다시 보낸다.
  6. RobotControl: "PAUSED" 멈춤, "RUNNING" 재개, "STOP" 정지,
     job_cancel=1 작업 취소, reset=1 오류 해제.

시험용으로 오류를 넣을 수 있다: ros2 param set /vehicle_sim fault_code 21
움직이는 모습을 보려면 화면판을 쓴다: ros2 run vehicle_sim vehicle_sim_ui
"""

from __future__ import annotations

import math

import rclpy
from rclpy.node import Node
from vehicle_interfaces.msg import RobotStatus
from vehicle_interfaces.srv import ManualCommand, RobotControl, SetJob


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
        p("fault_code", 0)                                          # 시험용 오류 주입

        self.state = "STOP"
        self.hold = "RELEASE"
        self.error_code = 0
        self.error_msg = ""
        self.job = None            # (job_id, set_dist, set_speed, ...)
        self.set_dist = 0.0
        self.mv_dist = 0.0
        self.speed = 0.0
        self.odometer = 0.0        # 누적 주행 거리 [m] (position.x 로 낸다)
        self.yaw = 0.0
        self.lift_h = 0.0
        self.lift_target = None
        self.outrigger_timer = 0.0
        self.outrigger_goal = None  # "SET" / "RELEASE" 로 가는 중
        # 아웃트리거 다리 3개의 내림 정도 (0 = 올림/해제, 1 = 내려 접지).
        # 화면(vehicle_sim_ui)이 그대로 그린다.
        self.legs = [0.0, 0.0, 0.0]
        self.leg_cmd = [0, 0, 0]    # 1 상승 / 2 하강 / 0 없음 (누르는 동안)
        self.leg_age = 0.0
        self.drive_after_release = False
        self.jog = (0.0, 0.0)       # (전후 속도, 회전 속도)
        self.jog_age = 0.0

        self.pub = self.create_publisher(RobotStatus, f"{ns}/robot_status", 10)
        self.create_service(RobotControl, f"{ns}/robot_control", self.on_control)
        self.create_service(SetJob, f"{ns}/set_job", self.on_set_job)
        self.create_service(ManualCommand, f"{ns}/manual_command", self.on_manual)
        self.dt = 1.0 / self.rate_hz
        self.create_timer(self.dt, self.tick)
        self.get_logger().info(f"차량 모의기 시작 — {ns}/robot_status, {ns}/robot_control, "
                               f"{ns}/set_job, {ns}/manual_command")

    # ------------------------------------------------------------ 서비스
    def on_set_job(self, req, res):
        if self.state in ("RUNNING", "PAUSED"):
            res.success, res.message = False, "Rejected: busy"
            return res
        self.job = req
        self.set_dist = float(req.set_dist)
        self.mv_dist = 0.0
        res.success, res.message = True, f"Job {req.job_id} set: {req.set_dist:+.3f} m"
        return res

    def on_control(self, req, res):
        res.success, res.message = True, "Done"
        if req.reset:
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

    def on_manual(self, req, res):
        if self.state in ("RUNNING", "PAUSED", "ERROR"):
            res.success, res.message = False, "Rejected: Not in manual mode"
            return res
        res.success, res.message = True, "Done"
        fwd = (1.0 if req.cmd_mv_fwd else 0.0) - (1.0 if req.cmd_mv_rear else 0.0)
        turn = (1.0 if req.cmd_trn_left else 0.0) - (1.0 if req.cmd_trl_right else 0.0)
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
            if self.hold != "SET":
                res.success, res.message = False, "Rejected: outriggers not set"
                return res
            self.lift_target = max(0.0, min(self.lift_max, float(req.lift_height)))
        # 아웃트리거 개별 상승(1)·하강(2) — 누르는 동안만(조그와 같은 deadman)
        legs = (req.cmd_man_outrg1, req.cmd_man_outrg2, req.cmd_man_outrg3)
        if any(legs):
            self.leg_cmd = [int(v) for v in legs]
            self.leg_age = 0.0
        else:
            self.leg_cmd = [0, 0, 0]
        if req.cmd_init:
            self.odometer = self.mv_dist = 0.0
            self.yaw = 0.0
        return res

    # ------------------------------------------------------------ 동작
    def _halt(self) -> None:
        self.speed = 0.0
        self.jog = (0.0, 0.0)
        self.lift_target = None
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
            "odometer": self.odometer, "yaw": self.yaw,
            "lift_h": self.lift_h, "lift_max": self.lift_max,
            "lift_moving": self.lift_moving,
            "legs": list(self.legs), "outrigger_moving": self.outrigger_goal is not None,
            "jog": self.jog,
        }

    def tick(self) -> None:
        dt = self.dt
        fault = int(self.get_parameter("fault_code").value)
        if fault and self.state != "ERROR":
            self.state, self.error_code = "ERROR", fault
            self.error_msg = f"모의 오류 {fault}"
            self.speed = 0.0
            self.lift_target = None

        # 아웃트리거 다리: 전체 명령이 있으면 그 목표로, 개별 명령은 누르는 동안.
        leg_step = dt / max(self.outrigger_s, 1e-6)
        if self.outrigger_goal is not None:
            goal = 1.0 if self.outrigger_goal == "SET" else 0.0
            self.legs = [min(1.0, max(0.0, v + math.copysign(leg_step, goal - v)))
                         if abs(goal - v) > 1e-9 else goal for v in self.legs]
        elif any(self.leg_cmd):
            self.leg_age += dt
            if self.leg_age > self.deadman_s:
                self.leg_cmd = [0, 0, 0]
            for i, cmd in enumerate(self.leg_cmd):
                if cmd == 2:      # 하강 = 내림
                    self.legs[i] = min(1.0, self.legs[i] + leg_step)
                elif cmd == 1:    # 상승 = 올림
                    self.legs[i] = max(0.0, self.legs[i] - leg_step)
            if all(v >= 0.999 for v in self.legs):
                self.hold = "SET"
                if self.state == "STOP":
                    self.state = "HOLD"
            elif all(v <= 0.001 for v in self.legs) and self.hold == "SET":
                self.hold = "RELEASE"
                if self.state == "HOLD":
                    self.state = "STOP"

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

        if self.state == "RUNNING" and self.job is not None:
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
            self.lift_h += math.copysign(min(abs(remain), self.lift_speed * dt), remain)
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
        msg.position.yaw = self.yaw
        msg.lift_h = self.lift_h
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
