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
            "tcp_absolute": {"address": 384, "count": 6},
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

    assert registers.read_entry("tcp_absolute").address == 384
    assert registers.read_entry("tcp_absolute").count == 6
    assert registers.read_entry("tcp_zero_relative").address == 280
    assert registers.read_entry("robot_mode").address == 66
    assert registers.read_entry("control_method").address == 71
    assert registers.read_entry("operation_mode").address == 72
    assert registers.read_entry("joint_position").address == 73
    # 쓰기 주소는 로봇 태스크가 읽는 범용 레지스터 대역에 있어야 한다.
    for name in ("linear_speed", "speed_ratio", "home_joint", "start_pose"):
        entry = registers.write_entry(name)
        assert entry.available, f"{name} 주소가 비어 있습니다"
        assert 256 <= entry.address <= 383, f"{name}이 범용 대역 밖입니다"
    assert registers.write_entry("home_joint").count == 6
    assert registers.write_entry("home_joint").kind == "angle"


def test_joint_angles_use_rotation_scale_for_every_axis():
    """관절 각도는 6축 모두 mrad이므로 위치 환산을 적용하면 안 된다."""
    registers = register_map.load()
    entry = registers.read_entry("joint_position")

    assert entry.kind == "angle"
    assert registers.scales_for(entry) == [1.0] * 6
    # 자세는 앞 3개만 위치 환산을 받는다.
    pose = registers.read_entry("tcp_absolute")
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
    node = make_node({384: [0] * 6, 280: [0] * 6})
    absolute, zero = FakePublisher(), FakePublisher()

    node.publish_pose("tcp_absolute", absolute)
    node.publish_pose("tcp_zero_relative", zero)

    assert node.robot_modbus.reads == [(384, 6), (280, 6)]
    assert len(absolute.messages) == 1
    assert len(zero.messages) == 1


def test_position_and_rotation_scaling():
    """위치는 0.1 mm 단위, 회전은 1 mrad 단위로 환산한다."""
    node = make_node({384: [1205, -3400, 5000, 1571, 0, -3141]})
    publisher = FakePublisher()

    node.publish_pose("tcp_absolute", publisher)

    assert publisher.messages[0] == [120.5, -340.0, 500.0, 1571.0, 0.0, -3141.0]


def test_short_or_missing_response_is_not_published():
    """레지스터를 다 읽지 못하면 잘못된 자세를 발행하지 않는다."""
    node = make_node({384: [1, 2, 3]})
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


def test_pose_write_fills_every_register():
    """자세 저장은 6개 레지스터를 한 번에 채운다."""
    node = make_node(map_data={
        "scale": {"position_per_count": 0.1, "rotation_per_count": 1.0},
        "read": {},
        "write": {
            "home_joint": {"address": 310, "count": 6, "kind": "angle"},
            "pose_src": {"address": 308, "count": 1},
        },
    })

    success, _ = node.write_pose("home_joint", [207.0, -1466.0, -1875.0, -1371.0, 1570.0, 207.0])

    assert success is True
    assert node.robot_modbus.writes == [
        (310, 207), (311, -1466), (312, -1875), (313, -1371), (314, 1570), (315, 207)
    ]


def test_pose_write_rejects_wrong_length():
    """성분이 모자라면 절반만 쓰지 않는다."""
    node = make_node()
    node.registers = register_map.RegisterMap({
        "scale": {}, "read": {},
        "write": {"home_joint": {"address": 310, "count": 6, "kind": "angle"}},
    })

    success, message = node.write_pose("home_joint", [1.0, 2.0])

    assert success is False
    assert "6개가 아닙니다" in message
    assert node.robot_modbus.writes == []


def test_jog_code_maps_axis_and_direction():
    """부호가 방향, 절댓값이 축 번호다. 범위를 벗어나면 움직이지 않는다."""
    node = make_node()

    assert node._jog_vector(1) == [1.0, 0, 0, 0, 0, 0]
    assert node._jog_vector(-6) == [0, 0, 0, 0, 0, -1.0]
    assert node._jog_vector(7) is None
    assert node._jog_vector(0) is None


class FakeChannel:
    """연결/해제만 흉내 내는 통신 채널."""

    def __init__(self, ok=True):
        self.ok = ok
        self.opened = 0
        self.closed = 0

    def connect(self):
        self.opened += 1
        return self.ok

    def disconnect(self):
        self.closed += 1


def make_connectable_node(dash_ok=True, primary_ok=True, modbus_ok=True):
    node = make_node()
    node.robot_ip = "10.0.0.9"
    node.robot_dash = FakeChannel(dash_ok)
    node.robot_primary = FakeChannel(primary_ok)
    node.robot_modbus_channel = FakeChannel(modbus_ok)
    node.robot_dash.connect_29999 = node.robot_dash.connect
    node.robot_dash.disconnect_29999 = node.robot_dash.disconnect
    node.robot_primary.connect_30001 = node.robot_primary.connect
    node.robot_primary.disconnect_30001 = node.robot_primary.disconnect
    modbus = node.robot_modbus
    modbus.connect = node.robot_modbus_channel.connect
    modbus.disconnect = node.robot_modbus_channel.disconnect
    node.connected = False
    node.pub_connected = FakePublisher()
    return node


def test_connect_requires_all_three_channels():
    """한 채널이라도 실패하면 연결로 보지 않는다."""
    node = make_connectable_node(modbus_ok=False)

    assert node.connect_all_servers() is False
    assert node.connected is False

    node = make_connectable_node()
    assert node.connect_all_servers() is True
    assert node.connected is True


def test_connect_service_reports_result():
    node = make_connectable_node(dash_ok=False)
    response = node.cb_connect(None, FakeResponse())

    assert response.success is False
    assert "10.0.0.9" in response.message


def test_disconnect_closes_every_channel():
    """한 채널 정리에 실패해도 나머지는 정리한다."""
    node = make_connectable_node()
    node.connect_all_servers()

    def boom():
        raise RuntimeError("소켓 오류")

    node.robot_dash.disconnect_29999 = boom
    node.cb_disconnect(None, FakeResponse())

    assert node.connected is False
    assert node.robot_primary.closed == 1
    assert node.robot_modbus_channel.closed == 1


def test_loop_does_not_touch_modbus_before_connect():
    """연결 전에 소켓을 건드리면 예외가 난다. 읽기를 건너뛰어야 한다."""
    node = make_connectable_node()
    node.pub_robot_mode = FakePublisher()

    node.update_robot_loop()

    assert node.robot_modbus.reads == []
    assert node.pub_connected.messages == [False]
