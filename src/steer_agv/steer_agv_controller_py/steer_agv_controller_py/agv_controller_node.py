"""ROS 서비스, PLC 피드백, 작업 제어를 연결하는 AGV 주 제어 노드."""

from math import isfinite
from time import monotonic

from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from rcl_interfaces.msg import ParameterDescriptor, SetParametersResult
from rclpy.node import Node
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.task import Future
from vehicle_interfaces.srv import ManualCommand, MeasureLap, ResetOdometry, RobotControl, SetJob, SetMvLift

from .job_manager import JobManager
from .debug_feed import DebugFeed, DriveFaultLog
from .odometry_estimator import OdometryEstimator
from .plc_client import PlcClient, PlcCodec, stopped_words
from .safety_monitor import SafetyMonitor
from .status_publisher import JOB_QOS, StatusPublisher, plc_status


class AgvControllerNode(Node):
    """10 ms 제어 주기와 100 ms 상태 발행 주기를 운용한다."""

    def __init__(self):
        super().__init__("QUAD_STEER_AGV_CONTROLLER")
        defaults = dict(
            debug_ui=False, rcs_log_topics="", job_feedback_topic="/vehicle/last_job_request",
            debug_log_dir="/home/rnd08/ros2_ws/src/new_codex_projects/steer_agv/debug",
            plc_enabled=False, plc_writes_enabled=False, plc_host="192.168.11.10", plc_port=8501,
            plc_fast_period=0.01, plc_slow_period=0.1, plc_response_timeout=0.2,
            plc_retry_period=1.0, plc_feedback_timeout=0.5, plc_life_timeout=3.0,
            startup_grace=3.0, command_timeout=0.25, manual_timeout=0.4, body_offset=0.6,
            turn_sign=1, acceleration=0.1, deceleration=0.1, jerk_ratio=1.0, obstacle_slow_speed=0.05,
            drive_progress_timeout=5.0,
            distance_tolerance=0.005, stop_speed_tolerance=0.005, arrival_hold_time=0.2,
            max_lift_height=1.5, max_lift_speed=0.2, lift_tolerance=0.003, qr_topic="/sensor/qr",
        )
        # 운전 중 바꿔도 상태 일관성을 해치지 않는 값만 동적 파라미터로 허용한다.
        self.tunable = {"plc_feedback_timeout", "plc_life_timeout", "manual_timeout",
                        "obstacle_slow_speed", "acceleration", "deceleration", "jerk_ratio",
                        "arrival_hold_time", "distance_tolerance", "stop_speed_tolerance",
                        "lift_tolerance", "drive_progress_timeout"}
        # "self.tunable" 에 있는 파라미터는 운전중 값을 변경 할 수 있다. 나머지는 read_only로 선언한다.
        cfg = self.config = {k: self.declare_parameter(k, v, ParameterDescriptor(read_only=k not in self.tunable)).value
                             for k, v in defaults.items()}
        for key, default in defaults.items():
            if isinstance(default, float) and (not isfinite(cfg[key]) or cfg[key] <= 0):
                raise ValueError(f"{key} must be positive and finite")
        if cfg["turn_sign"] not in (-1, 1) or cfg["plc_writes_enabled"] and not cfg["plc_enabled"]:
            raise ValueError("invalid turn_sign or PLC write configuration")
        # "self.safety" : plc life의 값이 최신화를 감시하는 역할을 한다.
        self.safety = SafetyMonitor(cfg["plc_feedback_timeout"], cfg["plc_life_timeout"])
        self.jobs = JobManager(cfg)
        self.log_feed = DebugFeed(cfg)
        self.debug = self.log_feed if cfg["debug_ui"] else None
        self.drive_log = DriveFaultLog(cfg["debug_log_dir"], self.log_feed)
        self.profile_job = None
        self.profile_until = 0.0
        self.pending_topics = set(cfg["rcs_log_topics"].split())
        if self.pending_topics:
            self.create_timer(1.0, self.discover_debug_topics)
        self.add_on_set_parameters_callback(self.validate_parameters)#파라미터 변경요청 검사 함수 지정
        self.odom = OdometryEstimator()
        self.feedback = StatusPublisher(self)
        self.boot = self.last_tick = monotonic()
        self.previous = None
        self.pending_reset = None
        self._diagnostics = self.create_publisher(DiagnosticArray, "/diagnostics", 10) #ui에 주는 진단토픽
        self._dm_monitor = self.create_publisher(DiagnosticArray, "/vehicle/plc_dm", 1)#ui에 주는 plc raw data 토픽
        self.job_feedback = self.create_publisher(DiagnosticStatus, cfg["job_feedback_topic"], JOB_QOS)
        self._plc = None
        self.create_timer(0.01, self.control_tick)
        self.create_timer(0.1, self._publish_status)
        for name, kind, handler in (
            ("set_job", SetJob, self.set_job),
            ("manual_command", ManualCommand, self.manual), ("lift_control", SetMvLift, self.lift),
            ("measure_lap", MeasureLap, self.calibrate), ("reset_odometry", ResetOdometry, self.reset_pose),
        ):
            self.create_service(kind, "/vehicle/"+name, self.service(handler, name))
            #서비스 생성, self.service 에서 디버그 기록이 가능하도록 조정하여 생성.
        self.control_group = MutuallyExclusiveCallbackGroup()
        self.create_service(RobotControl, "/vehicle/robot_control", self.control_service,
                            callback_group=self.control_group)
        if cfg["plc_enabled"]:
            self._plc = PlcClient(cfg["plc_host"], cfg["plc_port"], cfg["plc_response_timeout"],
                                  cfg["plc_retry_period"], cfg["plc_fast_period"], cfg["plc_slow_period"],
                                  cfg["plc_writes_enabled"], cfg["command_timeout"], debug=self.log_feed)
            self._plc.start()

    def sample(self):
        """통신 스레드가 완성한 최신 불변 PLC(DM 값) 스냅샷을 가져온다."""
        return self._plc.take_latest_snapshot() if self._plc else None
        #스냅샷 : PLC에서 읽은 DM값들을 해석하고 연결 상태·수신 시각 같은 정보와 함께 변경할수 없는 하나의 정보로 묶은것 
    def validate_parameters(self, parameters):
        """동적 파라미터가 양의 유한 실수인지 확인한다."""
        for param in parameters:
            if param.name in self.tunable and (not isinstance(param.value, float)
                                               or not isfinite(param.value) or param.value <= 0):
                return SetParametersResult(successful=False, reason=f"{param.name} must be positive and finite")
        return SetParametersResult(successful=True)

    def discover_debug_topics(self):
        """지정한 관찰용 토픽이 ROS 그래프에 나타나면 그 타입으로 구독한다."""
        if not self.pending_topics:
            return
        from rosidl_runtime_py.utilities import get_message
        from rclpy.qos import qos_profile_sensor_data
        for name, types in self.get_topic_names_and_types():
            if name in self.pending_topics and len(types) == 1:
                try:
                    msg_type = get_message(types[0])
                except (ImportError, AttributeError, ValueError):
                    continue
                self.create_subscription(msg_type, name, lambda msg, n=name: self.trace("TOPIC RX", n, msg),
                                         qos_profile_sensor_data)
                self.pending_topics.remove(name)

    def trace(self, direction, name, message):
        self.log_feed.record("rcs", f"{direction} {name} {message}")

    def service(self, handler, name):
        """일반 서비스를 동일한 검증·응답 형식으로 감싸는 콜백을 만든다."""
        def callback(request, response):
            self.trace("SERVICE RX", "/vehicle/"+name, request)
            # 요청 처리 직전·직후 제어 상태를 갱신해 오래된 PLC 값 사용을 줄인다.
            self.control_tick()
            try:
                handler(request, self.previous, monotonic())
                response.success, response.message = True, "accepted"
            except ValueError as exc:
                response.success, response.message = False, str(exc)
            self.control_tick()
            self.trace("SERVICE TX", "/vehicle/"+name, response)
            if name in ("set_job", "lift_control"):
                report = DiagnosticStatus(name="/vehicle/"+name, message=response.message)
                report.level = DiagnosticStatus.OK if response.success else DiagnosticStatus.WARN
                report.values = [KeyValue(key=key, value=str(getattr(request, key)))
                                 for key in request.get_fields_and_field_types()]
                self.job_feedback.publish(report)
            return response
        return callback

    def permit(self, sample, now):
        """PLC 쓰기와 차량 동작이 현재 허용되는지 공통 검사한다."""
        if not self.config["plc_writes_enabled"]:
            raise ValueError("PLC writes disabled")
        if self.pending_reset:
            raise ValueError("PLC RESET pending")
        self.safety.check_motion(sample, now)

    def set_job(self, r, s, now):
        """검증된 자동 주행 작업을 등록한다."""
        self.permit(s, now)
        self.jobs.register(r, s)

    def manual(self, r, s, now):
        """수동 조작 명령을 제한 시간과 함께 등록한다."""
        self.permit(s, now)
        self.jobs.set_manual(r, s, now)

    def lift(self, r, s, now):
        """리프트 높이·속도 작업을 등록한다."""
        self.permit(s, now)
        self.jobs.set_lift(r.job_id, r.set_height, r.set_speed, s)

    def calibrate(self, r, s, now):
        """아직 피드백 계약이 없는 QR 보정 요청을 명시적으로 거부한다."""
        if (r.mode or "LAP") not in ("LAP", "ALIGN"):
            raise ValueError("unknown calibration mode")
        raise ValueError("QR origin and alignment completion feedback unavailable")

    async def control_service(self, request, response):
        """STOP/RUN/PAUSE/RESET 요청을 처리하며 RESET은 PLC 수신 OK까지 기다린다."""
        self.trace("SERVICE RX", "/vehicle/robot_control", request)
        self.control_tick()
        try:
            ack = self.robot_control(request, self.previous, monotonic())
            if ack is not None:
                # Future를 비동기로 기다려 10 ms 타이머와 PLC 수신을 막지 않는다.
                if not await ack:
                    raise ValueError("PLC RESET acknowledgment missing")
                s = self.sample()
                if self.safety.current_fault(s, monotonic()) or s.fast.estop or s.slow.obstacle_stop:
                    raise ValueError("PLC accepted RESET but fault is still active")
                self.safety.latched = 0
            response.success, response.message = True, "accepted"
        except ValueError as exc:
            response.success, response.message = False, str(exc)
        self.control_tick()
        self.trace("SERVICE TX", "/vehicle/robot_control", response)
        return response

    def robot_control(self, r, s, now):
        """로봇 상태를 검사하고 요청을 수행할 수 있는 상태인지 확인한다. 작업 상태 또는 RESET 이벤트를 변경한다. 
            만약 작업을 수행할 수 있는 상태가 아니라면 에러(ValueError)를 반환한다."""
        if r.state not in ("", "STOP", "RUNNING", "PAUSE", "FINISH") or r.reset > 1 or r.job_cancel > 1:
            raise ValueError("invalid robot_control request")
        if (r.reset or r.job_cancel) and r.state in ("RUNNING", "FINISH"):
            raise ValueError(f"{r.state} conflicts with reset or cancel")
        if r.state == "FINISH":
            self.permit(s, now)
            if s.fast.state == 32 and self.jobs.command == 0:
                raise ValueError("waiting for PLC STOP feedback")
            if (s.fast.mode != 2 or s.fast.state not in (0, 2, 4, 32)
                    or self.jobs.command == 1 or self.jobs.lift or
                    any(abs(w.speed_mps) > self.config["stop_speed_tolerance"] for w in s.fast.wheels)):
                raise ValueError("FINISH requires stationary automatic mode and idle lift")
            self.jobs.stop(command=32)
            self.jobs.completed = True
            return
        if not r.job_cancel and (self.jobs.command == 32 or s and s.fast and s.fast.state == 32):
            if r.state in ("STOP", "PAUSE"):
                raise ValueError("FINISH waits for a new job or job_cancel")
        if r.job_cancel or r.state == "STOP":
            self.jobs.stop(discard=bool(r.job_cancel))
        elif r.state == "PAUSE":
            self.jobs.pause()
        if r.reset:
            if self.safety.current_fault(s, now) or s.fast.estop or s.slow.obstacle_stop:
                raise ValueError("reset cause still active")
            if self.jobs.command == 1 or s.fast.state not in (0, 2, 4) or self.jobs.manual or self.jobs.lift:
                raise ValueError("reset requires stationary state")
            event = self._plc.request_reset()
            ack = Future(executor=self.executor)
            self.pending_reset = event, ack, now+2*self.config["plc_response_timeout"]+0.15
            return ack
        if r.state == "RUNNING":
            self.permit(s, now)
            first_run = not self.jobs.started
            self.jobs.run(s, self.odom.pose[2], now)
            if first_run:
                # 새 JOB의 첫 RUN 자세로만 중심을 고정한다. 같은 JOB 재개 시에는 유지한다.
                self.jobs.anchor_circle_center(self.odom.pose)

    def reset_pose(self, r, s, now):
        """차량이 완전히 정지했고 미완료 작업이 없을 때만 odom을 재설정한다."""
        if (self.safety.current_fault(s, now) or s.fast.state not in (0, 2)
                or self.jobs.job and not self.jobs.completed or self.jobs.manual or self.jobs.lift
                or max(abs(w.speed_mps) for w in s.fast.wheels) > self.config["stop_speed_tolerance"]):
            raise ValueError("odometry reset requires a stationary vehicle without a pending drive")
        if not all(map(isfinite, (r.x, r.y, r.yaw))):
            raise ValueError("nonfinite pose")
        self.jobs.circle_center = None
        self.odom.pose = (r.x, r.y, r.yaw)
        self.odom.previous = None
        self.feedback.points.clear()

    def record_drive_fault(self, source, reason, sample, now):
        """정지 처리 전에 작업 진행도와 같은 스캔의 휠 피드백을 복사한다."""
        jobs = self.jobs
        job = jobs.job
        target = job.set_dist if job else None
        fast = sample.fast
        _, tx = self._plc.register_feedback()
        speed_word, ack = tx.get(106, (None, None))
        speed = None if speed_word is None else PlcCodec.decode_int16(speed_word)
        self.drive_log.record({
            "code": 103, "source": source, "reason": reason,
            "job_id": job.job_id if job else None, "target_distance_m": target,
            "progress_m": jobs.progress,
            "remaining_m": (abs(target) - (1 if target >= 0 else -1) * jobs.progress
                            if job else None),
            "distance_tolerance_m": self.config["distance_tolerance"],
            "seconds_since_progress_update": now - jobs.progress_at if jobs.started else None,
            "progress_timeout_s": self.config["drive_progress_timeout"],
            "job_speed_mps": job.set_speed if job else None,
            "last_tx_DM106_mmps": speed, "last_tx_DM106_ack": ack,
            "feedback_DM032_mmps": fast.feedback_speed_mmps,
            "DM032_raw": fast.feedback_speed_mmps & 0xffff,
            "odom_speed_mps": self.odom.twist[0],
            "wheel_speed_mps": {k: w.speed_mps for k, w in zip(("FL", "FR", "RL", "RR"), fast.wheels)},
            "DM001_mode": fast.mode, "DM002_state": fast.state, "DM004_error": fast.error_code,
        })

    def record_drive_profile(self, s, words, now, driving, moved, sample_dt, fault):
        """RUN 중과 마지막 RUN 이후 1초 동안 지령과 같은 주기의 피드백을 기록한다."""
        jobs = self.jobs
        if driving and jobs.job is not None:
            self.profile_job = jobs.job
            self.profile_until = now + 1.0
        job = self.profile_job
        if job is None:
            return
        ended = now >= self.profile_until or jobs.job is not job
        if ended:
            self.profile_job = None
        fast = s.fast if s else None
        tx_word, ack = self._plc.register_feedback()[1].get(106, (None, None))
        sent_speed = PlcCodec.decode_int16(tx_word) if tx_word is not None else None
        feedback_speed = fast.feedback_speed_mmps if fast else None
        progress = jobs.progress if jobs.job is job else None
        direction = 1 if job.set_dist >= 0 else -1
        row = {
            "job_id": job.job_id, "tick_s": now, "phase": "end" if ended else "drive" if driving else "after_stop",
            "plc_fast_s": s.fast_at if s else None, "encoder_dt_s": sample_dt,
            "target_m": job.set_dist, "progress_m": progress,
            "remaining_m": abs(job.set_dist) - direction * progress if progress is not None else None,
            "tolerance_m": self.config["distance_tolerance"], "job_speed_mps": job.set_speed,
            "requested_DM106_mmps": PlcCodec.decode_int16(words[106]),
            "last_tx_DM106_mmps": sent_speed,
            "feedback_DM032_mmps": feedback_speed,
            "DM032_raw": feedback_speed & 0xffff if fast else None,
            # 현재 관측한 피드백에서 마지막 OK 지령을 뺀 값이다. PLC 응답 지연도 포함된다.
            "feedback_minus_last_tx_mmps": (feedback_speed - sent_speed if feedback_speed is not None
                                            and sent_speed is not None and ack == "OK" else None),
            "last_tx_DM106_ack": ack, "requested_DM103": words[103],
            "plc_state": fast.state if fast else None, "error": fault,
            "obstacle_slow": s.slow.obstacle_slow if s and s.slow else None,
            "odom_vx_mps": self.odom.twist[0] if fast else None,
            "encoder_path_mps": moved / sample_dt if moved is not None and sample_dt and sample_dt > 0 else None,
        }
        for index, label in enumerate(("FL", "FR", "RL", "RR")):
            wheel = fast.wheels[index] if fast else None
            row[label + "_speed_mps"] = wheel.speed_mps if wheel else None
            row[label + "_distance_m"] = wheel.distance_m if wheel else None
        self.drive_log.profile(row, flush=ended)

    def control_tick(self):
        """피드백 판정, odom 갱신, 작업 계산, PLC 제출을 한 주기로 수행한다."""
        now = monotonic()
        self.config.update((p.name, p.value) for p in self.get_parameters(sorted(self.tunable)))
        self.safety.freshness = self.config["plc_feedback_timeout"]
        self.safety.life_timeout = self.config["plc_life_timeout"]
        if self.pending_reset:
            # PLC 통신 스레드의 WR DM104 OK 결과를 ROS Future로 전달한다.
            event, ack, deadline = self.pending_reset
            if event.done() or now >= deadline:
                accepted = event.done() and not event.cancelled() and event.result()
                if not accepted:
                    event.cancel()
                ack.set_result(bool(accepted))
                self.pending_reset = None
        s = self.sample()
        old = self.previous
        self.previous = s
        dt, self.last_tick = now - self.last_tick, now
        fault = self.safety.current_fault(s, now)
        if fault == 103 and s.fast.error_code == 103 and (not old or not old.fast or old.fast.error_code != 103):
            self.record_drive_fault("PLC", "DM004=103", s, now)
        if fault:
            if self.config["plc_enabled"] and (fault != 701 or now-self.boot >= self.config["startup_grace"] or old and old.connected):
                self.safety.latched = fault
        words = stopped_words()
        driving = self.jobs.command == 1
        moved = sample_dt = None
        # 정상 조건이 모두 확인되기 전까지 출력 기본값은 항상 정지 명령이다.
        if s and s.fast and s.slow:
            initialized = s.fast.state == 16 and (not old or not old.fast or old.fast.state != 16)
            if initialized:
                # PLC INITIALIZE 진입 시 기존 작업과 엔코더 기준을 모두 무효화한다.
                self.jobs.stop(discard=True)
                self.odom.previous = None
            if old and old.fast and (old.generation != s.generation or old.fast.mode != s.fast.mode
                    or s.interlock_epoch-old.interlock_epoch > 1):
                self.jobs.stop()
            if not old or old.generation != s.generation:
                self.jobs.hold_height = None
                self.jobs.halt_lift = False
            if old and old.fast and old.fast.state == 1 and s.fast.state != 1 and self.jobs.command == 1:
                self.jobs.stop()
            if dt >= self.config["command_timeout"]:
                self.jobs.stop()
            fresh = self.odom.stamp != s.fast_at or self.odom.generation != s.generation
            if fresh and fault != 701:
                try:
                    # 유효한 연속 샘플만 거리 차분 속도의 분모로 사용한다.
                    if (self.odom.previous is not None and self.odom.generation == s.generation
                            and not s.fast.invalid_wheel_dms):
                        sample_dt = s.fast_at - self.odom.stamp
                    moved = self.odom.update(s)
                    if self.jobs.started and not self.jobs.completed:
                        self.jobs.progress += moved
                    self.feedback.publish_motion(s, self.odom, self.get_clock().now().to_msg(), now)
                except ValueError:
                    self.safety.latched = 102
            if not (fault or self.safety.latched or s.fast.estop or s.slow.obstacle_stop or s.fast.state == 16):
                try:
                    words = self.jobs.outputs(s, self.odom, now, max(1e-6, min(0.05, dt)))
                except ValueError as exc:
                    self.safety.latched = 103
                    self.record_drive_fault("controller", str(exc), s, now)
                    self.jobs.stop()
            else:
                self.jobs.stop()
        elif old and old.fast:
            self.jobs.stop()
        if s and s.fast and fault != 701:
            try:
                self.jobs.apply_lift_stop(words, s)
            except ValueError:
                self.safety.latched = 203
                words = stopped_words()
        if self._plc and s:
            self._plc.submit(s, words)
        if self._plc and (driving or self.profile_job is not None):
            self.record_drive_profile(s, words, now, driving, moved, sample_dt, fault or self.safety.latched)

    def _publish_status(self):
        """진단과 외부 RobotStatus를 100 ms마다 발행한다."""
        now = monotonic()
        if self.debug:
            self.debug.set_params(self.config)
        s = self.sample()
        stamp = self.get_clock().now().to_msg()
        message = DiagnosticArray()
        message.header.stamp = stamp
        message.status = [plc_status(s, now, self.safety.freshness, self.safety.life_timeout)]
        self._diagnostics.publish(message)
        if self._dm_monitor.get_subscription_count():
            # 기본 진단과 별도로 원시 데이터가 필요한 구독자에게만 전송한다.
            status = message.status[0]
            status.values = []
            rx, tx = self._plc.register_feedback() if self._plc else ({}, {})
            for addr, value in rx.items():
                status.values.append(KeyValue(key=f"RX/{addr}", value=str(value)))
            for addr, (value, ack) in tx.items():
                status.values.append(KeyValue(key=f"TX/{addr}", value=str(value)))
                status.values.append(KeyValue(key=f"ACK/{addr}", value=ack))
            self._dm_monitor.publish(message)
        fault = self.safety.current_fault(s, now) or self.safety.latched
        if fault == 701 and not self.safety.latched and now-self.boot < self.config["startup_grace"]:
            fault = 0
        self.feedback.publish_status(s, self.odom, self.jobs, fault, stamp)

    def close(self):
        """노드 종료 전에 PLC 작업 스레드를 정지한다."""
        if self._plc:
            self._plc.close()
        self.drive_log.close()
        self.log_feed.close()
