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
    window.ros_status.tcp_pose_changed.emit(
        [636.8, -47.3, 581.0, 3141.0, 0.0, -1570.0]
    )

    assert screen.joint_values[0].text() == "207.0"
    assert screen.joint_values[2].text() == "-1875.0"
    assert screen.tcp_values["x"].text() == "636.8"
    assert screen.tcp_values["rz"].text() == "-1570.0"
    window.close()


def test_saving_reference_poses(qtbot, tmp_path, monkeypatch) -> None:
    """홈은 관절값을, 시작 포즈는 TCP 값을 기록한다."""
    monkeypatch.setenv("SMR_ROBOT_CONFIG_DIR", str(tmp_path))
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.cobot_jog_screen

    # 값을 받기 전에는 기록할 것이 없다.
    screen.command_requested.emit("save_home_pose")
    assert "저장된 값 없음" in screen.saved_labels["save_home_pose"].text()

    window.ros_status.joint_position_changed.emit(
        [207.0, -1466.0, -1875.0, -1371.0, 1570.0, 207.0]
    )
    window.ros_status.tcp_pose_changed.emit([636.8, -47.3, 581.0, 3141.0, 0.0, -1570.0])

    screen.command_requested.emit("save_home_pose")
    home = screen.saved_labels["save_home_pose"].text()
    assert "J1 207.0" in home and "J3 -1875.0" in home

    screen.command_requested.emit("save_start_pose")
    start = screen.saved_labels["save_start_pose"].text()
    assert "X 636.8" in start and "RZ -1570.0" in start

    # 로봇 쪽 설정 폴더에도 남아야 다음 기동에서 다시 쓸 수 있다.
    import json
    saved = json.loads((tmp_path / "reference_poses.json").read_text(encoding="utf-8"))
    assert saved["home_joint"]["values"][0] == 207.0
    assert saved["start_pose"]["values"][0] == 636.8
    window.close()


def test_jog_code_encodes_axis_and_direction(qtbot) -> None:
    """조그 코드는 부호가 방향, 절댓값이 축 번호(1~6)이며 0은 정지다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    sent: list[tuple[str, int, int]] = []
    window.ros_status.send_jog = lambda kind, axis, direction: sent.append(
        (kind, axis, direction)
    ) or True

    window.cobot_jog_screen.jog_pressed.emit("joint", 2, 1)
    window.cobot_jog_screen.jog_released.emit("joint", 2)
    window.cobot_jog_screen.jog_pressed.emit("tcp", 5, -1)

    assert sent == [("joint", 2, 1), ("joint", 0, 0), ("tcp", 5, -1)]
    window.close()


def test_speed_ratio_is_limited_to_robot_range(qtbot) -> None:
    """로봇 자체 속도 비율은 2~100 %만 허용한다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.screens["cobot"]

    assert CobotSettingsScreen.RATIO_FIELD in screen.values()
    assert screen.speed_ratio() == 100
    widget = screen._fields[CobotSettingsScreen.RATIO_FIELD]
    assert widget.minimum() == 2
    assert widget.maximum() == 100
    window.close()
