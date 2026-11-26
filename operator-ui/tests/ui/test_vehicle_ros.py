"""차량 제어 — 실제 ROS 위에서 모의기(vehicle_sim)와 끝까지 돌려 본다.

워크스페이스를 소싱해야(vehicle_interfaces·vehicle_sim) 돈다. 아니면 건너뛴다:
    source /opt/ros/humble/setup.bash && source install/setup.bash
"""

import threading
import time

import pytest

rclpy = pytest.importorskip("rclpy")
pytest.importorskip("vehicle_interfaces")
vehicle_sim_node = pytest.importorskip("vehicle_sim.vehicle_sim_node")

from rclpy.executors import SingleThreadedExecutor  # noqa: E402
from rclpy.parameter import Parameter  # noqa: E402

from smr_operator_ui.app import OperatorWindow  # noqa: E402


def _start(qtbot, tmp_path, monkeypatch, prefix: str, **params):
    ns = f"{prefix}{int(time.time() * 1000) % 100000}"      # 다른 시험·모의기와 섞이지 않게
    monkeypatch.setenv("SMR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SMR_VEHICLE_NS", ns)
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    window.ros_status.start()
    values = {"outrigger_s": 0.3, "lift_speed": 0.5, "drive_speed": 0.5, **params}
    sim = vehicle_sim_node.VehicleSim(parameter_overrides=[
        Parameter("namespace", value=ns), *(Parameter(k, value=v) for k, v in values.items())])
    executor = SingleThreadedExecutor()
    executor.add_node(sim)
    threading.Thread(target=executor.spin, daemon=True).start()
    qtbot.waitUntil(lambda: window.vehicle.online, timeout=5000)
    # 차량 제어 기본값은 '자동' — 상태가 들어오면 스스로 차량으로 붙는다.
    assert window.amr.active == "vehicle"
    vehicle = window.screens["vehicle"]
    vehicle.field(vehicle.SPEED_FIELD).setValue(500)        # 앞 시험이 저장한 값과 상관없게
    vehicle.field(vehicle.LIFT_SPEED_FIELD).setValue(200)   # 자동 리프트 0.2 m/s(AGV 노드 상한)
    window._apply_vehicle_settings()
    return window, sim, executor


def _stop(window, sim, executor) -> None:
    executor.shutdown()
    sim.destroy_node()
    window.ros_status.stop()
    window.close()


def test_vehicle_moves_through_the_real_interfaces(qtbot, tmp_path, monkeypatch) -> None:
    window, sim, executor = _start(qtbot, tmp_path, monkeypatch, "vt")
    try:
        with qtbot.waitSignal(window.amr.arrived, timeout=8000):
            window.amr.move_to(300.0, " mm")
        assert window.amr.position == 300.0
        # 도착하면 차량이 스스로 HOLD(아웃트리거 고정)한다 — AGV 노드와 같다.
        assert sim.state == "HOLD" and sim.hold == "SET"
        # SetJob.radius = 검사 대상 바깥 지름 / 2 [m], job_id 는 RCS-<켠 시각>-0001
        assert sim.radius == pytest.approx(window._outer_radius_mm() / 1000.0) and sim.radius > 0
        assert sim.job.job_id.startswith("RCS-") and sim.job.job_id.endswith("-0001")
        with qtbot.waitSignal(window.outrigger.arrived, timeout=2000):
            window.outrigger.move_to(1, " 고정")          # 이미 고정 — 바로 끝
        assert window._vehicle_lift.speed_mps == pytest.approx(0.2)
        with qtbot.waitSignal(window.lift.arrived, timeout=8000):
            window.lift.move_to(400.0, " mm")          # 자동 — lift_control
        # 도착은 5 mm 안에서 낸다(AGV 노드 허용 오차) — 표시는 리프트가 마저 선 뒤의 값을 기다려 본다.
        qtbot.waitUntil(lambda: window.vehicle.last_status["lift_h"] == pytest.approx(0.4), timeout=3000)
        qtbot.waitUntil(lambda: window.screens["manual"].lift_value.text() == "400 mm", timeout=3000)
        # 수동 화면 리프트 — ManualCommand(cmd_mv_lift · lift_height). 수동 모드에서만 받는다.
        said: list[str] = []
        window.main_screen.show_activity = said.append
        window._manual_lift(0.6)
        qtbot.waitUntil(lambda: any("manual mode required" in text for text in said), timeout=3000)
        sim.set_parameters([Parameter("mode", value=0)])          # 코봇 수동
        window._manual_lift(0.6)
        qtbot.waitUntil(lambda: abs(window.vehicle.last_status["lift_h"] - 0.6) < 1e-6, timeout=5000)
        # 위치 다시 맞추기 — ResetOdometry(x · y · yaw)
        window._reset_vehicle_odometry(1.5, -0.25, 0.3)
        qtbot.waitUntil(lambda: window.vehicle.last_status.get("y") == pytest.approx(-0.25), timeout=3000)
        status = window.vehicle.last_status
        assert status["x"] == pytest.approx(1.5) and status["yaw"] == pytest.approx(0.3)
        qtbot.waitUntil(lambda: any("위치를 x 1.500 m" in text for text in said), timeout=3000)
        # 수동 제어는 차량 상태가 들어오면 열린다. 로봇 태스크가 도는 중이면
        # 안전 인터락이 잠그므로(다른 노드가 500=1 을 내보낼 수 있다) 쉬는 것으로 둔다.
        window._on_robot_task_state(3)
        qtbot.waitUntil(lambda: window.screens["manual"].jog_buttons["cmd_mv_fwd"].isEnabled(),
                        timeout=3000)
    finally:
        _stop(window, sim, executor)


def test_next_drive_sends_finish_after_lowering_the_lift(qtbot, tmp_path, monkeypatch) -> None:
    """작업 끝(RobotControl state FINISH) — 차량은 도착하면 HOLD 하고, FINISH 를 받아야 아웃트리거를 올린다.

    다음 주행 전 순서: 리프트 0 → FINISH(홀딩 해제 · 아웃트리거 올림) → SetJob → (STOP 이 되면) RUNNING → HOLD.
    """
    window, sim, executor = _start(qtbot, tmp_path, monkeypatch, "vf")
    said: list[str] = []
    try:
        with qtbot.waitSignal(window.amr.arrived, timeout=8000):
            window.amr.move_to(300.0, " mm")
        first_job = sim.job.job_id
        with qtbot.waitSignal(window.lift.arrived, timeout=8000):
            window.lift.move_to(400.0, " mm")

        window.main_screen.show_activity = said.append
        with qtbot.waitSignal(window.amr.arrived, timeout=15000):
            window._prepare_to_drive(lambda: window.amr.move_to(600.0, " mm", label="다음 구역"))
        assert window.amr.position == 600.0
        lowered = next(i for i, t in enumerate(said) if "리프트를 내립니다" in t)
        finished = next(i for i, t in enumerate(said) if "작업 끝(finish)" in t)
        assert lowered < finished, "리프트를 내린 뒤에 FINISH 를 보낸다"
        assert sim.lift_h == pytest.approx(0.0)
        assert sim.state == "HOLD" and sim.hold == "SET", "새 도착 — 다시 HOLD"
        assert sim.job.job_id != first_job and sim.job.job_id.endswith("-0002")
        alarms = window.cobot_manual_screen.alarm_list
        texts = [alarms.item(i).text() for i in range(alarms.count())]
        assert not any("E2001" in text for text in texts), texts   # 거절 · 알람 없이 갔다
    finally:
        _stop(window, sim, executor)


def test_pause_resumes_the_same_job_and_stop_cancels_it(qtbot, tmp_path, monkeypatch) -> None:
    """일시정지 = PAUSE(차량에 작업이 남는다) → 재개 = RUNNING(같은 job_id 로 잇는다). 정지 = STOP + job_cancel."""
    window, sim, executor = _start(qtbot, tmp_path, monkeypatch, "vp", drive_speed=0.2)
    try:
        window.amr.move_to(800.0, " mm")
        qtbot.waitUntil(lambda: sim.state == "RUNNING" and sim.mv_dist > 0.1, timeout=5000)
        job = sim.job.job_id
        window.amr.pause()
        qtbot.waitUntil(lambda: sim.state == "PAUSE", timeout=3000)
        stopped_at = sim.mv_dist
        with qtbot.waitSignal(window.amr.arrived, timeout=10000):
            window.amr.move_to(800.0, " mm", label="재개")
        assert sim.job.job_id == job and sim.mv_dist == pytest.approx(0.8) and stopped_at < 0.8

        # 다음 이동을 시작해 놓고 정지 — 차량은 작업을 버린다(다음 set_job 을 받을 수 있게).
        window._prepare_to_drive(lambda: window.amr.move_to(1600.0, " mm"))
        qtbot.waitUntil(lambda: sim.state == "RUNNING", timeout=8000)
        window.amr.cancel()
        qtbot.waitUntil(lambda: sim.job is None and sim.state == "STOP", timeout=3000)
    finally:
        _stop(window, sim, executor)


def test_simulator_follows_the_agv_node_rules(qtbot) -> None:
    """모의기 규칙 = AGV 노드 규칙: 도착 HOLD, HOLD 에선 출발 못 함, FINISH → 새 작업, PAUSE 유지, 모드 · 이름."""
    from vehicle_interfaces.srv import ManualCommand, RobotControl, SetJob, SetMvLift
    context = rclpy.context.Context()
    rclpy.init(context=context)
    sim = vehicle_sim_node.VehicleSim(context=context, parameter_overrides=[
        Parameter("namespace", value=f"vr{int(time.time() * 1000) % 100000}"),
        Parameter("outrigger_s", value=0.1)])

    def job(job_id, dist=0.01):
        return sim.on_set_job(SetJob.Request(job_id=job_id, set_dist=dist, set_speed=0.5, radius=1.25),
                              SetJob.Response())

    def control(**kw):
        return sim.on_control(RobotControl.Request(**kw), RobotControl.Response())

    def ticks(n=10):
        for _ in range(n):
            sim.tick()

    try:
        assert job("J1").success
        assert control(state="RUNNING").success
        ticks()
        assert sim.state == "HOLD" and sim.hold == "SET" and sim.job_done, "도착하면 스스로 HOLD"
        assert not job("J1").success, "같은 job_id 는 다시 받지 않는다"
        assert job("J2").success, "끝난 작업 뒤 HOLD 에서는 등록은 받는다"
        res = control(state="RUNNING")
        assert not res.success and "release outriggers" in res.message
        res = sim.on_lift(SetMvLift.Request(job_id="L1", set_height=0.3, set_speed=0.5), SetMvLift.Response())
        assert not res.success and res.message == "automatic lift speed out of range"
        assert control(state="FINISH").success
        ticks(3)
        assert sim.state == "FINISH" and sim.hold == "RELEASE"
        res = control(state="STOP")
        assert not res.success and res.message == "FINISH waits for a new job or job_cancel"
        assert not control(state="PAUSED").success, "PAUSED 가 아니라 PAUSE"
        assert job("J3", dist=0.5).success
        assert not control(state="RUNNING").success, "FINISH 에서 등록하면 다음 주기에 STOP 이 된다"
        ticks(1)
        assert sim.state == "STOP" and control(state="RUNNING").success
        ticks(1)
        assert control(state="PAUSE").success and sim.state == "PAUSE"
        assert not job("J4").success, "PAUSE 중에는 새 작업을 받지 않는다"
        assert control(state="RUNNING").success
        ticks(1)
        assert control(state="STOP", job_cancel=1).success and sim.job is None and sim.state == "STOP"
        res = sim.on_manual(ManualCommand.Request(cmd_outrg_set=2), ManualCommand.Response())
        assert not res.success and res.message == "manual mode required"
        sim.set_parameters([Parameter("mode", value=1)])
        assert sim.on_manual(ManualCommand.Request(cmd_outrg_set=2), ManualCommand.Response()).success
        assert not job("J5").success, "수동 모드에서는 자동 작업을 받지 않는다"
    finally:
        sim.destroy_node()
        rclpy.shutdown(context=context)
