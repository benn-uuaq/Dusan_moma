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


def test_vehicle_moves_through_the_real_interfaces(qtbot, tmp_path, monkeypatch) -> None:
    ns = f"vt{int(time.time() * 1000) % 100000}"      # 다른 시험·모의기와 섞이지 않게
    monkeypatch.setenv("SMR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SMR_VEHICLE_NS", ns)
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    window.ros_status.start()
    sim = vehicle_sim_node.VehicleSim(parameter_overrides=[
        Parameter("namespace", value=ns), Parameter("outrigger_s", value=0.3),
        Parameter("lift_speed", value=0.5), Parameter("drive_speed", value=0.5)])
    executor = SingleThreadedExecutor()
    executor.add_node(sim)
    threading.Thread(target=executor.spin, daemon=True).start()
    try:
        qtbot.waitUntil(lambda: window.vehicle.online, timeout=5000)
        # 차량 제어 기본값은 '자동' — 상태가 들어오면 스스로 차량으로 붙는다.
        assert window.amr.active == "vehicle"

        with qtbot.waitSignal(window.amr.arrived, timeout=8000):
            window.amr.move_to(300.0, " mm")
        assert window.amr.position == 300.0
        # SetJob.radius = 검사 대상 바깥 지름 / 2 [m] (AGV 판 2026-11-20)
        assert sim.radius == pytest.approx(window._outer_radius_mm() / 1000.0) and sim.radius > 0
        with qtbot.waitSignal(window.outrigger.arrived, timeout=5000):
            window.outrigger.move_to(1, " 고정")
        # 자동 리프트 속도는 RCS 차량 설정 「리프트 속도」가 SetMvLift.set_speed 로 간다.
        vehicle = window.screens["vehicle"]
        vehicle.field(vehicle.LIFT_SPEED_FIELD).setValue(500)
        window._apply_vehicle_settings()
        assert window._vehicle_lift.speed_mps == pytest.approx(0.5)
        with qtbot.waitSignal(window.lift.arrived, timeout=8000):
            window.lift.move_to(400.0, " mm")          # 자동 — set_mv_lift
        assert window.vehicle.last_status["lift_h"] == pytest.approx(0.4)
        assert window.screens["manual"].lift_value.text() == "400 mm"
        window._manual_lift(0.6)                        # 수동 화면 — ManualCommand(cmd_mv_lift · lift_height)
        qtbot.waitUntil(lambda: abs(window.vehicle.last_status["lift_h"] - 0.6) < 1e-6, timeout=5000)
        # 위치 다시 맞추기 — ResetOdometry(x · y · yaw)
        said: list[str] = []
        window.main_screen.show_activity = said.append
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
        executor.shutdown()
        sim.destroy_node()
        window.ros_status.stop()
        window.close()


def test_next_drive_sends_job_finish_after_lowering_the_lift(qtbot, tmp_path, monkeypatch) -> None:
    """작업 끝(RobotControl job_finish, 2026-11-20) — 차량은 도착한 뒤 홀딩을 잡고 finish 를 기다린다.

    다음 주행 전 순서: 리프트 0 → job_finish(홀딩 해제 · 아웃트리거 올림) → SetJob · RUNNING.
    모의기는 finish 없이 온 새 SetJob 을 거절한다(require_finish).
    """
    ns = f"vf{int(time.time() * 1000) % 100000}"
    monkeypatch.setenv("SMR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SMR_VEHICLE_NS", ns)
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    window.ros_status.start()
    sim = vehicle_sim_node.VehicleSim(parameter_overrides=[
        Parameter("namespace", value=ns), Parameter("outrigger_s", value=0.3),
        Parameter("lift_speed", value=0.5), Parameter("drive_speed", value=0.5)])
    executor = SingleThreadedExecutor()
    executor.add_node(sim)
    threading.Thread(target=executor.spin, daemon=True).start()
    said: list[str] = []
    try:
        qtbot.waitUntil(lambda: window.vehicle.online, timeout=5000)
        assert window.amr.active == "vehicle" and window.vehicle.can_finish()
        vehicle = window.screens["vehicle"]
        vehicle.field(vehicle.LIFT_SPEED_FIELD).setValue(500)   # 리프트 0.5 m/s — 시험을 빨리
        window._apply_vehicle_settings()
        with qtbot.waitSignal(window.amr.arrived, timeout=8000):
            window.amr.move_to(300.0, " mm")
        assert sim.finish_wait, "도착한 뒤에는 홀딩을 잡고 finish 를 기다린다"
        with qtbot.waitSignal(window.outrigger.arrived, timeout=5000):
            window.outrigger.move_to(1, " 고정")
        with qtbot.waitSignal(window.lift.arrived, timeout=8000):
            window.lift.move_to(400.0, " mm")

        window.main_screen.show_activity = said.append
        with qtbot.waitSignal(window.amr.arrived, timeout=15000):
            window._prepare_to_drive(lambda: window.amr.move_to(600.0, " mm", label="다음 구역"))
        assert window.amr.position == 600.0
        assert any("작업 끝(finish)" in text for text in said)
        lowered = next(i for i, t in enumerate(said) if "리프트를 내립니다" in t)
        finished = next(i for i, t in enumerate(said) if "작업 끝(finish)" in t)
        assert lowered < finished, "리프트를 내린 뒤에 finish 를 보낸다"
        assert sim.lift_h == pytest.approx(0.0) and sim.hold == "RELEASE"
        assert sim.finish_wait, "새 도착 — 다시 finish 를 기다린다"
        alarms = window.cobot_manual_screen.alarm_list
        texts = [alarms.item(i).text() for i in range(alarms.count())]
        assert not any("E2001" in text for text in texts), texts   # 거절 · 알람 없이 갔다
    finally:
        executor.shutdown()
        sim.destroy_node()
        window.ros_status.stop()
        window.close()


def test_simulator_refuses_a_new_job_until_finish(qtbot) -> None:
    """모의기 규칙 8 — 끝난 작업에 job_finish 를 받기 전에는 새 SetJob 을 거절한다."""
    from vehicle_interfaces.srv import RobotControl, SetJob
    context = rclpy.context.Context()
    rclpy.init(context=context)
    sim = vehicle_sim_node.VehicleSim(context=context, parameter_overrides=[
        Parameter("namespace", value=f"vr{int(time.time() * 1000) % 100000}")])
    try:
        res = sim.on_set_job(SetJob.Request(job_id="J1", set_dist=0.01, set_speed=1.0), SetJob.Response())
        assert res.success
        sim.on_control(RobotControl.Request(state="RUNNING"), RobotControl.Response())
        for _ in range(20):
            sim.tick()
        assert sim.state == "STOP" and sim.finish_wait
        res = sim.on_set_job(SetJob.Request(job_id="J2", set_dist=0.01, set_speed=1.0), SetJob.Response())
        assert not res.success and "job_finish" in res.message
        res = sim.on_control(RobotControl.Request(job_finish=1), RobotControl.Response())
        assert res.success and res.message == "Job finished — hold released"
        assert not sim.finish_wait and sim.job is None
        res = sim.on_control(RobotControl.Request(job_finish=1), RobotControl.Response())
        assert res.success and res.message == "No job — ready", "서 있으면 끝낼 작업이 없어도 받는다"
        res = sim.on_set_job(SetJob.Request(job_id="J2", set_dist=0.01, set_speed=1.0), SetJob.Response())
        assert res.success
        sim.on_control(RobotControl.Request(state="RUNNING"), RobotControl.Response())
        res = sim.on_control(RobotControl.Request(job_finish=1), RobotControl.Response())
        assert not res.success and res.message == "Rejected: moving"
    finally:
        sim.destroy_node()
        rclpy.shutdown(context=context)
