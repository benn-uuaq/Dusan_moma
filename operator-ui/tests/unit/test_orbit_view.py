"""차량 구간 고리 — 구간 번호는 차량 구간에만 「현재/전체」로(2026-11-09).

ERUT 가 원주를 아주 잘게 나누면(129 구간 등) 구간마다 적던 번호가 겹쳐 읽을 수 없었다.
"""

import math

from smr_operator_ui.components.orbit_view import OrbitView


def test_only_the_current_section_number_is_shown(qtbot):
    view = OrbitView()
    qtbot.addWidget(view)
    view.set_sections(math.pi * 4000, 113, 98, 4, frozenset(range(4)))
    assert view.section_count() == 129
    assert view.section_position() == (5, 129)
    assert view.section_label() == "5/129"
    view.resize(420, 440)
    assert not view.grab().isNull()


def test_without_erut_sections_the_column_count_is_used(qtbot):
    """구간 배치가 없으면 열 수(기본 12)로 — 역시 번호 하나만."""
    view = OrbitView()
    qtbot.addWidget(view)
    assert view.section_label() == "1/12"
