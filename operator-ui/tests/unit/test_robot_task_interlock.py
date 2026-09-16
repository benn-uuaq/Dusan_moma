"""로봇 태스크(v4·v5)의 차량 인터락이 두 판에 똑같이 들어 있는지 본다.

레지스터 두 개로 주고받는 약속이다(robot_task/DUSAN_OVERVIEW.md 3-1-3).
  276  로봇 -> RCS  홈 위치 플래그 (홈이면 1, 벗어나면 0)
  309  RCS -> 로봇  차량 고정 확인 (차량 정지·아웃트리거 고정·리프트 정지면 1)

한쪽 판만 고치고 다른 쪽을 잊는 일, 스크립트만 고치고 `.task` 안의 캐시를
다시 채우지 않는 일(펜던트는 캐시로 돈다)을 여기서 잡는다.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
VERSIONS = {
    "dusan_task_v4": "dus",
    "dusan_task_v5": "dus5",
}
SCRIPT_FOLDERS = ("scripts", "scripts_nosensor")
ZERO_FOLDERS = ("scripts", "scripts_nosensor", "scripts_nosensor_seq")


def _text(version: str, folder: str, name: str) -> str:
    prefix = VERSIONS[version]
    return (ROOT / "robot_task" / version / folder / f"{prefix}_{name}.script").read_text(
        encoding="utf-8")


@pytest.mark.parametrize("version", sorted(VERSIONS))
@pytest.mark.parametrize("folder", SCRIPT_FOLDERS)
def test_init_publishes_the_home_flag_every_cycle(version, folder) -> None:
    s = _text(version, folder, "init")
    assert "def hm_flag():" in s
    # 발행 스레드 안에서 부른다 — 그래야 홈을 떠나는 순간 0 이 된다.
    assert "  write_port_register(293, alive)\n  hm_flag()" in s
    assert "write_port_register(276, 1)" in s and "write_port_register(276, 0)" in s
    # 홈 판정은 관절(Home_joint) 먼저, 안 맞으면 TCP(Home_pose).
    assert "get_actual_joint_positions()" in s and "home_tol_j" in s
    assert "Home_pose" in s and "home_tol_p" in s


@pytest.mark.parametrize("version", sorted(VERSIONS))
@pytest.mark.parametrize("folder", SCRIPT_FOLDERS)
def test_init_waits_for_the_vehicle_before_it_moves(version, folder) -> None:
    s = _text(version, folder, "init")
    assert "def ini_veh_wait():" in s
    assert "read_port_register(309, True) != 1" in s
    assert "write_port_register(290, 14)" in s, "왜 서 있는지 알려야 한다"
    assert "write_port_register(299, 8)" in s, "시간 초과는 오류로 남긴다"
    assert "veh_wait_cyc" in s and "halt()" in s
    # 로봇만 단독으로 돌리는 판(외부 제어가 아님)에서는 건너뛴다.
    assert "read_port_register(266, True) != 1" in s
    # 첫 움직임(다음 노드 move_home) 전에 부른다 = 파일의 마지막 명령.
    body = [line for line in s.splitlines() if line.strip() and not line.startswith("#")]
    assert body[-1] == "ini_veh_wait()"


@pytest.mark.parametrize("version", sorted(VERSIONS))
@pytest.mark.parametrize("folder", ZERO_FOLDERS)
def test_scan_starts_only_after_the_vehicle_is_held(version, folder) -> None:
    s = _text(version, folder, "goto_zero")
    assert "def gz_veh_wait():" in s
    call = s.index("\ngz_veh_wait()")
    wet = s.index("write_port_register(290, 8)")      # 적심 구간 시작
    assert call < wet, "적심·스캔에 들어가기 전에 확인해야 한다"


@pytest.mark.parametrize("version", sorted(VERSIONS))
def test_new_variables_are_declared_for_the_pendant(version) -> None:
    name = version.replace("dusan_task_", "dusan_")
    s = (ROOT / "robot_task" / version / f"{name}.configuration.variables").read_text(
        encoding="utf-8")
    for var in ("home_tol_j", "home_tol_p", "veh_wait_cyc"):
        assert f'name="{var}"' in s, f"{name}: {var} 선언이 없다"


@pytest.mark.parametrize("version", sorted(VERSIONS))
@pytest.mark.parametrize("tool", ("check_scripts.py", "sync_task_cache.py"))
def test_task_files_are_consistent(version, tool) -> None:
    """블록 균형과 `.task` 안의 스크립트 캐시를 태스크 자체 도구로 확인한다."""
    folder = ROOT / "robot_task" / version
    args = [sys.executable, str(folder / "tools" / tool)]
    if tool == "sync_task_cache.py":
        args.append("--check")
    done = subprocess.run(args, cwd=folder, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
