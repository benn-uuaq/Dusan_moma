"""차량 설정 화면 · 원주 거리 환산 · 리프트 대신 팔 올리기 (2026-11-09).

차량은 모재에서 「모재 거리 유지」만큼 떨어져 차량 폭의 가운데를 기준으로 돈다 —
모재 표면에서 s 만큼 옮기려면 차량은 s × (R + 거리 + 폭/2) ÷ R 만큼 달린다.
구역이 「로봇 세로 작업 범위」(1600) 안이면 리프트를 올리지 않고 팔을 든다(358).
"""

import math

import pytest
from PyQt6.QtWidgets import QPushButton

from smr_operator_ui.app import OperatorWindow


def _window(qtbot):
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    window._work_area_extra = {"eoat_w_mm": 0.0, "eoat_h_mm": 0.0, "eoat_type": 0.0}
    window._task_version = "dusan_v6"
    return window


def _set(window, **values):
    vehicle = window.screens["vehicle"]
    fields = {"width": vehicle.WIDTH_FIELD, "standoff": vehicle.STANDOFF_FIELD,
              "lift_max": vehicle.LIFT_MAX_FIELD, "reach": vehicle.REACH_FIELD,
              "base": vehicle.BASE_FIELD, "speed": vehicle.SPEED_FIELD}
    for key, value in values.items():
        vehicle.field(fields[key]).setValue(value)
    return vehicle


def _tap(window):
    events: list[tuple] = []
    window.erut.publish_event = lambda name, req, action, status_code=200, error=None, **kw: (
        events.append((name, action, {"status_code": status_code,
                                      **({"error": error} if error else {}), **kw})) or True)
    responses: list[dict] = []
    window.erut.publish_res = lambda req_id, action, status_code=200, error=None, **kw: (
        responses.append({"action": action, "status_code": status_code, "error": error}) or True)
    window._push_work_area_to_robot = lambda *values: None
    return events, responses


# ---- 화면 ----------------------------------------------------------------------
def test_vehicle_settings_is_in_the_settings_menu(qtbot) -> None:
    window = _window(qtbot)
    menu = window.screens["settings"]
    texts = [b.text() for b in menu.findChildren(QPushButton)]
    assert any("차량 설정" in t for t in texts)
    window.navigate("vehicle")
    assert window.stack.currentWidget() is window.screens["vehicle"]
    window.close()


def test_defaults_keep_todays_behaviour_and_lift_limit_is_3500(qtbot) -> None:
    window = _window(qtbot)
    vehicle = window.screens["vehicle"]
    assert vehicle.lift_max_mm() == 3500
    assert vehicle.reach_mm() == 1600                  # 로봇 상승 한계 — 베이스 위로
    assert vehicle.base_height_mm() == 0
    assert vehicle.arm_reach_mm() == 1600 and vehicle.top_limit_mm() == 5100
    assert vehicle.speed_mm_s() == 300
    assert (vehicle.width_mm(), vehicle.standoff_mm()) == (0, 0)
    assert vehicle.travel_scale(1300) == 1.0          # 폭 · 거리 0 이면 예전처럼 1 배
    window.close()


def test_surface_move_becomes_a_longer_vehicle_move(qtbot) -> None:
    """지름 1300, 거리 유지 500, 차량 폭 1000 → 차량 중심 1000 mm, 배율 (650 + 1000) ÷ 650."""
    window = _window(qtbot)
    window.main_screen.orbit_view.set_target_dimensions(1.3, 6.0)
    vehicle = _set(window, width=1000, standoff=500)
    vehicle.set_target_diameter(1300)

    assert vehicle.center_distance_mm() == 1000
    scale = (650 + 1000) / 650
    assert vehicle.travel_scale() == pytest.approx(scale)
    assert window._vehicle_x(200) == pytest.approx(200 * scale)        # 표면 200 → 차량 508
    assert f"× {scale:.3f}" in vehicle.scale_label.text()
    window.close()


def test_saving_applies_speed_and_the_manual_lift_limit(qtbot) -> None:
    window = _window(qtbot)
    vehicle = _set(window, speed=450, lift_max=3200)
    vehicle.save_requested.emit("vehicle", vehicle.values())

    assert window._vehicle_amr.speed_mps == pytest.approx(0.45)
    assert window.screens["manual"].lift_target.maximum() == 3200
    window.close()


# ---- 리프트 대신 팔 올리기 --------------------------------------------------------
@pytest.mark.parametrize("lift_now, bottom, height, expected", (
    (0, 800, 200, (0, 800)),          # 1.6 m 안 — 리프트 그대로, 팔 800
    (0, 0, 800, (0, 0)),
    (0, 1000, 600, (0, 1000)),        # 위 끝 1600 — 딱 닿는다
    (0, 1500, 500, (1500, 0)),        # 위 끝 2000 > 1600 — 리프트를 구역 아래 끝으로
    (1500, 1600, 800, (1500, 100)),   # 지금 리프트에서 닿는다 — 그대로, 팔 100
    (1500, 0, 800, (0, 0)),           # 리프트가 구역보다 높다 — 내린다
    (0, 3600, 600, (3500, 100)),      # 리프트 한계 3500 까지, 나머지는 팔
))
def test_lift_stays_when_the_arm_reaches(qtbot, lift_now, bottom, height, expected) -> None:
    window = _window(qtbot)
    window.lift.reset(float(lift_now))
    lift, offset, reason = window._plan_lift(bottom, height)
    assert reason == ""
    assert (lift, offset) == pytest.approx(expected)
    window.close()


def test_zone_beyond_the_lift_limit_and_the_arm_is_not_reachable(qtbot) -> None:
    window = _window(qtbot)
    _lift, _offset, reason = window._plan_lift(4600, 600)           # 위 끝 5200 > 3500 + 1600
    assert "5,200" in reason and "3,500" in reason
    assert "리프트 한계" in window._zone_reach_blocker(4600, 600)
    assert window._zone_reach_blocker(3600, 600) == ""
    window.close()


def test_older_tasks_always_use_the_lift(qtbot) -> None:
    """팔을 못 드는 태스크(v5)면 예전처럼 리프트를 구역 아래 끝으로 — 리프트 한계까지만."""
    window = _window(qtbot)
    window._task_version = "dusan_v5"
    window.lift.reset(0.0)
    assert window._plan_lift(800, 200) == (800, 0.0, "")
    assert window._plan_lift(3600, 200)[2]
    assert "리프트 한계" in window._zone_reach_blocker(3600, 200)
    window.close()


def test_erut_zone_out_of_reach_is_refused_with_the_reason(qtbot) -> None:
    window = _window(qtbot)
    _events, responses = _tap(window)
    window.erut_session._calibrated = True
    window.main_screen.orbit_view.set_target_dimensions(1.69, 6.0)

    window.erut_session.handle_request("start", {
        "req_id": "s-high", "job_id": "jb-high", "surface": "outer",
        "area": {"start": {"x": 0, "y": 4600}, "end": {"x": 600, "y": 5200}},
        "scan": {"pitch_left": 200, "pitch_right": 200, "speed": 100, "overlap": 20}})

    refused = responses[-1]
    assert refused["status_code"] == 400 and refused["error"]["code"] == "E6003"
    assert "리프트 한계" in refused["error"]["detail"]
    window.close()


def test_marking_clears_the_arm_raise(qtbot) -> None:
    window = _window(qtbot)
    sent: list[int] = []
    window._send_scan_z_offset = lambda mm: sent.append(int(mm))
    window.ros_status.call_command = lambda name: True
    window._schedule_play = lambda: None
    window._start_mark_task()
    assert sent == [0]
    window.close()


def test_calibration_lap_drives_the_vehicle_path_and_reports_the_surface(qtbot) -> None:
    """한 바퀴 = 둘레 × 배율 만큼 달리고, ERUT 에는 표면 둘레(π × 지름)로 낸다."""
    window = _window(qtbot)
    events, _responses = _tap(window)
    window._start_robot_scan = lambda: None
    window.outrigger.reset(0)
    _set(window, width=1000, standoff=500)
    targets: list[float] = []
    original = window.amr.move_to

    def record(target, unit="", label=None):
        targets.append(float(target))
        return original(target, unit, label=label) if label is not None else original(target, unit)
    window.amr.move_to = record

    window.erut_session.handle_request("calibrate", {"req_id": "cal-v", "diameter": 1300,
                                                     "height": 6000})
    qtbot.waitUntil(lambda: bool(events), timeout=10000)

    scale = (650 + 1000) / 650
    assert targets[-1] == pytest.approx(math.pi * 1300 * scale, abs=1)
    name, action, payload = events[-1]
    assert (name, action) == ("complete", "calibrate")
    assert abs(payload["total_length_mm"] - math.pi * 1300) <= 1
    window.close()


def test_robot_base_height_adds_to_what_the_arm_reaches(qtbot) -> None:
    """높이 0 = 모재 아래 끝 = 리프트 최하단의 시작 위치. 베이스가 그보다 507 mm 위면 팔은
    시작 위치에서 507 + 1600 = 2107 mm 까지, 구역 위 끝은 3500 + 507 + 1600 = 5607 mm 까지."""
    window = _window(qtbot)
    vehicle = _set(window, base=507)
    assert vehicle.arm_reach_mm() == 2107
    assert vehicle.top_limit_mm() == 5607
    assert "5,607" in vehicle.reach_label.text()

    window.lift.reset(0.0)
    assert window._plan_lift(1800, 300) == (0.0, 1800.0, "")          # 위 끝 2100 — 리프트 그대로
    assert window._plan_lift(1800, 400)[:2] == (1800.0, 0.0)          # 위 끝 2200 — 리프트로
    assert window._zone_reach_blocker(4600, 600) == ""                 # 5200 ≤ 5607
    reason = window._zone_reach_blocker(5100, 600)                     # 5700 > 5607
    assert "5,700" in reason and "507" in reason and "5,607" in reason
    window.close()
