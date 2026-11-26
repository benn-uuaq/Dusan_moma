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


def test_erut_holder_sets_the_mounted_holder(qtbot) -> None:
    """ERUT 가 보낸 5 · 8 은 「장착 홀더」에 바로 들어가 로봇에 간다(2026-11-26)."""
    window = _window(qtbot)
    notes: list[tuple] = []
    window.erut_session.notify = lambda code, name, text, **extra: notes.append((code, extra))
    window._work_area_extra["eoat_type"] = 0.0

    window._check_holder_channels(8)
    assert window._work_area_extra["eoat_type"] == 8.0
    assert window.screens["cobot"].eoat_type() == 8
    assert notes == []
    window._check_holder_channels(8)                     # 같으면 아무것도 안 한다
    assert notes == []
    window.close()


def test_unknown_channel_count_or_running_job_only_warns(qtbot) -> None:
    """5 · 8 이 아니거나(맞는 홀더가 없다) 로봇 태스크가 돌면 바꾸지 않고 알린다(M2003)."""
    window = _window(qtbot)
    notes: list[tuple] = []
    window.erut_session.notify = lambda code, name, text, **extra: notes.append((code, extra))
    window._work_area_extra["eoat_type"] = 5.0

    window._check_holder_channels(17)
    assert notes == [("M2003", {"channel_count": 17, "eoat_type": 5})]
    window._on_robot_task_state(1)
    window._check_holder_channels(8)
    assert notes[-1] == ("M2003", {"channel_count": 8, "eoat_type": 5})
    assert window._work_area_extra["eoat_type"] == 5.0
    window.close()
