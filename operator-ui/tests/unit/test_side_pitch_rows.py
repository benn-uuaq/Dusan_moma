"""ERUT 좌·우 끝 이동높이(if-0.12) — RCS 규칙(scan_row_heights)과 로봇 태스크(v6)가 같은 줄을 훑는지.

로봇 스크립트(dus6_init 의 줄 수 · dus6_up 의 상승)를 파이썬으로 바꿔 그대로 돌린다 —
Elite 스크립트는 `end` 만 빼면 거의 파이썬이다. 센서판 · 논센서판 둘 다 본다.
"""

import math
import pathlib

import pytest

from smr_operator_ui.components.rect_work_view import ideal_path
from smr_operator_ui.services.job_sequencer import SCAN_ROW_LIMIT, scan_row_heights

TASK = pathlib.Path(__file__).resolve().parents[3] / "robot_task" / "dusan_task_v6"
FOLDERS = ("scripts", "scripts_nosensor")


def _python(script: str) -> str:
    """Elite 스크립트 → 파이썬(end 는 pass)."""
    return "\n".join(line[:len(line) - len(line.lstrip())] + "pass"
                     if line.strip() == "end" else line for line in script.splitlines())


def _rows_block(folder: str) -> str:
    text = (TASK / folder / "dus6_init.script").read_text(encoding="utf-8")
    start = text.index("if (pitch_l < 0):")
    end = text.index("overlap = scan_h - pitch", start)
    return _python(text[start:end])


def _run_robot(folder: str, height: float, left: float, right: float, scan_h: float = 30.0,
               z_base: float = 0.0, zero_z: float = 0.0):
    """init 으로 줄 수를 세고, 펜던트 Loop 처럼 pass → up 을 돌며 줄 높이를 모은다."""
    scope = {"app_height": height, "scan_h": scan_h, "pitch_l": left, "pitch_r": right,
             "ceil": math.ceil}
    exec(_rows_block(folder), scope)
    moves, regs = [], {}
    scope.update({
        "row_idx": 0, "dir_sign": 1, "z_base": z_base, "a_scan": 1.0, "v_scan": 0.1,
        "zero_pose": [0.0, 0.0, zero_z, 0.0, 0.0, 0.0],
        "path_len": 0.0, "floor": math.floor,
        "write_port_register": lambda a, v: regs.__setitem__(a, v),
        "read_port_register": lambda a, signed=True: 100,
        "get_actual_tcp_pose": lambda: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "movel": lambda pose, a, v: moves.append(round(pose[2] * 1000 - zero_z * 1000, 3)),
    })
    up = _python((TASK / folder / "dus6_up.script").read_text(encoding="utf-8"))
    heights = [0.0]
    segments = []
    for _ in range(200):                                   # 펜던트 Loop 200 회
        if scope["row_idx"] >= scope["rows"]:
            break
        scope["row_idx"] += 1                              # pass_r / pass_l 의 끝
        scope["dir_sign"] = -scope["dir_sign"]
        if scope["row_idx"] < scope["rows"]:
            regs.pop(277, None)
            exec(up, scope)
            heights.append(round(scope["row_y"], 3))
            segments.append(regs.get(277))
    return scope["rows"], heights, moves, segments


CASES = [(200, 0, 40), (200, 40, 0), (200, 40, 40), (200, 0, 30), (200, 30, 0),
         (200, 15, 25), (200, 0, 300), (500, 12.5, 7.5), (0, 0, 40), (800, 4.1, 4)]


@pytest.mark.parametrize("folder", FOLDERS)
@pytest.mark.parametrize("height, left, right", CASES)
def test_robot_task_climbs_like_the_rcs_rule(folder, height, left, right):
    rows, heights, moves, segments = _run_robot(folder, height, left, right)
    expected = scan_row_heights(height, left, right)
    assert rows == len(expected)
    assert heights == pytest.approx(expected)
    # 오르는 상승만 움직이고 줄 바꿈(277 = 2)을 낸다 — 0 이면 같은 높이를 되돌아 훑는다.
    rises = [b - a for a, b in zip(expected, expected[1:])]
    assert moves == pytest.approx([h for h, r in zip(expected[1:], rises) if r > 0])
    assert segments == [2 if r > 0 else None for r in rises]


@pytest.mark.parametrize("folder", FOLDERS)
def test_without_side_pitch_the_robot_still_splits_evenly(folder):
    """356 · 357 이 0 · 0 이면 예전처럼 scan_h 로 고르게 나눈다(RCS 단독 작업)."""
    rows, heights, _moves, segments = _run_robot(folder, 140, 0, 0, scan_h=30)
    assert rows == 6 and heights == pytest.approx([0, 28, 56, 84, 112, 140])
    assert segments == [2] * 5


def test_first_line_is_at_the_bottom_and_the_last_on_the_top_edge():
    assert scan_row_heights(200, 0, 40) == [0, 40, 40, 80, 80, 120, 120, 160, 160, 200, 200]
    assert scan_row_heights(200, 40, 40) == [0, 40, 80, 120, 160, 200]
    # 위 끝을 넘는 상승은 위 끝까지로 줄인다.
    assert scan_row_heights(200, 0, 30)[-3:] == [180, 200, 200]
    assert scan_row_heights(200, 0, 300) == [0, 200, 200]
    assert scan_row_heights(0, 0, 40) == [0]


def test_row_limit_matches_the_pendant_loop():
    assert SCAN_ROW_LIMIT == 200
    assert len(scan_row_heights(800, 0, 5)) == SCAN_ROW_LIMIT + 1     # 넘으면 거기서 멈춘다


def test_screen_draws_the_side_pitch_rows():
    """화면 ㄹ자 경로 — 같은 높이가 이어지면 오르지 않고 되돌아 그린다."""
    pts, plan = ideal_path(300, 200, 30, 0, row_heights=[0, 40, 40, 80])
    assert plan["rows"] == 4 and plan["pitch"] == 40
    assert pts == [(0, 0), (300, 0), (300, 40), (0, 40), (300, 40), (300, 80), (0, 80)]


# ---- 리프트 대신 팔 올리기 (358, 2026-11-09) ---------------------------------------
def _arm_raise_block(folder: str) -> str:
    text = (TASK / folder / "dus6_init.script").read_text(encoding="utf-8")
    start = text.index("if (scan_z_ofs < 0):")
    end = text.index("\n", text.index("gripper_touch_flange[2] = gripper_touch_flange[2]", start))
    return _python(text[start:end])


class _Halt(Exception):
    pass


def _run_arm_raise(folder: str, offset: float, height: float):
    calls: list[tuple] = []

    def halt():
        raise _Halt

    scope = {
        "scan_z_ofs": offset, "app_height": height,
        "gripper_touch_flange": [-0.8, 0.0, -0.5, 1.57, 0.0, -1.57],
        "write_port_register": lambda a, v: calls.append(("reg", a, v)),
        "popup": lambda **kw: calls.append(("popup",)),
        "halt": halt,
    }
    try:
        exec(_arm_raise_block(folder), scope)
    except _Halt:
        calls.append(("halt",))
    return scope["gripper_touch_flange"], calls


@pytest.mark.parametrize("folder", FOLDERS)
def test_start_pose_rises_by_the_arm_offset(folder):
    """358 = 800 이면 시작 자세가 0.8 m 위로 — 원점 · 3점 측정 · ㄹ자가 다 같이 오른다."""
    pose, calls = _run_arm_raise(folder, 800, 200)
    assert pose[2] == pytest.approx(-0.5 + 0.8) and calls == []
    pose, calls = _run_arm_raise(folder, -50, 200)          # 음수는 0
    assert pose[2] == pytest.approx(-0.5)


@pytest.mark.parametrize("folder", FOLDERS)
def test_height_limit_is_checked_by_rcs_not_the_robot(folder):
    """최대 작업 가능 높이(베이스 높이 + 리프트 한계 + 팔 상승 한계)는 RCS 가 본다 — 로봇 태스크는
    팔 상승 한계를 시작 자세에서 재지 않는다(2026-11-09). 큰 올림도 그대로 올린다."""
    pose, calls = _run_arm_raise(folder, 3000, 800)
    assert pose[2] == pytest.approx(-0.5 + 3.0) and calls == []
    text = (TASK / folder / "dus6_init.script").read_text(encoding="utf-8")
    assert "z_reach_max" not in text and "write_port_register(299, 10)" not in text


@pytest.mark.parametrize("folder", FOLDERS)
def test_arm_offset_is_read_only_from_rcs(folder):
    """펜던트로 돌릴 때(266 = 0)는 0 — 358 은 266 = 1 일 때만 읽는다."""
    text = (TASK / folder / "dus6_init.script").read_text(encoding="utf-8")
    block = text[text.index("scan_z_ofs = 0"):text.index("scan_z_ofs = read_port_register(358, True)")]
    assert "if (read_port_register(266, True) == 1):" in block


@pytest.mark.parametrize("folder", FOLDERS)
def test_rows_climb_from_the_origin_height_not_the_start_pose(folder):
    """툴이 조금 기울면 원점(zero_pose)이 시작 자세 높이(z_base)보다 몇 mm 높게 잡힌다 —
    줄 바꿈은 원점 높이 기준이어야 한다. 예전에는 1 mm 올라가야 할 때 2.5 mm 내려갔다."""
    _rows, _heights, moves, _segments = _run_robot(folder, 1, 0, 1, z_base=0.5, zero_z=0.5035)
    assert moves == pytest.approx([1.0])           # 원점에서 1 mm 위 — 아래로 가지 않는다
