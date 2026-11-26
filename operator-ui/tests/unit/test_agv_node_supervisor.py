"""AGV 제어 노드를 RCS 가 띄우고 거두는지 (2026-11-24) — 가짜 실행 파일로 본다."""

import os
import stat

import pytest

from smr_operator_ui.services import agv_node_supervisor as agv


def _install(tmp_path, body: str):
    """AMENT_PREFIX_PATH 아래에 가짜 agv_controller 와 controller.yaml 을 둔다."""
    exe = tmp_path / "lib" / "steer_agv_controller_py" / "agv_controller"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n" + body + "\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    params = tmp_path / "share" / "steer_agv_bringup" / "config" / "controller.yaml"
    params.parent.mkdir(parents=True)
    params.write_text("QUAD_STEER_AGV_CONTROLLER:\n  ros__parameters:\n    plc_port: 8501\n")
    return exe, params


@pytest.fixture
def supervisor(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(agv, "LOG_PATH", tmp_path / "agv.log")
    node = agv.AgvNodeSupervisor()
    said: list[str] = []
    node.activity.connect(said.append)
    yield node, said
    node.stop()


def test_command_passes_the_plc_endpoint_after_the_params_file(supervisor, tmp_path, monkeypatch):
    node, _said = supervisor
    exe, params = _install(tmp_path, "sleep 30")
    monkeypatch.setenv("AMENT_PREFIX_PATH", str(tmp_path))
    command = node.command()
    assert command[:4] == [str(exe), "--ros-args", "--params-file", str(params)]
    pairs = [command[i + 1] for i, arg in enumerate(command) if arg == "-p"]
    assert pairs[:5] == ["plc_enabled:=true", "plc_host:=192.168.11.10", "plc_port:=8501",
                         "plc_writes_enabled:=true", "debug_ui:=false"], "연결 설정 값을 yaml 위에 덮는다"
    # AGV 노드 기본 기록 폴더는 AGV 개발자 PC 경로(/home/rnd08/…) — 이 PC 경로로 바꿔 넘긴다.
    assert pairs[5].startswith("debug_log_dir:=") and pairs[5].endswith("steer_agv_debug")
    assert not any(p.startswith("max_lift_height") for p in pairs), "모르면 노드 기본값"
    node.max_lift_m = 5
    assert node.command()[-2:] == ["-p", "max_lift_height:=5.000"], "실수로 — 노드가 float 로 선언했다"
    monkeypatch.setenv("AMENT_PREFIX_PATH", str(tmp_path / "없음"))
    assert node.command() is None


def test_start_stop_and_state(supervisor, tmp_path, monkeypatch):
    node, said = supervisor
    _install(tmp_path, "sleep 30")
    monkeypatch.setenv("AMENT_PREFIX_PATH", str(tmp_path))
    monkeypatch.setattr(node, "local_pids", lambda exclude=None: [])
    states: list[str] = []
    node.state_changed.connect(states.append)
    node.plc_host, node.plc_port = "127.0.0.1", 8501
    assert node.start() and node.running
    assert "127.0.0.1:8501" in node.state and "AGV 제어 노드를 시작했습니다" in said[-1]
    assert not node.start(), "이미 돌면 또 띄우지 않는다"
    node.stop()
    assert not node.running and node.state == "꺼짐" and states[-1] == "꺼짐"


def test_does_not_start_when_another_vehicle_node_is_there(supervisor, tmp_path, monkeypatch):
    node, said = supervisor
    _install(tmp_path, "sleep 30")
    monkeypatch.setenv("AMENT_PREFIX_PATH", str(tmp_path))
    monkeypatch.setattr(node, "local_pids", lambda exclude=None: [])
    node.external_present = lambda: True
    assert not node.start() and not node.running
    assert "외부 차량 노드" in node.state
    node.external_present = lambda: False
    monkeypatch.setattr(node, "local_pids", lambda exclude=None: [4242])
    assert not node.start() and "4242" in node.state


def test_a_node_that_dies_at_once_is_reported_with_its_log(supervisor, tmp_path, monkeypatch, qtbot):
    node, said = supervisor
    _install(tmp_path, 'echo "ValueError: invalid PLC endpoint"; exit 3')
    monkeypatch.setenv("AMENT_PREFIX_PATH", str(tmp_path))
    monkeypatch.setattr(node, "local_pids", lambda exclude=None: [])
    node._watch.setInterval(100)
    assert node.start()
    qtbot.waitUntil(lambda: not node.running and "종료 코드 3" in node.state, timeout=3000)
    assert "invalid PLC endpoint" in said[-1]


def test_finds_running_controllers_by_executable_name():
    """우리 자신(pytest)은 agv_controller 가 아니다 — 셀 것이 없다."""
    assert os.getpid() not in agv.AgvNodeSupervisor.local_pids()
