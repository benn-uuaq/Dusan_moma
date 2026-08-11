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

    outputs = {s["address"] for s in screen.signals if s["direction"] == "OUT"}
    inputs = {s["address"] for s in screen.signals if s["direction"] != "OUT"}
    assert set(screen.output_buttons) == outputs
    assert not (set(screen.output_buttons) & inputs)
    window.close()


def test_io_output_buttons_emit_address_and_state(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.show()
    screen = window.screens["io"]
    received: list[tuple[str, bool]] = []
    screen.output_requested.connect(lambda a, on: received.append((a, on)))

    on_button, off_button = screen.output_buttons["Y000"]
    qtbot.mouseClick(on_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(off_button, Qt.MouseButton.LeftButton)
    # 다른 신호의 버튼이 섞이지 않아야 한다.
    other_on, _ = screen.output_buttons["Y010"]
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


def test_jog_is_disabled_until_address_is_known(qtbot) -> None:
    """조그는 로봇을 움직이므로 주소가 없으면 잠근다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.cobot_jog_screen

    screen.set_enabled_commands(set())
    assert all(not b.isEnabled() for b in screen.jog_buttons.values())

    screen.set_enabled_commands({"jog_tcp"})
    assert screen.jog_buttons[("tcp", 0, 1)].isEnabled()
    assert not screen.jog_buttons[("joint", 0, 1)].isEnabled()
    window.close()


def test_axis_values_follow_topics(qtbot) -> None:
    """관절값과 TCP값이 각 축 이름 옆에 표시되어야 한다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.cobot_jog_screen

    window.ros_status.joint_position_changed.emit(
        [207.0, -1466.0, -1875.0, -1371.0, 1570.0, 207.0]
    )
    window.ros_status.tcp_pose_base_changed.emit(
        [636.8, -47.3, 581.0, 3141.0, 0.0, -1570.0]
    )

    assert screen.joint_values[0].text() == "207.0"
    assert screen.joint_values[2].text() == "-1875.0"
    assert screen.tcp_values["x"].text() == "636.8"
    assert screen.tcp_values["rz"].text() == "-1570.0"
    window.close()


def test_saving_reference_pose_records_current_tcp(qtbot) -> None:
    """저장 버튼은 현재 TCP 값을 기준 위치로 기록해 보여준다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.cobot_jog_screen

    # 값을 받기 전에는 기록할 것이 없다.
    screen.command_requested.emit("save_home_pose")
    assert "저장된 값 없음" in screen.saved_labels["save_home_pose"].text()

    window.ros_status.tcp_pose_base_changed.emit(
        [636.8, -47.3, 581.0, 3141.0, 0.0, -1570.0]
    )
    screen.command_requested.emit("save_home_pose")

    saved = screen.saved_labels["save_home_pose"].text()
    assert "636.8" in saved and "-1570.0" in saved
    # 다른 기준 위치는 영향을 받지 않는다.
    assert "저장된 값 없음" in screen.saved_labels["save_start_pose"].text()
    window.close()
