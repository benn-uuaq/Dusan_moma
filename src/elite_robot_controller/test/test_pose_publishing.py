"""레지스터 맵 적용, 자세 발행, 쓰기 거부 동작을 검증한다.

로봇 없이 실행할 수 있도록 Modbus 클라이언트와 퍼블리셔를 대체한다.
RobotControlNode.__init__은 세 채널 연결을 요구하므로 우회한다.
"""

from elite_robot_controller import register_map
from elite_robot_controller.robot.robot_control_node import RobotControlNode


class FakeModbus:
    """지정한 시작 주소에만 응답하는 Modbus 대역."""

    def __init__(self, registers=None):
        self.registers = registers or {}
        self.reads = []
        self.writes = []
        self.write_result = True

    def get_all_registers(self, start_address, count):
        self.reads.append((start_address, count))
        return self.registers.get(start_address, [])

    def get_register(self, address):
        self.reads.append((address, 1))
        values = self.registers.get(address)
        return values[0] if values else None

    def set_register(self, address, value):
        self.writes.append((address, value))
        return self.write_result


class FakePublisher:
    def __init__(self):
        self.messages = []

    def publish(self, msg):
        self.messages.append(list(msg.data) if hasattr(msg.data, "__len__") else msg.data)


class FakeLogger:
    def __init__(self):
        self.warnings = []

    def warn(self, message):
        self.warnings.append(message)


class FakeResponse:
    success = False
    message = ""


def make_node(registers=None, map_data=None):
    node = RobotControlNode.__new__(RobotControlNode)
    node.robot_modbus = FakeModbus(registers)
    node.registers = register_map.RegisterMap(map_data or _default_map())
    node._logger = FakeLogger()
    node.get_logger = lambda: node._logger
    return node


def _default_map():
    return {
        "scale": {"position_per_count": 0.1, "rotation_per_count": 1.0},
        "read": {
            "robot_mode": {"address": 66, "count": 1},
            "tcp_absolute": {"address": 260, "count": 6},
            "tcp_zero_relative": {"address": 280, "count": 6},
            "joint_position": {"address": None, "count": 6},
        },
        "write": {
            "linear_speed": {"address": 300, "count": 1},
            "save_home_pose": {"address": None, "count": 1},
        },
    }


def test_shipped_register_map_matches_known_addresses():
    """패키지에 담긴 설정 파일이 확인된 주소를 그대로 갖고 있어야 한다."""
    registers = register_map.load()

    assert registers.read_entry("tcp_absolute").address == 260
    assert registers.read_entry("tcp_absolute").count == 6
    assert registers.read_entry("tcp_zero_relative").address == 280
    assert registers.read_entry("robot_mode").address == 66
    assert registers.read_entry("control_method").address == 71
    assert registers.read_entry("operation_mode").address == 72
    assert registers.read_entry("joint_position").address == 73
    assert registers.read_entry("tcp_base_frame").address == 384
    # 아직 확인되지 않은 주소는 null로 남아 있어야 한다.
    assert registers.write_entry("save_home_pose").available is False
    assert registers.available_writes() == []


def test_joint_angles_use_rotation_scale_for_every_axis():
    """관절 각도는 6축 모두 mrad이므로 위치 환산을 적용하면 안 된다."""
    registers = register_map.load()
    entry = registers.read_entry("joint_position")

    assert entry.kind == "angle"
    assert registers.scales_for(entry) == [1.0] * 6
    # 자세는 앞 3개만 위치 환산을 받는다.
    pose = registers.read_entry("tcp_base_frame")
    assert registers.scales_for(pose) == [0.1, 0.1, 0.1, 1.0, 1.0, 1.0]


def test_angle_entry_is_published_without_position_scaling():
    node = make_node(
        {73: [207, -1466, -1875, -1371, 1570, 207]},
        {
            "scale": {"position_per_count": 0.1, "rotation_per_count": 1.0},
            "read": {"joint_position": {"address": 73, "count": 6, "kind": "angle"}},
            "write": {},
        },
    )
    publisher = FakePublisher()

    node.publish_pose("joint_position", publisher)

    assert publisher.messages[0] == [207.0, -1466.0, -1875.0, -1371.0, 1570.0, 207.0]


def test_pose_uses_addresses_from_map():
    node = make_node({260: [0] * 6, 280: [0] * 6})
    absolute, zero = FakePublisher(), FakePublisher()

    node.publish_pose("tcp_absolute", absolute)
    node.publish_pose("tcp_zero_relative", zero)

    assert node.robot_modbus.reads == [(260, 6), (280, 6)]
    assert len(absolute.messages) == 1
    assert len(zero.messages) == 1


def test_position_and_rotation_scaling():
    """위치는 0.1 mm 단위, 회전은 1 mrad 단위로 환산한다."""
    node = make_node({260: [1205, -3400, 5000, 1571, 0, -3141]})
    publisher = FakePublisher()

    node.publish_pose("tcp_absolute", publisher)

    assert publisher.messages[0] == [120.5, -340.0, 500.0, 1571.0, 0.0, -3141.0]


def test_short_or_missing_response_is_not_published():
    """레지스터를 다 읽지 못하면 잘못된 자세를 발행하지 않는다."""
    node = make_node({260: [1, 2, 3]})
    publisher = FakePublisher()

    node.publish_pose("tcp_absolute", publisher)
    node.publish_pose("tcp_zero_relative", publisher)

    assert publisher.messages == []


def test_entry_without_address_is_never_read():
    """주소가 없는 항목은 Modbus 요청 자체를 하지 않는다."""
    node = make_node()
    publisher = FakePublisher()

    node.publish_pose("joint_position", publisher)

    assert node.robot_modbus.reads == []
    assert publisher.messages == []


def test_write_is_rejected_when_address_is_unset():
    """주소 미정 항목은 쓰기를 시도하지 않는다. 엉뚱한 레지스터를 건드리면 위험하다."""
    node = make_node()

    success, message = node.write_register("save_home_pose", 1)

    assert success is False
    assert "주소가 설정되지 않았습니다" in message
    assert node.robot_modbus.writes == []


def test_write_uses_address_from_map():
    node = make_node()

    success, message = node.write_register("linear_speed", 150)

    assert success is True
    assert node.robot_modbus.writes == [(300, 150)]
    assert "300" in message


def test_write_failure_is_reported():
    node = make_node()
    node.robot_modbus.write_result = False

    success, _ = node.write_register("linear_speed", 150)

    assert success is False


def test_service_response_carries_result():
    node = make_node()
    response = node.cb_save_home_pose(None, FakeResponse())

    assert response.success is False
    assert node.get_logger().warnings
