import math

import pytest

from steer_agv_controller_py import agv_math
from steer_agv_controller_py.math_types import Vector3


def test_document_front_left_converts_to_ros():
    point = Vector3(-0.410, 0.680, 0)
    assert agv_math.transform_document_to_ros(point).value == Vector3(0.680, 0.410, 0)


@pytest.mark.parametrize("speed, expected_degrees", [(0.1, 0.0201038), (-0.1, -0.0201038)])
def test_body_angle_projects_forward_or_backward(speed, expected_degrees):
    radius = agv_math.calculate_center_radius(1.25, 1.0, 0.6).value
    angle = agv_math.calculate_body_angle_error(radius, speed, 0.01, math.pi, -math.pi, 1)
    assert math.degrees(angle.value) == pytest.approx(expected_degrees, abs=1e-6)


def test_jerk_at_one_hundred_percent_matches_one_second_ramp():
    assert agv_math.calculate_jerk_limit(0.1).value == pytest.approx(0.1)
    assert agv_math.calculate_jerk_limit(float("nan")).error
    assert agv_math.calculate_body_angle_error(0.0, 0.1, 0.01, 0, 0, 1).error


@pytest.mark.parametrize("signed_radius, expected_x", [(2.85, -1.85), (-2.85, 3.85)])
def test_circle_center_in_rotated_odom(signed_radius, expected_x):
    assert agv_math.circle_center_xy((1., 2., math.pi / 2), signed_radius) == pytest.approx((expected_x, 2.))
