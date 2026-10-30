"""if-0.11 — 이동 방향 알림(evt/motion)의 남은 거리 · 홀더 채널 수 견주기 (app.py)."""

from smr_operator_ui.app import OperatorWindow


def _window(qtbot):
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    return window


def test_scan_segment_goes_to_erut_with_the_remaining_distance(qtbot) -> None:
    window = _window(qtbot)
    sent: list[tuple] = []
    window.erut_session.set_scan_segment = lambda seg, remain=None: sent.append((seg, remain))
    window._robot_work_area = [600, 300, 300, 0, 6500, 0, 0, 0, 5]   # 호 길이 600 mm
    window._last_scan_state = [6, 1, 5, 0, 1, 0, 286, 0, 20, 0]      # 줄 간격 28.6 mm

    window._scan_arc = [0, 0, 1, 0]          # 원점에 있다 → 전진은 호 전체
    window._on_scan_segment(1)
    window._on_scan_segment(2)               # 줄 바꿈 → 줄 간격
    window._scan_arc = [6000, 600, 1, 0]     # 반대쪽 끝(600.0 mm) → 후진은 호 전체
    window._on_scan_segment(3)
    window._scan_arc = [2500, 850, 1, 0]     # 재개: 후진하다 250 mm 지점에서 멈췄다
    window._on_scan_segment(3)
    window._on_scan_segment(0)

    assert sent == [(1, 600.0), (2, 28.6), (3, 600.0), (3, 250.0), (0, None)]
    window.close()


def test_holder_channel_count_is_compared_with_the_mounted_eoat(qtbot) -> None:
    """채널 수로 EOAT 를 바꾸지 않는다 — 다르면 작업자와 ERUT 에 알린다."""
    window = _window(qtbot)
    notes: list[tuple] = []
    window.erut_session.notify = lambda code, name, text, **extra: notes.append((code, extra))
    window._work_area_extra["eoat_type"] = 5.0

    window._check_holder_channels(5)
    assert notes == []
    window._check_holder_channels(8)
    assert notes == [("M2003", {"channel_count": 8, "eoat_type": 5})]
    assert window._work_area_extra["eoat_type"] == 5.0
    window.close()
