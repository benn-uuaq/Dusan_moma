"""자동 주행, 수동 조작, 리프트 작업의 상태와 PLC 출력을 관리한다."""

from math import isfinite

from . import agv_math
from .motion_controller import MotionController
from .plc_client import PlcCodec, stopped_words


class JobManager:
    """서로 충돌할 수 있는 작업을 중재하는 단일 상태 소유자."""
    def __init__(self, config):
        self.config = config
        self.motion = MotionController(config)
        self.job = None
        self.circle_center = None
        self.command = None
        self.pausing = False
        self.progress = 0.0
        self.started = False
        self.completed = False
        self.manual = {}
        self.manual_until = 0.0
        self.lift = None
        self.halt_lift = False
        self.hold_height = None
        self.used_ids = set()
        self.best_progress = self.progress_at = 0.0

    def stop(self, command=0, discard=False):
        """동작 출력을 중지하고 필요하면 등록된 주행 작업까지 폐기한다."""
        self.pausing = False
        self.command = command
        self.manual.clear()
        if self.lift:
            self.halt_lift = True
        self.lift = None
        self.motion.speed = self.motion.accel = 0.0
        self.motion.settled = None
        if discard or command == 32:
            self.circle_center = None
        if discard:
            self.job = None
            self.progress = 0.0
            self.started = self.completed = False

    def anchor_circle_center(self, pose):
        """현재 작업의 기준 원을 odom에 고정한다. 후진해도 원 중심은 같은 쪽이다."""
        radius = agv_math.calculate_center_radius(self.job.radius, self.job.standoff, self.config["body_offset"])
        self.circle_center = agv_math.circle_center_xy(pose, self.config["turn_sign"] * radius.value)

    def pause(self):
        """주행 중에는 속도 프로파일을 유지한 채 정지 목표로 전환한다."""
        if self.command == 1:
            self.pausing = True
        else:
            self.stop(command=4)

    def register(self, request, sample):
        """자동 주행 작업을 검증해 등록한다. 등록만으로 RUN을 시작하지 않는다."""
        if self.command == 32 and sample.fast.state != 32:
            raise ValueError("waiting for PLC FINISH feedback")
        if sample.fast.mode != 2 or sample.fast.state not in (0, 2, 32) or self.command == 1 or self.lift:
            raise ValueError("set_job requires idle automatic mode")
        if self.job and not self.completed or not request.job_id.strip() or request.job_id in self.used_ids:
            raise ValueError("busy, missing or duplicate job_id")
        if not (0 <= request.set_speed <= 0.98 and request.radius > 0 and request.standoff >= 0):
            raise ValueError("invalid speed, radius or standoff")
        PlcCodec.encode_scaled(request.set_dist, 1000, True)
        for value in (request.radius, request.standoff):
            PlcCodec.encode_scaled(value, 1000)
        # 작업 등록만으로 PLC가 보고한 HOLD 상태를 해제해서는 안 된다.
        self.stop(command=2 if sample.fast.state == 2 else 0, discard=True)
        self.used_ids.add(request.job_id)
        self.job = request

    def run(self, sample, yaw, now):
        """등록된 작업을 시작하거나 PAUSE된 작업을 재개한다."""
        if self.pausing or (self.command == 4 and sample.fast.state != 4):
            raise ValueError("waiting for PLC PAUSE feedback")
        if not self.job or self.completed or self.lift or sample.fast.mode != 2:
            raise ValueError("no resumable automatic drive job")
        if self.job.set_dist != 0 and self.job.set_speed == 0:
            raise ValueError("a nonzero drive distance requires positive speed")
        if sample.fast.state not in (0, 4) or sample.fast.outrig:
            raise ValueError("release outriggers and stop or pause before RUNNING")
        if not self.started:
            self.motion.origin_heading = yaw
        self.started = True
        self.command = 1
        self.best_progress = self.progress * (1 if self.job.set_dist >= 0 else -1)
        self.progress_at = now

    def set_lift(self, job_id, height, speed, sample):
        """아웃트리거가 고정된 정지 상태에서만 리프트 목표를 설정한다."""
        f = sample.fast
        if f.state != 2:
            raise ValueError("lift requires PLC HOLD (DM002=2)")
        if not job_id or not f.outrig or self.command in (1, 32) or f.mode not in (0, 1, 2):
            raise ValueError("lift requires stopped and secured vehicle")
        if any(self.manual.values()):
            raise ValueError("manual switches are active")
        if not isfinite(height) or not 0 <= height <= self.config["max_lift_height"]:
            raise ValueError("lift height out of range")
        if f.mode == 2 and not (isfinite(speed) and 0 < speed <= self.config["max_lift_speed"]):
            raise ValueError("automatic lift speed out of range")
        words = {108: PlcCodec.encode_scaled(height, 1000)}
        if f.mode == 2:
            words[112] = PlcCodec.encode_scaled(speed, 1000)
        self.halt_lift = False
        self.hold_height = None
        self.lift = (job_id, f.mode, height, words)

    def apply_lift_stop(self, words, sample):
        """리프트 중단 시 현재 높이를 목표값으로 고정해 낙하·추가 이동을 막는다."""
        if self.halt_lift:
            self.hold_height = PlcCodec.encode_scaled(sample.fast.lift_height_m, 1000)
            self.halt_lift = False
        if self.hold_height is not None:
            words[108] = self.hold_height

    def set_manual(self, r, sample, now):
        """수동 선택 스위치를 검증하고 제한 시간 동안만 유효하게 저장한다."""
        f = sample.fast
        if f.mode not in (0, 1) or f.state not in (0, 2):
            raise ValueError("manual mode required")
        if r.cmd_init:
            raise ValueError("DM105 initialize trigger protocol is undefined")
        if r.cmd_outrg_set not in (0, 1, 2) or r.cmd_mv_lift not in (0, 1):
            raise ValueError("invalid manual selector")
        travel = any((r.cmd_mv_fwd, r.cmd_mv_rear, r.cmd_trn_left, r.cmd_trn_right))
        if (r.cmd_mv_fwd and r.cmd_mv_rear) or (r.cmd_trn_left and r.cmd_trn_right):
            raise ValueError("opposing manual commands")
        if travel and (f.mode != 1 or f.outrig or r.cmd_outrg_set or r.cmd_mv_lift or self.lift):
            raise ValueError("drive requires DRIVE Manual mode and released outriggers")
        if r.cmd_outrg_set and (r.cmd_mv_lift or self.lift or max(abs(w.speed_mps) for w in f.wheels) > self.config["stop_speed_tolerance"]):
            raise ValueError("outrigger requires stationary vehicle and lift")
        if r.cmd_mv_lift:
            self.set_lift("manual", r.lift_height, 0, sample)
        self.manual = dict(zip((120, 121, 122, 123, 126, 127), map(int, (
            r.cmd_mv_fwd, r.cmd_mv_rear, r.cmd_trn_left, r.cmd_trn_right,
            r.cmd_outrg_set == 2, r.cmd_outrg_set == 1))))
        self.manual_until = now + self.config["manual_timeout"]

    def outputs(self, sample, odom, now, dt):
        """현재 작업 상태를 안전한 PLC DM 출력 사전으로 합성한다."""
        words = stopped_words()
        if self.command == 1:
            if sample.fast.mode != 2 or sample.fast.outrig:
                self.stop()
                return words
            directed = self.progress * (1 if self.job.set_dist >= 0 else -1)
            if directed >= self.best_progress + 0.001:
                self.best_progress, self.progress_at = directed, now
            if now - self.progress_at >= self.config["drive_progress_timeout"]:
                # 명령 중인데 실제 이동량이 늘지 않으면 구동 이상으로 처리한다.
                raise ValueError("no drive progress")
            drive = self.motion.drive(self.job, self.progress, odom, dt, now,
                                      sample.slow.obstacle_slow, sample.fast.state == 1, self.pausing)
            words.update(drive)
            if drive[103] == 4:
                self.stop(command=4)
            elif drive[103] == 2:
                self.completed = True
                self.command = 2
        else:
            idle_command = sample.fast.state if sample.fast.state in (2, 32) else 0
            words[103] = self.command if self.command is not None else idle_command
        if self.manual and now < self.manual_until and sample.fast.mode in (0, 1) and sample.fast.state in (0, 2):
            words.update(self.manual)
            if sample.slow.obstacle_slow or sample.fast.outrig or sample.fast.mode != 1:
                # 감속 장애물은 수동 주행 스위치만 해제하고 기타 안전 출력은 유지한다.
                for address in range(120, 124):
                    words[address] = 0
        else:
            self.manual.clear()
        if self.lift:
            _, mode, target, lift_words = self.lift
            if not sample.fast.outrig or sample.fast.mode != mode or sample.fast.state != 2:
                self.halt_lift = True
                self.lift = None
            else:
                words.update(lift_words)
                if abs(sample.fast.lift_height_m - target) <= self.config["lift_tolerance"]:
                    self.lift = None
        return words
