"""ERUT 구간 마감 순서 (2026-11-24).

  · 구간을 다 돌면 로봇이 홈에 들어간 뒤 ERUT 에 완료(evt/complete action=start)를 낸다 — 예전에는 로봇이 구간 끝
    플래그(290 = 5)를 쓰자마자 냈다(로봇은 그 뒤에 홈으로 간다).
  · 마지막 높이 구간(구역 위 끝이 모재 높이에 닿는다)이면 다음 일이 리프트 하강이라 로봇 홈 → 리프트 0 mm 까지
    마친 뒤 낸다. 그동안 ERUT 에는 running.
  · ERUT 정지(abort) — 로봇 홈 뒤 리프트 0 mm. 리프트가 0 에 닿은 뒤에야 idle.
"""

from smr_operator_ui.app import OperatorWindow
from smr_operator_ui.services.job_sequencer import GridPlan, SequencerState


def _window(qtbot, zone_top_mm, target_height_mm):
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    events: list[tuple] = []
    window.erut.publish_event = lambda name, req, action, status_code=200, error=None, **kw: (
        events.append((name, action)) or True)
    window.erut.publish_res = lambda *a, **k: True
    window._send_home = lambda: None
    window.main_screen.orbit_view.set_target_dimensions(1.69, target_height_mm / 1000.0)
    session = window.erut_session
    session._start_req_id, session._job_id = "s-1", "jb-1"
    session._plan = GridPlan(column_count=1, row_count=1, cell_width=400.0, cell_height=600.0,
                             origin_y=zone_top_mm - 600.0)
    window.sequencer._state = SequencerState.DONE
    window._on_robot_task_state(3)                     # 태스크 멈춤
    window._on_robot_at_home(True)
    window._on_robot_at_home(False)                    # 구간 끝 — 로봇이 아직 홈으로 가는 중
    return window, events


def test_zone_complete_waits_for_the_robot_to_be_home(qtbot):
    window, events = _window(qtbot, zone_top_mm=1180, target_height_mm=6000)
    window.lift.reset(580.0)
    window._on_zone_complete()
    assert events == [] and window.erut_session.activity_state() == "running", "홈 전에는 완료를 내지 않는다"
    window._on_robot_at_home(True)
    assert ("complete", "start") in events
    assert window.lift.position == 580.0 and not window.lift.moving, "마지막 높이가 아니면 리프트는 그대로"
    window.close()


def test_last_height_zone_lowers_the_lift_before_complete(qtbot):
    window, events = _window(qtbot, zone_top_mm=6000, target_height_mm=6000)
    window.lift.reset(1200.0)
    window._on_zone_complete()
    window._on_robot_at_home(True)
    assert window.lift.moving and window.lift.target == 0.0, "로봇 홈 다음 리프트 0"
    assert events == [] and window.erut_session.activity_state() == "running"
    qtbot.waitUntil(lambda: ("complete", "start") in events, timeout=15000)
    assert window.lift.position == 0.0
    window.close()


def test_erut_abort_sends_the_lift_to_zero_after_the_robot_is_home(qtbot):
    window, _events = _window(qtbot, zone_top_mm=1180, target_height_mm=6000)
    window.lift.reset(900.0)
    window.erut_session.handle_request("abort", {"req_id": "ab-1"})
    assert not window.lift.moving, "로봇이 홈에 들어가기 전에는 리프트를 움직이지 않는다"
    assert window.erut_session.activity_state() != "idle"
    window._on_robot_at_home(True)
    assert window.lift.moving and window.lift.target == 0.0
    assert window.erut_session.activity_state() != "idle", "리프트가 0 에 닿기 전에는 idle 이 아니다"
    qtbot.waitUntil(lambda: not window.lift.moving, timeout=15000)
    window._robot_still = lambda: True
    qtbot.waitUntil(lambda: window.erut_session.activity_state() == "idle", timeout=3000)
    window.close()
