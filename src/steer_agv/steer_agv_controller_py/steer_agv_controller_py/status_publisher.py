"""내부 제어 상태를 ROS 상태·오도메트리·TF·진단 메시지로 발행한다."""

from collections import deque
from math import cos, isfinite, sin

from diagnostic_msgs.msg import DiagnosticStatus, KeyValue
from geometry_msgs.msg import PoseStamped, TransformStamped
from nav_msgs.msg import Odometry, Path
from rclpy.qos import DurabilityPolicy, QoSProfile
from sensor_msgs.msg import JointState
from std_msgs.msg import Bool, Empty
from tf2_ros import TransformBroadcaster
from vehicle_interfaces.msg import RobotStatus


from .plc_client import PlcSnapshot


JOB_QOS = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)


class StatusPublisher:
    """발행자와 경로 이력을 보관하며 외부 표현 형식을 한 곳에서 관리한다."""
    def __init__(self, node):
        self.status = node.create_publisher(RobotStatus, "/vehicle/robot_status", 10)
        self.odom = node.create_publisher(Odometry, "/odom", 10)
        self.joints = node.create_publisher(JointState, "/joint_states", 10)
        self.path = node.create_publisher(Path, "/vehicle/path", 1)
        self.start = node.create_publisher(Empty, "/vehicle/start_request", 10)
        self.estop = node.create_publisher(Bool, "/vehicle/robot_estop", 10)
        self.tf = TransformBroadcaster(node)
        self.points = deque(maxlen=1000)
        self.previous_ready = None
        self.generation = None
        self.last_path = 0.0

    def publish_motion(self, sample, estimate, stamp, now):
        """새 PLC 휠 샘플에 대응하는 odom, TF, joint state와 경로를 발행한다."""
        f = sample.fast
        if sample.generation == self.generation and self.previous_ready is False and f.auto_ready:
            # 같은 PLC 연결 세대에서 AUTO READY 상승 에지만 시작 요청으로 알린다.
            self.start.publish(Empty())
        self.generation = sample.generation
        self.previous_ready = f.auto_ready
        self.estop.publish(Bool(data=f.estop))
        joints = JointState()
        joints.header.stamp = stamp
        for label, wheel in zip(("fl", "fr", "rl", "rr"), f.wheels):
            joints.name.append(label+"_steer_joint")
            joints.position.append(wheel.angle_rad)
            if isfinite(wheel.distance_m):
                joints.name.append(label+"_wheel_joint")
                joints.position.append(wheel.distance_m / 0.125)
        if isfinite(f.lift_height_m):
            joints.name.append("lift_joint")
            joints.position.append(f.lift_height_m)
        self.joints.publish(joints)
        if f.invalid_wheel_dms:
            return
        msg = Odometry()
        msg.header.frame_id = "odom"
        msg.header.stamp = stamp
        msg.child_frame_id = "base_footprint"
        x, y, yaw = estimate.pose
        pose = msg.pose.pose
        pose.position.x, pose.position.y = x, y
        pose.orientation.z, pose.orientation.w = sin(yaw/2), cos(yaw/2)
        msg.twist.twist.linear.x, msg.twist.twist.linear.y, msg.twist.twist.angular.z = estimate.twist
        self.odom.publish(msg)
        tf = TransformStamped(header=msg.header, child_frame_id=msg.child_frame_id)
        tf.transform.translation.x, tf.transform.translation.y = x, y
        tf.transform.rotation = pose.orientation
        self.tf.sendTransform(tf)
        if now-self.last_path >= 0.1:
            point = PoseStamped(header=msg.header, pose=pose)
            if not self.points or self.points[-1].pose != pose:
                self.points.append(point)
            self.path.publish(Path(header=msg.header, poses=list(self.points)))
            self.last_path = now

    def publish_status(self, sample, estimate, jobs, fault, stamp):
        """상위 시스템용 RobotStatus를 현재 작업·PLC 상태와 결합해 발행한다."""
        msg = RobotStatus()
        msg.header.stamp = stamp
        msg.header.frame_id = "odom"
        msg.position.x, msg.position.y, msg.position.yaw = estimate.pose
        msg.state = "ERROR" if fault else "INITIALIZE"
        msg.error_code = fault
        if fault:
            msg.error_msg = f"{fault} {ERROR_NAMES.get(fault, 'PLC_FAULT')}"
        if jobs.job:
            msg.job_id, msg.set_dist, msg.mv_dist = jobs.job.job_id, jobs.job.set_dist, jobs.progress
        if jobs.lift:
            msg.job_id = jobs.lift[0]
        if sample and sample.fast and sample.slow and fault != 701:
            f, s = sample.fast, sample.slow
            if not fault:
                msg.state = {0:"STOP", 1:"RUNNING", 2:"HOLD", 4:"PAUSE", 8:"ERROR",
                             16:"INITIALIZE", 32:"FINISH"}.get(f.state, "ERROR")
            msg.hold = "SET" if f.outrig else "RELEASE"
            msg.estop, msg.obstacle_stop, msg.charging = f.estop, s.obstacle_stop, s.charging
            msg.sen1_dist, msg.sen2_dist = s.guide_distance_m
            msg.battery_pct = round(s.battery_soc_percent) if 0 <= s.battery_soc_percent <= 100 else 255
            if isfinite(f.lift_height_m):
                msg.lift_h = f.lift_height_m
            if all(isfinite(w.speed_mps) for w in f.wheels):
                msg.speed = estimate.twist[0]
        self.status.publish(msg)
        if jobs.circle_center is not None:
            # 작업마다 위치가 달라지므로 일반 /tf에 상태 주기(10 Hz)로 발행한다.
            center = TransformStamped(child_frame_id="path_circle_center")
            center.header.stamp = stamp
            center.header.frame_id = "odom"
            center.transform.translation.x, center.transform.translation.y = jobs.circle_center
            center.transform.rotation.w = 1.0
            self.tf.sendTransform(center)


ERROR_NAMES = {
    101:"DRIVE_MOTOR_FAULT", 102:"DRIVE_ENCODER_FAULT", 103:"DRIVE_TARGET_NOT_REACHED",
    104:"STANDOFF_LOST", 105:"ALIGN_FAILED", 201:"LIFT_MOTOR_FAULT", 202:"LIFT_LIMIT",
    203:"LIFT_POSITION_ERROR", 204:"LIFT_BRAKE_FAULT", 301:"OUTRIGGER_SET_FAILED",
    302:"OUTRIGGER_RELEASE_FAILED", 303:"LEVEL_FAILED", 304:"OUTRIGGER_LOST",
    401:"DISTANCE_SENSOR_FAULT", 402:"QR_NOT_FOUND", 403:"IMU_FAULT",
    501:"BATTERY_CRITICAL", 502:"CHARGER_FAULT", 601:"SAFETY_SCANNER_FAULT",
    602:"SAFETY_CIRCUIT_FAULT", 701:"PLC_COMM_LOST", 702:"DRIVER_COMM_LOST", 901:"NODE_INTERNAL",
}


def plc_status(
    sample: PlcSnapshot | None,
    now: float,
    feedback_timeout: float,
    life_timeout: float,
) -> DiagnosticStatus:
    """PLC 연결, 피드백 신선도, LIFE, 안전 상태를 ROS 진단으로 변환한다."""
    status = DiagnosticStatus(name="steer_agv: PLC feedback", hardware_id="steer_agv_plc")
    status.level = DiagnosticStatus.STALE
    if sample is None:
        status.message = "PLC polling disabled"
        return status
    status.values = [KeyValue(key="connection_generation", value=str(sample.generation))]
    if not sample.connected or sample.fast is None or sample.slow is None:
        status.message = sample.error or "Waiting for PLC feedback"
        return status
    if any(t is None or now - t >= feedback_timeout for t in (sample.fast_at, sample.slow_at)):
        status.message = "PLC feedback is stale"
        return status

    fast, slow = sample.fast, sample.slow
    feedback = {
        "life": fast.life,
        "mode": fast.mode,
        "state": fast.state,
        "error_code": fast.error_code,
        "estop": fast.estop,
        "outrig": fast.outrig,
        "auto_ready": fast.auto_ready,
        "feedback_speed_mmps": fast.feedback_speed_mmps,
        "obstacle_stop": slow.obstacle_stop,
        "obstacle_slow": slow.obstacle_slow,
        "battery_soc_percent": slow.battery_soc_percent,
        "charging": slow.charging,
    }
    invalid_dms = list(fast.invalid_wheel_dms)
    if not isfinite(fast.lift_height_m):
        invalid_dms.append(26)
    if invalid_dms:
        feedback["invalid_float_registers"] = ", ".join(
            f"DM{address:03d}~DM{address + 1:03d}" for address in invalid_dms)
    status.values.extend(KeyValue(key=key, value=str(value)) for key, value in feedback.items())
    if sample.life_changed_at is None or now - sample.life_changed_at >= life_timeout:
        status.level, status.message = DiagnosticStatus.ERROR, "PLC LIFE did not change"
    elif fast.estop or slow.obstacle_stop or fast.error_code or fast.state == 8:
        status.level, status.message = DiagnosticStatus.ERROR, "PLC reports a stop or error condition"
    elif invalid_dms:
        status.level = DiagnosticStatus.ERROR
        status.message = "Invalid PLC measurement: " + feedback["invalid_float_registers"]
    elif slow.obstacle_slow:
        status.level, status.message = DiagnosticStatus.WARN, "PLC reports obstacle slowdown"
    else:
        status.level, status.message = DiagnosticStatus.OK, "PLC feedback received"
    return status
