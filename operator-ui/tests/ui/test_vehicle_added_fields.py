"""RCS_AGV_인터페이스(2026-11-17)에 더한 칸 — 차량이 문서대로 채우면 RCS 가 바로 쓴다.

  RobotStatus: battery_pct · charging · estop · obstacle_stop · level_state · standoff_meas ·
               lap_state · lap_path_m · lap_origin_ok · lap_error (PLC 는 차량 노드가 맡아 칸이 없다)
  MeasureLap.srv(캘리브레이션 한 바퀴) · <ns>/start_request(차량 「작업 시작」 버튼)
"""

import math
import threading
import time

import pytest

from smr_operator_ui.app import OperatorWindow
from smr_operator_ui.services.job_sequencer import SequencerState


def _window(qtbot):
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    window.erut.publish_event = lambda *a, **k: True
    window.erut.publish_res = lambda *a, **k: True
    window.ros_status.call_command = lambda name: True
    window.ros_status.send_value = lambda *a: True
    window.ros_status.send_pose = lambda *a: True
    return window


# ---- 칸 옮기기 ----------------------------------------------------------------------
def test_status_dict_keeps_known_values_and_drops_unknown_defaults() -> None:
    msg_mod = pytest.importorskip("vehicle_interfaces.msg")
    from smr_operator_ui.services.vehicle_client import status_to_dict
    blank = status_to_dict(msg_mod.RobotStatus())
    # 기본값은 「모름」 — 칸을 싣지 않는다(RCS 는 없는 칸을 조건에서 뺀다).
    for key in ("battery_pct", "charging", "level_ok", "standoff_meas"):
        assert key not in blank
    assert blank["estop"] is False and blank["lap_state"] == "IDLE"
    msg = msg_mod.RobotStatus(battery_pct=72, charging=True, level_state=0,
                              standoff_meas=1.5, estop=True, lap_state="DONE", lap_path_m=4.2,
                              lap_origin_ok=True)
    d = status_to_dict(msg)
    assert (d["battery_pct"], d["charging"], d["level_ok"]) == (72, True, False)
    assert "plc_connected" not in d and not hasattr(msg, "plc_link"), "PLC 는 차량 노드가 맡는다"
    assert d["standoff_meas"] == 1.5 and d["estop"] is True
    assert (d["lap_state"], d["lap_path_m"], d["lap_origin_ok"]) == ("DONE", 4.2, True)


# ---- 안전 · 상태 반응 ------------------------------------------------------------------
def _vehicle_mode(window, status):
    window.amr.use_backend = getattr(window.amr, "use_backend", None)
    window.vehicle._online = True
    window.vehicle._last = dict(status)
    window.vehicle.status_changed.emit(dict(status))


def test_estop_stops_everything_and_raises_e1002(qtbot) -> None:
    window = _window(qtbot)
    raised: list[dict] = []
    original = window.erut_session.raise_error
    window.erut_session.raise_error = lambda fields: raised.append(fields) or original(fields)
    stops: list[int] = []
    window._stop_robot_scan = lambda: stops.append(1)
    _vehicle_mode(window, {"state": "STOP", "estop": True})
    assert raised[-1]["code"] == "E1002" and raised[-1]["level"] == "estop"
    assert stops == [1]
    _vehicle_mode(window, {"state": "STOP", "estop": False})
    assert "E1002" not in window.erut_session.error_codes()
    window.close()


def test_obstacle_stop_pauses_the_running_job(qtbot) -> None:
    window = _window(qtbot)
    window._start_robot_scan = lambda: None
    window._start_inspection()
    assert window.sequencer.state not in (SequencerState.IDLE, SequencerState.PAUSED)
    _vehicle_mode(window, {"state": "STOP", "obstacle_stop": True})
    assert window.sequencer.state is SequencerState.PAUSED
    assert "접근 감지" in window.main_screen.activity_label.text()
    window.close()


def test_level_and_estop_block_the_robot_ready_signal(qtbot) -> None:
    """로봇 팔을 뻗게 하는 차량 고정 확인(309)에 수평 · 비상정지를 더했다 — 모르면 뺀다."""
    window = _window(qtbot)
    for motion in (window.amr, window.lift, window.outrigger):
        motion.select("vehicle")
    window.outrigger.backend._position = 1.0
    base = {"state": "HOLD", "hold": "SET", "error_code": 0}

    def secured(status):
        window.vehicle._online = True
        window.vehicle._last = dict(status)
        return window._vehicle_secured()

    assert secured(base)                                       # 새 칸을 모르면 예전처럼
    assert secured({**base, "level_ok": True})
    assert not secured({**base, "level_ok": False})
    assert not secured({**base, "estop": True})
    window.close()


def test_battery_follows_the_vehicle_and_there_is_no_plc_lamp(qtbot) -> None:
    """PLC 는 차량(AMR) 노드가 맡는다 — 상단에 PLC 표시가 없다(2026-11-17)."""
    window = _window(qtbot)
    infos: list[dict] = []
    window.erut_session.client.publish_info = lambda content: infos.append(content) or True
    assert "PLC" not in window.top_bar.badges
    _vehicle_mode(window, {"state": "STOP", "battery_pct": 64, "charging": False})
    assert window.top_bar.battery_label.text() == "배터리  64%"
    # ERUT 에도 싣는다 — 상태 · 기능 목록.
    assert window.erut_session.battery_state() == {"battery": 64, "charging": False}
    assert "battery" in infos[-1]["capabilities"] and "charging" in infos[-1]["capabilities"]
    _vehicle_mode(window, {"state": "STOP"})
    assert window.top_bar.battery_label.text() == "배터리  -"
    assert window.erut_session.battery_state() == {}
    window.close()


def test_vehicle_start_button_runs_the_measurement(qtbot) -> None:
    window = _window(qtbot)
    window.main_screen.orbit_view.set_target_dimensions(1.3, 0.8)
    window.vehicle.start_requested.emit()
    assert window.erut_session.local_calibrating
    window.main_screen.stop_button.click()
    window.close()


# ---- 모의기와 끝까지: measure_lap -----------------------------------------------------
def test_calibration_uses_measure_lap_with_the_simulator(qtbot, tmp_path, monkeypatch) -> None:
    pytest.importorskip("rclpy")
    vehicle_sim_node = pytest.importorskip("vehicle_sim.vehicle_sim_node")
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.parameter import Parameter
    ns = f"vl{int(time.time() * 1000) % 100000}"
    monkeypatch.setenv("SMR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SMR_VEHICLE_NS", ns)
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    events: list[tuple] = []
    window.erut.publish_event = lambda name, req, action, status_code=200, error=None, **kw: (
        events.append((name, action, status_code, kw)) or True)
    window.erut.publish_res = lambda *a, **k: True
    window._start_robot_scan = lambda: None
    window.ros_status.start()
    sim = vehicle_sim_node.VehicleSim(parameter_overrides=[
        Parameter("namespace", value=ns), Parameter("outrigger_s", value=0.2)])
    executor = SingleThreadedExecutor()
    executor.add_node(sim)
    threading.Thread(target=executor.spin, daemon=True).start()
    try:
        qtbot.waitUntil(lambda: window.vehicle.online, timeout=5000)
        qtbot.waitUntil(lambda: window.vehicle.supports("measure_lap"), timeout=5000)
        assert window.amr.active == "vehicle"
        vehicle = window.screens["vehicle"]
        vehicle.field(vehicle.SPEED_FIELD).setValue(1000)          # 1 m/s — 시험을 빨리
        vehicle.save_requested.emit("vehicle", vehicle.values())
        window._robot_at_home = True
        window._on_robot_task_state(3)
        window.erut_session.handle_request("calibrate", {"req_id": "cal-ml", "geometry": {"shape": "cylinder", "diameter": 300,
                                                         "height": 500}})
        qtbot.waitUntil(lambda: any(e[0] == "complete" and e[1] == "calibrate" for e in events),
                        timeout=15000)
        name, action, code, payload = [e for e in events if e[1] == "calibrate"][-1]
        assert code == 200
        assert payload["total_length_mm"] == pytest.approx(math.pi * 300, abs=2)
        assert window.vehicle.last_status["lap_state"] == "DONE"
    finally:
        executor.shutdown()
        sim.destroy_node()
        window.ros_status.stop()
        window.close()
