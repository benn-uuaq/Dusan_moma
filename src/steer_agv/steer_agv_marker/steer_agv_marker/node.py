"""20 Hz로 카메라 마커를 처리하고 필터링된 AGV 자세를 발행한다."""

from math import cos, isfinite, sin
from time import monotonic

import cv2
import numpy as np
import rclpy
from geometry_msgs.msg import PoseStamped
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.time import Time
from tf2_ros import Buffer, TransformException, TransformListener

from .capture import Camera
from .filtering import SampleFilter
from .geometry import Detector, read_calibration, transform, world_from_tag


def tf_matrix(tf):
    """ROS Transform 메시지를 목적지_from_원본 4x4 행렬로 변환한다."""
    q = tf.rotation
    x, y, z, w = q.x, q.y, q.z, q.w
    rotation = [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]]
    p = tf.translation
    return transform(rotation, [p.x, p.y, p.z])


class MarkerLocalizer(Node):
    """카메라·TF·마커 검출·Kalman 필터를 연결하는 ROS 2 노드."""
    def __init__(self):
        super().__init__("steer_agv_marker_localizer")
        defaults = dict(device="/dev/ttyUSB0", baudrate=921600, calibration_file="",
                        marker_id=0, frame_id="marker_ground", marker_x=0., marker_y=0., marker_yaw=0.,
                        base_frame="base_link", optical_frame="cam_optical_frame",
                        position_sigma=0.025, angle_sigma=0.035, innovation_gate=16.27,
                        max_reprojection_px=2.0)
        cfg = {k: self.declare_parameter(k, v).value for k, v in defaults.items()}
        if not cfg["calibration_file"]:
            raise ValueError("set calibration_file to the ROS camera calibration YAML")
        if any(not isfinite(cfg[k]) or cfg[k] <= 0 for k in
               ("position_sigma", "angle_sigma", "innovation_gate", "max_reprojection_px", "baudrate")):
            raise ValueError("noise, gate, pixel error and baudrate must be positive")
        if not all(isfinite(cfg[k]) for k in ("marker_x", "marker_y", "marker_yaw")):
            raise ValueError("invalid marker world pose")
        k, dist, self.size = read_calibration(cfg["calibration_file"])
        self.detector = Detector(cfg["marker_id"], k, dist, world_from_tag(
            cfg["marker_x"], cfg["marker_y"], cfg["marker_yaw"]), cfg["max_reprojection_px"])
        self.filter = SampleFilter(cfg["position_sigma"], cfg["angle_sigma"], cfg["innovation_gate"])
        self.frame, self.base, self.optical = cfg["frame_id"], cfg["base_frame"], cfg["optical_frame"]
        self.tf = Buffer()
        # URDF의 cam joint를 포함한 optical_frame <- base_frame 변환을 TF에서 얻는다.
        self.listener = TransformListener(self.tf, self)
        self.pose_pub = self.create_publisher(PoseStamped, "/vehicle/marker_pose", 1)
        self.camera = Camera(cfg["device"], cfg["baudrate"], self.size, self.get_clock(), self.get_logger())
        self.create_timer(1/20, self.tick)
        self.camera.thread.start()

    def tick(self):
        """새 프레임 하나를 처리하며 유효한 10개 샘플이 모였을 때만 발행한다."""
        latest = self.camera.take()
        if latest is None:
            return
        acquired, stamp, image = latest
        if monotonic()-acquired > 0.2 or image.shape[:2][::-1] != self.size:
            # 오래되거나 보정 해상도와 다른 영상은 필터 연속성을 끊는다.
            self.filter.reset()
            self.get_logger().warning("stale frame or image size differs from calibration", throttle_duration_sec=5)
            return
        try:
            extrinsic = self.tf.lookup_transform(self.optical, self.base, Time())
            # Detector 내부 계산: world_from_tag * tag_from_optical * optical_from_base
            measurement = self.detector.measure(image, tf_matrix(extrinsic.transform))
        except (TransformException, cv2.error, np.linalg.LinAlgError) as exc:
            self.filter.reset()
            self.get_logger().warning(str(exc), throttle_duration_sec=5)
            return
        result = self.filter.observe(acquired, measurement)
        if result is None:
            # 미검출, 이상치, 또는 아직 10개가 모이지 않은 경우 topic을 발행하지 않는다.
            return
        x, y, yaw = result
        msg = PoseStamped()
        msg.header.frame_id, msg.header.stamp = self.frame, stamp
        msg.pose.position.x, msg.pose.position.y = float(x), float(y)
        msg.pose.orientation.z, msg.pose.orientation.w = sin(yaw/2), cos(yaw/2)
        self.pose_pub.publish(msg)


def main(args=None):
    """마커 위치추정 노드를 실행하고 카메라 스레드를 안전하게 종료한다."""
    rclpy.init(args=args)
    node = None
    try:
        node = MarkerLocalizer()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if node:
            node.camera.close()
            node.destroy_node()
        rclpy.try_shutdown()
