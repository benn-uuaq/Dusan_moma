"""일시정지 · 정지는 구간 이동 중인 차량 · 오르내리는 리프트도 바로 세운다 (2026-11-12).

예전에는 가던 곳까지 가고 도착만 미뤘다. 재개하면 같은 목표로 다시 보낸다.
"""

from smr_operator_ui.app import OperatorWindow
from smr_operator_ui.services import SequencerState


def _window(qtbot):
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    window.ros_status.call_command = lambda name: True
    window.ros_status.send_value = lambda *a: True
    window.ros_status.send_pose = lambda *a: True
    window._start_robot_scan = lambda: None
    return window


def _moving(window):
    return [m for m in (window.amr, window.lift, window.outrigger) if m.moving]


def test_pause_stops_the_moving_vehicle_or_lift_and_resume_sends_it_again(qtbot) -> None:
    window = _window(qtbot)
    window._start_inspection()
    qtbot.waitUntil(lambda: bool(_moving(window)), timeout=5000)
    motion = _moving(window)[0]
    target = motion.target

    window._toggle_pause()
    assert window.sequencer.state is SequencerState.PAUSED
    assert _moving(window) == [], "일시정지인데 차량 · 리프트가 계속 간다"
    assert window._paused_motions and window._paused_motions[0] == (motion, target)

    window._toggle_pause()                         # 재개 — 같은 목표로 다시 간다
    assert window.sequencer.state is not SequencerState.PAUSED
    assert motion.moving and motion.target == target
    window.close()


def test_stop_stops_the_moves_and_forgets_them(qtbot) -> None:
    window = _window(qtbot)
    window._start_inspection()
    qtbot.waitUntil(lambda: bool(_moving(window)), timeout=5000)
    window._toggle_pause()
    window.main_screen.stop_button.click()
    assert _moving(window) == [] and window._paused_motions == []
    window.close()


def test_deferred_moves_wait_for_resume(qtbot) -> None:
    """일시정지 중에는 미뤄 둔 이동(로봇이 홈에 선 뒤 하려던 것)도 하지 않는다."""
    window = _window(qtbot)
    window._start_inspection()
    qtbot.waitUntil(lambda: bool(_moving(window)), timeout=5000)
    window._toggle_pause()
    ran: list[int] = []
    window._defer_until_robot_idle("시험 이동", lambda: ran.append(1))
    window._run_pending_motions()
    assert ran == []
    window._toggle_pause()
    qtbot.waitUntil(lambda: ran == [1], timeout=3000)
    window.close()


def test_erut_pause_also_stops_the_moves(qtbot) -> None:
    window = _window(qtbot)
    window._start_inspection()
    qtbot.waitUntil(lambda: bool(_moving(window)), timeout=5000)
    window.erut_session.pause_requested.emit()      # ERUT pause 와 같은 길(sequencer.pause)
    assert _moving(window) == []
    window.close()
