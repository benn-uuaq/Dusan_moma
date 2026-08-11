from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton

from smr_operator_ui.app import OperatorWindow


def test_settings_menu_opens_connection_screen(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.show()
    window.navigate("settings")
    window.screens["settings"].navigate.emit("connection")
    assert window.stack.currentWidget() is window.screens["connection"]
    window.screens["connection"].back_requested.emit()
    assert window.stack.currentWidget() is window.screens["settings"]
    window.close()


def test_connection_screen_collects_all_device_addresses(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    values = window.screens["connection"].values()
    # 협동로봇, 차량용 PLC, MQTT Broker가 한 화면에 모여 있어야 한다.
    for key in (
        "협동로봇 IP",
        "Dashboard 포트",
        "Primary 포트",
        "Modbus 포트",
        "PLC IP",
        "PLC 포트",
        "MQTT Broker 주소",
        "MQTT 포트",
    ):
        assert key in values
    assert window.screens["connection"].settings_scope == "connection"
    window.close()


def test_cobot_settings_no_longer_duplicates_address(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    # 주소 항목은 연결 설정 화면으로 옮겼으므로 여기에 남아 있으면 안 된다.
    assert "IP 주소" not in window.screens["cobot"].values()
    window.close()


def test_cobot_endpoint_follows_connection_settings(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window._apply_stored_settings("connection", {"협동로봇 IP": "10.0.0.7"})
    assert "10.0.0.7" in window.cobot_manual_screen.endpoint_label.text()
    window.close()


def test_cobot_manual_buttons_emit_dashboard_commands(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.show()
    screen = window.cobot_manual_screen
    received: list[str] = []
    screen.command_requested.connect(received.append)

    by_text = {b.text(): b for b in screen.findChildren(QPushButton)}
    for label, command in (
        ("연결", "connect"),
        ("전원 ON", "power_on"),
        ("브레이크 해제", "brake_release"),
        ("정지", "stop"),
        ("홈 이동", "home"),
    ):
        qtbot.mouseClick(by_text[label], Qt.MouseButton.LeftButton)
        assert received[-1] == command
    window.close()


def test_cobot_manual_status_updates(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.cobot_manual_screen
    screen.set_connected(True)
    assert "연결됨" in screen.connection_state.text()
    screen.apply_status({"robot_mode": "RUNNING"})
    assert screen.metrics["robot_mode"].value_label.text() == "RUNNING"
    screen.set_alarms(["ER112 Response timeout"])
    assert screen.alarm_list.count() == 1
    screen.set_alarms([])
    assert screen.alarm_list.item(0).text() == "활성 알람 없음"
    window.close()


def test_cobot_manual_shows_six_pose_components(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.cobot_manual_screen
    axes = ("x", "y", "z", "rx", "ry", "rz")
    assert tuple(screen.tcp_rows) == axes
    assert tuple(screen.zero_rows) == axes

    # 현재값과 제로점 기준은 서로 독립적으로 갱신되어야 한다.
    screen.apply_tcp({"x": "120.5", "rz": "-3.20"})
    assert screen.tcp_rows["x"].value_label.text() == "120.5"
    assert screen.tcp_rows["rz"].value_label.text() == "-3.20"
    assert screen.zero_rows["x"].value_label.text() == "-"

    screen.apply_zero_point({"x": "0.0"})
    assert screen.zero_rows["x"].value_label.text() == "0.0"
    assert screen.tcp_rows["x"].value_label.text() == "120.5"

    # 전달하지 않은 성분은 이전 값을 유지한다.
    screen.apply_tcp({"y": "10.0"})
    assert screen.tcp_rows["x"].value_label.text() == "120.5"
    window.close()


def test_cobot_manual_layout_fits_fixed_console_height(qtbot) -> None:
    """1280x720 고정 콘솔에서 카드나 버튼이 잘리지 않아야 한다."""
    from PyQt6.QtWidgets import QFrame

    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.resize(1280, 720)
    window.navigate("cobot_manual")
    window.show()
    qtbot.waitExposed(window)
    screen = window.cobot_manual_screen

    undersized = [
        card.findChild(type(screen.endpoint_label)).text()
        for card in screen.findChildren(QFrame)
        if card.objectName() == "Surface"
        and (
            card.height() < card.minimumSizeHint().height()
            or card.width() < card.minimumSizeHint().width()
        )
    ]
    assert undersized == []
    clipped = [
        b.text()
        for b in screen.findChildren(QPushButton)
        if b.width() < b.minimumSizeHint().width()
    ]
    assert clipped == []
    window.close()
