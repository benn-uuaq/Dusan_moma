"""스캔 중 현재 위치 점이 ㄹ자 경로 선을 따라간다 (2026-11-09).

경로 폭은 호 길이다. 로봇의 원점 기준 Y 변위(280~285 의 Y)는 호가 아니라 현 쪽 값이라
호 위 거리에 비례하지 않는다 — 그걸 폭 ÷ 현 으로 늘려 그리면 줄 가운데서 점이 선을 앞서거나
처졌다. ㄹ자 스캔 중(290 = 6)에는 로봇이 내는 호 위 거리(286)로 가로를 그린다.
"""

import math

import pytest

from smr_operator_ui.app import OperatorWindow
from smr_operator_ui.components.rect_work_view import RectWorkView, probe_chord_mm

WIDTH, HEIGHT, RADIUS = 721.0, 500.0, 834.6


def _view(qtbot) -> RectWorkView:
    view = RectWorkView()
    qtbot.addWidget(view)
    view.set_work_area(WIDTH, HEIGHT, 30.0, 0.0, radius_mm=RADIUS)
    return view


def test_arc_position_lands_on_the_path_unchanged(qtbot) -> None:
    view = _view(qtbot)
    view.set_position(WIDTH / 2, 0.0, along_arc=True)
    assert view.position_on_path_mm() == pytest.approx((WIDTH / 2, 0.0))
    view.set_position(WIDTH, 120.0, along_arc=True)
    assert view.position_on_path_mm() == pytest.approx((WIDTH, 120.0))


def test_chord_position_is_still_stretched_to_the_path_width(qtbot) -> None:
    view = _view(qtbot)
    chord = probe_chord_mm(WIDTH, RADIUS)
    view.set_position(chord, 0.0)
    assert view.position_on_path_mm() == pytest.approx((WIDTH, 0.0))


def _window(qtbot) -> OperatorWindow:
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    window.main_screen.rect_view.set_work_area(WIDTH, HEIGHT, 30.0, 0.0, radius_mm=RADIUS)
    window._position_stale = False
    return window


def _arc(window, arc_mm: float) -> None:
    window._on_scan_arc([int(round(arc_mm * 10)), 0, 0, 0, 0, 0, 0, 0])


def test_dot_follows_the_arc_position_while_scanning(qtbot) -> None:
    """줄 가운데(호 360.5 mm)에서 로봇 Y 변위는 현 쪽 값이지만 점은 경로 가운데에 선다."""
    window = _window(qtbot)
    window._track_erut_contact([6, 1, 0, 0])
    _arc(window, WIDTH / 2)
    half_angle = (WIDTH / 2) / RADIUS
    rel_y = RADIUS * math.sin(half_angle)                # 호가 아닌 값 — 357 mm 남짓
    window._show_tcp_pose_zero([0.0, rel_y, 60.0, 0.0, 0.0, 0.0])

    h, v = window.main_screen.rect_view.position_on_path_mm()
    assert h == pytest.approx(WIDTH / 2, abs=0.1)
    assert v == pytest.approx(60.0)
    window.close()


def test_outside_the_scan_the_dot_uses_the_robot_coordinates(qtbot) -> None:
    """원점 복귀 · 후퇴 중에는 로봇이 280~285 를 0 으로 내는데 286 은 남아 있다 —
    그때 호 위 거리를 쓰면 점이 세로 0 에 가로만 떠 있게 된다."""
    window = _window(qtbot)
    window._track_erut_contact([3, 0, 0, 0])
    _arc(window, 400.0)
    window._show_tcp_pose_zero([0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    assert window.main_screen.rect_view.position_on_path_mm() == pytest.approx((0.0, 0.0))
    window.close()


def test_scanning_without_an_arc_falls_back_to_the_chord(qtbot) -> None:
    window = _window(qtbot)
    window._track_erut_contact([6, 0, 0, 0])
    _arc(window, 0.0)
    chord = probe_chord_mm(WIDTH, RADIUS)
    window._show_tcp_pose_zero([0.0, chord, 0.0, 0.0, 0.0, 0.0])
    h, _v = window.main_screen.rect_view.position_on_path_mm()
    assert h == pytest.approx(WIDTH)
    window.close()
