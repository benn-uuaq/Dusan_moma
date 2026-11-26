"""마커를 이용한 미터 단위 자세 계산. 행렬 이름은 목적지_from_원본 규칙이다."""
from math import atan2, cos, sin
from pathlib import Path

import cv2
import numpy as np
import yaml


# ArUco 4x4_50은 ID와 모서리 순서를 함께 복원하므로 평면 내 회전을 구하기 좋다.
# 실제 140 mm 정사각형의 네 모서리는 IPPE PnP 계산에 미터 축척을 제공한다.
DICTIONARY = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
SIDE_M = 0.140
CORNERS = SIDE_M / 2 * np.array([[-1, 1, 0], [1, 1, 0], [1, -1, 0], [-1, -1, 0]], dtype=float)


def transform(rotation, translation):
    """3x3 회전과 3차원 이동을 4x4 동차변환 행렬로 결합한다."""
    result = np.eye(4)
    result[:3, :3] = rotation
    result[:3, 3] = np.asarray(translation).reshape(3)
    return result


def world_from_tag(x, y, yaw):
    """수직 설치된 마커 좌표계를 월드 좌표계에 배치한다."""
    # 인쇄물 위쪽은 +Z이다. yaw=0이면 법선은 +X, 인쇄물 오른쪽은 +Y를 향한다.
    return transform([[-sin(yaw), 0, cos(yaw)], [cos(yaw), 0, sin(yaw)], [0, 1, 0]], [x, y, 0])


def read_calibration(filename):
    """ROS camera_calibration YAML을 읽고 내부 파라미터를 검증한다."""
    with Path(filename).expanduser().open() as f:
        data = yaml.safe_load(f)
    if data["distortion_model"] not in ("plumb_bob", "rational_polynomial"):
        raise ValueError("camera must use plumb_bob or rational_polynomial calibration")
    k = np.array(data["camera_matrix"]["data"], dtype=float).reshape(3, 3)
    dist = np.asarray(data["distortion_coefficients"]["data"], dtype=float).reshape(-1)
    size = (int(data["image_width"]), int(data["image_height"]))
    if (min(size) < 1 or min(k[0, 0], k[1, 1]) <= 0 or not np.allclose(k[2], [0, 0, 1])
            or dist.size not in (4, 5, 8, 12, 14) or not np.isfinite(k).all() or not np.isfinite(dist).all()):
        raise ValueError("invalid camera calibration")
    return k, dist, size


class Detector:
    """지정 ID의 ArUco를 검출해 월드 좌표의 AGV 평면 자세를 계산한다."""
    def __init__(self, marker_id, intrinsics, distortion, world_tag, max_error=2.0):
        if not 0 <= marker_id < 50:
            raise ValueError("ArUco ID must be in [0, 49]")
        self.marker_id, self.k, self.dist = marker_id, intrinsics, distortion
        self.world_tag, self.max_error = world_tag, max_error
        ar = cv2.aruco
        params = ar.DetectorParameters() if hasattr(ar, "ArucoDetector") else ar.DetectorParameters_create()
        params.cornerRefinementMethod = ar.CORNER_REFINE_SUBPIX
        if hasattr(ar, "ArucoDetector"):
            self.detect = ar.ArucoDetector(DICTIONARY, params).detectMarkers
        else:
            self.detect = lambda img: ar.detectMarkers(img, DICTIONARY, parameters=params)

    def measure(self, image, optical_from_base):
        """영상 한 장에서 [AGV x, y, yaw] 측정값을 반환한다."""
        corners, ids, _ = self.detect(image)
        matches = [] if ids is None else np.flatnonzero(ids.ravel() == self.marker_id)
        if len(matches) != 1:
            return None
        pixels = np.asarray(corners[matches[0]], dtype=float).reshape(4, 2)
        ok, rotations, translations, _ = cv2.solvePnPGeneric(
            CORNERS, pixels, self.k, self.dist, flags=cv2.SOLVEPNP_IPPE_SQUARE)
        if not ok:
            return None
        best_cost, best = float("inf"), None
        for rotation, translation in zip(rotations, translations):
            # 평면 PnP에는 두 해가 생길 수 있으므로 물리 조건과 재투영 오차로 고른다.
            if not np.isfinite(rotation).all() or not np.isfinite(translation).all():
                continue
            rotation, translation = cv2.solvePnPRefineLM(
                CORNERS, pixels, self.k, self.dist, rotation.copy(), translation.copy())
            rot = cv2.Rodrigues(rotation)[0]
            if (rot @ CORNERS.T + translation)[2].min() <= 0 or float(rot[:, 2] @ translation.ravel()) >= 0:
                continue
            projected = cv2.projectPoints(CORNERS, rotation, translation, self.k, self.dist)[0].reshape(4, 2)
            rms = np.sqrt(np.mean(np.sum((projected-pixels)**2, axis=1)))
            world_base = self.world_tag @ np.linalg.inv(transform(rot, translation)) @ optical_from_base
            tilt = np.arccos(np.clip(world_base[2, 2], -1, 1))
            # 비용이 비슷한 평면 해는 AGV가 지면에 수직이라는 조건으로 구분한다.
            cost = rms + tilt
            if rms <= self.max_error and tilt < np.deg2rad(25) and cost < best_cost:
                best_cost = cost
                best = np.array([*world_base[:2, 3], atan2(world_base[1, 0], world_base[0, 0])])
        return best
