"""작업 영역 그림의 순수 계산 함수(치수·이상적 경로)를 검증한다."""

import pytest

from smr_operator_ui.components.rect_work_view import (
    MAX_PROBE_CHORD_MM, ideal_path, max_safe_arc_mm, plan_dimensions,
    probe_chord_mm,
)


def test_plan_dimensions_without_eoat_uses_full_width() -> None:
    """EOAT 폭을 안 주면(0) 예전처럼 경로가 격자 전체 폭을 오간다."""
    plan = plan_dimensions(1110.0, 500.0, 257.0, 20.0)
    assert plan["arc_run"] == 1110.0
    assert plan["arc_margin"] == 0.0


def test_path_reaches_both_ends_of_the_grid() -> None:
    """좌우 끝도 **가운데 프로브 중심** 기준이라 TCP 가 끝까지 간다.

    예전에는 프로브가 EOAT 폭만큼 퍼져 있다고 보고 양 끝에서 절반씩
    물러났지만, 이제 여백이 없다.
    """
    plan = plan_dimensions(1110.0, 500.0, 257.0, 20.0, eoat_w_mm=257.0)
    assert plan["arc_run"] == 1110.0
    assert plan["arc_margin"] == 0.0


def test_eoat_width_no_longer_narrows_the_path() -> None:
    """EOAT 가로 값을 뭘 주든 이동 폭이 달라지지 않는다."""
    wide = plan_dimensions(200.0, 500.0, 257.0, 20.0, eoat_w_mm=400.0)
    none = plan_dimensions(200.0, 500.0, 257.0, 20.0, eoat_w_mm=0.0)
    assert wide["arc_run"] == none["arc_run"] == 200.0
    assert wide["arc_margin"] == 0.0


def test_ideal_path_stays_within_inset_bounds() -> None:
    """경로의 모든 가로 좌표가 [margin, width - margin] 안에 있어야 한다

    (격자 가장자리에 그대로 닿으면 안 된다).
    """
    pts, plan = ideal_path(1110.0, 500.0, 257.0, 20.0, eoat_w_mm=257.0)
    margin = plan["arc_margin"]
    for h_mm, _v_mm in pts:
        assert margin - 1e-6 <= h_mm <= plan["width"] - margin + 1e-6
    # 가로 이동 폭 자체는 여전히 arc_run과 같다.
    hs = [h for h, _ in pts]
    assert max(hs) - min(hs) == plan["arc_run"]


def test_path_runs_from_the_bottom_face_to_the_top_face() -> None:
    """경로는 **가운데 프로브**가 지나는 높이다.

    십자 프로브는 맨 아랫줄에서 ㅗ 자세로 밑면(0)을, 맨 윗줄에서 ㅜ 자세로
    윗면(height)을 직접 훑어야 한다. 그래서 줄은 0..height 를 균등 분할한
    자리에 놓인다.
    """
    pts, plan = ideal_path(1110.0, 500.0, 257.0, 20.0, eoat_w_mm=257.0)

    heights = sorted({v for _h, v in pts})
    assert heights[0] == 0.0, "맨 아랫줄이 밑면을 훑지 않는다"
    assert heights[-1] == plan["height"], "맨 윗줄이 윗면을 훑지 않는다"
    assert heights == [i * plan["pitch"] for i in range(plan["rows"])]


def test_pitch_is_derived_from_the_faces_not_from_the_given_overlap() -> None:
    """겹침은 입력이 아니라 결과다.

    줄 위치가 밑면·윗면에 묶여 있으므로 피치는 height/칸수 로 정해지고,
    겹침은 scan_h - pitch 로 따라 나온다. 밖에서 받은 겹침 값은 "최소
    이만큼"이라는 하한으로만 쓴다.
    """
    plan = plan_dimensions(966.0, 500.0, 244.5, 0.0, eoat_w_mm=244.5)
    assert plan["rows"] == 4
    assert plan["pitch"] == pytest.approx(500.0 / 3)
    assert plan["overlap"] == pytest.approx(244.5 - 500.0 / 3)
    # 겹침을 0 으로 줘도 피치는 스캐너 높이보다 작다 — 같으면 밴드가
    # 맞닿기만 해 경계에 틈이 남는다.
    assert plan["pitch"] < plan["scan_h"]


def test_the_given_overlap_no_longer_changes_the_pitch() -> None:
    """겹침 입력은 격자 **안** 피치를 건드리지 않는다.

    바깥에서 받는 겹침은 격자끼리의 겹침 허용도라, 리프트가 다음 격자로
    올라갈 때 얼마를 덜 올라가는지를 정한다(JobSequencer.lift_pitch).
    격자 안 줄 간격은 프로브 커버 하나로만 정해진다 — 로봇도 같은 규칙이다.
    """
    loose = plan_dimensions(966.0, 500.0, 244.5, 0.0, eoat_w_mm=244.5)
    tight = plan_dimensions(966.0, 500.0, 244.5, 150.0, eoat_w_mm=244.5)
    assert tight["rows"] == loose["rows"]
    assert tight["pitch"] == loose["pitch"]
    # 어느 쪽이든 밑면과 윗면은 그대로 지난다.
    for plan in (loose, tight):
        assert (plan["rows"] - 1) * plan["pitch"] == pytest.approx(plan["height"])


def test_path_horizontal_span_is_unchanged_by_the_vertical_lift() -> None:
    """세로를 띄운다고 가로(TCP 실제 이동 폭)가 달라지면 안 된다."""
    pts, plan = ideal_path(1110.0, 500.0, 257.0, 20.0, eoat_w_mm=257.0)
    hs = [h for h, _v in pts]
    assert min(hs) == plan["arc_margin"]
    assert max(hs) - min(hs) == plan["arc_run"]


def test_origin_sits_at_the_work_area_corner_not_the_path_start() -> None:
    """원점은 로봇 좌표가 아니라 **작업 영역 자체의 기준점**이다.

    좌우 끝도 상승도 전부 가운데 프로브 중심 기준이 되면서, ㄹ자 시작점이
    곧 원점(그림 좌표 0,0 = 화면 오른쪽 아래 구석)과 같은 자리가 됐다.
    스캔이 아닐 때 로봇이 보내는 (0,0) 도 여기라 따로 세워 둘 필요가 없다.
    """
    pts, plan = ideal_path(1110.0, 500.0, 257.0, 20.0, eoat_w_mm=257.0)

    assert pts[0] == (0.0, 0.0)
    assert plan["arc_margin"] == 0.0


def test_max_safe_arc_matches_the_configured_limit() -> None:
    """안전 한계 계산이 로봇 config 의 app_arc(721)와 같아야 한다.

    현 = 2R·sin(arc_run / 2R) 가 700mm 를 넘지 않는 최대 정수 호 길이다.
    프로브는 벽 바깥면(반지름 + 두께)을 타므로 그 반지름으로 잰다.
    """
    assert max_safe_arc_mm(834.6, 10.0) == 721.0
    # 경계 확인 — 721 은 안전하고 722 는 넘는다.
    assert probe_chord_mm(721, 834.6, 10.0) < MAX_PROBE_CHORD_MM
    assert probe_chord_mm(722, 834.6, 10.0) > MAX_PROBE_CHORD_MM


def test_no_limit_when_radius_is_unknown() -> None:
    """반지름을 모르면(평면으로 보는 경우) 한계를 두지 않는다."""
    assert max_safe_arc_mm(0.0, 0.0, 257.0) == 0.0


def test_rise_segment_follows_the_path_vertically() -> None:
    """줄을 바꾸는 상승 구간에서도 경로를 따라가야 한다.

    로봇은 이때 Y 를 그대로 두고 Z 만 올리므로, 화면에서는 같은 가로
    자리에서 아래 줄 높이 -> 위 줄 높이로 곧게 올라간다.
    """
    pts, plan = ideal_path(978.0, 500.0, 257.0, 0.0, eoat_w_mm=257.0)
    end_h = plan["arc_margin"] + plan["arc_run"]

    # 1줄 끝 -> 상승 중 -> 2줄 시작. 가로는 그대로, 세로만 오른다.
    rise = [(end_h, 0.0), (end_h, plan["pitch"] / 2), (end_h, plan["pitch"])]
    assert all(h == end_h for h, _v in rise)
    # 그 두 높이는 실제 경로의 줄 높이와 같아야 한다.
    row_heights = sorted({v for _h, v in pts})
    assert row_heights[0] == 0.0
    assert row_heights[1] == pytest.approx(plan["pitch"])


def test_max_safe_arc_uses_outer_surface_radius() -> None:
    """한계는 벽 **바깥면**(반지름 + 두께) 기준으로 잰다.

    도면 R834.6 에 두께 10 이면 훑는 면은 R844.6 이고, 로봇이 좌우로
    실제 이동하는 현이 700mm 를 넘지 않는 최대 작업 호가 721mm 다.
    """
    assert max_safe_arc_mm(834.6, 10.0) == 721.0
    assert probe_chord_mm(721, 834.6, 10.0) < MAX_PROBE_CHORD_MM
    assert probe_chord_mm(722, 834.6, 10.0) > MAX_PROBE_CHORD_MM


def test_plan_reports_the_chord_the_robot_will_publish() -> None:
    """그림은 격자 폭 전체로 그리고, 로봇 좌표(현)는 늘려서 얹는다.

    로봇이 내보내는 가로 좌표는 cur-zero 의 Y 변위(= 두 끝점 사이 직선거리
    = 현)라 호 길이보다 짧다(721 -> 699.3). 그림을 현으로 좁혀 그리면
    격자 끝이 비어 보이므로, 그림은 폭 전체로 그리고 마커만 width/chord
    로 펴서 얹는다 — TPAC 이 호를 평면화해 보는 것과 같은 그림이다.
    """
    plan = plan_dimensions(721.0, 500.0, 30.0, 0.0,
                           radius_mm=834.6, thickness_mm=10.0)

    assert plan["arc_run"] == 721.0            # 로봇이 실제로 훑는 호 길이
    assert plan["draw_run"] == 721.0           # 그림은 격자 폭 전체
    assert plan["chord"] == probe_chord_mm(721.0, 834.6, 10.0)
    assert plan["chord"] < plan["arc_run"]


def test_flat_wall_draws_the_arc_length_as_is() -> None:
    """반지름을 모르면 평면이라 호 = 현이다(마커를 늘릴 것이 없다)."""
    plan = plan_dimensions(978.0, 500.0, 30.0, 0.0)
    assert plan["chord"] == plan["arc_run"] == plan["draw_run"]


def test_path_starts_at_the_origin_corner_and_runs_outward() -> None:
    """반시계 방향으로 바뀐 뒤에도 경로는 원점(0)에서 시작한다.

    화면에서 원점이 어느 구석인지는 to_px() 가 정한다(지금은 왼쪽 아래).
    경로 값 자체는 늘 원점에서 잰 거리라 0 에서 폭까지 오간다.
    """
    pts, plan = ideal_path(721.0, 500.0, 30.0, 0.0,
                           radius_mm=834.6, thickness_mm=10.0)
    hs = [h for h, _v in pts]
    assert min(hs) == 0.0
    assert max(hs) == plan["width"]
    assert pts[0] == (0.0, 0.0), "원점에서 시작하지 않는다"
