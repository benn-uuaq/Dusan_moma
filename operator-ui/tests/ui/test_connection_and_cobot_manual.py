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
    # "연결"/"연결 해제"는 이 화면에서 빠졌다 — 연결 설정 화면이 맡는다.
    assert "연결" not in by_text
    for label, command in (
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


def test_cobot_badge_reflects_real_connection_state(qtbot) -> None:
    """PLC/AMR/UT는 아직 자리표시자라 항상 "연결됨"이지만, Cobot은

    실제 장비라 TPAC처럼 처음엔 "연결 안 됨"에서 시작해 ros_status의
    connected_changed로만 초록으로 바뀌어야 한다(예전엔 다른 셋과
    똑같이 항상 "연결됨"으로 고정돼 있었다).
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    cobot_badge = window.top_bar.badges["Cobot"]
    assert "연결 안 됨" in cobot_badge.state_label.text()

    window.ros_status.connected_changed.emit(True)
    assert "연결됨" in cobot_badge.state_label.text()
    assert "안 됨" not in cobot_badge.state_label.text()

    window.ros_status.connected_changed.emit(False)
    assert "연결 안 됨" in cobot_badge.state_label.text()
    window.close()


def test_top_bar_shrinks_font_to_fit_narrow_width(qtbot) -> None:
    """상단바 내용(브랜드+날짜/시간+배지 5개+배터리)을 기본 글자

    크기 그대로 늘어놓으면 좁은 창에서는 다 안 들어가 잘렸다("상단부
    글자와 날짜 잘리는 문제"). 개별 라벨을 눌러 찌그러뜨리는 대신,
    필요한 폭이 실제 폭보다 크면 전체 글자 크기를 비례해서 줄여
    무엇 하나 잘리지 않게 해야 한다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    top_bar = window.top_bar

    # 넉넉한 폭에서는 기본(등록해 둔) 글자 크기 그대로 유지돼야 한다.
    top_bar.resize(3000, top_bar.height())
    top_bar._rescale_to_fit()
    base_px = top_bar.clock.font().pixelSize()
    assert base_px == 20

    # 배지 다섯 개가 다 못 들어가는 폭에서는 잘리는 대신 전체 글자 크기가
    # 줄어야 하고, 그 결과 필요한 폭이 지금 폭 안에 들어와야 한다.
    # (배포 최소 폭 1280 px 에서는 이제 줄이지 않고도 들어간다 — 상단
    #  제품 문구를 뺐다.)
    narrow = top_bar.layout().sizeHint().width() - 120
    top_bar.resize(narrow, top_bar.height())
    top_bar._rescale_to_fit()
    narrow_px = top_bar.clock.font().pixelSize()
    assert narrow_px < base_px
    assert top_bar.layout().sizeHint().width() <= top_bar.width()

    # 다시 넉넉해지면 원래 크기로 돌아와야 한다.
    top_bar.resize(3000, top_bar.height())
    top_bar._rescale_to_fit()
    assert top_bar.clock.font().pixelSize() == base_px
    window.close()


def test_connection_badges_all_have_the_same_width(qtbot) -> None:
    """연결 상태 배지는 문구가 달라도 크기가 같아야 한다(들쭉날쭉 금지)."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    badges = window.top_bar.badges
    badges["PLC"].set_connected(True)            # "연결됨"
    badges["AMR"].set_connected(False)           # "연결 안 됨"
    widths = {name: badge.width() for name, badge in badges.items()}
    assert len(set(widths.values())) == 1, widths
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


def test_robot_ip_propagates_without_database_save(qtbot) -> None:
    """로봇 IP는 "연결 설정" 한 곳에서만 입력하고 즉시 다른 화면에 퍼져야 한다.

    예전에는 PostgreSQL 저장에 성공했을 때만 반영해서, DB가 없는 환경
    (SMR_DATABASE_URL 미설정)에서는 주소를 고쳐도 TPAC 설정·Cobot 수동
    제어가 옛 주소를 그대로 들고 있었다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    conn = window.screens["connection"]

    conn.field(conn.ROBOT_IP_FIELD).setText("192.168.1.101")
    conn.field(conn.ROBOT_PORT_FIELD).setValue(1502)

    tpac = window.screens["tpac_bridge"]
    assert tpac.robot_ip.text() == "192.168.1.101"
    assert tpac.robot_port.value() == 1502
    assert "192.168.1.101" in window.cobot_manual_screen.endpoint_label.text()
    # 다른 화면에서는 고칠 수 없어야 한 곳 관리가 지켜진다.
    assert tpac.robot_ip.isEnabled() is False
    window.close()


def test_connection_screen_owns_connect_buttons(qtbot) -> None:
    """연결/연결 해제는 "연결 설정" 화면에만 있고, 다른 화면은 그리로 보낸다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    conn = window.screens["connection"]

    assert hasattr(conn, "connect_btn") and hasattr(conn, "disconnect_btn")
    # 연결 전에는 해제가 잠겨 있고, 연결되면 뒤바뀐다.
    conn.set_link_state(False)
    assert conn.connect_btn.isEnabled() and not conn.disconnect_btn.isEnabled()
    conn.set_link_state(True)
    assert not conn.connect_btn.isEnabled() and conn.disconnect_btn.isEnabled()

    # 예전에 연결 버튼을 갖고 있던 화면들에는 더 이상 없어야 한다.
    assert not hasattr(window.screens["tpac_bridge"], "robot_connect_btn")
    window.close()


def test_cobot_manual_links_to_connection_screen(qtbot) -> None:
    """연결 버튼 대신 "연결 설정" 버튼이 그 화면으로 보내야 한다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.show()
    screen = window.cobot_manual_screen
    goto = {b.text(): b for b in screen.findChildren(QPushButton)}["연결 설정"]
    qtbot.mouseClick(goto, Qt.MouseButton.LeftButton)
    assert window.stack.currentWidget() is window.screens["connection"]
    window.close()


def test_settings_fields_never_overlap_at_any_scale(qtbot) -> None:
    """배율이 커져도 입력칸끼리 겹치면 안 된다.

    입력칸 min-height 가 배율에 따라 같이 커지는데 세로가 모자라면 예전에는
    위젯이 서로 겹쳐 글자가 반씩 잘려 보였다. 이제는 모자라면 스크롤된다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    conn = window.screens["connection"]
    names = ["협동로봇 IP", "Dashboard 포트", "Primary 포트", "Modbus 포트"]

    for size in ((1280, 720), (1920, 1080), (2000, 1057)):
        window.resize(*size)
        window.show()
        window.navigate("connection")
        spans = []
        for name in names:
            widget = conn.field(name)
            top = widget.mapTo(conn, widget.rect().topLeft()).y()
            spans.append((top, top + widget.height()))
        for (_top_a, bottom_a), (top_b, _bottom_b) in zip(spans, spans[1:]):
            assert top_b >= bottom_a, f"{size}에서 입력칸이 겹친다"
    window.close()


def test_task_state_is_shown_on_the_status_card(qtbot) -> None:
    """로봇이 보고하는 태스크 상태가 화면에 보여야 한다.

    작업 영역 전송이 "태스크 실행 중"이면 막히는데, 값이 안 보이면
    "실행 중이 아닌데 왜 안 나가냐"를 가려낼 수가 없다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.show()
    window.navigate("cobot_manual")
    block = window.cobot_manual_screen.metrics["task_state"]

    for state, label in ((1, "실행 중"), (2, "일시 중지"), (3, "중지됨")):
        window.ros_status.task_state_changed.emit(state)
        assert block.value_label.text() == label
    # 모르는 값도 숨기지 않고 그대로 보여 준다.
    window.ros_status.task_state_changed.emit(7)
    assert "7" in block.value_label.text()
    assert block.isVisible(), "항목이 레이아웃에서 빠졌다"
    window.close()
