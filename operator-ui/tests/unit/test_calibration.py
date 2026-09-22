"""ERUT 캘리브레이션 — 3점 접촉으로 벽 반지름을 재고 오차를 낸다.

접촉점은 툴 **플랜지** 자세다(레지스터 330/340/350). 프로브가 플랜지보다
앞에 있으므로(347~349), 그대로 원을 맞추면 프로브 길이만큼 작은 반지름이
나온다 — 오프셋을 반영해야 실제 벽 반지름이 된다.
"""

import math

from smr_operator_ui.services import calibration


def _poses(points, offset=(0.0, 0.0, 0.0), rotvec=(0.0, 0.0, 0.0)):
    """접촉점 3개(mm)를 레지스터 한 벌(330~355)로 만든다."""
    values = [0] * calibration.POSE_LENGTH
    for base, (x, y, z) in zip((calibration.CENTER, calibration.RIGHT,
                                calibration.ORIGIN), points):
        values[base:base + 6] = [
            round(x * 10), round(y * 10), round(z * 10),
            round(rotvec[0] * 1000), round(rotvec[1] * 1000), round(rotvec[2] * 1000),
        ]
    for i, value in enumerate(offset):
        values[calibration.EOAT_OFFSET + i] = round(value * 10)
    return values


def _arc(radius, angles_deg, z=0.0):
    return [(radius * math.cos(math.radians(a)), radius * math.sin(math.radians(a)), z)
            for a in angles_deg]


def test_measures_the_wall_radius_from_three_points():
    poses = _poses(_arc(845.0, (90, 66, 114)))

    result = calibration.evaluate(poses, 845.0)

    assert result.ok
    # 접촉점이 0.1mm 단위라 반지름은 1mm 안팎의 분해능을 갖는다.
    assert abs(result.radius_mm - 845.0) < 1.5
    assert result.error_mm < 1.5
    assert result.level_mm == 0.0


def test_reports_the_gap_against_the_entered_radius():
    """입력값이 5 mm 크면 오차도 5 mm 쯤으로 나와야 한다."""
    poses = _poses(_arc(845.0, (90, 66, 114)))

    result = calibration.evaluate(poses, 850.0)

    assert result.ok
    assert 3.5 < result.error_mm < 6.5


def test_probe_offset_moves_the_contact_point_to_the_wall():
    """플랜지가 벽에서 120 mm 떨어져 있어도 벽 반지름이 나와야 한다."""
    wall, standoff = 845.0, 120.0
    points = []
    for angle in (90, 66, 114):
        rad = math.radians(angle)
        # 플랜지는 벽 안쪽(중심 쪽)으로 standoff 만큼 떨어져 있다.
        points.append(((wall - standoff) * math.cos(rad),
                       (wall - standoff) * math.sin(rad), 0.0))
    # 툴 z 가 벽을 향하도록 각 점에서 바깥을 보게 돌려 놓는다.
    values = [0] * calibration.POSE_LENGTH
    for base, (angle, point) in zip((calibration.CENTER, calibration.RIGHT,
                                     calibration.ORIGIN),
                                    zip((90, 66, 114), points)):
        # z 축을 (cos, sin, 0) 방향으로 돌리는 회전 벡터: z -> 그 방향
        rad = math.radians(angle)
        axis = (-math.sin(rad), math.cos(rad), 0.0)   # z 를 돌릴 축
        turn = math.pi / 2
        values[base:base + 6] = [
            round(point[0] * 10), round(point[1] * 10), round(point[2] * 10),
            round(axis[0] * turn * 1000), round(axis[1] * turn * 1000),
            round(axis[2] * turn * 1000),
        ]
    values[calibration.EOAT_OFFSET + 2] = round(standoff * 10)

    with_offset = calibration.evaluate(values, wall)
    values_no_offset = list(values)
    values_no_offset[calibration.EOAT_OFFSET + 2] = 0
    without = calibration.evaluate(values_no_offset, wall)

    assert with_offset.ok and with_offset.error_mm < 2.0
    # 오프셋을 무시하면 프로브 길이만큼 통째로 틀린 값이 나온다.
    assert without.error_mm > 100.0


def test_level_error_is_the_height_spread():
    poses = _poses([(845.0, 0.0, 10.0), (820.0, 200.0, 10.6), (820.0, -200.0, 9.8)])

    result = calibration.evaluate(poses, 845.0)

    assert round(result.level_mm, 1) == 0.8


def test_points_in_a_line_have_no_radius():
    poses = _poses([(800.0, 0.0, 0.0), (800.0, 100.0, 0.0), (800.0, -100.0, 0.0)])

    result = calibration.evaluate(poses, 845.0)

    assert not result.ok and "한 줄" in result.reason


def test_missing_or_incomplete_measurements_are_refused():
    assert not calibration.evaluate([], 845.0).ok
    assert not calibration.evaluate([0] * 26, 845.0).ok
    poses = _poses(_arc(845.0, (90, 66, 114)))
    # 305 = 접촉 수. 3번을 다 못 잡았으면 좌표계를 세우면 안 된다.
    result = calibration.evaluate(poses, 845.0, [0, 0, 0, 0, 3, 2])
    assert not result.ok and "접촉" in result.reason


def test_without_an_entered_radius_there_is_no_error_number():
    poses = _poses(_arc(845.0, (90, 66, 114)))

    result = calibration.evaluate(poses, 0.0)

    assert not result.ok
    assert result.radius_mm > 800.0      # 잰 값은 그대로 알려 준다
