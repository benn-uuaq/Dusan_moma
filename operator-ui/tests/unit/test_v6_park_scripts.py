"""v6 받침대 동작 — 팔을 받침대에 올리기(dus6_park) / 받침대에서 홈으로(dus6_unpark) (2026-11-25).

차량이 움직이기 전: 홈 -> ① 회전(movej) -> ② 들기 -> ③ 앞(+Y)으로 뻗기 -> ④ 내려놓기(movel).
차량이 고정된 뒤: 거꾸로 ④ -> ③ -> ② -> ① -> 홈(movej) = 로봇팔 ready.
EliteScript 는 파이썬 규칙이라 `end` 줄만 빼면 파이썬으로 돌릴 수 있다 — 가짜 로봇에 돌려 순서를 본다.
"""

import pathlib
import re

import pytest

TASK = pathlib.Path(__file__).resolve().parents[3] / "robot_task" / "dusan_task_v6"

HOME = [-2.949, -1.648, -2.664, -1.950, -1.378, -0.001]
TURN = [-4.520, -1.648, -2.664, -1.950, -1.378, -0.001]
LIFT = [-4.520, -1.900, -2.300, -1.950, -1.378, -0.001]
REACH = [-4.520, -1.500, -1.900, -2.300, -1.378, -0.001]
REST = [-4.520, -1.400, -1.950, -2.350, -1.378, -0.001]
BETWEEN = [-3.700, -1.648, -2.664, -1.950, -1.378, -0.001]     # ① 로 가다 멈춘 자리


class Halt(Exception):
    pass


class FakeRobot:
    """관절값 하나로 움직이는 가짜 로봇. FK 는 항등(pose = 관절값)으로 둔다."""

    def __init__(self, at, regs=None, fixed_after=0):
        self.q = list(at)
        self.regs = dict(regs or {})
        self.moves = []
        self.writes = []
        self.popups = []
        self.syncs = 0
        self.fixed_after = fixed_after       # sync() 를 이만큼 부른 뒤 309 = 1

    def env(self, **variables):
        env = {
            "Home_joint": list(HOME), "Park_turn_joint": list(TURN), "Park_lift_joint": list(LIFT),
            "Park_reach_joint": list(REACH), "Park_rest_joint": list(REST),
            "park_v_l": 0.1, "park_v_touch": 0.02, "home_tol_j": 0.02, "home_tol_p": 5,
            "v_move_j_set": 0.34907, "a_move_j_set": 0.69813, "a_move_set": 0.4, "veh_wait_cyc": 15000,
            "set_tcp": lambda tcp: None,
            "get_actual_joint_positions": lambda: list(self.q),
            "get_actual_tool_flange_pose": lambda: list(self.q),
            "get_forward_kin": lambda q: list(q),
            "movej": self._movej, "movel": self._movel,
            "read_port_register": lambda addr, _input=True: self._read(addr),
            "write_port_register": lambda addr, value: self.writes.append((addr, value)),
            "popup": lambda s, title="", blocking=True: self.popups.append(s),
            "halt": self._halt, "sync": self._sync,
        }
        env.update(variables)
        return env

    def _movej(self, q, a, v):
        self.moves.append(("movej", list(q), v))
        self.q = list(q)

    def _movel(self, pose, a, v):
        self.moves.append(("movel", list(pose), v))
        self.q = list(pose)

    def _read(self, addr):
        if addr == 309:
            return 1 if self.syncs >= self.fixed_after else 0
        return self.regs.get(addr, 0)

    def _sync(self):
        self.syncs += 1

    @staticmethod
    def _halt():
        raise Halt()

    def reg(self, addr):
        values = [v for a, v in self.writes if a == addr]
        return values


def _python(name: str) -> str:
    text = (TASK / "scripts" / name).read_text(encoding="utf-8")
    return "\n".join(line for line in text.splitlines() if line.strip() != "end")


def _run(name, robot, **variables):
    env = robot.env(**variables)
    try:
        exec(compile(_python(name), name, "exec"), env)
    except Halt:
        return env, True
    return env, False


def _path(robot):
    return [(kind, target) for kind, target, _v in robot.moves]


# ---- 올리기 (홈 -> 받침대) --------------------------------------------------------
def test_park_from_home_turns_lifts_reaches_and_lowers_slowly() -> None:
    robot = FakeRobot(HOME)
    _env, halted = _run("dus6_park.script", robot)
    assert not halted and not robot.popups
    assert _path(robot) == [("movej", TURN), ("movel", LIFT), ("movel", REACH), ("movel", REST)]
    assert robot.moves[-1][2] == 0.02, "받침대에 닿는 구간은 park_v_touch"
    assert robot.moves[1][2] == 0.1 and robot.moves[2][2] == 0.1
    assert robot.reg(360) == [1, 2] and robot.reg(276) == [0]


@pytest.mark.parametrize("at, rest_of_path", (
    (TURN, [("movel", LIFT), ("movel", REACH), ("movel", REST)]),
    (LIFT, [("movel", REACH), ("movel", REST)]),
    (REACH, [("movel", REST)]),
))
def test_park_continues_from_the_pose_it_stopped_at(at, rest_of_path) -> None:
    robot = FakeRobot(at)
    _run("dus6_park.script", robot)
    assert _path(robot) == rest_of_path and robot.reg(360)[-1] == 2


def test_park_on_the_rest_does_nothing() -> None:
    robot = FakeRobot(REST)
    _run("dus6_park.script", robot)
    assert robot.moves == [] and robot.reg(360) == [2] and robot.reg(276) == []


def test_park_between_poses_refuses_to_move_and_keeps_the_state() -> None:
    """자세 사이면 움직이지 않는다. 360 은 그대로 둔다 — 팔이 받침대 길에 있을 수 있어 RCS 「로봇 홈」이
    스캔 대기 위치 길이 아니라 되돌리기(거절)로 계속 와야 한다."""
    robot = FakeRobot(BETWEEN)
    _env, halted = _run("dus6_park.script", robot)
    assert halted and robot.moves == [] and robot.reg(360) == []
    assert "NOT AT HOME" in robot.popups[0]


def test_unpark_between_poses_refuses_and_keeps_the_state() -> None:
    robot = FakeRobot(BETWEEN)
    _env, halted = _run("dus6_unpark.script", robot)
    assert halted and robot.moves == [] and robot.reg(360) == [] and robot.reg(276) == []


@pytest.mark.parametrize("name", ("dus6_park.script", "dus6_unpark.script"))
def test_untaught_pose_refuses_to_move(name) -> None:
    robot = FakeRobot(HOME if name == "dus6_park.script" else REST)
    _env, halted = _run(name, robot, Park_reach_joint=[0, 0, 0, 0, 0, 0])
    assert halted and robot.moves == [] and robot.reg(360) == [9]
    assert "TEACH" in robot.popups[0]


# ---- 되돌리기 (받침대 -> 홈 = 로봇팔 ready) ------------------------------------------
def test_unpark_reverses_the_path_and_ends_ready_at_home() -> None:
    robot = FakeRobot(REST)
    _env, halted = _run("dus6_unpark.script", robot)
    assert not halted
    assert _path(robot) == [("movel", REACH), ("movel", LIFT), ("movel", TURN), ("movej", HOME)]
    assert robot.moves[0][2] == 0.02, "받침대에서 떼는 구간은 park_v_touch"
    assert robot.reg(360) == [3, 4] and robot.reg(276) == [0, 1]


def test_unpark_at_home_only_reports_ready() -> None:
    robot = FakeRobot(HOME)
    _run("dus6_unpark.script", robot)
    assert robot.moves == [] and robot.reg(360) == [4] and robot.reg(276) == [1]


def test_unpark_uses_the_home_saved_in_rcs() -> None:
    saved = [-2.5, -1.6, -2.6, -1.9, -1.3, 0.1]
    regs = {328: 1, **{322 + i: round(v * 1000) for i, v in enumerate(saved)}}
    robot = FakeRobot(REST, regs=regs)
    _run("dus6_unpark.script", robot)
    assert robot.moves[-1][0] == "movej" and robot.moves[-1][1] == pytest.approx(saved)


def test_unpark_waits_for_the_vehicle_when_rcs_runs_the_job() -> None:
    robot = FakeRobot(REST, regs={266: 1}, fixed_after=50)
    _run("dus6_unpark.script", robot)
    assert robot.syncs == 50 and robot.moves and robot.reg(360)[-1] == 4


def test_unpark_gives_up_when_the_vehicle_never_fixes() -> None:
    robot = FakeRobot(REST, regs={266: 1}, fixed_after=10 ** 9)
    _env, halted = _run("dus6_unpark.script", robot, veh_wait_cyc=30)
    assert halted and robot.moves == [] and robot.reg(299) == [8]
    assert robot.reg(360) == [], "팔은 받침대 위 그대로 — 360 = 2 를 남긴다"


def test_pendant_only_unpark_does_not_wait_for_the_vehicle() -> None:
    robot = FakeRobot(REST, fixed_after=10 ** 9)          # 266 != 1 — RCS 없이 펜던트만
    _run("dus6_unpark.script", robot)
    assert robot.syncs == 0 and robot.reg(360)[-1] == 4


# ---- 태스크 · 변수 ------------------------------------------------------------------
@pytest.mark.parametrize("task, script", (("dusan_v6_park.task", "dus6_park.script"),
                                          ("dusan_v6_unpark.task", "dus6_unpark.script")))
def test_task_runs_the_script_once(task, script) -> None:
    text = (TASK / task).read_text(encoding="utf-8")
    assert re.findall(r'path="Dusan/dusan_v6/([^"]+)"', text) == [f"scripts/{script}"]
    assert 'isTaskAlwaysLoops="false"' in text and 'installationName="dusan_v6"' in text


def test_park_variables_are_declared() -> None:
    variables = (TASK / "dusan_v6.configuration.variables").read_text(encoding="utf-8")
    for name in ("Park_turn_joint", "Park_lift_joint", "Park_reach_joint", "Park_rest_joint",
                 "park_v_l", "park_v_touch"):
        assert f'name="{name}"' in variables, name
    for name in ("dus6_park.script", "dus6_unpark.script"):
        code = re.sub(r'"[^"]*"', '""', re.sub(r"#.*$", "", _python(name), flags=re.M))   # 주석 · 글 빼고
        used = set(re.findall(r"\b(Park_\w+|park_\w+)\b", code))
        assert used >= {"Park_turn_joint", "Park_rest_joint", "park_v_touch"}
        assert {u for u in used if not re.search(fr'name="{u}"', variables)} == set()


def test_register_360_belongs_to_the_park_scripts_only() -> None:
    for script in (TASK.glob("scripts*/*.script")):
        if script.name in ("dus6_park.script", "dus6_unpark.script"):
            continue
        assert not re.search(r"port_register\(360\b", script.read_text(encoding="utf-8")), script.name
