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
    params = {'home_tol_j': 0.02, 'home_leave_tol_j': 0.05, 'home_refresh_s': 60.0,
              'home_near_tol_deg': 3.0}
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
    # 물러난 뒤 대기 위치 선상에 맞춘다 — 남은 만큼 벽 반대로, 높이는 올리거나 내린다.
    assert "mh_up[0] = mh_up[0] - mh_dn * mh_nx" in script
    assert "mh_up[2] < mh_ref[2] - 0.001 or mh_up[2] > mh_ref[2] + 0.001" in script
    assert script.index("mh_dn * mh_nx") < script.index("mh_up[2] = mh_ref[2]")
    # 벽 방향은 대기 위치 툴 Z 로 정한다 — +X(v5)나 -X(v6)를 박아 두지 않는다.
    assert "mh_rz = pose_trans(mh_ref, [0, 0, 1, 0, 0, 0])" in script
    assert "mh_cur[0] - mh_ref[0]) / mh_cz" not in script
    assert "mh_up[0] > mh_ref[0]" not in script
    # 조립 위치를 모르면 조립 위치 검사도 없다.
    assert "mh_asm" not in script
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


def test_entering_within_the_near_tolerance_counts_as_home():
    """펜던트 홈과 RCS 홈이 관절마다 3도 안쪽으로 다르면 홈으로 본다(2026-11-12).

    예전에는 들어올 때 home_tol_j(0.02 rad ≈ 1.1도) 안이어야 해서, 펜던트로 홈에 둔 팔이
    RCS 홈과 조금만 달라도 홈이 아니라 홈 이동 · 차량 이동이 막혔다.
    """
    node = _node(_shifted(0.03), flag=0)          # 약 1.7도
    node._watch_home()
    assert node.robot_modbus.writes == [(276, 1)]


def test_entering_beyond_the_near_tolerance_is_not_home():
    node = _node(_shifted(0.06), flag=0)          # 약 3.4도
    node._watch_home()
    assert node.robot_modbus.writes == []


# ---- 벽 방향을 대기 위치 툴 Z 로 (2026-11-05, v6 는 벽이 -X) -----------------------
def _rot(rpy):
    """RPY -> 3x3 회전 행렬(Rz·Ry·Rx)."""
    from math import cos, sin
    r, p, y = rpy
    rx = [[1, 0, 0], [0, cos(r), -sin(r)], [0, sin(r), cos(r)]]
    ry = [[cos(p), 0, sin(p)], [0, 1, 0], [-sin(p), 0, cos(p)]]
    rz = [[cos(y), -sin(y), 0], [sin(y), cos(y), 0], [0, 0, 1]]

    def mul(a, b):
        return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
                for i in range(3)]
    return mul(rz, mul(ry, rx))


class _Robot:
    """스크립트를 파이썬으로 돌려 보는 가짜 로봇. 위치만 따라간다(자세는 그대로)."""

    def __init__(self, joints, flange, kin):
        self.joints = list(joints)
        self.flange = list(flange)
        self.kin = kin
        self.moves = []

    def env(self):
        from math import sqrt

        def pose_trans(pose, delta):
            rot = _rot(pose[3:])
            out = list(pose)
            for i in range(3):
                out[i] = pose[i] + sum(rot[i][k] * delta[k] for k in range(3))
            return out

        def movel(pose, a, v):
            self.moves.append(("movel", list(pose)))
            self.flange = list(pose)

        def movej(joints, a, v):
            self.moves.append(("movej", list(joints)))
            self.joints = list(joints)

        return {
            "sqrt": sqrt, "pose_trans": pose_trans, "movel": movel, "movej": movej,
            "set_tcp": lambda tcp: None,
            "write_port_register": lambda addr, value: None,
            "get_actual_joint_positions": lambda: list(self.joints),
            "get_actual_tool_flange_pose": lambda: list(self.flange),
            "get_forward_kin": lambda joints: list(self.kin[tuple(joints)]),
        }

    def run(self, script):
        """스크립트(def ... end)를 파이썬으로 바꿔 실행한다(end -> pass)."""
        lines = [line[:len(line) - len(line.lstrip())] + "pass"
                 if line.strip() == "end" else line for line in script.splitlines()]
        scope = self.env()
        exec("\n".join(lines), scope)
        scope["move_home_now"]()
        return self.moves


V6_STANDBY_POSE = [-0.47265, 0.15858, 0.49596, 1.57105, 0.0002, -1.5708]
V5_STANDBY_POSE = [0.40795, 0.05529, 0.40515, 1.57121, 0.00018, 1.61144]
AT_WALL = [0.3, -1.0, 2.0, 0.5, 1.0, 0.2]


def _run_home(standby_pose, flange, joints=AT_WALL, assembly=None):
    node = _node(HOME, flag=1)
    node.robot_dash = FakeVars({"Home_joint": HOME, "Standby_joint": STANDBY,
                                "probe_dist": 100.0})
    node.robot_primary = FakePrimary()
    node.connected = True
    node._HOME_AFTER_STOP_S = 0
    node._assembly_joint = assembly
    node.cb_move_home(None, FakeResponse())
    robot = _Robot(joints, flange, {tuple(STANDBY): standby_pose})
    return robot.run(node.robot_primary.scripts[0])


def test_v6_retreats_toward_plus_x_away_from_a_minus_x_wall():
    """v6: 벽이 -X. 물러나기·맞추기 모두 +X 로 가야 한다(예전엔 +X 벽만 알았다)."""
    at_wall = [-0.80363, 0.0, -0.50713, 1.57115, 0.00022, -1.57076]
    moves = _run_home(V6_STANDBY_POSE, at_wall)
    kinds = [kind for kind, _ in moves]
    assert kinds == ["movel", "movel", "movel", "movej", "movej"]
    retreat, align, lift = (pose for _, pose in moves[:3])
    assert abs(retreat[0] - (-0.70363)) < 1e-3, "툴 -Z(= +X)로 probe_dist 100 mm"
    assert abs(align[0] - V6_STANDBY_POSE[0]) < 1e-3, "대기 위치 X 까지 +X 로"
    assert abs(align[1] - at_wall[1]) < 1e-3, "옆(Y)으로는 안 간다"
    assert abs(lift[2] - V6_STANDBY_POSE[2]) < 1e-6
    assert moves[3][1] == STANDBY and moves[4][1] == HOME


def test_v5_still_retreats_toward_minus_x_from_a_plus_x_wall():
    at_wall = [0.80, 0.0, -0.3, 1.57121, 0.00018, 1.61144]
    moves = _run_home(V5_STANDBY_POSE, at_wall)
    retreat, align = moves[0][1], moves[1][1]
    assert retreat[0] < at_wall[0] - 0.09, "툴 -Z(= -X)로 물러난다"
    assert abs(align[0] - V5_STANDBY_POSE[0]) < 0.01


def test_v6_already_behind_the_standby_does_not_move_toward_the_wall():
    """대기 위치보다 벽에서 먼 자리면 물러나지도, 벽 쪽으로 당기지도 않는다."""
    behind = [-0.30, 0.10, 0.49596, 1.57105, 0.0002, -1.5708]
    moves = _run_home(V6_STANDBY_POSE, behind)
    assert [kind for kind, _ in moves] == ["movej", "movej"]


def test_at_the_assembly_pose_goes_straight_home():
    """조립 위치에 있으면 왔던 길(movej) 그대로 홈만 간다."""
    assembly = [1.0, -1.0, 1.0, 0.0, 1.0, 0.0]
    at_wall = [-0.80363, 0.0, -0.50713, 1.57115, 0.00022, -1.57076]
    moves = _run_home(V6_STANDBY_POSE, at_wall, joints=[q + 0.03 for q in assembly],
                      assembly=assembly)
    assert moves == [("movej", HOME)]


def test_away_from_the_assembly_pose_still_goes_through_the_standby():
    assembly = [1.0, -1.0, 1.0, 0.0, 1.0, 0.0]
    at_wall = [-0.80363, 0.0, -0.50713, 1.57115, 0.00022, -1.57076]
    moves = _run_home(V6_STANDBY_POSE, at_wall, assembly=assembly)
    assert moves[-2:] == [("movej", STANDBY), ("movej", HOME)]


# ---- 조립 위치 이동 (2026-11-05) ----------------------------------------------------
ASSEMBLY_MRAD = [1000.0, -1000.0, 1000.0, 0.0, 1000.0, 0.0]


class FakeMsg:
    def __init__(self, data):
        self.data = data


def _assembly_node(joints_rad):
    node = _node(joints_rad, flag=1)
    node.robot_dash = FakeVars({"Home_joint": HOME})
    node.robot_primary = FakePrimary()
    node.connected = True
    node._HOME_AFTER_STOP_S = 0
    node._assembly_joint = None
    return node


def test_assembly_joint_topic_is_kept_in_radians():
    node = _assembly_node(HOME)
    node.cb_assembly_joint(FakeMsg(ASSEMBLY_MRAD))
    assert node._assembly_joint == [1.0, -1.0, 1.0, 0.0, 1.0, 0.0]
    # 전부 0 은 RCS 에서 「삭제」한 것이다(2026-11-12) — 조립 위치를 잊는다.
    node.cb_assembly_joint(FakeMsg([0.0] * 6))
    assert node._assembly_joint is None
    node.cb_assembly_joint(FakeMsg([1.0] * 5))
    assert node._assembly_joint is None, "6 개가 아니면 무시"


def test_move_assembly_from_home_sends_one_movej():
    node = _assembly_node(HOME)
    node.cb_assembly_joint(FakeMsg(ASSEMBLY_MRAD))
    res = node.cb_move_assembly(None, FakeResponse())
    assert res.success
    script = node.robot_primary.scripts[0]
    assert "movej([1.0, -1.0, 1.0, 0.0, 1.0, 0.0], a=1.4, v=0.5)" in script
    assert "movel" not in script and "set_tcp([0, 0, 0, 0, 0, 0])" in script
    assert node.robot_modbus.writes[0] == (276, 0), "가는 동안은 홈이 아니다"


def test_move_assembly_is_refused_away_from_home():
    node = _assembly_node(_shifted(0.2))
    node.cb_assembly_joint(FakeMsg(ASSEMBLY_MRAD))
    res = node.cb_move_assembly(None, FakeResponse())
    assert not res.success and "홈에서만" in res.message
    assert node.robot_primary.scripts == []


def test_move_assembly_without_a_saved_pose_is_refused():
    node = _assembly_node(HOME)
    res = node.cb_move_assembly(None, FakeResponse())
    assert not res.success and "저장" in res.message
    assert node.robot_primary.scripts == []


# ---- 저장 전 TCP 를 툴 플랜지로 (2026-11-09) ----------------------------------------
def test_flange_tcp_sets_the_tcp_to_the_flange_when_idle():
    node = _assembly_node(HOME)
    res = node.cb_flange_tcp(None, FakeResponse())
    assert res.success and node.robot_primary.scripts == ["set_tcp([0, 0, 0, 0, 0, 0])"]


def test_flange_tcp_is_refused_while_a_task_runs():
    node = _assembly_node(HOME)
    node._last_codes = {'task_state': 1}
    res = node.cb_flange_tcp(None, FakeResponse())
    assert not res.success and node.robot_primary.scripts == []


# ---- 홈 근처 · 위치별 이동 (2026-11-12) ---------------------------------------------
def test_move_home_near_home_skips_the_standby_detour():
    """관절마다 3도 안쪽이면 홈 근처다 — 대기 위치를 거치지 않고 movej 홈만 한다."""
    import math
    _node_, script = _home_script({"Home_joint": HOME, "Standby_joint": STANDBY,
                                   "probe_dist": 100.0})
    assert f"if (mh_far > {max(0.02, math.radians(3.0))}):" in script


def _move_node(variables, joints=HOME, **regs):
    node = _node(joints, flag=1, **regs)
    node.robot_dash = FakeVars(variables)
    node.robot_primary = FakePrimary()
    node.connected = True
    node._HOME_AFTER_STOP_S = 0
    node._assembly_joint = None
    return node


def test_move_standby_from_home_is_one_movej_and_away_goes_through_the_retreat():
    node = _move_node({"Home_joint": HOME, "Standby_joint": STANDBY, "probe_dist": 100.0})
    res = node.cb_move_standby(None, FakeResponse())
    assert res.success
    script = node.robot_primary.scripts[0]
    assert script.startswith("def move_standby_now():")
    assert f"mh_sb = {STANDBY}" in script
    # 홈 근처면 movej 대기 위치, 아니면 벽에서 물러나는 길(대기 위치 툴 Z 로 벽 방향).
    assert "if (mh_far <= " in script and "else:" in script
    assert "get_forward_kin(mh_sb)" in script
    assert "movej(mh_home" not in script.split("else:")[0], "홈에서는 홈으로 되돌아가지 않는다"
    assert node.robot_modbus.writes[0] == (276, 0), "가는 동안은 홈이 아니다"


def test_move_standby_without_any_standby_is_refused():
    node = _move_node({"Home_joint": HOME, "probe_dist": 100.0})
    res = node.cb_move_standby(None, FakeResponse())
    assert not res.success and "스캔 대기 위치" in res.message
    assert node.robot_primary.scripts == []


def test_move_gripper_stops_probe_dist_in_front_of_the_contact_pose():
    contact = [-0.74949, 0.16058, -0.68296, 1.5355, -2.0e-05, -1.5708]
    node = _move_node({"Home_joint": HOME, "Standby_joint": STANDBY, "probe_dist": 100.0,
                       "gripper_target_pose": contact})
    res = node.cb_move_gripper(None, FakeResponse())
    assert res.success and "100 mm 앞" in res.message
    script = node.robot_primary.scripts[0]
    assert f"mg_c = {contact}" in script
    assert "mg_t = pose_trans(mg_c, [0, 0, -0.1, 0, 0, 0])" in script
    # 대기 위치를 거쳐 높이 먼저 맞추고 직선으로 — 접촉 위치 자체(mg_c)로는 안 간다.
    assert script.index("mh_sb") < script.index("movel(mg_t")
    assert "movel(mg_c" not in script and "movej(mg_c" not in script


def test_move_gripper_uses_the_saved_registers_when_pose_src_is_set():
    from elite_robot_controller import register_map
    node = _move_node({"Home_joint": HOME, "Standby_joint": STANDBY, "probe_dist": 50.0,
                       "gripper_target_pose": [0.0] * 6}, pose_src=1)
    node.registers = register_map.RegisterMap({
        'scale': {'position_per_count': 0.1, 'rotation_per_count': 1.0},
        'read': {}, 'write': {'pose_src': {'address': 308, 'count': 1},
                              'start_pose': {'address': 316, 'count': 6, 'kind': 'pose'}}})
    node.robot_modbus.registers[316] = [-7495, 1606, -6830, 1536, 0, -1571]
    assert node._gripper_contact_target() == [-0.7495, 0.1606, -0.683, 1.536, 0.0, -1.571]
