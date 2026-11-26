from math import cos, pi, sin

import cv2
import numpy as np
import pytest

from steer_agv_marker.filtering import SampleFilter, wrap
from steer_agv_marker.geometry import CORNERS, DICTIONARY, Detector, transform, world_from_tag

K = np.array([[850., 0, 400], [0, 850., 300], [0, 0, 1]])
DIST = np.zeros(5)
BASE_OPTICAL = transform([[0,0,1], [-1,0,0], [0,-1,0]], [.45, 0, .30])


def synthetic(yaw, marker_id=0):
    # Ground truth independently places the robot 1.5 m in front of a vertical print.
    world_base = transform([[cos(yaw), -sin(yaw), 0], [sin(yaw), cos(yaw), 0], [0,0,1]], [1.5, .04, 0])
    world_tag = transform([[0,0,1], [1,0,0], [0,1,0]], [0,0,.3])
    optical_tag = np.linalg.inv(world_base @ BASE_OPTICAL) @ world_tag
    pixels = cv2.projectPoints(CORNERS, cv2.Rodrigues(optical_tag[:3,:3])[0], optical_tag[:3,3], K, DIST)[0]
    draw = getattr(cv2.aruco, "generateImageMarker", None) or cv2.aruco.drawMarker
    marker = draw(DICTIONARY, marker_id, 600)
    homography = cv2.getPerspectiveTransform(np.float32([[0,0],[599,0],[599,599],[0,599]]),
                                             (4*pixels.reshape(4,2)+1.5).astype("float32"))
    large = cv2.warpPerspective(marker, homography, (3200,2400), borderValue=255)
    return cv2.resize(large, (800,600), interpolation=cv2.INTER_AREA)


@pytest.mark.parametrize("yaw", [pi, pi-0.18])
def test_image_to_base_pose(yaw):
    detector = Detector(0, K, DIST, world_from_tag(0,0,0))
    pose = detector.measure(synthetic(yaw), np.linalg.inv(BASE_OPTICAL))
    assert pose is not None
    assert pose[:2] == pytest.approx([1.5, .04], abs=.09)
    assert abs(wrap(pose[2]-yaw)) < .08
    assert detector.measure(synthetic(yaw, 2), np.linalg.inv(BASE_OPTICAL)) is None
    assert detector.measure(np.full((600,800),255,np.uint8), np.linalg.inv(BASE_OPTICAL)) is None


def test_ten_accepted_frames_outlier_dropout_and_wrapped_yaw():
    filt = SampleFilter()
    valid = 0
    for i in range(12):
        z = np.array([.01*i, 0., wrap(pi-.02+.005*i)])
        if i in (0, 6):
            z[1] = 20.0
        else:
            valid += 1
        pose = filt.observe(i*.05, z)
        if valid < 10:
            assert pose is None
    assert pose[:2] == pytest.approx([.11,0.], abs=.01)
    assert abs(wrap(pose[2]-z[2])) < .01
    assert filt.observe(.60, np.array([100., 0., 0.])) is None
    assert filt.observe(.65, None) is None
    assert filt.observe(.70, z) is None


def test_serial_camera_to_ros_topic(tmp_path, monkeypatch):
    import os
    from time import monotonic
    import yaml
    import rclpy
    from geometry_msgs.msg import PoseStamped, TransformStamped
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from tf2_ros import StaticTransformBroadcaster
    from steer_agv_marker.node import MarkerLocalizer

    calibration = tmp_path / "camera.yaml"
    calibration.write_text(yaml.safe_dump(dict(image_width=800, image_height=600,
        distortion_model="plumb_bob", camera_matrix={"data": K.ravel().tolist()},
        distortion_coefficients={"data": DIST.tolist()})))
    master, slave = os.openpty()
    monkeypatch.setenv("ROS_LOCALHOST_ONLY", "1")
    monkeypatch.setenv("ROS_DOMAIN_ID", "172")
    rclpy.init(args=["--ros-args", "-p", "device:="+os.ttyname(slave),
                     "-p", "calibration_file:="+str(calibration)])
    node = MarkerLocalizer()
    peer = Node("test_marker_subscriber")
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    executor.add_node(peer)
    messages = []
    peer.create_subscription(PoseStamped, "/vehicle/marker_pose", messages.append, 1)
    broadcaster = StaticTransformBroadcaster(peer)
    tf = TransformStamped()
    tf.header.frame_id, tf.child_frame_id = "base_link", "cam_optical_frame"
    tf.transform.translation.x, tf.transform.translation.z = .45, .30
    q = tf.transform.rotation
    q.x, q.y, q.z, q.w = -.5, .5, -.5, .5
    broadcaster.sendTransform(tf)

    def wait(condition, timeout=4):
        deadline = monotonic()+timeout
        while not condition():
            assert monotonic() < deadline
            executor.spin_once(timeout_sec=.005)

    def send(img):
        data = cv2.imencode(".jpg", img)[1].tobytes()
        while data:
            data = data[os.write(master, data):]

    try:
        wait(lambda: node.tf.can_transform("cam_optical_frame", "base_link", rclpy.time.Time())
             and node.pose_pub.get_subscription_count() > 0)
        frame = synthetic(pi)
        for i in range(1,11):
            send(frame)
            wait(lambda: max(len(node.filter.seed), len(node.filter.samples)) == i)
            if i < 10:
                assert not messages
        wait(lambda: len(messages) == 1)
        assert messages[0].header.frame_id == "marker_ground"
        assert messages[0].pose.position.x == pytest.approx(1.5, abs=.09)
        send(np.full_like(frame, 255))
        wait(lambda: not node.filter.samples)
        end = monotonic()+.16
        while monotonic() < end:
            executor.spin_once(timeout_sec=.01)
        assert len(messages) == 1
    finally:
        node.camera.close()
        executor.shutdown()
        node.destroy_node()
        peer.destroy_node()
        rclpy.shutdown()
        os.close(master)
        os.close(slave)
