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
