"""디지털 출력 한 비트 켜고 끄기.

컨트롤러는 디지털 IO 를 레지스터 하나에 16비트로 모아 둔다(사용 설명서
15.2: 0 표준 입력 / 2 표준 출력). 그래서 출력 하나만 바꾸려면 지금 값을
읽어 그 비트만 뒤집어 다시 써야 한다. 물 분사 밸브와 마킹기가 이 경로로
붙을 예정이라, 값을 못 읽었을 때 **쓰지 않는 것**이 중요하다 — 나머지
출력이 한꺼번에 0 으로 꺼진다.
"""

from elite_robot_controller import register_map
from test_pose_publishing import FakePublisher, make_node


def _io_map():
    return {
        'scale': {'position_per_count': 0.1, 'rotation_per_count': 1.0},
        'read': {'digital_out': {'address': 2, 'count': 1, 'kind': 'raw'},
                 'digital_in': {'address': 0, 'count': 1, 'kind': 'raw'}},
        'write': {'digital_out': {'address': 2, 'count': 1, 'kind': 'raw'}},
    }


def _node(value=0b0000_0101):
    node = make_node({2: [value], 0: [0b0000_0010]}, _io_map())
    node.connected = True
    node.pub_digital_out = FakePublisher()
    return node


def test_shipped_map_has_the_documented_io_addresses():
    registers = register_map.load()
    assert registers.read_entry('digital_in').address == 0
    assert registers.read_entry('digital_out').address == 2
    assert registers.write_entry('digital_out').address == 2


def test_turning_one_output_on_keeps_the_others():
    node = _node(0b0000_0101)

    ok, message = node.set_digital_out(1, True)

    assert ok, message
    assert node.robot_modbus.writes == [(2, 0b0000_0111)]
    assert node.pub_digital_out.messages == [0b0000_0111]


def test_turning_one_output_off_keeps_the_others():
    node = _node(0b0000_0101)

    assert node.set_digital_out(0, False)[0]

    assert node.robot_modbus.writes == [(2, 0b0000_0100)]


def test_high_bit_is_written_as_a_signed_value():
    """드라이버가 쓴 값을 부호 있는 16비트로 읽어 확인하므로 맞춰 보낸다."""
    node = _node(0)

    assert node.set_digital_out(15, True)[0]

    assert node.robot_modbus.writes == [(2, -32768)]


def test_nothing_is_written_when_the_current_value_is_unknown():
    node = _node()
    node.robot_modbus.registers[2] = []

    ok, message = node.set_digital_out(3, True)

    assert not ok
    assert node.robot_modbus.writes == []
    assert '읽지 못해' in message


def test_out_of_range_and_disconnected_are_refused():
    node = _node()
    assert not node.set_digital_out(16, True)[0]
    assert not node.set_digital_out(-1, True)[0]
    node.connected = False
    assert not node.set_digital_out(0, True)[0]
    assert node.robot_modbus.writes == []


def test_topic_carries_index_and_value():
    node = _node(0)

    class Msg:
        data = [2, 1]

    node.cb_digital_out(Msg())

    assert node.robot_modbus.writes == [(2, 0b0000_0100)]

    class Short:
        data = [2]

    node.cb_digital_out(Short())
    assert node.robot_modbus.writes == [(2, 0b0000_0100)]
    assert node.get_logger().warnings


def test_mark_go_is_written_to_its_register():
    """마킹 끝 신호(278) — 로봇은 마킹 자리에서 이 값이 1 이 될 때까지 선다."""
    node = make_node({}, {
        'scale': {'position_per_count': 0.1, 'rotation_per_count': 1.0},
        'read': {},
        'write': {'mark_go': {'address': 278, 'count': 1}},
    })

    class Msg:
        data = 1

    node.cb_mark_go(Msg())

    assert node.robot_modbus.writes == [(278, 1)]
    assert register_map.load().write_entry('mark_go').address == 278
    assert register_map.load().read_entry('scan_arc').address == 286


def test_resume_row_is_written_and_never_negative():
    """이어 할 줄(279) — 재개할 때 끝낸 줄 수를 쓴다. 음수는 0(처음부터)."""
    node = make_node({}, {
        'scale': {'position_per_count': 0.1, 'rotation_per_count': 1.0},
        'read': {},
        'write': {'resume_row': {'address': 279, 'count': 1}},
    })

    class Msg:
        data = 3

    class Bad:
        data = -2

    node.cb_resume_row(Msg())
    node.cb_resume_row(Bad())

    assert node.robot_modbus.writes == [(279, 3), (279, 0)]
    assert register_map.load().write_entry('resume_row').address == 279
