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
def test_eoat_offset_is_not_used_by_any_motion_script(version) -> None:
    folder = ROOT / "robot_task" / version
    for path in sorted(folder.glob("scripts*/*.script")):
        if path.stem.endswith("_init"):
            continue
        assert "eoat_ofs" not in _code(path.read_text(encoding="utf-8")), path.name


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
