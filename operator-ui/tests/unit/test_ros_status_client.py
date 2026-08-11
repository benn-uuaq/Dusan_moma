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


def test_stop_is_safe_when_never_started(qtbot):
    """시작하지 않은 상태에서 창을 닫아도 예외가 나지 않아야 한다."""
    client = RosStatusClient()
    client.stop()
    client.stop()
