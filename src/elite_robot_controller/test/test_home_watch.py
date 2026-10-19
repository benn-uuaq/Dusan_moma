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


def _node(joints_rad, flag, task_state=3, home=HOME, pose_src=0, regs_310=None,
          home_src=0, regs_322=None):
    regs = {276: [flag], 308: [pose_src], 328: [home_src]}
    if regs_310 is not None:
        regs[310] = regs_310
    if regs_322 is not None:
        regs[322] = regs_322
    node = make_node(regs, {
        'scale': {'position_per_count': 0.1, 'rotation_per_count': 1.0},
        'read': {'home_flag': {'address': 276, 'count': 1}},
        'write': {'home_flag': {'address': 276, 'count': 1},
                  'pose_src': {'address': 308, 'count': 1},
                  'standby_joint': {'address': 310, 'count': 6, 'kind': 'angle'},
                  'home_joint': {'address': 322, 'count': 6, 'kind': 'angle'},
                  'home_src': {'address': 328, 'count': 1}},
    })
    node.robot_dash = FakeDash(home)
    params = {'home_tol_j': 0.02, 'home_leave_tol_j': 0.05, 'home_refresh_s': 60.0}
    node.get_parameter = lambda name: FakeParam(params[name])
    node._last_codes = {'task_state': task_state}
    node._last_joint_mrad = [round(q * 1000) for q in joints_rad]
    node._joint_variables = {}
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


def test_saved_home_registers_win_when_home_src_is_set():
    """328 = 1 이면 태스크처럼 322~327 의 홈 관절을 쓴다(RCS 「홈 위치 저장」)."""
    other = [0.5, -1.0, 1.0, 0.0, 1.0, 0.0]
    node = _node(other, flag=0, home_src=1, regs_322=[round(q * 1000) for q in other])

    node._watch_home()

    assert node.robot_modbus.writes == [(276, 1)]
    assert node.robot_dash.asked == 0


def test_standby_registers_are_not_the_home():
    """310~315(308)는 2026-10-16 부터 스캔 대기 위치다 — 홈 판정에 안 쓴다."""
    standby = [0.5, -1.0, 1.0, 0.0, 1.0, 0.0]
    node = _node(standby, flag=1, pose_src=1, regs_310=[round(q * 1000) for q in standby])

    node._watch_home()

    assert node.robot_modbus.writes == [(276, 0)]


def test_empty_saved_home_falls_back_to_the_robot_variable():
    """328 = 1 이어도 322~327 이 전부 0 이면 저장한 적이 없는 것이다."""
    node = _node(HOME, flag=0, home_src=1, regs_322=[0] * 6)

    node._watch_home()

    assert node.robot_modbus.writes == [(276, 1)]
    assert node.robot_dash.asked == 1


def test_home_joint_is_not_asked_every_cycle():
    node = _node(HOME, flag=1)
    for _ in range(5):
        node._watch_home()

    assert node.robot_dash.asked == 1


# ---- 홈 이동(cb_move_home): 스캔 대기 위치를 거친다 (2026-10-16) ------------------
STANDBY = [0.79941, -1.55029, -2.70026, 1.10839, 0.81203, -3.14139]


class FakeVars:
    """이름별 로봇 변수. 없는 이름은 로봇처럼 문자열 오류를 돌려준다."""

    def __init__(self, values):
        self.values = values
        self.last_error = ""

    def get_variable(self, name):
        return self.values.get(name, "NOT_FOUND")

    def robot_stop(self):
        return "Stopped"


class FakePrimary:
    def __init__(self):
        self.scripts = []

    def send_script(self, script):
        self.scripts.append(script)
        return True


class FakeResponse:
    success = None
    message = ""


def _home_script(variables, **regs):
    node = _node(HOME, flag=1, **regs)
    node.robot_dash = FakeVars(variables)
    node.robot_primary = FakePrimary()
    node.connected = True
    node._HOME_AFTER_STOP_S = 0
    node.cb_move_home(None, FakeResponse())
    assert len(node.robot_primary.scripts) == 1
    return node, node.robot_primary.scripts[0]


def test_move_home_goes_through_the_standby_then_home():
    node, script = _home_script({"Home_joint": HOME, "Standby_joint": STANDBY,
                                 "probe_dist": 100.0})
    assert f"mh_home = {HOME}" in script
    assert f"mh_sb = {STANDBY}" in script
    order = [script.index("movel(pose_trans(mh_cur"), script.index("movel(mh_up"),
             script.index("movej(mh_sb"), script.index("movej(mh_home"),
             script.index("write_port_register(276, 1)")]
    assert order == sorted(order)
    # 물러난 뒤 대기 위치 선상에 맞춘다 — 남은 만큼 X 를 뒤로, 높이는 올리거나 내린다.
    assert "mh_up[0] = mh_ref[0]" in script
    assert "mh_up[2] < mh_ref[2] - 0.001 or mh_up[2] > mh_ref[2] + 0.001" in script
    assert script.index("mh_up[0] = mh_ref[0]") < script.index("mh_up[2] = mh_ref[2]")
    # 물러날 때 대기 위치 X 와 probe_dist 중 작은 쪽, 플랜지 기준.
    assert "get_forward_kin(mh_sb)" in script and "get_actual_tool_flange_pose()" in script
    assert "set_tcp([0, 0, 0, 0, 0, 0])" in script and "0.1):" in script
    assert node.robot_modbus.writes[0] == (276, 0), "가는 동안은 홈이 아니다"


def test_move_home_uses_saved_registers_for_home_and_standby():
    home = [0.5, -1.0, 1.0, 0.0, 1.0, 0.0]
    standby = [0.6, -1.1, 1.1, 0.1, 1.1, 0.1]
    _node_, script = _home_script(
        {"Home_joint": HOME, "Standby_joint": STANDBY, "probe_dist": 100.0},
        home_src=1, regs_322=[round(q * 1000) for q in home],
        pose_src=1, regs_310=[round(q * 1000) for q in standby])
    assert f"mh_home = {home}" in script
    assert f"mh_sb = {standby}" in script


def test_move_home_without_a_standby_variable_uses_the_home():
    """태스크를 아직 안 올려 Standby_joint 가 없으면 홈을 대기 위치로 쓴다."""
    _node_, script = _home_script({"Home_joint": HOME, "probe_dist": 100.0})
    assert f"mh_sb = {HOME}" in script


# ---- 이동 중 흔들림 (2026-10-19) --------------------------------------------------
def _shifted(delta):
    return [q + delta for q in HOME]


def test_small_shake_keeps_the_home_flag():
    """이미 홈이면 home_leave_tol_j(0.05 rad) 안의 흔들림은 홈으로 본다."""
    node = _node(_shifted(0.03), flag=1)
    node._watch_home()
    assert node.robot_modbus.writes == []


def test_leaving_beyond_the_wider_tolerance_clears_the_flag():
    node = _node(_shifted(0.06), flag=1)
    node._watch_home()
    assert node.robot_modbus.writes == [(276, 0)]


def test_entering_home_still_needs_the_tight_tolerance():
    """홈이 아닌 데서 들어올 때는 예전처럼 home_tol_j(0.02 rad) 안이어야 한다."""
    node = _node(_shifted(0.03), flag=0)
    node._watch_home()
    assert node.robot_modbus.writes == []
