"""주행 작업을 PLC 속도·BODY 각도·반경 명령으로 변환한다."""

from math import degrees

from . import agv_math
from .plc_client import PlcCodec


class MotionController:
    """jerk 제한 프로파일과 원주 주행의 BODY 조향 지령을 계산한다."""
    def __init__(self, config):
        self.config = config
        self.speed = self.accel = self.origin_heading = 0.0
        self.settled = None

    def drive(self, job, progress, odom, dt, now, slow, running, pausing=False):
        """현재 주행 상태에서 다음 PLC 출력 레지스터 값을 만든다."""
        direction = 1 if job.set_dist >= 0 else -1
        remaining = abs(job.set_dist) - direction * progress
        tolerance = self.config["distance_tolerance"]
        limit = job.set_speed or self.config["acceleration"]
        target = min(job.set_speed, self.config["obstacle_slow_speed"]) if slow else job.set_speed
        if pausing:
            # RUN을 유지하며 감속하고 실제 휠 속도까지 정지 기준에 들면 PAUSE를 쓴다.
            decel = self.config["deceleration"]
            jerk = agv_math.calculate_jerk_limit(decel, self.config["jerk_ratio"]).value
            self.speed, self.accel = agv_math.profile_step(
                self.speed, self.accel, job.set_speed, 0.0, limit, jerk, dt, decel)
            if self.speed < 0.0005 and max(map(abs, odom.twist)) <= self.config["stop_speed_tolerance"]:
                return {103: 4, 106: 0, 109: 0}
        elif remaining < -tolerance:
            raise ValueError("drive target overshot")
        elif remaining <= tolerance and self.speed < 0.0005:
            # 목표점에 도달해도 실제 odom 속도가 안정된 시간만큼 0이어야 HOLD한다.
            self.speed = self.accel = 0.0
            if max(map(abs, odom.twist)) <= self.config["stop_speed_tolerance"]:
                if self.settled is None:
                    self.settled = now
                if now - self.settled >= self.config["arrival_hold_time"]:
                    return {103: 2, 106: 0, 109: 0}
            else:
                self.settled = None
        elif running:
            self.settled = None
            jerk = agv_math.calculate_jerk_limit(limit, self.config["jerk_ratio"]).value
            available = max(0.0, remaining - tolerance / 2)
            decel = job.set_speed or self.config["deceleration"]
            # 감속 중 제동 판단이 잠시 해제되어도 잔여 거리에서 허용할 속도까지만 접근한다.
            approach = agv_math.approach_speed_limit(target, available, decel, jerk,
                                                     self.config["stop_speed_tolerance"])
            self.speed, self.accel = agv_math.profile_step(
                self.speed, self.accel, approach, available, limit, jerk, dt, decel,
                feedback_speed=direction * odom.path_speed_mps)
        radius = agv_math.calculate_center_radius(job.radius, job.standoff, self.config["body_offset"]).value
        turn = self.config["turn_sign"]
        heading = self.origin_heading + turn * progress / radius
        # PLC가 개별 휠각을 계산하므로 ROS는 다음 스캔의 BODY 각도 오차만 전달한다.
        angle = agv_math.calculate_body_angle_error(radius, odom.twist[0], dt, heading, odom.pose[2], turn)
        if angle.error:
            raise ValueError(angle.error)
        return {
            103: 1, 106: PlcCodec.encode_scaled(direction * self.speed, 1000, True),
            107: PlcCodec.encode_scaled(job.set_dist, 1000, True),
            109: PlcCodec.encode_scaled(degrees(angle.value), 100, True),
            110: PlcCodec.encode_scaled(job.standoff, 1000),
            111: PlcCodec.encode_scaled(job.radius, 1000),
        }
