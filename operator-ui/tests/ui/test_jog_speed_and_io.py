from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton, QTableWidget

from smr_operator_ui.app import OperatorWindow
from smr_operator_ui.screens import CobotSettingsScreen


def test_mode_slot_buttons_are_not_clipped(qtbot) -> None:
    """세 줄짜리 슬롯 문구가 잘리지 않아야 한다."""
    window = OperatorWindow(start_ros=False, start_erut=False)
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


def test_io_screen_is_monitoring_only(qtbot) -> None:
    """I/O 화면은 조회만 한다 — 조작 버튼 · 출력 제어가 없다(2026-11-12, 수동 제어 화면에 있다)."""
    from PyQt6.QtWidgets import QPushButton
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    screen = window.screens["io"]
    table = screen.findChild(QTableWidget)

    assert table.columnCount() == 5
    assert all(s["direction"] == "IN" for s in screen.signals)
    buttons = [b.text() for b in screen.findChildren(QPushButton)]
    assert "ON" not in buttons and "OFF" not in buttons
    assert not hasattr(screen, "output_requested")
    window.close()


def test_io_list_keeps_only_holder_sensors_and_vehicle_state(qtbot) -> None:
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    screen = window.screens["io"]
    addresses = [s["address"] for s in screen.signals]
    assert addresses == ["TDI0", "TDI1", "TDI2", "CI0", "CI1", "CI2", "CI3", "SAFETY",
                         "ESTOP", "SCAN", "HOLD", "LIFT", "LEVEL", "BATT", "STANDOFF", "STATE"]
    window.close()


def test_vehicle_status_reaches_the_io_list(qtbot) -> None:
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    screen = window.screens["io"]
    window.vehicle.status_changed.emit({
        "state": "HOLD", "hold": "SET", "error_code": 0, "lift_h": 1.2,
        "roll": 0.0, "pitch": 0.01745, "estop": False})
    value = lambda a: screen.value_items[a].text()        # noqa: E731
    state = lambda a: screen.status_items[a].text()       # noqa: E731
    assert value("HOLD") == "고정" and value("LIFT") == "1,200 mm"
    assert value("ESTOP") == "OFF" and state("ESTOP") == "정상"
    assert "pitch 1.00°" in value("LEVEL")
    assert value("SCAN") == "-" and state("SCAN") == "모름"     # 차량이 아직 모르는 값
    window.vehicle.status_changed.emit({"state": "ERROR", "error_code": 21, "estop": True})
    assert value("STATE") == "ERROR · 오류 21" and state("STATE") == "오류"
    assert state("ESTOP") == "정지"
    window.vehicle.online_changed.emit(False)              # 차량 상태가 끊겼다
    assert value("HOLD") == "-" and state("HOLD") == "수신 없음"
    window.close()


def test_cobot_settings_has_linear_speed(qtbot) -> None:
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    screen = window.screens["cobot"]

    assert CobotSettingsScreen.SPEED_FIELD in screen.values()
    assert screen.linear_speed() == 100
    window.close()


def test_cobot_settings_has_no_task_picker(qtbot) -> None:
    """태스크는 로봇 쪽에서 고정이라 여기서 고르는 목록이 없어야 한다."""
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    screen = window.screens["cobot"]

    assert "선택 작업" not in screen.values()
    assert screen.task_status_label.text() == "확인 전"
    window.close()


def test_task_status_refresh_reaches_the_service_and_back(qtbot) -> None:
    """새로고침 버튼 → 29999 task_status 서비스 호출 → 응답이 라벨에 반영된다."""
    window = OperatorWindow(start_ros=False, start_erut=False)
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
    window = OperatorWindow(start_ros=False, start_erut=False)
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
    window = OperatorWindow(start_ros=False, start_erut=False)
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
    window = OperatorWindow(start_ros=False, start_erut=False)
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
    window = OperatorWindow(start_ros=False, start_erut=False)
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
    window = OperatorWindow(start_ros=False, start_erut=False)
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
    window = OperatorWindow(start_ros=False, start_erut=False)
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
    window = OperatorWindow(start_ros=False, start_erut=False)
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
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    screen = window.screens["cobot"]

    assert CobotSettingsScreen.RATIO_FIELD in screen.values()
    assert screen.speed_ratio() == 100
    widget = screen._fields[CobotSettingsScreen.RATIO_FIELD]
    assert widget.minimum() == 2
    assert widget.maximum() == 100
    window.close()


def test_saved_poses_are_shown_on_start(qtbot, tmp_path, monkeypatch) -> None:
    """저장 파일이 있으면 UI를 다시 열어도 기준 위치가 보여야 한다.

    형식 판이 없는 옛 파일의 home_joint 는 그때의 「홈」 = 지금의 **스캔 대기
    위치**다(2026-10-16). 대기 위치로 옮겨 보여 주고 파일도 고쳐 둔다.
    """
    import json

    monkeypatch.setenv("SMR_ROBOT_CONFIG_DIR", str(tmp_path))
    (tmp_path / "reference_poses.json").write_text(
        json.dumps({
            "home_joint": {"values": [207.0, -1466.0, -1875.0, -1371.0, 1570.0, 207.0],
                           "saved_at": "2026-08-11T16:42:08"},
        }),
        encoding="utf-8",
    )

    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)

    labels = window.cobot_jog_screen.saved_labels
    standby = labels["save_standby_pose"].text()
    assert "J1 207.0" in standby
    assert "2026-08-11 16:42" in standby
    assert "저장된 값 없음" in labels["save_home_pose"].text()
    saved = json.loads((tmp_path / "reference_poses.json").read_text(encoding="utf-8"))
    assert saved["_layout"] == 2 and "home_joint" not in saved
    assert saved["standby_joint"]["values"][0] == 207.0
    window.close()


def test_home_and_standby_are_saved_separately(qtbot, tmp_path, monkeypatch) -> None:
    """홈 위치 저장 -> home_joint(322~327), 스캔 대기 위치 저장 -> standby_joint(310~315)."""
    import json

    monkeypatch.setenv("SMR_ROBOT_CONFIG_DIR", str(tmp_path))
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    sent: list[tuple[str, list]] = []
    window.ros_status.send_pose = lambda name, v: sent.append((name, list(v))) or True
    screen = window.cobot_jog_screen
    assert set(screen.save_buttons) == {"save_home_pose", "save_standby_pose", "save_start_pose",
                                        "save_assembly_pose"}

    window.ros_status.joint_position_changed.emit([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    screen.command_requested.emit("save_standby_pose")
    window.ros_status.joint_position_changed.emit([7.0, 8.0, 9.0, 10.0, 11.0, 12.0])
    screen.command_requested.emit("save_home_pose")

    assert sent == [("standby_joint", [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]),
                    ("home_joint", [7.0, 8.0, 9.0, 10.0, 11.0, 12.0])]
    assert "J1 1.0" in screen.saved_labels["save_standby_pose"].text()
    assert "J1 7.0" in screen.saved_labels["save_home_pose"].text()
    saved = json.loads((tmp_path / "reference_poses.json").read_text(encoding="utf-8"))
    assert saved["standby_joint"]["values"][0] == 1.0
    assert saved["home_joint"]["values"][0] == 7.0
    window.close()

    # 새 형식 파일은 다시 열어도 홈을 대기 위치로 옮기지 않는다.
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    assert "J1 7.0" in window.cobot_jog_screen.saved_labels["save_home_pose"].text()
    assert "J1 1.0" in window.cobot_jog_screen.saved_labels["save_standby_pose"].text()
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

    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    poses: list[tuple[str, list]] = []
    values: list[tuple[str, int]] = []
    window.ros_status.send_pose = lambda name, v: poses.append((name, list(v))) or True
    window.ros_status.send_value = lambda name, v: values.append((name, v)) or True

    # 끊긴 상태에서는 아무것도 보내지 않는다.
    window.ros_status.connected_changed.emit(False)
    assert poses == [] and values == []

    window.ros_status.connected_changed.emit(True)

    # 옛 파일의 home_joint 는 스캔 대기 위치로 옮겨져 310~315 로 간다(예전과 같은 자리).
    assert ("standby_joint", [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]) in poses
    assert ("start_pose", [10.0, 20.0, 30.0, 0.0, 0.0, 0.0]) in poses
    # 저장 안 한(지운) 홈은 0 으로 비운다 — 로봇은 펜던트 홈을 쓴다(2026-11-12).
    assert [v for name, v in poses if name == "home_joint"] == [[0] * 6]
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
    window = OperatorWindow(start_ros=False, start_erut=False)
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


# ---- 조립 위치(홀더·EOAT 탈착 자세, 2026-11-05) -----------------------------------
def _assembly_window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("SMR_ROBOT_CONFIG_DIR", str(tmp_path))
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    sent: list[tuple[str, list]] = []
    calls: list[str] = []
    window.ros_status.send_pose = lambda name, v: sent.append((name, list(v))) or True
    window.ros_status.call_command = lambda name: calls.append(name) or True
    return window, sent, calls


def test_assembly_pose_is_saved_as_joints(qtbot, tmp_path, monkeypatch) -> None:
    """「조립 위치 저장」은 관절값을 assembly_joint 로 보내고 파일에도 남긴다."""
    import json

    window, sent, _calls = _assembly_window(qtbot, tmp_path, monkeypatch)
    screen = window.cobot_jog_screen
    assert screen.save_buttons["save_assembly_pose"].toolTip() == "조립 위치 저장"

    window.ros_status.joint_position_changed.emit([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    window.ros_status.tcp_pose_changed.emit([636.8, -47.3, 581.0, 3141.0, 0.0, -1570.0])
    screen.command_requested.emit("save_assembly_pose")

    assert sent == [("assembly_joint", [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])]
    assert "J1 1.0" in screen.saved_labels["save_assembly_pose"].text()
    saved = json.loads((tmp_path / "reference_poses.json").read_text(encoding="utf-8"))
    assert saved["assembly_joint"]["values"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    window.close()

    # 다시 열면 화면에 보이고, 로봇에 붙을 때 노드에 다시 보낸다.
    window, sent, _calls = _assembly_window(qtbot, tmp_path, monkeypatch)
    assert "J1 1.0" in window.cobot_jog_screen.saved_labels["save_assembly_pose"].text()
    window._restore_robot_settings(True)
    assert ("assembly_joint", [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]) in sent
    window.close()


def test_move_assembly_only_from_home(qtbot, tmp_path, monkeypatch) -> None:
    """「조립 위치 이동」은 저장돼 있고, 홈에 있고, 로봇이 쉴 때만 나간다."""
    window, _sent, calls = _assembly_window(qtbot, tmp_path, monkeypatch)
    manual = window.cobot_manual_screen

    window._on_robot_at_home(True)
    manual.command_requested.emit("move_assembly")
    assert calls == [] and "저장되어 있지 않습니다" in manual.activity_label.text()

    window.ros_status.joint_position_changed.emit([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    window.cobot_jog_screen.command_requested.emit("save_assembly_pose")

    window._on_robot_at_home(False)
    manual.command_requested.emit("move_assembly")
    assert calls == [] and "홈에서만" in manual.activity_label.text()

    window._on_robot_at_home(True)
    window._on_robot_task_state(1)              # 로봇 태스크 실행 중
    manual.command_requested.emit("move_assembly")
    assert calls == [] and "동작 중" in manual.activity_label.text()

    window._on_robot_task_state(3)
    manual.command_requested.emit("move_assembly")
    assert calls == ["move_assembly"]
    window.close()


# ---- 저장하는 위치는 툴 플랜지 기준 (2026-11-09) -------------------------------------
def test_start_pose_is_saved_after_the_tcp_is_set_to_the_flange(qtbot, tmp_path, monkeypatch) -> None:
    """시작 포즈(TCP 좌표)는 로봇 TCP 를 플랜지로 맞춘 뒤 새로 읽은 값으로 저장한다."""
    monkeypatch.setenv("SMR_ROBOT_CONFIG_DIR", str(tmp_path))
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    calls: list[str] = []
    sent: list[tuple[str, list]] = []
    window.ros_status.call_command = lambda name: calls.append(name) or True
    window.ros_status.send_pose = lambda name, v: sent.append((name, list(v))) or True
    window.FLANGE_SETTLE_MS = 50
    screen = window.cobot_jog_screen

    window.ros_status.tcp_pose_changed.emit([900.0, 0.0, 0.0, 0.0, 0.0, 0.0])   # 홀더 끝점 TCP 값
    screen.command_requested.emit("save_start_pose")
    assert calls == ["flange_tcp"] and sent == [], "TCP 를 맞추기 전에는 저장하지 않는다"

    window.ros_status.tcp_pose_changed.emit([655.0, 0.0, 0.0, 0.0, 0.0, 0.0])   # 플랜지로 바뀐 값
    qtbot.waitUntil(lambda: bool(sent), timeout=2000)
    assert sent == [("start_pose", [655.0, 0.0, 0.0, 0.0, 0.0, 0.0])]

    # 관절값으로 저장하는 위치는 TCP 와 상관없다 — 바로 저장한다.
    calls.clear()
    window.ros_status.joint_position_changed.emit([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    screen.command_requested.emit("save_standby_pose")
    assert calls == [] and sent[-1][0] == "standby_joint"
    window.close()


def test_start_pose_is_not_saved_while_the_robot_task_runs(qtbot, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SMR_ROBOT_CONFIG_DIR", str(tmp_path))
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    calls: list[str] = []
    window.ros_status.call_command = lambda name: calls.append(name) or True
    window.ros_status.tcp_pose_changed.emit([655.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    window._on_robot_task_state(1)
    window.cobot_jog_screen.command_requested.emit("save_start_pose")
    assert calls == []
    assert "태스크가 도는 중" in window.cobot_jog_screen.activity_label.text()
    window.close()
