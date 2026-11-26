"""팔 받침대(레지스터 360) — 홈 명령은 받침대 길에서 되돌리기 태스크로 간다 (2026-11-26).

받침대 위(④)나 그 길(① 회전 · ② 들기 · ③ 뻗기)에서 스캔 대기 위치를 거치는 홈 이동을 하면 받침대를
끌고 간다. 그래서 받침대 태스크를 거꾸로 돌리는 dusan_v6_unpark.task 를 불러와 튼다.
"""

from test_home_watch import FakeParam, FakePrimary, FakeResponse
from test_pose_publishing import make_node

HOME = [0.1, -1.2, 1.5, -0.3, 1.57, 0.0]
TURN = [-1.47, -1.2, 1.5, -0.3, 1.57, 0.0]
LIFT = [-1.47, -1.4, 1.3, -0.3, 1.57, 0.0]
REACH = [-1.47, -1.0, 1.0, -0.5, 1.57, 0.0]
REST = [-1.47, -0.9, 1.1, -0.6, 1.57, 0.0]
WALL = [0.8, -1.5, -2.7, 1.1, 0.8, -3.1]
UNPARK = "Dusan/dusan_v6/dusan_v6_unpark.task"


class FakeDash:
    """29999 — 변수 읽기와 stop · task -p · play 를 적어 둔다."""

    def __init__(self, variables, refuse=()):
        self.variables = variables
        self.refuse = refuse
        self.sent = []
        self.last_error = ""

    def get_variable(self, name):
        return self.variables.get(name, "NOT_FOUND")

    def _reply(self, command):
        self.sent.append(command)
        return f"Failed to execute: {command}" if command in self.refuse else "OK"

    def robot_remote_control_on(self):
        return self._reply("remoteControl -on")

    def robot_stop(self):
        return self._reply("stop")

    def robot_play(self):
        return self._reply("play")

    def send_command_29999(self, command):
        return self._reply(command)


def _node(joints, arm_park=0, park_poses=True, refuse=()):
    regs = {276: [0], 308: [0], 328: [0], 360: [arm_park]}
    node = make_node(regs, {
        'scale': {'position_per_count': 0.1, 'rotation_per_count': 1.0},
        'read': {'home_flag': {'address': 276, 'count': 1},
                 'arm_park': {'address': 360, 'count': 1}},
        'write': {'home_flag': {'address': 276, 'count': 1},
                  'arm_park': {'address': 360, 'count': 1},
                  'pose_src': {'address': 308, 'count': 1},
                  'standby_joint': {'address': 310, 'count': 6, 'kind': 'angle'},
                  'home_joint': {'address': 322, 'count': 6, 'kind': 'angle'},
                  'home_src': {'address': 328, 'count': 1}},
    })
    variables = {"Home_joint": HOME, "Standby_joint": WALL, "probe_dist": 100.0,
                 "gripper_target_pose": [-0.7, 0.1, -0.6, 1.5, 0.0, -1.5]}
    if park_poses:
        variables.update({"Park_turn_joint": TURN, "Park_lift_joint": LIFT,
                          "Park_reach_joint": REACH, "Park_rest_joint": REST})
    node.robot_dash = FakeDash(variables, refuse)
    node.robot_primary = FakePrimary()
    params = {'home_tol_j': 0.02, 'home_leave_tol_j': 0.05, 'home_refresh_s': 60.0,
              'home_near_tol_deg': 3.0, 'unpark_task_path': UNPARK}
    node.get_parameter = lambda name: FakeParam(params[name])
    node._param_str = lambda name, default='': str(params.get(name, default))
    node._last_codes = {'task_state': 3, 'arm_park': arm_park}
    node._last_joint_mrad = [round(q * 1000) for q in joints]
    node._joint_variables = {}
    node._assembly_joint = None
    node.connected = True
    node._HOME_AFTER_STOP_S = 0
    return node


def test_home_on_the_rest_runs_the_unpark_task_instead_of_the_standby_path():
    node = _node(REST, arm_park=2)
    res = node.cb_move_home(None, FakeResponse())
    assert res.success and node.UNPARK_MARK in res.message
    assert node.robot_primary.scripts == [], "스캔 대기 위치를 거치는 30001 홈 프로그램은 안 보낸다"
    assert node.robot_dash.sent == ["remoteControl -on", "stop", f"task -p {UNPARK}", "play"]
    assert node.robot_modbus.writes[0] == (276, 0)


def test_power_cycle_lost_register_still_finds_the_rest_by_joints():
    """360 은 전원을 내리면 0 이 된다 — 관절값이 받침대 자세면 되돌리기로 간다."""
    node = _node(REST, arm_park=0)
    res = node.cb_move_home(None, FakeResponse())
    assert res.success and f"task -p {UNPARK}" in node.robot_dash.sent


def test_stopped_between_park_poses_still_goes_to_the_unpark_task():
    """올리다 멈췄다(360 = 1, 자세 사이) — 되돌리기 태스크가 자세를 보고 거절한다. 대기 위치 길로는 안 간다."""
    between = [-0.7, -1.2, 1.5, -0.3, 1.57, 0.0]
    node = _node(between, arm_park=1)
    node.cb_move_home(None, FakeResponse())
    assert node.robot_primary.scripts == [] and "play" in node.robot_dash.sent


def test_home_from_the_wall_keeps_the_standby_path():
    node = _node(WALL, arm_park=4)
    res = node.cb_move_home(None, FakeResponse())
    assert res.success and node.UNPARK_MARK not in res.message
    assert len(node.robot_primary.scripts) == 1 and "movej(mh_sb" in node.robot_primary.scripts[0]
    assert f"task -p {UNPARK}" not in node.robot_dash.sent


def test_untaught_park_poses_do_not_divert_the_home_command():
    node = _node(WALL, arm_park=9, park_poses=False)
    node.cb_move_home(None, FakeResponse())
    assert len(node.robot_primary.scripts) == 1


def test_at_home_a_stale_parked_state_does_not_divert():
    node = _node(HOME, arm_park=2)
    node.cb_move_home(None, FakeResponse())
    assert len(node.robot_primary.scripts) == 1 and "play" not in node.robot_dash.sent


def test_refused_task_load_is_reported():
    node = _node(REST, arm_park=2, refuse=(f"task -p {UNPARK}",))
    res = node.cb_move_home(None, FakeResponse())
    assert not res.success and "task -p" in res.message and "play" not in node.robot_dash.sent


def test_standby_and_gripper_moves_are_refused_on_the_park_path():
    for call in ("cb_move_standby", "cb_move_gripper"):
        node = _node(REST, arm_park=2)
        res = getattr(node, call)(None, FakeResponse())
        assert not res.success and "받침대" in res.message
        assert node.robot_primary.scripts == []


def test_back_home_outside_the_task_settles_the_state_to_ready():
    """펜던트 조그 등으로 홈에 돌아왔다 — 360 을 4(ready)로 맞춘다(홈 명령이 계속 되돌리기로 가지 않게)."""
    node = _node(HOME, arm_park=2)
    node._watch_home()
    assert (360, 4) in node.robot_modbus.writes


def test_ready_or_away_from_home_leaves_the_state_alone():
    node = _node(HOME, arm_park=4)
    node._watch_home()
    assert (360, 4) not in node.robot_modbus.writes
    node = _node(REST, arm_park=2)
    node._watch_home()
    assert not any(addr == 360 for addr, _v in node.robot_modbus.writes)


def test_shipped_map_has_the_arm_park_register():
    from elite_robot_controller import register_map
    registers = register_map.load()
    assert registers.read_entry("arm_park").address == 360
    assert registers.write_entry("arm_park").address == 360


# ---- 받침대 거치(move_rest) — RCS 가 주행 전에 부른다 (2026-11-26) ----------------------
PARK = "Dusan/dusan_v6/dusan_v6_park.task"


def _rest_node(joints, arm_park=4, task_state=3):
    node = _node(joints, arm_park=arm_park)
    params = {'home_tol_j': 0.02, 'home_leave_tol_j': 0.05, 'home_refresh_s': 60.0,
              'home_near_tol_deg': 3.0, 'unpark_task_path': UNPARK, 'park_task_path': PARK}
    node.get_parameter = lambda name: FakeParam(params[name])
    node._param_str = lambda name, default='': str(params.get(name, default))
    node._last_codes['task_state'] = task_state
    return node


def test_rest_from_home_runs_the_park_task():
    node = _rest_node(HOME)
    res = node.cb_move_rest(None, FakeResponse())
    assert res.success and node.PARK_MARK in res.message
    assert node.robot_dash.sent == ["remoteControl -on", "stop", f"task -p {PARK}", "play"]


def test_rest_when_already_on_the_rest_does_nothing():
    node = _rest_node(REST, arm_park=2)
    res = node.cb_move_rest(None, FakeResponse())
    assert res.success and "이미" in res.message and node.robot_dash.sent == []


def test_rest_from_the_wall_or_while_a_task_runs_is_refused():
    node = _rest_node(WALL)
    res = node.cb_move_rest(None, FakeResponse())
    assert not res.success and "홈에서만" in res.message and node.robot_dash.sent == []
    node = _rest_node(HOME, task_state=1)
    res = node.cb_move_rest(None, FakeResponse())
    assert not res.success and node.robot_dash.sent == []


def test_rest_continues_from_a_park_pose():
    node = _rest_node(TURN, arm_park=1)
    res = node.cb_move_rest(None, FakeResponse())
    assert res.success and f"task -p {PARK}" in node.robot_dash.sent
