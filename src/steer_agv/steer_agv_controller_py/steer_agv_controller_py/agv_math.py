"""AGV 제어에 필요한 순수 수식 모음. 단위는 m, rad, s를 사용한다."""

from math import atan2, cos, isfinite, pi, sin, sqrt, tau

from .math_types import CalcResult, Vector3


def transform_document_to_ros(point: Vector3) -> CalcResult[Vector3]:
    """문서 좌표(+Y 전방, +X 우측)를 ROS 좌표(+X 전방, +Y 좌측)로 바꾼다."""
    if not all(map(isfinite, (point.x, point.y, point.z))):
        return CalcResult(error="coordinates must be finite")
    return CalcResult(value=Vector3(point.y, -point.x, point.z))


def normalize_angle(angle: float) -> CalcResult[float]:
    """각도를 [-pi, pi) 범위로 정규화한다."""
    if not isfinite(angle):
        return CalcResult(error="angle must be finite")
    return CalcResult(value=(angle + pi) % tau - pi)


def calculate_center_radius(radius: float, standoff: float, body_offset: float) -> CalcResult[float]:
    """기준 원에서 차체 중심까지의 실제 회전 반경을 계산한다."""
    center = radius + standoff + body_offset
    if not isfinite(center) or radius <= 0 or standoff < 0 or body_offset < 0:
        return CalcResult(error="invalid center radius geometry")
    return CalcResult(value=center)


def circle_center_xy(pose, signed_radius):
    """odom 자세의 왼쪽 법선으로 부호 있는 반경만큼 이동한 원 중심(m)을 구한다."""
    x, y, yaw = pose
    return x - signed_radius * sin(yaw), y + signed_radius * cos(yaw)


def calculate_body_angle_error(
    center_radius: float,
    current_speed: float,
    scan_dt: float,
    path_heading: float,
    body_heading: float,
    turn_sign: int,
) -> CalcResult[float]:
    """다음 PLC 스캔 시점의 목표 접선과 현재 차체 방향 사이 각도를 구한다."""
    if not all(map(isfinite, (center_radius, current_speed, scan_dt, path_heading, body_heading))):
        return CalcResult(error="arc prediction inputs must be finite")
    if center_radius <= 0 or scan_dt <= 0 or turn_sign not in (-1, 1):
        return CalcResult(error="invalid radius, scan period or turn direction")
    step = turn_sign * current_speed * scan_dt / center_radius
    return normalize_angle(path_heading + step - body_heading)


def calculate_jerk_limit(acceleration_limit: float, jerk_ratio: float = 1.0) -> CalcResult[float]:
    """목표 가속도 도달 비율로 jerk 한계를 계산한다. 100%는 1초 도달이다."""
    limit = acceleration_limit * jerk_ratio
    if not isfinite(limit) or limit <= 0 or acceleration_limit <= 0 or jerk_ratio <= 0:
        return CalcResult(error="invalid acceleration limit or jerk ratio")
    return CalcResult(value=limit)


# ROS 차체 좌표 기준 (전방 x, 좌측 y): FL, FR, RL, RR 순서이다.
WHEEL_POSITIONS = ((0.680, 0.410), (0.680, -0.410), (-0.680, 0.410), (-0.680, -0.410))


def body_delta(vectors):
    """네 휠 변위를 최소제곱으로 결합해 차체 중심의 dx, dy, dyaw를 구한다."""
    dx = sum(x for x, y in vectors) / 4
    dy = sum(y for x, y in vectors) / 4
    rotation = sum(x * vy - y * vx for (x, y), (vx, vy) in zip(WHEEL_POSITIONS, vectors))
    return dx, dy, rotation / sum(x*x + y*y for x, y in WHEEL_POSITIONS)


def wheel_vector(distance, angle):
    """휠 진행 거리와 조향각을 차체 좌표의 2차원 벡터로 바꾼다."""
    return distance * cos(angle), distance * sin(angle)


def midpoint_angle(before, after):
    """-pi/pi 경계를 고려해 두 조향각 사이의 중간 각도를 구한다."""
    return before + atan2(sin(after - before), cos(after - before)) / 2


def integrate_pose(pose, delta):
    """차체 좌표 변위를 원호 운동으로 월드 좌표 자세에 적분한다."""
    x, y, yaw = pose
    dx, dy, rotation = delta
    half = rotation / 2
    scale = sin(half) / half if abs(half) > 1e-8 else 1.0
    c, s = cos(yaw + half), sin(yaw + half)
    return (x + scale*(c*dx - s*dy), y + scale*(s*dx + c*dy),
            normalize_angle(yaw + rotation).value)


def braking_distance(speed, accel, decel, jerk):
    """jerk 감소, 등감속, jerk 해제 구간을 적분해 정지 거리를 예측한다."""
    if accel < 0 and accel*accel >= 2*jerk*speed:
        t = (-accel - sqrt(accel*accel - 2*jerk*speed)) / jerk
        return speed*t + accel*t*t/2 + jerk*t**3/6
    peak = min(decel, sqrt(jerk*speed + accel*accel/2))
    if peak == 0:
        return 0.0
    ramp = max(0.0, (accel + peak) / jerk)
    distance = speed*ramp + accel*ramp*ramp/2 - jerk*ramp**3/6
    intermediate = speed + accel*ramp - jerk*ramp*ramp/2
    release_speed = peak*peak / (2*jerk)
    plateau = max(0.0, (intermediate - release_speed) / peak)
    return distance + intermediate*plateau - peak*plateau**2/2 + peak**3/(6*jerk*jerk)


def approach_speed_limit(target, remaining, decel, jerk, stop_speed):
    """잔여 거리에 따라 접근 속도를 낮춘다. 마지막 제동 판단은 profile_step에서 한다."""
    # 최고 감속도까지 jerk를 변화시키는 시간과 목표 속도의 등감속 시간을 합한 보수적 기준이다.
    horizon = decel / jerk + target / decel
    distance_speed = remaining / horizon
    # 기존 상한이 목표 속도의 50% 아래로 내려간 구간만 완화한다.
    # 50%에서 연속적으로 시작해 25% 이하에서는 기존 거리 비례 상한을 35% 높인다.
    extra = 0.35 * min(distance_speed, max(0.0, target / 2 - distance_speed))
    return min(target, max(stop_speed, distance_speed + extra))


def profile_step(speed, accel, target, remaining, limit, jerk, dt, decel=None, feedback_speed=0.0):
    """정지 거리와 jerk 제한을 만족하는 다음 속도·가속도를 계산한다."""
    decel = limit if decel is None else decel
    # 제동거리는 목표 방향의 엔코더 속도를 반영한다. 지령 생성은 이전 지령에서 이어간다.
    approach_speed = max(speed, feedback_speed)
    braking = braking_distance(approach_speed, accel, decel, jerk) + approach_speed*dt >= remaining
    error = (0.0 if braking else target) - speed
    # 다음 주기부터 jerk로 가속도를 해제할 수 있도록 속도 여유를 확보한다.
    release = sqrt((jerk*dt)**2 + 2*jerk*abs(error)) - jerk*dt
    wanted = min(limit, release) if error > 0 else -min(decel, release)
    accel += max(-jerk*dt, min(jerk*dt, wanted-accel))
    following = max(0.0, speed + accel*dt)
    if following == 0:
        return 0.0, 0.0
    return min(following, max(target, speed)), accel
