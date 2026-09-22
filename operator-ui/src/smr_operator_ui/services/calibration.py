"""3점 접촉으로 벽 좌표계를 맞춘 결과를 계산한다 (ERUT req/calibrate).

로봇은 스캔을 시작하기 전에 벽을 세 곳(중앙·원점쪽·반대쪽) 눌러 본다.
그 접촉 자세는 레지스터에 남는다(330~335 중앙 · 340~345 우 · 350~355 원점,
347~349 프로브 중심 오프셋). 세 점은 같은 높이의 한 호 위에 있으므로,
프로브 중심으로 옮긴 뒤 원을 맞추면 **실제 벽 반지름**이 나온다.

  calibration_error_mm = |잰 반지름 - 입력 반지름(260 + 261)|

입력한 모재 치수와 실제 벽이 얼마나 어긋나 있는지를 그대로 나타내는 값이라
ERUT 규격(탭5)의 `calibration_error_mm` 자리에 그대로 쓴다. 세 점의 높이
편차(level_mm)는 로봇이 벽에 대해 얼마나 기울어져 있는지를 보여 준다 —
수평 보정이 붙으면 이 값이 그 입력이 된다.

단위: 레지스터는 위치 0.1 mm, 회전 mrad 다(로봇 태스크가 x10000 / x1000).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

#: 접촉 자세의 시작 주소(330)로부터의 자리. probe_poses 한 벌은 330~355 다.
POSE_BASE = 330
CENTER, RIGHT, ORIGIN = 0, 10, 20        # 330, 340, 350
EOAT_OFFSET = 17                         # 347~349
POSE_LENGTH = 26

#: 위치 레지스터 1 = 0.1 mm, 회전 레지스터 1 = 1 mrad.
MM_PER_COUNT = 0.1
RAD_PER_COUNT = 0.001


@dataclass(frozen=True)
class CalibrationResult:
    """캘리브레이션 한 번의 결과. 실패해도 이유를 담아 돌려준다."""

    ok: bool
    error_mm: float = 0.0
    radius_mm: float = 0.0
    level_mm: float = 0.0
    reason: str = ""
    points: list[tuple[float, float, float]] = field(default_factory=list)

    def describe(self) -> str:
        if not self.ok:
            return f"캘리브레이션 계산 실패 — {self.reason}"
        return (f"잰 벽 반지름 {self.radius_mm:.1f} mm · "
                f"오차 {self.error_mm:.2f} mm · 높이 편차 {self.level_mm:.2f} mm")


def rotate(vector: tuple[float, float, float],
           rotvec: tuple[float, float, float]) -> tuple[float, float, float]:
    """회전 벡터(rad)로 벡터를 돌린다 (로드리게스)."""
    angle = math.sqrt(sum(v * v for v in rotvec))
    if angle < 1e-12:
        return tuple(vector)
    kx, ky, kz = (v / angle for v in rotvec)
    cos, sin = math.cos(angle), math.sin(angle)
    vx, vy, vz = vector
    dot = kx * vx + ky * vy + kz * vz
    cross = (ky * vz - kz * vy, kz * vx - kx * vz, kx * vy - ky * vx)
    return tuple(
        v * cos + c * sin + k * dot * (1 - cos)
        for v, c, k in zip(vector, cross, (kx, ky, kz))
    )


def probe_center(pose: list[int], offset_mm: tuple[float, float, float]
                 ) -> tuple[float, float, float]:
    """접촉 자세(레지스터 6개)와 툴 로컬 오프셋으로 프로브 중심을 구한다 [mm]."""
    position = tuple(value * MM_PER_COUNT for value in pose[:3])
    rotvec = tuple(value * RAD_PER_COUNT for value in pose[3:6])
    turned = rotate(offset_mm, rotvec)
    return tuple(p + t for p, t in zip(position, turned))


def circle_through(a: tuple[float, float], b: tuple[float, float],
                   c: tuple[float, float]) -> tuple[float, float, float] | None:
    """세 점을 지나는 원의 (중심 x, 중심 y, 반지름). 한 줄에 서면 None."""
    (ax, ay), (bx, by), (cx, cy) = a, b, c
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    return ux, uy, math.hypot(ax - ux, ay - uy)


def evaluate(probe_poses: list[int], expected_radius_mm: float,
             probe_result: list[int] | None = None) -> CalibrationResult:
    """레지스터 값으로 캘리브레이션 결과를 만든다.

    `probe_poses` 는 330~355 한 벌, `expected_radius_mm` 은 입력한 반지름
    + 두께(로봇이 호를 그릴 때 쓰는 값)다. `probe_result` 를 주면 접촉
    횟수(305)로 세 점을 다 찾았는지까지 본다.
    """
    if not probe_poses or len(probe_poses) < POSE_LENGTH:
        return CalibrationResult(False, reason="측정 값을 아직 받지 못했습니다")
    if probe_result and len(probe_result) >= 6 and probe_result[5] < 3:
        return CalibrationResult(
            False, reason=f"벽 접촉이 {probe_result[5]}번뿐입니다(3번 필요)")
    offset = tuple(probe_poses[EOAT_OFFSET + i] * MM_PER_COUNT for i in range(3))
    points = [probe_center(probe_poses[base:base + 6], offset)
              for base in (CENTER, RIGHT, ORIGIN)]
    if all(abs(value) < 1e-9 for point in points for value in point):
        return CalibrationResult(False, reason="접촉점이 비어 있습니다")
    circle = circle_through(points[0][:2], points[1][:2], points[2][:2])
    if circle is None:
        return CalibrationResult(
            False, reason="세 점이 한 줄에 있어 반지름을 낼 수 없습니다",
            points=points)
    radius = circle[2]
    zs = [point[2] for point in points]
    level = max(zs) - min(zs)
    if expected_radius_mm <= 0:
        return CalibrationResult(
            False, radius_mm=radius, level_mm=level, points=points,
            reason="입력 반지름이 없어 오차를 낼 수 없습니다")
    return CalibrationResult(
        True, error_mm=abs(radius - expected_radius_mm), radius_mm=radius,
        level_mm=level, points=points)
