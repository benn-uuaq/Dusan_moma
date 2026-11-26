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
              "contact": vehicle.CONTACT_FIELD, "speed": vehicle.SPEED_FIELD}
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


def test_defaults_keep_todays_behaviour_and_lift_limit_is_5000(qtbot) -> None:
    """실제 리프트 한계 높이는 5000 mm(2026-11-24 — 예전 기본 3500)."""
    window = _window(qtbot)
    vehicle = window.screens["vehicle"]
    vehicle.apply_values({vehicle.LIFT_MAX_FIELD: vehicle.DEFAULTS[vehicle.LIFT_MAX_FIELD]})
    assert vehicle.lift_max_mm() == 5000
    assert vehicle.reach_mm() == 1600                  # 로봇 상승 한계 — 베이스 위로
    assert vehicle.contact_height_mm() == 0 and not vehicle.contact_from_saved_pose()
    assert vehicle.arm_reach_mm() == 1600 and vehicle.top_limit_mm() == 6600
    assert vehicle.speed_mm_s() == 100                 # 차량 주행 속도 기본 0.1 m/s
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
    _set(window, lift_max=3500)
    window.lift.reset(float(lift_now))
    lift, offset, reason = window._plan_lift(bottom, height)
    assert reason == ""
    assert (lift, offset) == pytest.approx(expected)
    window.close()


def test_zone_beyond_the_lift_limit_and_the_arm_is_not_reachable(qtbot) -> None:
    window = _window(qtbot)
    _set(window, lift_max=3500)
    _lift, _offset, reason = window._plan_lift(4600, 600)           # 위 끝 5200 > 3500 + 1600
    assert "5,200" in reason and "3,500" in reason
    assert "리프트 한계" in window._zone_reach_blocker(4600, 600)
    assert window._zone_reach_blocker(3600, 600) == ""
    window.close()


def test_older_tasks_always_use_the_lift(qtbot) -> None:
    """팔을 못 드는 태스크(v5)면 예전처럼 리프트를 구역 아래 끝으로 — 리프트 한계까지만."""
    window = _window(qtbot)
    _set(window, lift_max=3500)
    window._task_version = "dusan_v5"
    window.lift.reset(0.0)
    assert window._plan_lift(800, 200) == (800, 0.0, "")
    assert window._plan_lift(3600, 200)[2]
    assert "리프트 한계" in window._zone_reach_blocker(3600, 200)
    window.close()


def test_erut_zone_out_of_reach_is_refused_with_the_reason(qtbot) -> None:
    window = _window(qtbot)
    _set(window, lift_max=3500)                         # 위 끝 5200 > 3500 + 1600
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


def test_calibration_does_not_drive_a_lap_and_reports_the_surface(qtbot) -> None:
    """한 바퀴는 작업자가 돈다(2026-11-24) — 차량은 달리지 않고, ERUT 에는 표면 둘레(π × 지름)로 낸다."""
    window = _window(qtbot)
    events, _responses = _tap(window)
    window._start_robot_scan = lambda: None
    window.outrigger.reset(0)
    _set(window, width=1000, standoff=500)
    targets: list[float] = []
    original = window.amr.move_to

    def record(target, unit="", label=None, timeout_ms=None):
        targets.append(float(target))
        return original(target, unit, label=label) if label is not None else original(target, unit)
    window.amr.move_to = record

    window.erut_session.handle_request("calibrate", {"req_id": "cal-v", "geometry": {"shape": "cylinder", "diameter": 1300,
                                                     "height": 6000}})
    qtbot.waitUntil(lambda: bool(events), timeout=10000)

    assert targets == []
    name, action, payload = events[-1]
    assert (name, action) == ("complete", "calibrate")
    assert abs(payload["total_length_mm"] - math.pi * 1300) <= 1
    window.close()


def test_gripper_contact_height_sets_what_the_arm_reaches(qtbot) -> None:
    """높이 0 = 모재 아래 끝 = 리프트 최하단에서 그리퍼를 붙인 그리퍼 접촉 위치(2026-11-23).

    그 위치가 로봇 베이스보다 300 mm 아래(−300)이고 상승 한계가 베이스 기준 960 이면, 팔은 리프트를 그대로
    두고 접촉 위치에서 960 + 300 = 1260 mm 까지 닿는다. 구역 시작 y 1100 은 위 끝이 그 안이면 리프트 없이
    팔을 1100 들고, 넘으면 리프트로 간다. 최대 작업 가능 높이 = 3500 + 1260 = 4760.
    """
    window = _window(qtbot)
    vehicle = _set(window, reach=960, contact=-300, lift_max=3500)
    assert vehicle.arm_reach_mm() == 1260
    assert vehicle.top_limit_mm() == 4760
    assert "4,760" in vehicle.reach_label.text() and "이 화면 값" in vehicle.reach_label.text()

    window.lift.reset(0.0)
    assert window._plan_lift(1100, 150) == (0.0, 1100.0, "")          # 위 끝 1250 — 리프트 그대로, 팔 1100
    assert window._plan_lift(1100, 200)[:2] == (1100.0, 0.0)          # 위 끝 1300 — 리프트로
    assert window._zone_reach_blocker(4100, 600) == ""                 # 4700 ≤ 4760
    reason = window._zone_reach_blocker(4300, 600)                     # 4900 > 4760
    assert "4,900" in reason and "4,760" in reason and "-300" in reason
    window.close()


def test_saved_gripper_contact_pose_gives_the_contact_height(qtbot) -> None:
    """조그 화면에 저장한 그리퍼 접촉 위치의 Z(로봇 베이스 기준)를 그대로 쓴다 — 칸은 잠긴다. 지우면 칸 값."""
    window = _window(qtbot)
    vehicle = _set(window, reach=960, contact=0)
    window._reference_poses["start_pose"] = {"values": [700.0, 10.0, -452.4, 90.0, 0.0, -90.0]}
    window._sync_contact_height()
    assert vehicle.contact_height_mm() == pytest.approx(-452.4) and vehicle.contact_from_saved_pose()
    assert not vehicle.field(vehicle.CONTACT_FIELD).isEnabled()
    assert vehicle.arm_reach_mm() == pytest.approx(1412.4)
    assert "저장한 그리퍼 접촉 위치" in vehicle.reach_label.text()
    vehicle.apply_values({vehicle.CONTACT_FIELD: 100})                # 설정 파일의 옛 값이 와도
    assert vehicle.contact_height_mm() == pytest.approx(-452.4), "저장한 접촉 위치가 이긴다"
    window._reference_poses.pop("start_pose")
    window._sync_contact_height()
    assert vehicle.field(vehicle.CONTACT_FIELD).isEnabled() and not vehicle.contact_from_saved_pose()
    window.close()


def test_each_zone_tells_the_robot_its_bottom_height(qtbot) -> None:
    """구역마다 아래 끝의 모재 높이를 로봇에 쓴다(359) — 바닥 근처 줄 바꿈에서 물러섰다 오르는 기준."""
    window = _window(qtbot)
    _set(window, reach=960, contact=-300)
    sent: list[tuple] = []
    window.ros_status.writable = lambda name: True
    window.ros_status.send_value = lambda name, value: sent.append((name, value)) or True
    window.lift.reset(0.0)
    window._move_lift_for_cell(1100.0)
    assert ("scan_z_offset", 1100) in sent and ("scan_y_base", 1100) in sent
    sent.clear()
    window._move_lift_for_cell(0.0)
    assert ("scan_y_base", 0) in sent
    window.close()


# ---- 캘리브레이션 최대 시간 (2026-11-12) ------------------------------------------------
def test_align_time_limits_the_calibration_and_is_announced_to_erut(qtbot) -> None:
    """「정렬 최대 시간」(QR 정렬 + 아웃트리거 고정) 하나가 캘리브레이션 한도이자 ERUT 에 알리는 calibrate 시간이다
    (2026-11-24 — 「캘리브레이션 최대 시간」 · 「곡판 QR 정렬 최대 시간」을 합쳤다)."""
    window = _window(qtbot)
    events, _responses = _tap(window)
    window._start_robot_scan = lambda: None
    window.outrigger.reset(0)
    infos: list[dict] = []
    window.erut_session.client.publish_info = lambda content: infos.append(content) or True
    vehicle = window.screens["vehicle"]
    assert not hasattr(vehicle, "CALIB_FIELD")
    vehicle.field(vehicle.ALIGN_FIELD).setValue(90)
    assert vehicle.calibration_time_s() == vehicle.align_time_s() == 90

    window.erut_session.handle_request("calibrate", {"req_id": "cal-t", "geometry": {"shape": "cylinder", "diameter": 1690,
                                                     "height": 2000}})
    # 계산값으로 바꾸지 않는다 — 넣은 값 그대로.
    assert vehicle.field(vehicle.ALIGN_FIELD).value() == 90
    assert window._calibration_lap is None or window._calibration_lap["limit_s"] == 90
    # ERUT 에 알린다 — evt/info timings.complete_within_ms.calibrate
    assert infos and infos[-1]["timings"]["complete_within_ms"] == {"calibrate": 90_000}
    window.close()


def test_default_align_time_is_announced_and_old_plate_value_carries_over(qtbot) -> None:
    window = _window(qtbot)
    vehicle = window.screens["vehicle"]
    assert vehicle.calibration_time_s() == 120
    assert window.erut_session._info_content()["timings"]["complete_within_ms"] == {"calibrate": 120_000}
    # 예전 저장값(「곡판 QR 정렬 최대 시간」)은 새 칸으로 옮긴다.
    vehicle.apply_values({vehicle.OLD_ALIGN_FIELD: 75})
    assert vehicle.align_time_s() == 75
    window.close()


def test_vehicle_keeps_the_standoff_the_rcs_sends(qtbot) -> None:
    """거리 유지는 RCS 가 SetJob.standoff 로 보낸다(2026-11-12)."""
    window = _window(qtbot)
    vehicle = _set(window, standoff=1500)
    vehicle.save_requested.emit("vehicle", vehicle.values())
    assert window._vehicle_amr.standoff_m == pytest.approx(1.5)
    window.close()
