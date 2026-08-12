import pytest

from PyQt6.QtCore import Qt

from smr_operator_ui.app import OperatorWindow
from smr_operator_ui.services import MqttServer, MqttTopics
from smr_operator_ui.state import CyclePhase


def test_start_and_pause_cycle(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.show()
    qtbot.mouseClick(window.main_screen.start_button, Qt.MouseButton.LeftButton)
    assert window.simulator.snapshot.cycle.running is True
    assert window.simulator.snapshot.cycle.phase is CyclePhase.SECURING
    qtbot.mouseClick(window.main_screen.pause_button, Qt.MouseButton.LeftButton)
    assert window.simulator.snapshot.cycle.paused is True
    assert window.simulator.snapshot.cycle.phase is CyclePhase.PAUSED
    window.close()


def test_manual_navigation(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.show()
    window.main_screen.manual_requested.emit()
    assert window.stack.currentWidget() is window.screens["manual"]
    window.screens["manual"].back_requested.emit()
    assert window.stack.currentWidget() is window.main_screen
    window.close()


def test_back_returns_to_previous_screen(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.show()
    window.navigate("settings")
    window.navigate("system")
    window.screens["system"].back_requested.emit()
    assert window.stack.currentWidget() is window.screens["settings"]
    window.screens["settings"].back_requested.emit()
    assert window.stack.currentWidget() is window.main_screen
    assert list(window._navigation_history) == []
    window.close()


def test_direct_main_navigation_clears_history(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.show()
    window.navigate("settings")
    window.navigate("system")

    window.navigate("main")

    assert window.stack.currentWidget() is window.main_screen
    assert list(window._navigation_history) == []
    window.close()


def test_mqtt_job_command_updates_target_dimensions(qtbot, monkeypatch) -> None:
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    saved: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        window.settings_service,
        "save",
        lambda scope, values: saved.append((scope, values)),
    )

    mqtt_server.command_received.emit(
        MqttTopics.JOB_COMMAND,
        {
            "timestamp": "1784727779111",
            "job_id": "jb00000001",
            "job_info": {
                "diameter": "2500",
                "height": "6000",
                "thickness": "500",
                "target_distance": "8560",
            },
        },
    )

    assert window.main_screen.orbit_view.target_dimensions() == (2.5, 6.0)
    assert saved[0][0] == "inspection_target"
    assert saved[0][1]["job_id"] == "jb00000001"
    assert saved[0][1]["thickness_m"] == 0.5
    window.close()


def test_mqtt_job_command_applies_grid_cell(qtbot, monkeypatch) -> None:
    """원통이 커서 AMR 구역 + Cobot 세로 격자로 나눠 스캔한다.

    grid 블록이 오면 OrbitView의 AMR 위치, RectWorkView의 격자 치수·이름이
    갱신되고, 값이 로봇(ROS)과 저장소(DB) 양쪽으로 전달되어야 한다.
    """
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    saved: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        window.settings_service, "save",
        lambda scope, values: saved.append((scope, values)),
    )
    sent_poses: list[tuple[str, list]] = []
    monkeypatch.setattr(
        window.ros_status, "send_pose",
        lambda name, values: sent_poses.append((name, list(values))) or True,
    )

    mqtt_server.command_received.emit(
        MqttTopics.JOB_COMMAND,
        {
            "timestamp": "1784727779111",
            "job_id": "jb00000001",
            "job_info": {
                "diameter": "2500", "height": "6000",
                "thickness": "500", "target_distance": "8560",
            },
            "grid": {
                "cell_id": "A0", "segment_index": "1", "segment_count": "12",
                "grid_index": "0", "grid_count": "9",
                "width": "600", "height": "800", "scan_h": "150", "overlap": "20",
            },
        },
    )

    assert window.simulator.snapshot.cycle.current_segment == 1
    assert window.simulator.snapshot.cycle.total_segments == 12
    assert window.main_screen.rect_view.work_area() == (600.0, 800.0, 150.0, 20.0)
    assert "A0" in window.main_screen.rect_view._cell_label
    assert ("work_area", {"width_mm": 600.0, "height_mm": 800.0,
                          "scan_h_mm": 150.0, "overlap_mm": 20.0}) in saved
    assert sent_poses == [("work_area", [600.0, 800.0, 150.0, 20.0])]
    window.close()


def test_mqtt_job_command_without_grid_keeps_previous_cell(qtbot) -> None:
    """옛 payload(격자 없음)를 받아도 검사대상 처리는 계속 되고 죽지 않는다."""
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)

    mqtt_server.command_received.emit(
        MqttTopics.JOB_COMMAND,
        {
            "timestamp": "1784727779111",
            "job_id": "jb00000001",
            "job_info": {
                "diameter": "2500", "height": "6000",
                "thickness": "500", "target_distance": "8560",
            },
        },
    )

    assert window.main_screen.orbit_view.target_dimensions() == (2.5, 6.0)
    window.close()


def test_mqtt_amr_run_starts_inspection(qtbot) -> None:
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)

    mqtt_server.command_received.emit(
        MqttTopics.MC_COMMAND,
        {
            "timestamp": "1784727720000",
            "amr": "run",
            "cobot": "stop",
        },
    )

    assert window.simulator.snapshot.cycle.running is True
    assert window.simulator.snapshot.cycle.phase is CyclePhase.SECURING
    window.close()


@pytest.mark.parametrize("amr_command", ["stop", "ems"])
def test_mqtt_amr_stop_or_ems_pauses_inspection(
    qtbot,
    amr_command,
) -> None:
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.simulator.start_cycle()

    payload = {
        "timestamp": "1784727720000",
        "amr": amr_command,
        "cobot": "stop",
    }
    mqtt_server.command_received.emit(MqttTopics.MC_COMMAND, payload)

    assert window.simulator.snapshot.cycle.running is True
    assert window.simulator.snapshot.cycle.paused is True
    assert window.simulator.snapshot.cycle.phase is CyclePhase.PAUSED

    # QoS 1 재전송으로 같은 명령을 다시 받아도 검사가 재개되지 않아야 한다.
    mqtt_server.command_received.emit(MqttTopics.MC_COMMAND, payload)
    assert window.simulator.snapshot.cycle.paused is True
    assert window.simulator.snapshot.cycle.phase is CyclePhase.PAUSED
    window.close()
