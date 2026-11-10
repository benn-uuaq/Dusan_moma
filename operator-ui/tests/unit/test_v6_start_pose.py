"""v6 — 저장하는 위치는 툴 플랜지 TCP 기준, 홀더 오프셋은 3점 측정 · 스캔에만 (2026-11-09).

홈 · 스캔 대기 위치 · 그리퍼 접촉 위치(시작 포즈) · 조립 위치는 툴 플랜지 TCP
[0, 0, 0, 0, 0, 0] 로 저장하고, 장착 홀더(5축 · 8축 · 노그리퍼)와 무관하다. 홀더 · EOAT 길이는
3점 측정 동작(곡률 중심 · 호 회전)과 스캔 중 좌표 계산에만 쓴다.
"""

import pathlib
import re

import pytest

TASK = pathlib.Path(__file__).resolve().parents[3] / "robot_task" / "dusan_task_v6"
FOLDERS = ("scripts", "scripts_nosensor")


def _code(folder: str, name: str) -> str:
    """주석을 뺀 스크립트 본문."""
    text = (TASK / folder / name).read_text(encoding="utf-8")
    return "\n".join(re.sub(r"#.*$", "", line) for line in text.splitlines())


@pytest.mark.parametrize("folder", FOLDERS)
def test_gripper_position_is_used_as_a_flange_pose(folder):
    """티칭한 그리퍼 접촉 위치를 그대로 플랜지 pose 로 쓴다 — 어떤 홀더 오프셋도 빼지 않는다."""
    code = _code(folder, "dus6_init.script")
    assert re.search(r"^gripper_touch_flange = gripper_target_pose\s*$", code, re.M)
    assert "teach_ofs" not in code


@pytest.mark.parametrize("folder", FOLDERS)
def test_moves_to_the_start_pose_use_the_flange_tcp(folder):
    code = _code(folder, "dus6_move_start.script")
    assert "eoat_ofs" not in code
    assert "set_tcp([0, 0, 0, 0, 0, 0])" in code


@pytest.mark.parametrize("folder", FOLDERS)
def test_waypoint_between_probes_keeps_the_tip_tcp_through_the_blend(folder):
    """프로브 사이 경유점은 블렌드(r_l)로 지나가 다음 노드(끝점 TCP)로 이어진다.

    블렌드 중에 TCP 를 바꾸면 목표가 홀더 길이만큼 튀어 4·5·6 축이 꺾이며 멈춘다
    (2026-11-10, 왼쪽 → 오른쪽 프로브 이동 중). 그래서 경유점도 끝점 TCP 로 지나가고, TCP 는
    블렌드 없는 movel 뒤(로봇이 서 있을 때)에만 바꾼다. 목표 자체는 플랜지 pose start_pose.
    """
    code = _code(folder, "dus6_move_start_waypoint.script")
    body = code[code.index("def mw_tip_movel"):]
    func, main = body.split("\nend\n", 1)
    assert "set_tcp(eoat_ofs)" in func
    assert "movel(pose_trans(target, eoat_ofs), a = a_move, v = v_move, r = blend)" in func
    assert "mw_tip_movel(start_pose, r_l)" in main
    # 블렌드 이동 뒤에는 TCP 를 바꾸지 않는다 — 다음 노드가 끝점 TCP 로 그대로 이어 간다.
    assert "set_tcp" not in main[main.index("mw_tip_movel(start_pose, r_l)"):]


@pytest.mark.parametrize("folder", FOLDERS)
@pytest.mark.parametrize("name", ("dus6_probe_r.script", "dus6_goto_zero.script"))
def test_nodes_after_the_waypoint_do_not_switch_an_equal_tcp(folder, name):
    """경유점 다음 노드는 TCP 가 이미 끝점이면 다시 걸지 않는다(움직이는 중일 수 있다)."""
    code = _code(folder, name)
    assert re.search(r"if \(pose_dist\(get_tcp_offset\(\), eoat_ofs\) > 0\.000001\):\s*\n\s*set_tcp\(eoat_ofs\)",
                     code)


@pytest.mark.parametrize("folder", FOLDERS)
@pytest.mark.parametrize("name", ("dus6_home.script", "dus6_go_home.script", "dus6_leave_home.script",
                                  "dus6_finish.script"))
def test_home_and_standby_moves_never_use_the_holder_offset(folder, name):
    path = TASK / folder / name
    if not path.exists():
        pytest.skip("이 판에는 없는 스크립트")
    assert "eoat_ofs" not in _code(folder, name)


@pytest.mark.parametrize("folder", FOLDERS)
def test_three_point_probing_still_uses_the_holder_offset(folder):
    """3점 측정은 실제로 붙은 홀더 길이로 곡률 중심 · 호 회전을 잡는다."""
    assert "eoat_ofs" in _code(folder, "dus6_probe_l.script")
