from PyQt6.QtCore import Qt

from smr_operator_ui.app import OperatorWindow
from smr_operator_ui.state import CyclePhase


def test_start_and_pause_cycle(qtbot) -> None:
    window = OperatorWindow()
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
    window = OperatorWindow()
    qtbot.addWidget(window)
    window.show()
    window.main_screen.manual_requested.emit()
    assert window.stack.currentWidget() is window.screens["manual"]
    window.screens["manual"].back_requested.emit()
    assert window.stack.currentWidget() is window.main_screen
    window.close()


def test_back_returns_to_previous_screen(qtbot) -> None:
    window = OperatorWindow()
    qtbot.addWidget(window)
    window.show()
    window.navigate("settings")
    window.navigate("system")
    window.screens["system"].back_requested.emit()
    assert window.stack.currentWidget() is window.screens["settings"]
    window.screens["settings"].back_requested.emit()
    assert window.stack.currentWidget() is window.main_screen
    window.close()
