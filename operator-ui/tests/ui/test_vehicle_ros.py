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
