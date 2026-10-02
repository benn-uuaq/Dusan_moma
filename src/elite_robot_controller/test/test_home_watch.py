"""태스크가 안 도는 동안 홈 플래그(276)를 실제 관절값으로 판정한다.

태스크가 돌 때는 태스크가 제어주기마다 276 을 쓰지만, 안 돌 때는 아무도 안
쓴다. 그러면 RCS 를 막 켰을 때(레지스터 0)나 펜던트로 손으로 옮긴 뒤 값이
실제와 달라, 로봇이 홈에 있어도 ERUT 에 deployed 로 나간다(if-0.5 evt/home).
"""

from test_pose_publishing import make_node


HOME = [0.1, -1.2, 1.5, -0.3, 1.57, 0.0]


class FakeDash:
    def __init__(self, value):
        self.value = value
        self.asked = 0

    def get_variable(self, name):
        self.asked += 1
        return self.value


class FakeParam:
    def __init__(self, value):
        self.value = value


def _node(joints_rad, flag, task_state=3, home=HOME, pose_src=0, regs_310=None):
    regs = {276: [flag], 308: [pose_src]}
    if regs_310 is not None:
        regs[310] = regs_310
    node = make_node(regs, {
        'scale': {'position_per_count': 0.1, 'rotation_per_count': 1.0},
        'read': {'home_flag': {'address': 276, 'count': 1}},
        'write': {'home_flag': {'address': 276, 'count': 1},
                  'pose_src': {'address': 308, 'count': 1}},
    })
    node.robot_dash = FakeDash(home)
    params = {'home_tol_j': 0.02, 'home_refresh_s': 60.0}
    node.get_parameter = lambda name: FakeParam(params[name])
    node._last_codes = {'task_state': task_state}
    node._last_joint_mrad = [round(q * 1000) for q in joints_rad]
    node._home_target = None
    node._home_target_at = 0.0
    return node


def test_robot_at_home_raises_a_stale_zero_flag():
    node = _node(HOME, flag=0)

    node._watch_home()

    assert node.robot_modbus.writes == [(276, 1)]


def test_robot_away_from_home_clears_a_stale_one():
    away = list(HOME)
    away[1] += 0.3
    node = _node(away, flag=1)

    node._watch_home()

    assert node.robot_modbus.writes == [(276, 0)]


def test_within_tolerance_counts_as_home():
    near = [q + 0.015 for q in HOME]
    node = _node(near, flag=1)

    node._watch_home()

    assert node.robot_modbus.writes == []


def test_running_task_owns_the_flag():
    """태스크가 돌 때는 태스크가 쓴다 — 노드가 끼어들지 않는다."""
    node = _node(HOME, flag=0, task_state=1)

    node._watch_home()

    assert node.robot_modbus.writes == []


def test_unknown_home_joint_leaves_the_flag_alone():
    node = _node(HOME, flag=0, home='NOT_FOUND')

    node._watch_home()

    assert node.robot_modbus.writes == []


def test_saved_reference_registers_win_when_pose_src_is_set():
    """308 = 1 이면 태스크처럼 310~315 의 홈 관절을 쓴다."""
    other = [0.5, -1.0, 1.0, 0.0, 1.0, 0.0]
    node = _node(other, flag=0, pose_src=1, regs_310=[round(q * 1000) for q in other])

    node._watch_home()

    assert node.robot_modbus.writes == [(276, 1)]
    assert node.robot_dash.asked == 0


def test_home_joint_is_not_asked_every_cycle():
    node = _node(HOME, flag=1)
    for _ in range(5):
        node._watch_home()

    assert node.robot_dash.asked == 1
