"""ERUT 에 넘긴 스캔 경로 계산(tools/erut_scan_path_plan.py)이 RCS 화면 · 로봇과 같은지.

ERUT 가 이 파일을 가져다 스캔 계획을 그린다 — RCS 쪽 규칙을 바꾸면 여기서 걸려
넘긴 파일도 같이 고치게 한다.
"""

import importlib.util
import pathlib

import pytest

from smr_operator_ui.components.rect_work_view import (
    ideal_path, max_safe_arc_mm, plan_dimensions,
)

_PATH = pathlib.Path(__file__).resolve().parents[3] / "tools" / "erut_scan_path_plan.py"
_spec = importlib.util.spec_from_file_location("erut_scan_path_plan", _PATH)
plan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plan)


@pytest.mark.parametrize("width,height,scan_h", [
    (140, 140, 30), (721, 500, 30), (721, 1200, 167.5), (300, 200, 30),
    (600, 800, 167.5), (500, 30, 30), (500, 0, 30),
])
def test_rows_and_path_match_the_rcs_screen(width, height, scan_h) -> None:
    mine = plan.plan_rows(height, scan_h)
    rcs = plan_dimensions(width, height, scan_h, 0.0)
    for key in ("rows", "pitch", "overlap", "top", "covered"):
        assert mine[key] == pytest.approx(rcs[key]), key
    rcs_points, _p = ideal_path(width, height, scan_h, 0.0)
    assert plan.scan_path(width, height, scan_h) == pytest.approx(rcs_points)


def test_screen_example_140_by_140() -> None:
    r = plan.plan_rows(140, 30)
    assert (r["rows"], r["pitch"], round(r["overlap"], 1)) == (6, 28.0, 2.0)


@pytest.mark.parametrize("radius", [845.0, 855.0, 1200.0, 300.0])
def test_max_width_matches_the_robot_limit(radius) -> None:
    assert plan.max_width_mm(radius) == max_safe_arc_mm(radius, 0.0)
