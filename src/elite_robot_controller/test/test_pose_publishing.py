"""자세 레지스터 주소와 단위 환산을 검증한다.

로봇 없이 실행할 수 있도록 Modbus 클라이언트와 퍼블리셔를 대체한다.
RobotControlNode.__init__은 세 채널 연결을 요구하므로 우회한다.
"""

from elite_robot_controller.robot.robot_control_node import RobotControlNode


class FakeModbus:
    """지정한 시작 주소에만 응답하는 Modbus 대역."""

    def __init__(self, registers):
        self.registers = registers
        self.calls = []

    def get_all_registers(self, start_address, count):
        self.calls.append((start_address, count))
        return self.registers.get(start_address, [])


class FakePublisher:
    def __init__(self):
        self.messages = []

    def publish(self, msg):
        self.messages.append(list(msg.data))


def make_node(registers):
    node = RobotControlNode.__new__(RobotControlNode)
    node.robot_modbus = FakeModbus(registers)
    return node


def test_absolute_and_zero_relative_addresses():
    """절대 TCP는 260, 원점 기준 상대 pose는 280에서 6개씩 읽는다."""
    node = make_node({260: [0] * 6, 280: [0] * 6})
    absolute, zero = FakePublisher(), FakePublisher()

    node.publish_pose(RobotControlNode.REG_TCP_ABSOLUTE, absolute)
    node.publish_pose(RobotControlNode.REG_TCP_ZERO_RELATIVE, zero)

    assert RobotControlNode.REG_TCP_ABSOLUTE == 260
    assert RobotControlNode.REG_TCP_ZERO_RELATIVE == 280
    assert node.robot_modbus.calls == [(260, 6), (280, 6)]
    assert len(absolute.messages) == 1
    assert len(zero.messages) == 1


def test_position_and_rotation_scaling():
    """위치는 0.1 mm 단위, 회전은 1 mrad 단위로 환산한다."""
    node = make_node({260: [1205, -3400, 5000, 1571, 0, -3141]})
    publisher = FakePublisher()

    node.publish_pose(RobotControlNode.REG_TCP_ABSOLUTE, publisher)

    assert publisher.messages[0] == [120.5, -340.0, 500.0, 1571.0, 0.0, -3141.0]


def test_short_or_missing_response_is_not_published():
    """레지스터를 다 읽지 못하면 잘못된 자세를 발행하지 않는다."""
    node = make_node({260: [1, 2, 3]})
    publisher = FakePublisher()

    node.publish_pose(RobotControlNode.REG_TCP_ABSOLUTE, publisher)
    node.publish_pose(RobotControlNode.REG_TCP_ZERO_RELATIVE, publisher)

    assert publisher.messages == []
