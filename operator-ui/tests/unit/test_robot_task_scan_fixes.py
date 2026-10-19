"""로봇 태스크(v4·v5) 스캔 문제 네 가지가 두 판에 모두 고쳐져 있는지 본다.

  1. 스캔이 100 mm/s 가 아니라 10 mm/s 이하로 느려짐
     - 속도에 비율을 스크립트에서 곱해 셀마다 누적됐다 -> 기준값에서 매번 새로 정한다
     - v5 servoj 호는 실제로 흐른 시간으로 전진한다
     - v5 servoj 호에는 컨트롤러 비율이 안 걸려 307 을 읽어 그 호에만 곱한다
  2. 1A 뒤 1B 에서 좌우 프로브 회전이 커져 특이점
     - 발행 스레드가 260~265(RCS 입력 칸)에 TCP 를 덮어써, 다음 셀이 그 좌표를
       반지름·두께로 읽었다 -> 그 자리에 쓰지 않는다
  3. EOAT 중심 오프셋 [x, y, z] mm — 스캔 좌표에만, 로봇 동작에는 안 씀
  4. push_offset [mm] — 센서가 잡은 자리에서 더 민다
"""

from pathlib import Path

import re

import pytest

ROOT = Path(__file__).resolve().parents[3]
VERSIONS = {"dusan_task_v4": "dus", "dusan_task_v5": "dus5"}
INIT_FOLDERS = ("scripts", "scripts_nosensor")


def _script(version: str, folder: str, name: str) -> str:
    prefix = VERSIONS[version]
    path = ROOT / "robot_task" / version / folder / f"{prefix}_{name}.script"
    return path.read_text(encoding="utf-8")


def _code(text: str) -> str:
    """주석을 뺀 코드만(주석에 옛 코드를 설명으로 남겨 두므로)."""
    return "\n".join(line.split("#")[0] for line in text.splitlines())


def _variables(version: str) -> str:
    name = version.replace("dusan_task_", "dusan_")
    return (ROOT / "robot_task" / version / f"{name}.configuration.variables").read_text(
        encoding="utf-8")


# ---- 1. 속도 ---------------------------------------------------------------------
@pytest.mark.parametrize("version", sorted(VERSIONS))
@pytest.mark.parametrize("folder", INIT_FOLDERS)
def test_speeds_are_reset_from_base_values_not_compounded(version, folder) -> None:
    code = _code(_script(version, folder, "init"))
    assert "spd_ratio" not in code, "비율을 스크립트에서 곱하면 셀마다 누적된다"
    for line in ("v_l = v_l_set", "v_j = v_j_set", "v_scan = v_scan_set"):
        assert line in code, line
    assert "v_scan = v_scan *" not in code and "v_l = v_l *" not in code


@pytest.mark.parametrize("folder", INIT_FOLDERS)
@pytest.mark.parametrize("side", ("pass_r", "pass_l"))
def test_v5_servo_arc_advances_by_real_elapsed_time(folder, side) -> None:
    code = _code(_script("dusan_task_v5", folder, side))
    assert "controller_timestamp()" in code
    assert "sv_s = sv_s + sv_v * sv_h" in code and "sv_v = sv_v + a_scan * sv_h" in code
    assert "* sv_dt\n" not in code.split("while (sv_set < sv_nset):")[1].split("servoj(")[0]


@pytest.mark.parametrize("folder", INIT_FOLDERS)
@pytest.mark.parametrize("side", ("pass_r", "pass_l"))
def test_v5_servo_arc_follows_the_speed_ratio_locally(folder, side) -> None:
    """servoj 에는 컨트롤러 비율(29999 speed -v)이 안 걸린다 — 15 % 에서도 스캔만
    100 mm/s 였다. 호 함수가 307 을 읽어 지역값으로만 줄인다(전역 v_scan 은 그대로)."""
    code = _code(_script("dusan_task_v5", folder, side))
    arc = code.split("_servo_arc(end_pose):")[1].split("\nend\n")[0]
    assert "read_port_register(307, True)" in arc
    assert "sv_vmax = v_scan * sv_ratio / 100.0" in arc
    assert "if (v_top > sv_vmax):" in arc and "v_top = sv_vmax" in arc
    assert "v_top > v_scan" not in arc
    assert "v_scan = " not in code, "전역 v_scan 에 곱하면 셀마다 누적된다"
    # 비어 있거나(0) 범위 밖이면 100 % 로 본다.
    assert "if (sv_ratio < 2 or sv_ratio > 100):" in arc


# ---- 2. 입력 칸을 덮어쓰지 않는다 ---------------------------------------------------
@pytest.mark.parametrize("version", sorted(VERSIONS))
@pytest.mark.parametrize("folder", INIT_FOLDERS)
def test_task_never_writes_into_the_rcs_input_block(version, folder) -> None:
    code = _code(_script(version, folder, "init"))
    assert "_w6(260" not in code, "260~265 는 RCS 가 쓰는 반지름·두께·EOAT 칸이다"
    for addr in range(256, 265):
        assert f"_w1({addr}," not in code and f"write_port_register({addr}," not in code


# ---- 3. EOAT 중심 오프셋 ------------------------------------------------------------
@pytest.mark.parametrize("version", sorted(VERSIONS))
@pytest.mark.parametrize("folder", INIT_FOLDERS)
def test_eoat_offset_moves_scan_coordinates_only(version, folder) -> None:
    code = _code(_script(version, folder, "init"))
    # [x, y, z] mm 를 m 로 바꿔 eoat_type 으로 고른다.
    assert "eoat5_offset[0] * 0.001" in code and "eoat8_offset[2] * 0.001" in code
    # 스캔 좌표(280~285 · 호 위치)는 오프셋을 반영한 점으로 계산한다.
    assert "scan_pose = pose_trans(cur_pose, eoat_ofs)" in code
    assert "zero_scan = pose_trans(zero_pose, eoat_ofs)" in code
    assert "adx = scan_pose[0] - arc_ox" in code
    # 로봇이 가는 자리(시작 자세)에는 오프셋을 쓰지 않는다.
    assert "gripper_touch_flange = gripper_target_pose" in code
    start = code.split("start_pose = ")[1].splitlines()[0]
    assert "eoat" not in start


@pytest.mark.parametrize("version", sorted(VERSIONS))
@pytest.mark.parametrize("folder", INIT_FOLDERS)
@pytest.mark.parametrize("script", ("probe_l", "mark_point"))
def test_arc_center_is_taken_from_the_probe_center(version, folder, script) -> None:
    """곡률 중심을 프로브 중심(플랜지 + eoat_ofs)에서 잡아야 벽 위 호 = C-scan 거리다."""
    code = _code(_script(version, folder, script))
    assert "tip = pose_trans(mid, eoat_ofs)" in code
    assert "o_t = [tip[0] + dx / n * r, tip[1] + dy / n * r, 0, 0, 0, 0]" in code
    assert "o_t = [mid[0] + dx / n * r" not in code
    if script == "probe_l":
        assert "c_tip = pose_trans(p_c_pose, eoat_ofs)" in code
        assert "arc_ox = c_tip[0] + d0x / n0 * scan_radius * 0.001" in code
        assert "arc_phi0 = atan2(z_tip[1] - arc_oy, z_tip[0] - arc_ox)" in code


@pytest.mark.parametrize("folder", INIT_FOLDERS)
@pytest.mark.parametrize("side", ("pass_r", "pass_l"))
def test_v5_line_length_is_the_probe_center_arc(folder, side) -> None:
    code = _code(_script("dusan_task_v5", folder, side))
    assert "tp = pose_trans(p0, eoat_ofs)" in code
    assert "return dphi * sgn * r_tip * 1000.0" in code


@pytest.mark.parametrize("version", sorted(VERSIONS))
def test_start_pose_and_home_do_not_use_the_offset(version) -> None:
    folder = ROOT / "robot_task" / version
    for path in sorted(folder.glob("scripts*/*.script")):
        code = _code(path.read_text(encoding="utf-8"))
        if "start_pose = " in code:
            line = code.split("start_pose = ")[1].splitlines()[0]
            assert "eoat" not in line, path.name
        if path.stem.endswith("_home"):
            assert "eoat_ofs" not in code, path.name
        if path.stem.endswith(("_move_start", "_move_start_waypoint")) and "_tip_movel" in code:
            # 끝점 TCP 로 옮겨 가도 목표는 티칭한 start_pose(플랜지) 그대로다.
            assert "_tip_movel(start_pose," in code, path.name


def test_offset_geometry_makes_wall_arc_equal_cscan() -> None:
    """태스크 계산을 그대로 옮긴 모델로 본다: 오프셋이 있어도 벽 위 호 = 721 = C-scan."""
    import math

    np = pytest.importorskip("numpy")

    def rv2m(rv):
        th = np.linalg.norm(rv)
        if th < 1e-12:
            return np.eye(3)
        k = np.asarray(rv) / th
        K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
        return np.eye(3) + math.sin(th) * K + (1 - math.cos(th)) * K @ K

    def m2rv(R):
        th = math.acos(max(-1.0, min(1.0, (np.trace(R) - 1) / 2)))
        if th < 1e-9:
            return [0.0, 0.0, 0.0]
        return list(th / (2 * math.sin(th)) * np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]]))

    def T(p):
        M = np.eye(4); M[:3, :3] = rv2m(p[3:]); M[:3, 3] = p[:3]
        return M

    def P(M):
        return list(M[:3, 3]) + m2rv(M[:3, :3])

    def pt(a, b):
        return P(T(a) @ T(b))

    def inv(a):
        return P(np.linalg.inv(T(a)))

    def wrap(a):
        return (a + math.pi) % (2 * math.pi) - math.pi

    R_MM, L_MM = 844.6, 721.0
    R, half = R_MM / 1000, L_MM / (2 * R_MM)
    for ofs_mm in ([0, 0, 0], [0, 0, 100], [30, 20, 100], [-15, 0, 180]):
        ofs = [v / 1000 for v in ofs_mm] + [0, 0, 0]
        rot = np.column_stack([[0, 1.0, 0], np.cross([1.0, 0, 0], [0, 1.0, 0]), [1.0, 0, 0]])
        p_c = list(np.array([-R, 0, 0.5]) - rot @ np.array(ofs[:3])) + m2rv(rot)   # 프로브 끝이 벽에 닿은 플랜지

        def al_arc(mid, th):                                  # dus5_probe_l.al_arc 그대로
            fwd = pt(mid, [0, 0, 1.0, 0, 0, 0])
            dx, dy = fwd[0] - mid[0], fwd[1] - mid[1]
            n = math.hypot(dx, dy)
            tip = pt(mid, ofs)
            o_t = [tip[0] + dx / n * R, tip[1] + dy / n * R, 0, 0, 0, 0]
            return pt(o_t, pt([0, 0, 0, 0, 0, th], pt(inv(o_t), mid))), o_t

        a, O = al_arc(p_c, half)
        b, _ = al_arc(p_c, -half)
        zero, right = (a, b) if a[1] >= b[1] else (b, a)
        dphi = wrap(math.atan2(right[1] - O[1], right[0] - O[0]) - math.atan2(zero[1] - O[1], zero[0] - O[0]))
        tips = [pt(pt(O, pt([0, 0, 0, 0, 0, dphi * k / 200], pt(inv(O), zero))), ofs) for k in range(201)]
        # 벽 위 반지름 그대로 남는가
        assert max(abs(math.hypot(t[0], t[1]) * 1000 - R_MM) for t in tips) < 0.05, ofs_mm
        # TPAC 이 좌표로 계산하는 거리 = 명령한 호 길이
        path = sum(math.dist(p[:3], q[:3]) for p, q in zip(tips, tips[1:])) * 1000
        assert path == pytest.approx(L_MM, abs=0.2), (ofs_mm, path)
        # pub_rel 의 286(C-scan) 끝값과 v5 줄 길이도 같다
        cscan = abs(wrap(math.atan2(tips[-1][1] - O[1], tips[-1][0] - O[0])
                         - math.atan2(tips[0][1] - O[1], tips[0][0] - O[0]))) * R_MM
        r_tip = math.hypot(tips[0][0] - O[0], tips[0][1] - O[1]) * 1000
        assert cscan == pytest.approx(L_MM, abs=0.2)
        assert abs(dphi) * r_tip == pytest.approx(L_MM, abs=0.2)


# ---- 4. push_offset -------------------------------------------------------------
@pytest.mark.parametrize("version", sorted(VERSIONS))
@pytest.mark.parametrize("probe,prefix", (("probe_c", "pc"), ("probe_l", "al"), ("probe_r", "pr")))
def test_push_offset_is_pressed_after_the_sensor_hits(version, probe, prefix) -> None:
    code = _code(_script(version, "scripts", probe))
    assert f"def {prefix}_push():" in code
    assert "push_offset * 0.001" in code and "push_offset > 0" in code
    # 센서가 적정 눌림을 잡은 바로 뒤, 접점을 돌려주기 전에 민다.
    hit = code.index("n_hit = n_hit + 1")
    push = code.index(f"{prefix}_push()", hit)
    assert push < code.index("return", hit)


@pytest.mark.parametrize("side", ("pass_r", "pass_l"))
def test_v5_press_control_keeps_the_extra_push(side) -> None:
    code = _code(_script("dusan_task_v5", "scripts", side))
    assert "get_standard_digital_in(di_limit) and (push_offset <= 0 or sv_d > 0)" in code


@pytest.mark.parametrize("version", sorted(VERSIONS))
def test_new_variables_are_declared(version) -> None:
    text = _variables(version)
    for name in ("eoat5_offset", "eoat8_offset", "eoat_ofs", "push_offset",
                 "v_l_set", "v_j_set", "v_scan_set"):
        assert f'name="{name}"' in text, name
    # 값은 현장에서 펜던트로 넣는다(5축 [0, 0, 245] 등) — 꼴만 본다.
    assert re.search(r'name="eoat5_offset" value="\[[-0-9., ]+\]"', text)
    assert 'name="v_scan_set" value="0.1"' in text
    assert "eoat5_offset_x" not in text, "옛 스칼라 오프셋(m)은 뺐다"


# ---- TPAC 동기 신호: 스캔 구간 레지스터 277 ---------------------------------------
@pytest.mark.parametrize("version", sorted(VERSIONS))
@pytest.mark.parametrize("folder", INIT_FOLDERS)
@pytest.mark.parametrize("side,segment", (("pass_r", 1), ("pass_l", 3)))
def test_scan_lines_announce_their_segment_before_moving(version, folder, side, segment) -> None:
    code = _code(_script(version, folder, side))
    lines = [l.strip() for l in code.splitlines() if l.strip()]
    motion = next(i for i, l in enumerate(lines)
                  if l.startswith("movec(") or "_servo_arc(" in l and l.startswith("pass_len ="))
    # 줄 시작: 구간 번호 -> sig_hold -> 움직임 -> 0 -> sig_hold
    assert lines[motion - 2:motion] == [f"write_port_register(277, {segment})", "sleep(sig_hold)"]
    assert lines[motion + 1:motion + 3] == ["write_port_register(277, 0)", "sleep(sig_hold)"]


@pytest.mark.parametrize("version", sorted(VERSIONS))
@pytest.mark.parametrize("folder", INIT_FOLDERS)
def test_index_finish_and_init_set_the_segment(version, folder) -> None:
    up = _code(_script(version, folder, "up"))
    assert up.index("write_port_register(277, 2)") < up.index("movel(tgt_pose")
    finish = _code(_script(version, folder, "finish"))
    assert finish.index("write_port_register(277, 0)") < finish.index("write_port_register(290, 5)")
    init_lines = _code(_script(version, folder, "init")).splitlines()
    assert "write_port_register(277, 0)" in [l.rstrip() for l in init_lines], "최상위(들여쓰기 없이)에서 0 으로"
    assert 'name="sig_hold" value="0.25"' in _variables(version)


# ---- 직선 동작 운영 상한: 100 mm/s · 400 mm/s² ------------------------------------
@pytest.mark.parametrize("version", sorted(VERSIONS))
@pytest.mark.parametrize("folder", INIT_FOLDERS)
def test_linear_motion_is_capped_at_100_mm_s_and_400_mm_s2(version, folder) -> None:
    code = _code(_script(version, folder, "init"))
    assert "v_l_limit = 0.100" in code and "a_lin_limit = 0.400" in code
    assert "a_l = a_lin_limit" in code and "a_scan = a_lin_limit" in code
    text = _variables(version)
    assert 'name="v_l_set" value="0.1"' in text and 'name="a_l" value="0.4"' in text
    assert 'name="v_scan_set" value="0.1"' in text and 'name="a_scan" value="0.4"' in text


# ---- 프로브 사이 이동은 EOAT 끝점 기준 (2026-10-12) ----------------------------------
# 플랜지 TCP 로 movel 하면 자세가 플랜지를 중심으로 돌아 245mm 앞의 끝점이 휘둘렸다.
# 이동하는 동안만 set_tcp(eoat_ofs) 로 끝점을 TCP 로 잡는다.
_TIP_MOVES = {
    "scripts": {"probe_l": "l_stand", "probe_r": "r_stand", "goto_zero": "z_stand",
                "mark_point": "mk_stand", "move_start": "start_pose"},
    "scripts_nosensor": {"probe_l": "l_stand", "probe_r": "r_stand", "goto_zero": "z_stand",
                         "mark_point": "mk_stand", "move_start": "start_pose"},
    "scripts_nosensor_seq": {"goto_zero": "z_stand"},
}


@pytest.mark.parametrize("folder,stem,target",
                         [(f, s, t) for f, m in _TIP_MOVES.items() for s, t in m.items()])
def test_moves_between_probes_turn_about_the_probe_tip(folder, stem, target) -> None:
    code = _code(_script("dusan_task_v5", folder, stem))
    lines = [l.strip() for l in code.splitlines() if l.strip()]
    call = next(i for i, l in enumerate(lines) if l.endswith(f"_tip_movel({target}, 0)"))
    # 선 뒤에 플랜지 TCP 로 되돌린다 — 접촉 탐색 · 다음 계산은 플랜지 기준이다.
    assert lines[call + 1] == "set_tcp([0, 0, 0, 0, 0, 0])"
    assert f"movel({target}, a = a_l, v = v_l)" not in lines[:call], "끝점 TCP 로 바꾸기 전에 플랜지로 갔다"
    assert "set_tcp(eoat_ofs)" in code and "movel(pose_trans(target, eoat_ofs)" in code


@pytest.mark.parametrize("folder", ("scripts", "scripts_nosensor"))
def test_waypoint_keeps_the_tip_tcp_through_the_blend(folder) -> None:
    """블렌드로 지나가는 동안 TCP 를 바꾸지 않는다 — 다음 노드가 선 뒤 되돌린다."""
    code = _code(_script("dusan_task_v5", folder, "move_start_waypoint"))
    assert "mw_tip_movel(start_pose, r_l)" in code
    assert "set_tcp([0, 0, 0, 0, 0, 0])" not in code


@pytest.mark.parametrize("folder", ("scripts", "scripts_nosensor"))
def test_init_starts_on_the_flange_and_publishes_flange_poses(folder) -> None:
    code = _code(_script("dusan_task_v5", folder, "init"))
    assert "set_tcp([0, 0, 0, 0, 0, 0])" in code
    assert "cur_pose = get_actual_tool_flange_pose()" in code
    assert "hm_p = get_actual_tool_flange_pose()" in code



# ---- 스캔 대기 위치를 거치는 홈 출입 (2026-10-15) --------------------------------
# 예전의 「홈」은 이제 스캔 대기 위치(Standby_joint, RCS 「홈 위치 저장」 = 310~315)다.
# 홈(Home_joint)은 펜던트에서 따로 티칭한다. 순서는
#   시작 : 홈 --movej--> 대기 위치 --> 시작 위치
#   끝   : 스캔 끝 --> (대기 위치 기준) 물러나고 상승 --movej--> 대기 위치 --movej--> 홈
# 물러날 때는 대기 위치보다 뒤(베이스 X)로 가지 않는다 — 예전에는 스캔 끝이 베이스
# -X 로 back_off 를, 마킹 끝이 probe_dist 를 무조건 물러났다.
V5 = ROOT / "robot_task" / "dusan_task_v5"
RETREATS = (
    ("scripts", "finish", "fn_retreat", "back_off * 0.001"),
    ("scripts_nosensor", "finish", "fn_retreat", "back_off * 0.001"),
    ("scripts", "mark_point", "mk_retreat", "probe_dist * 0.001"),
    ("scripts_nosensor", "mark_point", "mk_retreat", "probe_dist * 0.001"),
    ("scripts", "home", "hm_retreat", "probe_dist * 0.001"),
    ("scripts", "leave_home", "lh_retreat", "probe_dist * 0.001"),
)


def _func(code: str, name: str) -> str:
    return code.split(f"def {name}(")[1].split("\nend\n")[0]


def _main(code: str) -> str:
    """마지막 def 블록 뒤의 최상위 코드."""
    last = code.rindex("\ndef ")
    return code[code.index("\nend\n", last) + len("\nend\n"):]


@pytest.mark.parametrize("folder, name, func, limit", RETREATS)
def test_retreat_never_goes_behind_the_scan_standby(folder, name, func, limit) -> None:
    code = _code(_script("dusan_task_v5", folder, name))
    body = _func(code, func)
    # 기준점 = 대기 위치 관절값의 플랜지 자세. 계산값 변수는 안 쓴다.
    assert "get_forward_kin(Standby_joint)" in body
    assert "Home_" not in body and "Standby_pose" not in body
    # TCP 를 플랜지로 돌린 뒤 플랜지로 본다 — EOAT 끝점 TCP 가 남아 있어도 같다.
    assert body.index("set_tcp([0, 0, 0, 0, 0, 0])") < body.index("get_forward_kin(")
    assert "get_actual_tool_flange_pose()" in body and "get_actual_tcp_pose()" not in body
    # 벽 쪽을 볼 때만, 상한과 대기 위치 X 까지 남은 거리 중 작은 쪽만큼 툴 -Z 로.
    assert "_cz > 0.2):" in body and "> limit):" in body and "[0, 0, -" in body
    assert f"{func}({limit})" in code
    # 기준을 안 보는 옛 후퇴가 남아 있지 않다.
    assert "- back_off * 0.001" not in code
    assert "movel(pose_trans(get_actual_tcp_pose(), [0, 0, -probe_dist" not in code


def test_leaving_home_goes_to_the_standby_by_movej() -> None:
    code = _code(_script("dusan_task_v5", "scripts", "leave_home"))
    main = _main(code)
    assert "movej(Standby_joint, a = a_j, v = v_j)" in main
    assert "Home_joint" not in main, "홈으로 가지 않는다 — 홈에서 대기 위치로 나간다"
    # 홈이 아닌 데서 시작했으면 물러나고 대기 위치 선상에 맞춘 뒤에 movej.
    assert main.index("lh_retreat(") < main.index("lh_align()") < main.index("movej(")
    assert "Home_joint[lh_i]" in _func(code, "lh_at_home")


@pytest.mark.parametrize("name, func", (("go_home", "gh_align"), ("home", "hm_align"),
                                        ("leave_home", "lh_align")))
def test_standby_line_alignment_moves_back_and_up_or_down(name, func) -> None:
    """물러난 뒤 대기 위치 **선상**에 맞춘다(2026-10-16): 아직 앞이면 대기 위치 X 까지
    뒤로, 높이는 대기 위치 높이로 올리거나 **내린다**(예전에는 올라가기만 했다)."""
    body = _func(_code(_script("dusan_task_v5", "scripts", name)), func)
    p = {"gh_align": "ga", "hm_align": "ha", "lh_align": "la"}[func]   # 지역 변수 접두
    assert "set_tcp([0, 0, 0, 0, 0, 0])" in body
    assert f"if ({p}_p[0] > {p}_ref[0] + 0.001):" in body and f"{p}_p[0] = {p}_ref[0]" in body
    assert f"{p}_p[2] < {p}_ref[2] - 0.001 or {p}_p[2] > {p}_ref[2] + 0.001" in body
    # X(뒤로) 먼저, 높이는 그다음.
    assert body.index(f"{p}_p[0] = {p}_ref[0]") < body.index(f"{p}_p[2] = {p}_ref[2]")


@pytest.mark.parametrize("name, func", (("go_home", "gh_align"), ("home", "hm_align")))
def test_going_home_passes_the_standby_by_movej(name, func) -> None:
    code = _code(_script("dusan_task_v5", "scripts", name))
    main = _main(code)
    order = [main.index(f"{func}()"),
             main.index("movej(Standby_joint, a = a_j, v = v_j)"),
             main.index("movej(Home_joint, a = a_j, v = v_j)")]
    assert order == sorted(order)
    align = _func(code, func)
    assert "get_forward_kin(Standby_joint)" in align and "movel(" in align
    if name == "go_home":
        assert "write_port_register(290" not in code, "완료(290 = 5)는 finish 가 냈다"


@pytest.mark.parametrize("task", sorted(p.name for p in V5.glob("dusan_v5*.task")))
def test_tasks_enter_and_leave_home_through_the_standby(task) -> None:
    text = (V5 / task).read_text(encoding="utf-8")
    nodes = re.findall(r'display="([^"]*)"', text)
    assert not any(n.endswith("move_home") for n in nodes), "펜던트 move_home 노드가 남았다"
    assert "dus5_init" in nodes[0]
    # init 바로 다음 동작(루프판은 init 이 둘이다)이 홈 -> 대기 위치다.
    assert "dus5_leave_home" in [n for n in nodes if "dus5_init" not in n][0]
    last = "dus5_home" if "mark" in task else "dus5_go_home"
    assert last in nodes[-1]
    if "mark" not in task:
        assert "dus5_finish" in nodes[-2]


@pytest.mark.parametrize("folder", INIT_FOLDERS)
def test_init_reads_310_as_standby_and_322_as_home(folder) -> None:
    code = _code(_script("dusan_task_v5", folder, "init"))
    block = code.split("if (read_port_register(308, True) == 1):")[1].split("\nend\n")[0]
    assert "Standby_joint = ini_joints(310)" in block
    assert "Home_joint" not in block, "310~315 는 스캔 대기 위치다 — 홈이 아니다"
    # 홈은 RCS 「홈 위치 저장」(322~327, 328 = 1) — 2026-10-16.
    home = code.split("if (read_port_register(328, True) == 1):")[1].split("\nend\n")[0]
    assert "Home_joint = ini_joints(322)" in home
    # 플래그가 서 있어도 비어 있는(전부 0) 칸은 읽지 않는다.
    assert "if (ini_regs_set(310)):" in block and "if (ini_regs_set(316)):" in block
    assert "if (ini_regs_set(322)):" in home
    after = code.split("if (read_port_register(308, True) == 1):")[1].split("\nend\n", 1)[1]
    assert "Home_pose = get_forward_kin(Home_joint)" in after
    assert "Standby_pose = get_forward_kin(Standby_joint)" in after
    assert code.index("set_tcp([0, 0, 0, 0, 0, 0])") < code.index("Home_pose = get_forward_kin")


def test_standby_variables_are_declared() -> None:
    text = _variables("dusan_task_v5")
    for name in ("Standby_joint", "Standby_pose", "Home_joint", "Home_pose"):
        assert f'name="{name}"' in text, name


# ---- 스캔 끝을 TPAC 에 먼저 알리고 물러난다 (2026-10-15) ------------------------
# 290 = 6 인 채로 물러나서, 후퇴 경로와 그 뒤 0,0,0 으로 떨어지는 좌표까지 TPAC
# 스캔 데이터에 들어갔다. 마지막 스캔 좌표에서 277 = 0 · 290 = 5 를 먼저 쓰고
# sig_hold 동안 선 뒤 물러나며, 5 인 동안 발행 스레드는 280~287 을 고치지 않는다.
@pytest.mark.parametrize("folder", INIT_FOLDERS)
def test_finish_closes_the_scan_before_retreating(folder) -> None:
    main = _main(_code(_script("dusan_task_v5", folder, "finish")))
    order = [main.index("write_port_register(277, 0)"),
             main.index("write_port_register(290, 5)"),
             main.index("write_port_register(295, 1)"),
             main.index("sleep(sig_hold)"),
             main.index("fn_retreat(back_off * 0.001)")]
    assert order == sorted(order)


@pytest.mark.parametrize("folder", INIT_FOLDERS)
def test_scan_coordinates_hold_after_the_scan_ends(folder) -> None:
    body = _func(_code(_script("dusan_task_v5", folder, "init")), "pub_rel")
    hold = body.index("if (pub_st != 5):")
    assert body.index("pub_st = read_port_register(290, True)") < hold
    # 280~287 을 쓰는 곳은 모두 5 가 아닐 때만이다.
    for write in ("ini_w6(280, rel_pose)", "ini_w6(280, [0, 0, 0, 0, 0, 0])",
                  "ini_w1(286, arc_s * 10)", "ini_w1(287, path_len + arc_s)"):
        assert body.index(write) > hold, write
    assert "if (zero_ok == True and pub_st == 6):" in body
    # 워치독 · 홈 판정은 상태와 상관없이 계속 돈다.
    tail = body.split("if (pub_st != 5):")[1]
    assert "write_port_register(293, alive)" in tail and "hm_flag()" in tail
