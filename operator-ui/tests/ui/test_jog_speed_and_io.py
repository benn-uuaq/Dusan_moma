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
    assert screen.linear_speed() == 100
    window.close()


def test_cobot_settings_has_no_task_picker(qtbot) -> None:
    """태스크는 로봇 쪽에서 고정이라 여기서 고르는 목록이 없어야 한다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.screens["cobot"]

    assert "선택 작업" not in screen.values()
    assert screen.task_status_label.text() == "확인 전"
    window.close()


def test_task_status_refresh_reaches_the_service_and_back(qtbot) -> None:
    """새로고침 버튼 → 29999 task_status 서비스 호출 → 응답이 라벨에 반영된다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.screens["cobot"]
    calls: list[str] = []
    window.ros_status.call_command = lambda name: calls.append(name) or True

    refresh_button = next(b for b in screen.findChildren(QPushButton) if b.text() == "새로고침")
    qtbot.mouseClick(refresh_button, Qt.MouseButton.LeftButton)
    assert "task_status" in calls

    window._show_command_result("task_status", True, "[task -s] Result: Task is running")
    assert screen.task_status_label.text() == "Task is running"

    window._show_command_result("task_status", False, "ROS 2에 연결되어 있지 않습니다.")
    assert "실패" in screen.task_status_label.text()
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


def test_jog_enables_on_robot_connection_not_register_address(qtbot) -> None:
    """조그는 Modbus 레지스터가 아니라 30001 소켓을 쓰므로, 레지스터

    주소 유무가 아니라 로봇 연결 여부로 잠기고 풀려야 한다. modbus_registers.
    json에는 jog_joint/jog_tcp 항목이 아예 없다(30001로 처리하기 때문) —
    예전에는 그래서 `writable("jog_joint")`가 항상 False가 되어 실제
    연결과 무관하게 버튼이 영영 잠겨 있었다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.cobot_jog_screen

    # 시작 시점(로봇 미연결)에는 잠겨 있어야 한다.
    assert all(not b.isEnabled() for b in screen.jog_buttons.values())

    window._update_jog_enabled(True)
    assert screen.jog_buttons[("tcp", 0, 1)].isEnabled()
    assert screen.jog_buttons[("joint", 0, 1)].isEnabled()

    window._update_jog_enabled(False)
    assert all(not b.isEnabled() for b in screen.jog_buttons.values())
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


def test_saved_poses_are_shown_on_start(qtbot, tmp_path, monkeypatch) -> None:
    """저장 파일이 있으면 UI를 다시 열어도 기준 위치가 보여야 한다."""
    import json

    monkeypatch.setenv("SMR_ROBOT_CONFIG_DIR", str(tmp_path))
    (tmp_path / "reference_poses.json").write_text(
        json.dumps({
            "home_joint": {"values": [207.0, -1466.0, -1875.0, -1371.0, 1570.0, 207.0],
                           "saved_at": "2026-08-11T16:42:08"},
        }),
        encoding="utf-8",
    )

    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)

    home = window.cobot_jog_screen.saved_labels["save_home_pose"].text()
    assert "J1 207.0" in home
    assert "2026-08-11 16:42" in home
    window.close()


def test_reconnect_rewrites_volatile_registers(qtbot, tmp_path, monkeypatch) -> None:
    """레지스터는 전원을 내리면 사라지므로 연결될 때 다시 써야 한다."""
    import json

    monkeypatch.setenv("SMR_ROBOT_CONFIG_DIR", str(tmp_path))
    (tmp_path / "reference_poses.json").write_text(
        json.dumps({
            "home_joint": {"values": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "saved_at": "x"},
            "start_pose": {"values": [10.0, 20.0, 30.0, 0.0, 0.0, 0.0], "saved_at": "x"},
        }),
        encoding="utf-8",
    )

    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    poses: list[tuple[str, list]] = []
    values: list[tuple[str, int]] = []
    window.ros_status.send_pose = lambda name, v: poses.append((name, list(v))) or True
    window.ros_status.send_value = lambda name, v: values.append((name, v)) or True

    # 끊긴 상태에서는 아무것도 보내지 않는다.
    window.ros_status.connected_changed.emit(False)
    assert poses == [] and values == []

    window.ros_status.connected_changed.emit(True)

    assert ("home_joint", [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]) in poses
    assert ("start_pose", [10.0, 20.0, 30.0, 0.0, 0.0, 0.0]) in poses
    # 속도 설정도 같은 이유로 다시 올린다.
    assert ("linear_speed", 100) in values
    assert ("speed_ratio", 100) in values
    window.close()


def test_jog_sends_once_and_stops_only_on_release(qtbot) -> None:
    """되풀이해 재전송하면 로봇이 매번 새 스크립트를 실행해 덜컹거리므로

    누르는 동안(held)에는 다시 보내지 않고 딱 한 번만 보낸다. 계속
    움직이는 건 speedj/speedl의 hold 시간이 맡고, 실제 정지는 뗄 때
    나가는 jog_released(→29999 stop) 하나로만 이뤄져야 한다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.cobot_jog_screen
    sent: list[tuple[str, int, int]] = []
    released: list[tuple[str, int]] = []
    screen.jog_pressed.connect(lambda k, a, d: sent.append((k, a, d)))
    screen.jog_released.connect(lambda k, a: released.append((k, a)))

    screen._on_jog_pressed("tcp", 0, 1)
    assert sent == [("tcp", 0, 1)]

    # 누르고 있는 동안 아무리 기다려도 다시 나가면 안 된다.
    qtbot.wait(300)
    assert sent == [("tcp", 0, 1)]
    assert released == []

    screen._on_jog_released("tcp", 0)
    assert released == [("tcp", 0)]
    window.close()


def test_robot_output_button_reaches_the_robot(qtbot) -> None:
    """로봇 DO 버튼은 제어 노드로 [번호, 값]이 나가야 한다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.screens["io"]
    sent: list[tuple[int, bool]] = []
    window.ros_status.send_digital_out = lambda index, on: sent.append((index, on)) or True

    on_button, off_button = screen.output_buttons["DO1"]
    qtbot.mouseClick(on_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(off_button, Qt.MouseButton.LeftButton)

    assert sent == [(1, True), (1, False)]
    window.close()


def test_plc_output_button_says_it_is_not_wired_yet(qtbot) -> None:
    """차량 PLC 출력은 아직 경로가 없다 — 로봇으로 보내면 안 된다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.screens["io"]
    sent: list[tuple[int, bool]] = []
    window.ros_status.send_digital_out = lambda index, on: sent.append((index, on)) or True

    qtbot.mouseClick(screen.output_buttons["Y000"][0], Qt.MouseButton.LeftButton)

    assert sent == []
    assert "PLC" in screen.activity_label.text()
    window.close()


def test_robot_io_values_follow_the_bit_word(qtbot) -> None:
    """레지스터 한 개(16비트)가 DO/DI 표시를 한꺼번에 갱신한다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.screens["io"]

    window.ros_status.digital_out_changed.emit(0b0000_0110)
    window.ros_status.digital_in_changed.emit(0b0000_0001)

    assert screen.value_items["DO0"].text() == "OFF"
    assert screen.value_items["DO1"].text() == "ON"
    assert screen.value_items["DO2"].text() == "ON"
    assert "ON" in screen.output_state_labels["DO1"].text()
    assert screen.value_items["DI0"].text() == "ON"
    assert screen.value_items["DI1"].text() == "OFF"
    # 로봇 값이 PLC 신호를 건드리면 안 된다.
    assert screen.value_items["Y000"].text() == "OFF"
    window.close()


def test_robot_output_failure_is_reported_on_screen(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.screens["io"]
    window.ros_status.send_digital_out = lambda index, on: False

    qtbot.mouseClick(screen.output_buttons["DO0"][0], Qt.MouseButton.LeftButton)

    assert "연결" in screen.activity_label.text()
    window.close()
