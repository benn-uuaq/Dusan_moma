"""RCS 기준 위치 → 펜던트 변수 (2026-11-25).

운전 모드(모재마다 받침대 높이가 달라 그리퍼 접촉 위치가 다르다)를 불러오면 레지스터뿐 아니라 펜던트 변수
(Home_joint · Standby_joint · gripper_target_pose)에도 그 값을 쓴다 — 29999 `variable -set`.
"""

from elite_robot_controller.robot.robot_control_node import RobotControlNode


class _Dash:
    def __init__(self, reply="OK"):
        self.reply = reply
        self.sets = []

    def set_variable(self, name, value):
        self.sets.append((name, value))
        return self.reply


class _Logger:
    def __init__(self):
        self.lines = []

    def info(self, text):
        self.lines.append(text)

    warn = warning = error = info


def _node(task_state=0, reply="OK"):
    node = RobotControlNode.__new__(RobotControlNode)
    node.robot_dash = _Dash(reply)
    node._logger = _Logger()
    node.get_logger = lambda: node._logger
    node.connected = True
    node._last_codes = {"task_state": task_state}
    node._joint_variables = {"Home_joint": ([0.0] * 6, 1.0)}
    return node


def test_values_are_converted_to_metres_and_radians():
    # RCS 값: 위치 mm · 회전 · 관절 mrad
    assert RobotControlNode.pose_variable_text([-900.54, -23.7, -343.56, 1507, -5, -1568]) == \
        "[-0.900540,-0.023700,-0.343560,1.507000,-0.005000,-1.568000]"


def test_each_pose_goes_to_its_pendant_variable():
    node = _node()
    home = [-2949.01, -1647.98, -2664.01, -1950.01, -1377.99, -0.99]
    node._write_pose_variable("home_joint", home)
    node._write_pose_variable("standby_joint", [-2801, -1930, -2266, -2042.01, -1231, -9])
    node._write_pose_variable("start_pose", [-900.54, -23.7, -343.56, 1507, -5, -1568])
    names = [name for name, _ in node.robot_dash.sets]
    assert names == ["Home_joint", "Standby_joint", "gripper_target_pose"]
    assert node.robot_dash.sets[0][1].startswith("[-2.949010,")
    assert "Home_joint" not in node._joint_variables, "다음에 펜던트 값을 다시 읽는다"


def test_cleared_poses_and_assembly_are_not_written():
    node = _node()
    node._write_pose_variable("start_pose", [0] * 6)          # RCS 에서 지웠다 — 펜던트 값을 둔다
    node._write_pose_variable("assembly_joint", [1, 2, 3, 4, 5, 6])   # 펜던트 변수가 없다
    assert node.robot_dash.sets == []


def test_waits_while_the_task_runs_then_writes():
    node = _node(task_state=RobotControlNode._TASK_RUNNING)
    node._write_pose_variable("start_pose", [-900.54, -23.7, -343.56, 1507, -5, -1568])
    assert node.robot_dash.sets == [], "태스크가 도는 동안은 쓰지 않는다"
    node._last_codes["task_state"] = 0
    node._flush_pose_variables()
    assert [name for name, _ in node.robot_dash.sets] == ["gripper_target_pose"]
    node._flush_pose_variables()
    assert len(node.robot_dash.sets) == 1, "한 번만"


def test_refused_variable_is_logged_and_dropped():
    node = _node(reply="Error: Can not find variable")
    node._write_pose_variable("standby_joint", [-2801, -1930, -2266, -2042, -1231, -9])
    assert any("펜던트 변수 쓰기 실패" in line for line in node._logger.lines)
    assert node._pending_variables == {}
