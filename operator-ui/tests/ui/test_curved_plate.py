"""곡판(curved_plate) 모재 — if-0.13 geometry · 2026-11-18 결정.

  · 곡판 calibrate = 차량이 한 바퀴 돌지 않고 **판 가운데** QR 마커에 정렬만 한다(MeasureLap ALIGN).
  · 스캔 때 차량은 움직이지 않는다 — 리프트 · 로봇만. 구역은 판 가운데 기준(erut_session).
  · x 는 판 왼쪽 끝 0 ~ 판 가로, 감기지 않는다. 로봇은 원통과 같은 곡률(geometry.diameter)로 훑는다.
"""

import threading
import time

import pytest

from smr_operator_ui.app import OperatorWindow

PLATE = {"shape": "curved_plate", "diameter": 3000, "height": 2000, "width": 1000}


def _window(qtbot):
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    # 저장된 검사 대상(앞 시험이 저장한 원통)을 늦게 불러와 calibrate 의 곡판을 덮지 않게 — 다 불러온 뒤 시작한다.
    loaded: list[str] = []
    window.settings_service.loaded.connect(lambda scope, _values: loaded.append(scope))
    qtbot.waitUntil(lambda: "inspection_target" in loaded, timeout=5000)
    window.erut.publish_res = lambda *a, **k: True
    window.ros_status.call_command = lambda name: True
    window.ros_status.send_value = lambda *a: True
    window.ros_status.send_pose = lambda *a: True
    return window


def test_plate_calibration_without_a_vehicle_takes_the_spot_as_origin(qtbot) -> None:
    """더미 차량(차량 노드 없음) — 놓인 자리를 QR 원점으로 치고 바로 끝낸다."""
    window = _window(qtbot)
    events: list[tuple] = []
    window.erut.publish_event = lambda name, req, action, status_code=200, error=None, **kw: (
        events.append((name, action, status_code, kw)) or True)
    window._robot_at_home = True
    window._on_robot_task_state(3)
    window.erut_session.handle_request("calibrate", {"req_id": "cal-p", "geometry": dict(PLATE)})
    qtbot.waitUntil(lambda: any(e[0] == "complete" and e[1] == "calibrate" for e in events),
                    timeout=10000)
    _name, _action, code, payload = [e for e in events if e[1] == "calibrate"][-1]
    assert code == 200
    assert "total_length_mm" not in payload, "곡판은 판 가로를 재지 않는다"
    assert window.main_screen.orbit_view.target_shape() == ("curved_plate", 1.0)
    assert window.main_screen.orbit_view.target_dimensions() == (3.0, 2.0)
    # 캘리브레이션 최대 시간은 「곡판 QR 정렬 최대 시간」이다.
    vehicle = window.screens["vehicle"]
    assert vehicle.calibration_time_s() == vehicle.align_time_s() == 120
    window.close()


def test_plate_x_is_not_wrapped_for_the_vehicle(qtbot) -> None:
    window = _window(qtbot)
    window._set_target_geometry({"shape": "cylinder", "diameter": 1000, "height": 1000})
    wrapped = window._vehicle_x(3300) / window._travel_scale()
    assert wrapped == pytest.approx(3300 - 3141.59, abs=1)
    window._set_target_geometry(dict(PLATE))
    # 차량 위치 0 = 판 가운데 QR(x 500) — 감기지 않는다.
    assert window._vehicle_x(900) / window._travel_scale() == pytest.approx(400)
    assert window._vehicle_x(500) == pytest.approx(0)
    assert window._target_length_mm() == pytest.approx(1000)
    window.close()


def test_plate_target_is_saved_with_its_width(qtbot) -> None:
    window = _window(qtbot)
    saved: list[tuple] = []
    window.settings_service.save = lambda scope, values: saved.append((scope, dict(values)))
    window._set_target_geometry(dict(PLATE))
    scope, values = [s for s in saved if s[0] == "inspection_target"][-1]
    assert values == {"diameter_m": 3.0, "height_m": 2.0, "shape": "curved_plate", "width_m": 1.0}
    # 예전 저장값(모양 없음)은 원통으로 읽는다.
    window._apply_stored_settings("inspection_target", {"diameter_m": 1.69, "height_m": 6.0})
    assert window.main_screen.orbit_view.target_shape() == ("cylinder", 0.0)
    window.close()


def test_erut_plate_start_keeps_the_plate_as_the_rcs_target(qtbot) -> None:
    window = _window(qtbot)
    window.erut.publish_event = lambda *a, **k: True
    window.erut_session._calibrated = True
    window.erut_session._calibrated_geometry = {k: float(v) if k != "shape" else v
                                                for k, v in PLATE.items()}
    window.erut_session.handle_request("prepare", {
        "req_id": "p1", "job_id": "jb-plate", "surface": "outer", "geometry": dict(PLATE),
        "area": {"start": {"x": 250, "y": 0}, "end": {"x": 750, "y": 200}},
        "scan": {"pitch_left": 20, "pitch_right": 20, "overlap": 0, "speed_pct": 50}})
    assert window.main_screen.orbit_view.target_shape() == ("curved_plate", 1.0)
    window.close()


def test_one_align_time_covers_cylinder_and_plate(qtbot) -> None:
    """「정렬 최대 시간」 하나가 원통 · 곡판 캘리브레이션 한도다(2026-11-24 — 둘을 합쳤다)."""
    window = _window(qtbot)
    vehicle = window.screens["vehicle"]
    vehicle.field(vehicle.ALIGN_FIELD).setValue(90)
    assert vehicle.align_time_s() == 90 and vehicle.calibration_time_s() == 90
    window.close()


def test_plate_calibration_aligns_with_the_simulator(qtbot, tmp_path, monkeypatch) -> None:
    """차량 노드(모의기)에 measure_lap mode=ALIGN 을 보내고, DONE · lap_origin_ok 뒤 HOLD(아웃트리거 고정)로 끝낸다."""
    pytest.importorskip("rclpy")
    vehicle_sim_node = pytest.importorskip("vehicle_sim.vehicle_sim_node")
    from vehicle_interfaces.srv import MeasureLap
    if not hasattr(MeasureLap.Request(), "mode"):
        pytest.skip("vehicle_interfaces 를 다시 빌드해야 한다(MeasureLap.mode)")
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.parameter import Parameter
    ns = f"vp{int(time.time() * 1000) % 100000}"
    monkeypatch.setenv("SMR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SMR_VEHICLE_NS", ns)
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    events: list[tuple] = []
    window.erut.publish_event = lambda name, req, action, status_code=200, error=None, **kw: (
        events.append((name, action, status_code, {**kw, **({"error": error} if error else {})})) or True)
    window.erut.publish_res = lambda *a, **k: True
    window.ros_status.call_command = lambda name: True
    window.ros_status.send_value = lambda *a: True
    window.ros_status.send_pose = lambda *a: True
    window._start_robot_scan = lambda: None
    window.ros_status.start()
    sim = vehicle_sim_node.VehicleSim(parameter_overrides=[
        Parameter("namespace", value=ns), Parameter("outrigger_s", value=0.2),
        Parameter("align_s", value=0.5)])
    executor = SingleThreadedExecutor()
    executor.add_node(sim)
    threading.Thread(target=executor.spin, daemon=True).start()
    try:
        qtbot.waitUntil(lambda: window.vehicle.online, timeout=5000)
        qtbot.waitUntil(lambda: window.vehicle.supports("measure_lap"), timeout=5000)
        window._robot_at_home = True
        window._on_robot_task_state(3)
        window.erut_session.handle_request("calibrate", {"req_id": "cal-pa", "geometry": dict(PLATE)})
        qtbot.waitUntil(lambda: any(e[0] == "complete" and e[1] == "calibrate" for e in events),
                        timeout=15000)
        _name, _action, code, payload = [e for e in events if e[1] == "calibrate"][-1]
        assert code == 200 and "total_length_mm" not in payload, payload
        # 정렬(ALIGN) 뒤 RCS 가 HOLD(거리 0 작업 → 도착 HOLD)로 아웃트리거를 고정했다 — 그다음 prepare.
        qtbot.waitUntil(lambda: window.vehicle.last_status.get("state") == "HOLD", timeout=3000)
        assert window.vehicle.last_status.get("hold") == "SET" and window.outrigger.position == 1
        assert sim.lap_origin_ok is True and sim.odometer == pytest.approx(0.0)
    finally:
        executor.shutdown()
        sim.destroy_node()
        window.ros_status.stop()
        window.close()


def test_plate_scan_does_not_drive_the_vehicle(qtbot) -> None:
    """곡판 구역 — 차량은 QR 에 정렬한 자리 그대로, 리프트 · 로봇만 움직인다(2026-11-18)."""
    window = _window(qtbot)
    window.erut.publish_event = lambda *a, **k: True
    window._start_robot_scan = lambda: None
    window.erut_session._calibrated = True
    window.erut_session._calibrated_geometry = {k: float(v) if k != "shape" else v
                                                for k, v in PLATE.items()}
    window._set_target_geometry(dict(PLATE))
    window.amr.reset(0.0)
    window._robot_at_home = True            # 로봇이 홈 · 태스크 정지 — 차량 이동을 미루지 않는다
    window._on_robot_task_state(3)
    moves: list[float] = []
    original = window.amr.move_to
    window.amr.move_to = lambda target, *a, **k: moves.append(float(target)) or original(target, *a, **k)
    window.erut_session.handle_request("start", {
        "req_id": "s1", "job_id": "jb-plate", "surface": "outer", "geometry": dict(PLATE),
        # 판 가운데(500) 기준 600 mm — 지름 3000 에서 팔이 한 번에 훑는 가로(약 700) 안
        "area": {"start": {"x": 200, "y": 0}, "end": {"x": 800, "y": 200}},
        "scan": {"pitch_left": 20, "pitch_right": 20, "overlap": 0, "speed_pct": 50}})
    qtbot.waitUntil(lambda: bool(moves), timeout=5000)
    assert all(m == pytest.approx(0.0) for m in moves), moves
    assert window.outrigger.position == 0 or window.outrigger.target != 0, "주행이 없으니 아웃트리거도 풀지 않는다"
    window.close()
