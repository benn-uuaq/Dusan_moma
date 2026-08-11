from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton, QTableWidget

from smr_operator_ui.app import OperatorWindow
from smr_operator_ui.screens import CobotSettingsScreen


def test_mode_slot_buttons_are_not_clipped(qtbot) -> None:
    """세 줄짜리 슬롯 문구가 잘리지 않아야 한다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.resize(1280, 720)
    window.navigate("modes")
    window.show()
    qtbot.waitExposed(window)

    clipped = [
        b.text().replace("\n", " ")
        for b in window.screens["modes"].findChildren(QPushButton)
        if b.text().startswith("슬롯") and b.height() < b.minimumSizeHint().height()
    ]
    assert clipped == []
    window.close()


def test_io_controls_are_outside_the_table(qtbot) -> None:
    """조작 버튼은 표 칸이 아니라 별도 출력 제어 영역에 둔다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.screens["io"]
    table = screen.findChild(QTableWidget)

    assert table.columnCount() == 5
    embedded = [
        (r, c)
        for r in range(table.rowCount())
        for c in range(table.columnCount())
        if table.cellWidget(r, c) is not None
    ]
    assert embedded == []
    window.close()


def test_io_screen_controls_only_outputs(qtbot) -> None:
    """입력 신호에는 조작 버튼을 두지 않는다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.screens["io"]

    outputs = {row[0] for row in screen._ROWS if row[2] == "OUT"}
    inputs = {row[0] for row in screen._ROWS if row[2] != "OUT"}
    assert set(screen.output_state_labels) == outputs
    assert not (set(screen.output_state_labels) & inputs)
    window.close()


def _output_buttons(screen, address):
    """해당 출력 신호 카드 안의 ON/OFF 버튼을 찾는다."""
    card = screen.output_state_labels[address].parent()
    return card.findChildren(QPushButton)


def test_io_output_buttons_emit_address_and_state(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.show()
    screen = window.screens["io"]
    received: list[tuple[str, bool]] = []
    screen.output_requested.connect(lambda a, on: received.append((a, on)))

    on_button, off_button = _output_buttons(screen, "Y000")
    qtbot.mouseClick(on_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(off_button, Qt.MouseButton.LeftButton)
    # 다른 신호의 버튼이 섞이지 않아야 한다.
    other_on, _ = _output_buttons(screen, "Y010")
    qtbot.mouseClick(other_on, Qt.MouseButton.LeftButton)

    assert received == [("Y000", True), ("Y000", False), ("Y010", True)]
    window.close()


def test_io_value_update_reaches_table_and_controls(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.screens["io"]

    screen.set_value("Y000", "ON")

    assert screen.value_items["Y000"].text() == "ON"
    assert "ON" in screen.output_state_labels["Y000"].text()
    window.close()


def test_cobot_settings_has_linear_speed(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.screens["cobot"]

    assert CobotSettingsScreen.SPEED_FIELD in screen.values()
    assert screen.linear_speed() == 150
    window.close()


def test_jog_screen_has_all_axes(qtbot) -> None:
    """관절 6축과 TCP 6축 각각에 + / - 버튼이 있어야 한다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.cobot_jog_screen

    assert len(screen.jog_buttons) == 24
    for kind in ("joint", "tcp"):
        for axis in range(6):
            for direction in (1, -1):
                assert (kind, axis, direction) in screen.jog_buttons
    window.close()


def test_jog_press_and_release_are_reported(qtbot) -> None:
    """조그는 누르는 동안 움직이므로 누름과 뗌을 모두 알려야 한다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.show()
    screen = window.cobot_jog_screen
    pressed: list[tuple[str, int, int]] = []
    released: list[tuple[str, int]] = []
    screen.jog_pressed.connect(lambda k, a, d: pressed.append((k, a, d)))
    screen.jog_released.connect(lambda k, a: released.append((k, a)))
    # 주소가 없으면 버튼이 잠겨 눌리지 않으므로 먼저 열어 준다.
    screen.set_enabled_commands({"jog_tcp"})

    button = screen.jog_buttons[("tcp", 2, -1)]
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)

    assert pressed == [("tcp", 2, -1)]
    assert released == [("tcp", 2)]
    window.close()


def test_commands_are_disabled_until_address_is_known(qtbot) -> None:
    """레지스터 주소가 없으면 버튼을 잠가 엉뚱한 곳에 쓰지 않게 한다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.cobot_jog_screen

    screen.set_enabled_commands(set())
    assert all(not b.isEnabled() for b in screen.save_buttons.values())
    assert all(not b.isEnabled() for b in screen.jog_buttons.values())

    screen.set_enabled_commands({"save_home_pose", "jog_tcp"})
    assert screen.save_buttons["save_home_pose"].isEnabled()
    assert not screen.save_buttons["save_start_pose"].isEnabled()
    assert screen.jog_buttons[("tcp", 0, 1)].isEnabled()
    assert not screen.jog_buttons[("joint", 0, 1)].isEnabled()
    window.close()


def test_current_position_follows_tcp_topic(qtbot) -> None:
    """조그 화면의 현재 위치도 TCP 토픽을 따라가야 한다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)

    window.ros_status.tcp_pose_changed.emit([120.5, -340.0, 500.0, 1571.0, 0.0, -3141.0])

    rows = window.cobot_jog_screen.position_rows
    assert rows["x"].value_label.text() == "120.5"
    assert rows["rz"].value_label.text() == "-3141.0"
    window.close()
