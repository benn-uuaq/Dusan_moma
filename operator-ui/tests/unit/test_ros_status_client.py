"""ROS 자세 토픽 수신과 화면 반영을 검증한다.

실제 ROS 노드를 띄우지 않고 시그널을 직접 발생시켜, 값 변환과 화면 연결만
확인한다. 구독 자체는 rclpy가 담당하므로 여기서 다시 시험하지 않는다.
"""

from smr_operator_ui.app import OperatorWindow
from smr_operator_ui.services import RosStatusClient, RosTopics


class FakeMessage:
    def __init__(self, data):
        self.data = data


def test_topic_names_match_robot_control_node():
    """토픽 이름이 elite_robot_controller의 발행 이름과 같아야 한다."""
    assert RosTopics.TCP_POSE == "robot/status/tcp_pose"
    assert RosTopics.TCP_POSE_ZERO == "robot/status/tcp_pose_zero"


def test_pose_message_is_emitted_as_list(qtbot):
    client = RosStatusClient()
    received: list[list] = []
    client.tcp_pose_changed.connect(received.append)

    client._on_tcp_pose(FakeMessage([120.5, -340.0, 500.0, 1571.0, 0.0, -3141.0]))

    assert received == [[120.5, -340.0, 500.0, 1571.0, 0.0, -3141.0]]


def test_pose_with_wrong_length_is_rejected(qtbot):
    """성분이 6개가 아니면 화면에 일부만 반영되지 않도록 버린다."""
    client = RosStatusClient()
    received: list[list] = []
    errors: list[str] = []
    client.tcp_pose_changed.connect(received.append)
    client.error_occurred.connect(errors.append)

    client._on_tcp_pose(FakeMessage([1.0, 2.0, 3.0]))

    assert received == []
    assert len(errors) == 1


def test_window_shows_received_poses(qtbot):
    """두 토픽 값이 각각 TCP 현재값과 제로점 기준 카드에 들어가야 한다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.cobot_manual_screen

    window.ros_status.tcp_pose_changed.emit([120.5, -340.0, 500.0, 1571.0, 0.0, -3141.0])
    window.ros_status.tcp_pose_zero_changed.emit([0.0, 0.0, 10.0, 0.0, 0.0, 0.0])

    assert screen.tcp_rows["x"].value_label.text() == "120.5"
    assert screen.tcp_rows["y"].value_label.text() == "-340.0"
    assert screen.tcp_rows["rz"].value_label.text() == "-3141.0"
    assert screen.zero_rows["z"].value_label.text() == "10.0"
    assert screen.zero_rows["x"].value_label.text() == "0.0"
    # 두 카드는 서로 영향을 주지 않아야 한다.
    assert screen.tcp_rows["z"].value_label.text() == "500.0"
    window.close()


def test_zero_relative_y_maps_straight_to_horizontal_position(qtbot):
    """가로 위치는 rel_y를 그대로 쓴다(부호를 뒤집지 않는다).

    dusan_v3부터 원점이 Y+에서 Y-로 바뀌어(dus_probe_l.script), 첫 패스가
    Y-에서 Y+로 움직인다 — cur-zero(rel_y)가 이미 오른쪽(+)과 같은 방향이라
    부호를 뒤집으면 위치 점이 반대로(왼쪽/음수 쪽으로) 움직이는 것처럼 보인다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)

    # 위치 점은 스캔 중(state=6)에만 그린다 — 먼저 스캔 중임을 알린다.
    window.ros_status.scan_state_changed.emit([6, 1, 3, 0, 1, 0, 237, 0, 0, 0])
    window.ros_status.tcp_pose_zero_changed.emit([0.0, 250.0, 10.0, 0.0, 0.0, 0.0])

    rect_view = window.main_screen.rect_view
    assert rect_view._pos_h_mm == 250.0
    assert rect_view._pos_v_mm == 10.0
    window.close()


def test_status_codes_are_translated(qtbot):
    """레지스터 값과 함께 운영자용 문구를 전달한다."""
    client = RosStatusClient()
    modes: list[tuple[int, str]] = []
    methods: list[tuple[int, str]] = []
    operations: list[tuple[int, str]] = []
    client.robot_mode_changed.connect(lambda c, n: modes.append((c, n)))
    client.control_method_changed.connect(lambda c, n: methods.append((c, n)))
    client.operation_mode_changed.connect(lambda c, n: operations.append((c, n)))

    client._on_robot_mode(FakeMessage(7))
    client._on_control_method(FakeMessage(2))
    client._on_operation_mode(FakeMessage(-1))

    assert modes == [(7, "RUNNING")]
    assert methods == [(2, "원격 제어")]
    assert operations == [(-1, "지정 안 됨")]


def test_unknown_status_code_keeps_raw_value(qtbot):
    """정의에 없는 값도 버리지 않고 원값을 볼 수 있어야 한다."""
    client = RosStatusClient()
    modes: list[tuple[int, str]] = []
    client.robot_mode_changed.connect(lambda c, n: modes.append((c, n)))

    client._on_robot_mode(FakeMessage(99))

    assert modes == [(99, "알 수 없음 (99)")]


def test_blank_alarm_is_ignored(qtbot):
    client = RosStatusClient()
    received: list[str] = []
    client.alarm_received.connect(received.append)

    client._on_alarm(FakeMessage("   "))
    client._on_alarm(FakeMessage("[ALARM] Response timeout"))

    assert received == ["[ALARM] Response timeout"]


def test_window_shows_status_and_alarms(qtbot):
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.cobot_manual_screen

    window.ros_status.robot_mode_changed.emit(7, "RUNNING")
    window.ros_status.control_method_changed.emit(2, "원격 제어")
    window.ros_status.operation_mode_changed.emit(0, "자동")
    assert screen.metrics["robot_mode"].value_label.text() == "RUNNING"
    assert screen.metrics["control_method"].value_label.text() == "원격 제어"
    assert screen.metrics["operation_mode"].value_label.text() == "자동"

    # 첫 알람이 오면 '활성 알람 없음' 안내는 사라지고 최신 항목이 위에 온다.
    window.ros_status.alarm_received.emit("[ALARM] first")
    window.ros_status.alarm_received.emit("[ALARM] second")
    assert screen.alarm_list.count() == 2
    assert "second" in screen.alarm_list.item(0).text()
    assert "first" in screen.alarm_list.item(1).text()
    assert "활성 알람 없음" not in screen.alarm_list.item(1).text()
    window.close()


def test_alarm_list_is_capped(qtbot):
    """알람이 계속 쌓여도 표시 개수를 넘지 않아야 한다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.cobot_manual_screen

    for index in range(screen.MAX_ALARMS + 10):
        screen.add_alarm(f"[ALARM] {index}")

    assert screen.alarm_list.count() == screen.MAX_ALARMS
    assert f"{screen.MAX_ALARMS + 9}" in screen.alarm_list.item(0).text()
    window.close()


def test_stop_is_safe_when_never_started(qtbot):
    """시작하지 않은 상태에서 창을 닫아도 예외가 나지 않아야 한다."""
    client = RosStatusClient()
    client.stop()
    client.stop()


def test_dashboard_commands_are_mapped_to_services():
    """수동 제어 화면의 명령이 모두 서비스에 연결되어 있어야 한다."""
    from smr_operator_ui.screens import CobotManualScreen

    # connect/disconnect 는 이 화면에서 빠지고 "연결 설정" 화면이 맡는다
    # (명령 자체는 그대로 쓰이며, 아래 목록에서 따로 확인한다).
    screen_commands = {
        command
        for group in (CobotManualScreen._POWER,
                      CobotManualScreen._PROGRAM, CobotManualScreen._MOTION)
        for _label, command in group
    }
    missing = screen_commands - set(RosStatusClient.COMMAND_SERVICES)
    assert missing == set(), f"서비스에 연결되지 않은 명령: {missing}"

    # 대시보드 명령은 29999 소켓으로 나가므로 레지스터 주소가 필요 없다.
    for command in ("connect", "disconnect", "power_on", "power_off",
                    "brake_release", "play", "pause", "stop"):
        service, register = RosStatusClient.COMMAND_SERVICES[command]
        assert service == f"robot/dashboard/{command}"
        assert register is None
    # 홈 이동도 30001 스크립트로 처리하므로 레지스터 주소가 필요 없다.
    assert RosStatusClient.COMMAND_SERVICES["home"] == ("robot/command/move_home", None)


def test_connection_state_reaches_the_screen(qtbot):
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    screen = window.cobot_manual_screen

    window.ros_status.connected_changed.emit(True)
    assert "연결됨" in screen.connection_state.text()

    window.ros_status.connected_changed.emit(False)
    assert "연결 안 됨" in screen.connection_state.text()
    window.close()


def test_command_without_ros_reports_clearly(qtbot):
    """ROS에 붙지 않은 상태에서 눌러도 조용히 실패하지 않아야 한다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)

    window.cobot_manual_screen.command_requested.emit("power_on")

    assert "실패" in window.cobot_manual_screen.activity_label.text()
    window.close()


def test_connected_topic_is_reported_only_on_change(qtbot):
    """연결 상태는 10 Hz 로 계속 오지만 바뀔 때만 알려야 한다.

    그대로 흘려보내면 `_restore_robot_settings()` 가 초당 열 번 저장값을
    다시 밀어 넣어, 운영자가 방금 바꾼 속도가 곧바로 100 으로 되돌아간다.
    """
    from smr_operator_ui.services import RosStatusClient

    client = RosStatusClient()
    seen: list[bool] = []
    client.connected_changed.connect(seen.append)

    class Msg:
        def __init__(self, data): self.data = data

    # 같은 값이 계속 들어와도 처음 한 번만 나간다.
    for _ in range(10):
        client._on_connected(Msg(True))
    assert seen == [True]

    client._on_connected(Msg(False))
    client._on_connected(Msg(False))
    assert seen == [True, False]


def test_saved_settings_are_not_resent_every_cycle(qtbot, monkeypatch):
    """연결 토픽이 반복돼도 저장값을 되풀이해 쓰면 안 된다.

    되풀이하면 속도 바로 40 % 를 보내도 100 ms 안에 저장값(100 %)으로
    덮어써져 "바꿨다가 바로 100 으로 되돌아가는" 증상이 된다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    sent: list[tuple[str, int]] = []
    monkeypatch.setattr(
        window.ros_status, "send_value",
        lambda name, value: sent.append((name, value)) or True,
    )

    class Msg:
        def __init__(self, data): self.data = data

    for _ in range(10):
        window.ros_status._on_connected(Msg(True))

    speed_writes = [v for name, v in sent if name == "speed_ratio"]
    assert len(speed_writes) == 1, f"저장값이 되풀이해 나갔다: {speed_writes}"
    window.close()


def test_speed_scale_ignores_values_out_of_range(qtbot):
    """로봇이 아직 값을 못 주면 0 이 온다. 하한(2 %)으로 눌러 버리면 안 된다.

    0 은 "속도가 2 %" 가 아니라 값이 없다는 뜻이다. 그대로 쓰면 화면이
    2 % 와 100 % 를 오가는 것처럼 보인다.
    """
    from smr_operator_ui.services import RosStatusClient

    client = RosStatusClient()
    seen: list[int] = []
    client.speed_scale_changed.connect(seen.append)

    class Msg:
        def __init__(self, data): self.data = data

    for bad in (0, 1, 101, -5):
        client._on_speed_scale(Msg(bad))
    assert seen == [], f"범위 밖 값이 새어 나갔다: {seen}"

    client._on_speed_scale(Msg(45))
    assert seen == [45]


def test_speed_scale_is_reported_only_on_change(qtbot):
    """노드가 매 주기 발행하므로 같은 값이 초당 수십 번 화면을 때리면 안 된다."""
    from smr_operator_ui.services import RosStatusClient

    client = RosStatusClient()
    seen: list[int] = []
    client.speed_scale_changed.connect(seen.append)

    class Msg:
        def __init__(self, data): self.data = data

    for _ in range(20):
        client._on_speed_scale(Msg(100))
    client._on_speed_scale(Msg(30))
    for _ in range(20):
        client._on_speed_scale(Msg(30))

    assert seen == [100, 30]


def test_speed_bar_shows_no_value_when_robot_has_none(qtbot):
    """값이 없을 때 속도 바가 2 % 로 눌리지 않고 직전 값을 유지해야 한다."""
    from smr_operator_ui.components import SpeedBar

    bar = SpeedBar()
    qtbot.addWidget(bar)
    bar.set_actual(65)
    assert bar.value() == 65
    assert "65" in bar.value_label.text()

    bar.set_actual(0)
    assert bar.value() == 65, "값 없음이 슬라이더를 하한으로 끌어내렸다"


def test_silent_node_is_shown_as_disconnected(qtbot, monkeypatch):
    """노드가 죽거나 멈춰 연결 토픽이 끊기면 마지막 '연결됨'을 붙들고 있지 않는다."""
    from smr_operator_ui.services import ros_status_client as rsc

    class Msg:
        def __init__(self, data):
            self.data = data

    now = [100.0]
    monkeypatch.setattr(rsc.time, "monotonic", lambda: now[0])
    client = rsc.RosStatusClient()
    seen = []
    client.connected_changed.connect(seen.append)
    client._on_connected(Msg(True))
    now[0] += client.CONNECTED_STALE_S - 1
    client._check_connected_stale()
    assert seen == [True], "아직 한도 안쪽"
    now[0] += 2
    client._check_connected_stale()
    assert seen == [True, False]
    client._check_connected_stale()
    assert seen == [True, False], "한 번만 알린다"
    client._on_connected(Msg(True))                 # 노드가 다시 살아났다
    assert seen == [True, False, True]


def test_unexpected_link_loss_is_announced_but_manual_disconnect_is_not(qtbot):
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    conn = window.screens["connection"]
    window.ros_status.call_command = lambda name: True
    window.ros_status.connected_changed.emit(True)
    window.ros_status.connected_changed.emit(False)            # 로봇이 끊겼다
    assert "끊겼습니다" in conn.save_status.text()
    assert "연결 안 됨" in conn.link_state.text()
    assert "연결 안 됨" in window.top_bar.badges["Cobot"].state_label.text()
    window.ros_status.connected_changed.emit(True)             # 자동 재연결
    assert "다시 연결됐습니다" in conn.save_status.text()

    window._disconnect_robot()                                  # 운영자가 끊음
    window.ros_status.connected_changed.emit(False)
    assert conn.save_status.text() == "연결을 끊었습니다.", "운영자가 끊은 건 끊김 경고가 아니다"
    window.close()
