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
    window = OperatorWindow(start_mqtt=False, start_ros=False)
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
        with qtbot.waitSignal(window.outrigger.arrived, timeout=5000):
            window.outrigger.move_to(1, " 고정")
        with qtbot.waitSignal(window.lift.arrived, timeout=8000):
            window.lift.move_to(400.0, " mm")
        assert window.vehicle.last_status["lift_h"] == pytest.approx(0.4)
        assert window.screens["manual"].lift_value.text() == "400 mm"
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
