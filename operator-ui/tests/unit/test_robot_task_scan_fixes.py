"""로봇 태스크(v4·v5) 스캔 문제 네 가지가 두 판에 모두 고쳐져 있는지 본다.

  1. 스캔이 100 mm/s 가 아니라 10 mm/s 이하로 느려짐
     - 속도에 비율을 스크립트에서 곱해 셀마다 누적됐다 -> 기준값에서 매번 새로 정한다
     - v5 servoj 호는 실제로 흐른 시간으로 전진한다
  2. 1A 뒤 1B 에서 좌우 프로브 회전이 커져 특이점
     - 발행 스레드가 260~265(RCS 입력 칸)에 TCP 를 덮어써, 다음 셀이 그 좌표를
       반지름·두께로 읽었다 -> 그 자리에 쓰지 않는다
  3. EOAT 중심 오프셋 [x, y, z] mm — 스캔 좌표에만, 로봇 동작에는 안 씀
  4. push_offset [mm] — 센서가 잡은 자리에서 더 민다
"""

from pathlib import Path

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
        if path.stem.endswith(("_home", "_move_start", "_move_start_waypoint")):
            assert "eoat_ofs" not in code, path.name


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
    assert 'name="eoat5_offset" value="[0, 0, 0]"' in text
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
