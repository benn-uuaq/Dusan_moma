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
    assert "plc_connected" not in d and "plc_state" not in d, "PLC 전용 필드는 없다(AGV 판) — 701 로 본다"
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


def test_battery_follows_the_vehicle_and_the_plc_lamp_is_not_a_placeholder(qtbot) -> None:
    """PLC 는 차량(AMR) 노드가 맡는다 — 상단 PLC 는 차량 상태로 본다(2026-11-20).

    11-17 에 뺀 PLC 표시는 늘 「연결됨」인 자리표시자였다. 다시 넣은 것은 차량 상태가 안 오면 「모름」,
    오면 「연결됨」, 차량 노드가 701(PLC_COMM_LOST)을 내면 「연결 실패」다.
    """
    window = _window(qtbot)
    infos: list[dict] = []
    window.erut_session.client.publish_info = lambda content: infos.append(content) or True
    assert "모름" in window.top_bar.badges["PLC"].state_label.text()
    _vehicle_mode(window, {"state": "STOP", "battery_pct": 64, "charging": False})
    assert "연결됨" in window.top_bar.badges["PLC"].state_label.text(), "차량 상태가 오고 701 이 없다"
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
def _sim_window(qtbot, tmp_path, monkeypatch, prefix, **params):
    pytest.importorskip("rclpy")
    vehicle_sim_node = pytest.importorskip("vehicle_sim.vehicle_sim_node")
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.parameter import Parameter
    ns = f"{prefix}{int(time.time() * 1000) % 100000}"
    monkeypatch.setenv("SMR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SMR_VEHICLE_NS", ns)
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    events: list[tuple] = []
    window.erut.publish_event = lambda name, req, action, status_code=200, error=None, **kw: (
        events.append((name, action, status_code, {**kw, **({"error": error} if error else {})})) or True)
    window.erut.publish_res = lambda *a, **k: True
    window._start_robot_scan = lambda: None
    window.ros_status.start()
    sim = vehicle_sim_node.VehicleSim(parameter_overrides=[
        Parameter("namespace", value=ns), Parameter("outrigger_s", value=0.2),
        Parameter("align_s", value=0.3), *[Parameter(k, value=v) for k, v in params.items()]])
    executor = SingleThreadedExecutor()
    executor.add_node(sim)
    threading.Thread(target=executor.spin, daemon=True).start()

    def close():
        executor.shutdown()
        sim.destroy_node()
        window.ros_status.stop()
        window.close()
    return window, sim, events, close


def test_calibration_aligns_at_the_qr_then_holds_with_the_simulator(qtbot, tmp_path, monkeypatch) -> None:
    """calibrate = measure_lap ALIGN(한 바퀴 없음) → HOLD(거리 0 작업 → 도착 HOLD) → 결과 (2026-11-24)."""
    window, sim, events, close = _sim_window(qtbot, tmp_path, monkeypatch, "vl")
    try:
        qtbot.waitUntil(lambda: window.vehicle.online, timeout=5000)
        qtbot.waitUntil(lambda: window.vehicle.supports("measure_lap"), timeout=5000)
        assert window.amr.active == "vehicle"
        modes: list[str] = []
        original = window.vehicle.measure_lap

        def record(*args, **kwargs):
            modes.append(kwargs.get("mode", ""))
            return original(*args, **kwargs)
        window.vehicle.measure_lap = record
        window._robot_at_home = True
        window._on_robot_task_state(3)
        window.erut_session.handle_request("calibrate", {"req_id": "cal-ml", "geometry": {"shape": "cylinder", "diameter": 300,
                                                         "height": 500}})
        qtbot.waitUntil(lambda: any(e[0] == "complete" and e[1] == "calibrate" for e in events),
                        timeout=15000)
        name, action, code, payload = [e for e in events if e[1] == "calibrate"][-1]
        assert code == 200, payload
        assert modes == ["ALIGN"], "LAP 은 쓰지 않는다 — 한 바퀴는 작업자가 돈다"
        # 잰 둘레가 없으니 π × 바깥 지름.
        assert payload["total_length_mm"] == pytest.approx(math.pi * 300, abs=2)
        assert sim.lap_origin_ok is True and sim.odometer == pytest.approx(0.0)
        qtbot.waitUntil(lambda: sim.state == "HOLD" and sim.hold == "SET", timeout=3000)
        assert window.amr.position == pytest.approx(0.0), "QR 자리가 원점"
    finally:
        close()


def test_calibration_fails_when_the_vehicle_refuses_alignment(qtbot, tmp_path, monkeypatch) -> None:
    """차량이 measure_lap(ALIGN)을 거절하면 캘리브레이션은 실패다 — 예전처럼 SetJob 한 바퀴로 대신하지 않는다
    (AGV 제어 노드는 QR 피드백이 정해질 때까지 measure_lap 을 거절한다, 2026-11-23)."""
    window, sim, events, close = _sim_window(qtbot, tmp_path, monkeypatch, "vk", lap_supported=False)
    try:
        qtbot.waitUntil(lambda: window.vehicle.online, timeout=5000)
        qtbot.waitUntil(lambda: window.vehicle.supports("measure_lap"), timeout=5000)
        window._robot_at_home = True
        window._on_robot_task_state(3)
        window.erut_session.handle_request("calibrate", {"req_id": "cal-fb", "geometry": {
            "shape": "cylinder", "diameter": 300, "height": 500}})
        qtbot.waitUntil(lambda: any(e[0] == "complete" and e[1] == "calibrate" for e in events),
                        timeout=15000)
        name, action, code, payload = [e for e in events if e[1] == "calibrate"][-1]
        assert code == 500
        assert "QR 정렬" in payload["error"]["detail"]
        assert sim.odometer == pytest.approx(0.0) and sim.hold == "RELEASE", "달리지도 고정하지도 않는다"
        assert window.erut_session._calibrated is False
    finally:
        close()


# ---- 비상정지 — 차량(PLC) · 로봇 컨트롤러 어느 쪽이든 (2026-11-18) -----------------------------
def test_robot_controller_estop_stops_all_and_reports_once(qtbot) -> None:
    """로봇 컨트롤러 비상정지 → 로봇 · 작업을 세우고 E1002. 차량과 같이 서는 것은 안전 회로가 맡는다 —
    RCS 는 <ns>/robot_estop 에 내지 않는다(AGV 노드가 PLC 비상정지를 내는 토픽, 2026-11-23)."""
    window = _window(qtbot)
    raised: list[dict] = []
    original = window.erut_session.raise_error
    window.erut_session.raise_error = lambda fields: raised.append(fields) or original(fields)
    assert not hasattr(window.vehicle, "send_robot_estop")
    stops: list[int] = []
    window._stop_robot_scan = lambda: stops.append(1)
    window.ros_status.estop_changed.emit(True)
    assert raised[-1]["code"] == "E1002" and "로봇 컨트롤러" in raised[-1]["detail"]
    assert stops == [1]
    assert "비상정지" in window.top_bar.badges["Cobot"].state_label.text()
    # 차량도 비상정지로 알려 와도 E1002 는 하나뿐이다.
    _vehicle_mode(window, {"state": "STOP", "estop": True})
    assert [f["code"] for f in raised].count("E1002") == 1
    # 로봇 쪽만 풀면 아직 비상정지다 — 둘 다 풀려야 푼다.
    window.ros_status.estop_changed.emit(False)
    assert "E1002" in window.erut_session.error_codes()
    _vehicle_mode(window, {"state": "STOP", "estop": False})
    assert "E1002" not in window.erut_session.error_codes()
    window.close()


def test_vehicle_plc_estop_from_the_simulator(qtbot, tmp_path, monkeypatch) -> None:
    """차량(PLC) 비상정지 — 모의기(= AGV 노드)가 robot_status.estop 과 <ns>/robot_estop 으로 낸다. RCS 는 E1002 를
    하나 내고, 그 토픽에 발행하지 않는다. 비상정지 동안 차량은 움직이는 명령을 거절한다."""
    pytest.importorskip("rclpy")
    vehicle_sim_node = pytest.importorskip("vehicle_sim.vehicle_sim_node")
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.parameter import Parameter
    ns = f"ve{int(time.time() * 1000) % 100000}"
    monkeypatch.setenv("SMR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SMR_VEHICLE_NS", ns)
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    window.erut.publish_event = lambda *a, **k: True
    window.erut.publish_res = lambda *a, **k: True
    window.ros_status.call_command = lambda name: True
    window.ros_status.send_value = lambda *a: True
    window._stop_robot_scan = lambda: None
    window.ros_status.start()
    sim = vehicle_sim_node.VehicleSim(parameter_overrides=[Parameter("namespace", value=ns)])
    executor = SingleThreadedExecutor()
    executor.add_node(sim)
    threading.Thread(target=executor.spin, daemon=True).start()
    try:
        qtbot.waitUntil(lambda: window.vehicle.online, timeout=5000)
        assert sim.count_publishers(f"{ns}/robot_estop") == 1, "robot_estop 은 차량만 낸다"
        sim.set_parameters([Parameter("estop", value=True)])
        qtbot.waitUntil(lambda: window.vehicle.last_status.get("estop") is True, timeout=5000)
        qtbot.waitUntil(lambda: "E1002" in window.erut_session.error_codes(), timeout=3000)
        from vehicle_interfaces.srv import SetJob
        res = sim.on_set_job(SetJob.Request(job_id="J-1", set_dist=1.0, set_speed=0.1, radius=1.0),
                             SetJob.Response())
        assert res.success is False and "E-STOP" in res.message      # 비상정지 동안 움직이는 명령을 거절한다
        sim.set_parameters([Parameter("estop", value=False)])
        qtbot.waitUntil(lambda: "E1002" not in window.erut_session.error_codes(), timeout=5000)
    finally:
        executor.shutdown()
        sim.destroy_node()
        window.ros_status.stop()
        window.close()


# ---- 차량 오류 번호(error_code) — 서 있을 때 생긴 오류 · 리셋 (2026-11-19) ---------------------------
def _alarm_texts(window) -> list[str]:
    lst = window.cobot_manual_screen.alarm_list
    return [lst.item(i).text() for i in range(lst.count())]


def test_vehicle_error_while_idle_only_alarms(qtbot) -> None:
    window = _window(qtbot)
    _vehicle_mode(window, {"state": "STOP", "error_code": 0})
    _vehicle_mode(window, {"state": "ERROR", "error_code": 301, "error_msg": "OUTRIGGER_SET_FAILED"})
    assert "[E2001] 차량: 차량 오류 301 OUTRIGGER_SET_FAILED" in _alarm_texts(window)[0]
    assert "E2001" not in window.erut_session.error_codes(), "쉬는 중이면 ERUT 장애로 올리지 않는다"
    window.close()


def test_vehicle_error_during_a_job_stops_it_with_e2001(qtbot) -> None:
    window = _window(qtbot)
    window._start_robot_scan = lambda: None
    window._start_inspection()
    assert window._job_running()
    _vehicle_mode(window, {"state": "ERROR", "error_code": 304, "error_msg": "OUTRIGGER_LOST"})
    assert "E2001" in window.erut_session.error_codes()
    assert window.sequencer.state in (SequencerState.STOPPED, SequencerState.IDLE)
    window.close()


def test_alarm_reset_also_resets_the_vehicle(qtbot) -> None:
    window = _window(qtbot)
    sent: list[dict] = []
    window.vehicle.control = lambda state="", reset=False, job_cancel=False: sent.append(
        {"state": state, "reset": reset}) or True
    _vehicle_mode(window, {"state": "ERROR", "error_code": 201, "error_msg": "LIFT_MOTOR_FAULT"})
    window._reset_alarms()
    assert {"state": "", "reset": True} in sent
    window.close()


def test_saved_speed_and_standoff_go_out_with_the_next_drive_job(qtbot, tmp_path, monkeypatch) -> None:
    """차량 설정 「저장」은 차량에 바로 쏘지 않는다 — AGV 노드에 값만 바꾸는 명령이 없어 다음 주행 작업
    (SetJob.set_speed · standoff)에 실려 간다. 달리는 중인 작업은 처음 받은 값 그대로 끝까지 간다(2026-11-24 검토)."""
    window, sim, events, close = _sim_window(qtbot, tmp_path, monkeypatch, "vs")
    try:
        qtbot.waitUntil(lambda: window.vehicle.online, timeout=5000)
        assert window.amr.active == "vehicle"
        vehicle = window.screens["vehicle"]

        def save(speed, standoff):
            vehicle.field(vehicle.SPEED_FIELD).setValue(speed)
            vehicle.field(vehicle.STANDOFF_FIELD).setValue(standoff)
            vehicle.save_requested.emit("vehicle", vehicle.values())

        said: list[str] = []
        window.main_screen.show_activity = said.append
        save(300, 400)
        assert sim.job is None or sim.job.set_speed != pytest.approx(0.3), "저장만으로는 차량에 아무것도 안 간다"
        assert any("다음 주행 작업(SetJob)부터" in t and "300 mm/s" in t and "400 mm" in t for t in said)
        window.amr.move_to(window.amr.position + 600, " mm")
        qtbot.waitUntil(lambda: sim.job is not None and sim.state == "RUNNING", timeout=5000)
        assert (sim.job.set_speed, sim.job.standoff) == (pytest.approx(0.3), pytest.approx(0.4))

        save(500, 600)                                         # 달리는 중에 저장
        assert "처음 받은 값 그대로" in said[-1]
        assert (sim.job.set_speed, sim.job.standoff) == (pytest.approx(0.3), pytest.approx(0.4)), \
            "달리는 작업은 바뀌지 않는다"
        qtbot.waitUntil(lambda: not window.amr.moving, timeout=10000)

        # 다음 주행(작업 끝 → 새 작업)부터 새 값이다.
        finished = []
        window._vehicle_finish.arrived.connect(lambda: finished.append(1))
        window._vehicle_finish.move_to(0)
        qtbot.waitUntil(lambda: bool(finished), timeout=5000)
        window.amr.move_to(window.amr.position + 100, " mm")
        qtbot.waitUntil(lambda: sim.job is not None and sim.job.set_speed == pytest.approx(0.5), timeout=5000)
        assert sim.job.standoff == pytest.approx(0.6)
        qtbot.waitUntil(lambda: not window.amr.moving, timeout=10000)
    finally:
        close()


def test_calibration_alignment_uses_saved_not_typed_values(qtbot) -> None:
    """QR 정렬(measure_lap)도 주행과 같이 저장한 값 — 입력칸에 쳐 두기만 한 값은 쓰지 않는다."""
    window = _window(qtbot)
    vehicle = window.screens["vehicle"]
    vehicle.field(vehicle.SPEED_FIELD).setValue(250)
    vehicle.field(vehicle.STANDOFF_FIELD).setValue(300)
    vehicle.save_requested.emit("vehicle", vehicle.values())
    vehicle.field(vehicle.SPEED_FIELD).setValue(900)           # 저장 안 함
    sent: list[tuple] = []
    window.vehicle.measure_lap = lambda job_id, speed, standoff, dist, mode="": sent.append((speed, standoff, mode)) or True
    window._start_measured_lap({"limit_s": 0})
    assert sent == [(pytest.approx(0.25), pytest.approx(0.3), "ALIGN")]
    window._lap_timer().stop()
    window.close()


@pytest.mark.parametrize("how", ["rcs_stop", "erut_abort"])
def test_stop_and_abort_cancel_the_vehicle_job(qtbot, tmp_path, monkeypatch, how) -> None:
    """RCS 「정지」 · ERUT 정지(abort) → 차량에 STOP + job_cancel — 남은 작업이 없어 다음 주행이 받아들여진다
    (2026-11-24 검토)."""
    window, sim, _events, close = _sim_window(qtbot, tmp_path, monkeypatch, "vc")
    try:
        qtbot.waitUntil(lambda: window.vehicle.online, timeout=5000)
        assert window.amr.active == "vehicle"
        sent: list[tuple] = []
        original = window.vehicle.control
        window.vehicle.control = lambda state="", reset=False, job_cancel=False: (
            sent.append((state, job_cancel)), original(state, reset=reset, job_cancel=job_cancel))[1]
        vehicle = window.screens["vehicle"]
        vehicle.field(vehicle.SPEED_FIELD).setValue(100)
        vehicle.save_requested.emit("vehicle", vehicle.values())
        window.amr.move_to(window.amr.position + 2000, " mm")       # 0.1 m/s 로 20 s — 도중에 끊는다
        qtbot.waitUntil(lambda: sim.state == "RUNNING", timeout=5000)
        if how == "rcs_stop":
            window._stop_inspection()
        else:
            window.erut_session.handle_request("abort", {"req_id": "ab-1"})
        assert ("STOP", True) in sent
        qtbot.waitUntil(lambda: sim.state == "STOP" and sim.job is None, timeout=3000)
        accepted: list[bool] = []
        window.vehicle.command_result.connect(lambda name, ok, _m: accepted.append(ok) if name == "set_job" else None)
        window.amr.move_to(window.amr.position + 100, " mm")
        qtbot.waitUntil(lambda: bool(accepted), timeout=3000)
        assert accepted[-1] is True, "남은 작업이 없어 새 작업을 받는다"
        window.amr.cancel()
    finally:
        close()


def test_lift_finish_off_target_fails_quickly_with_the_simulator(qtbot, tmp_path, monkeypatch) -> None:
    """PLC 가 목표 옆에 세우고 FINISH 를 내면 180 s 를 기다리지 않고 바로 알린다(2026-11-24 현장 780 mm)."""
    window, sim, _events, close = _sim_window(qtbot, tmp_path, monkeypatch, "vf", lift_stop_error=0.012)
    try:
        qtbot.waitUntil(lambda: window.vehicle.online, timeout=5000)
        problems: list[str] = []
        window._vehicle_lift.problem.connect(problems.append)
        window._vehicle_lift.speed_mps = 0.2
        sim.hold, sim.state = "SET", "HOLD"
        window._vehicle_lift.move_to(780.0, " mm")
        qtbot.waitUntil(lambda: bool(problems), timeout=10000)
        assert "도착 신호(FINISH)" in problems[0] and "목표 780 mm" in problems[0]
        assert sim.lift_state == "FINISH"
    finally:
        close()
