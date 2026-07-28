import pytest

from PyQt6.QtCore import Qt

from smr_operator_ui.app import OperatorWindow
from smr_operator_ui.services import MqttServer, MqttTopics
from smr_operator_ui.state import CyclePhase


def test_start_and_pause_cycle(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False)
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
    window = OperatorWindow(start_mqtt=False)
    qtbot.addWidget(window)
    window.show()
    window.main_screen.manual_requested.emit()
    assert window.stack.currentWidget() is window.screens["manual"]
    window.screens["manual"].back_requested.emit()
    assert window.stack.currentWidget() is window.main_screen
    window.close()


def test_back_returns_to_previous_screen(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False)
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
    window = OperatorWindow(start_mqtt=False)
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
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False)
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


def test_mqtt_amr_run_starts_inspection(qtbot) -> None:
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False)
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
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False)
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
