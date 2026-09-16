"""차량 제어 선택(자동 / 더미 / ROS 차량 고정)과 작업 시작 안내 시험.

실제로 겪은 문제: 차량 모의기를 띄워 놨는데도 RCS 가 더미로 돌아 차량이
가만히 있었다. 그래서 기본을 '자동'으로 두고, 차량 상태가 들어오면 스스로
차량으로 붙는다.
"""

import time

import pytest

from smr_operator_ui.app import OperatorWindow


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("SMR_DATA_DIR", str(tmp_path / "data"))
    w = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(w)
    w.ros_status.call_command = lambda name: True
    w.ros_status.set_task_paths = lambda scan, mark: True
    yield w
    w.close()


def _alarms(window) -> list[str]:
    lst = window.cobot_manual_screen.alarm_list
    return [lst.item(i).text() for i in range(lst.count())]


def _status(window, arriving: bool) -> None:
    """차량 상태가 들어오는/끊긴 상황을 만든다(클라이언트의 실제 판정 경로)."""
    client = window.vehicle
    client._last_time = time.monotonic() if arriving else 0.0
    client._check_online()


def _pick(window, text: str) -> None:
    combo = window.screens["connection"].field("차량 제어")
    index = combo.findText(text)
    assert index >= 0, text
    combo.setCurrentIndex(index)
    combo.activated.emit(index)          # 목록에서 고른 것 — 저장은 안 누른다


def test_auto_follows_the_vehicle_status(window) -> None:
    combo = window.screens["connection"].field("차량 제어")
    assert combo.currentText().startswith("자동"), "기본은 자동"
    assert window.amr.active == "dummy", "차량 상태가 없으면 더미"

    _status(window, True)         # 차량 노드가 떴다
    assert window.amr.active == "vehicle"
    assert window.lift.active == "vehicle" and window.outrigger.active == "vehicle"

    _status(window, False)        # 꺼지면 더미로 돌아온다
    assert window.amr.active == "dummy"


def test_fixed_choices_win_over_auto(window) -> None:
    _pick(window, "더미 (차량 없이)")
    _status(window, True)
    assert window.amr.active == "dummy", "더미로 고정했으면 차량이 있어도 더미"
    assert any("더미" in text for text in _alarms(window)), _alarms(window)

    _pick(window, "ROS 차량 노드 고정")
    assert window.amr.active == "vehicle"


def test_top_bar_shows_only_the_link_state(window) -> None:
    badge = window.top_bar.badges["AMR"]
    assert badge.state_label.text() == "● 연결 안 됨"
    _status(window, True)
    assert badge.state_label.text() == "● 연결됨"


def test_starting_a_job_says_the_vehicle_is_only_a_dummy(window) -> None:
    from datetime import date
    window._start_inspection()
    # 시작할 때 여러 줄이 이어 나와 화면 문구는 덮인다 — 기록으로 확인한다.
    lines = [r[4] for r in window.data_recorder.read_events(date.today())]
    assert any("더미" in text and "실제로 움직이지 않습니다" in text for text in lines), lines[-3:]
    window._stop_inspection()


def test_starting_a_job_warns_when_the_vehicle_is_not_reachable(window) -> None:
    _pick(window, "ROS 차량 노드 고정")
    window._start_inspection()
    assert any("차량" in text for text in _alarms(window)), _alarms(window)
    window._stop_inspection()


def test_mode_is_not_switched_while_a_job_runs(window) -> None:
    window._start_inspection()
    _pick(window, "ROS 차량 노드 고정")
    assert window.amr.active == "dummy", "작업 중에는 바꾸지 않는다"
    assert "작업 중" in window.main_screen.activity_label.text()
    window._stop_inspection()


# ---- 로봇이 끝난 뒤에 차량·리프트를 움직인다 ------------------------------------------
def test_lift_waits_until_the_robot_task_ends(window, qtbot) -> None:
    """로봇은 완료 플래그를 먼저 쓰고 홈으로 간다 — 그동안 리프트를 올리면 안 된다."""
    window._on_robot_task_state(1)                      # 로봇 태스크 실행 중(홈 이동 중)
    window.sequencer.lift_target_requested.emit(480.0)

    assert window.lift.target != 480.0, "아직 올리면 안 된다"
    assert "로봇 태스크가 실행 중" in window.main_screen.activity_label.text()

    window._on_robot_task_state(3)                      # 태스크 종료(홈 도착)
    assert window.lift.target == 480.0


def test_deferred_move_runs_anyway_after_the_wait_limit(window, qtbot) -> None:
    """태스크가 스스로 안 끝나는 구성도 있으므로 한도를 넘기면 진행한다."""
    said: list[str] = []
    window.main_screen.activity_shown.connect(said.append)   # 뒤 문구에 덮이므로 모아 둔다
    window.ROBOT_TASK_WAIT_MS = 120
    window._on_robot_task_state(1)
    window.sequencer.lift_target_requested.emit(300.0)
    assert window.lift.target != 300.0

    qtbot.waitUntil(lambda: window.lift.target == 300.0, timeout=2000)
    assert any("그대로 진행합니다" in text for text in said), said


def test_stopping_drops_the_deferred_moves(window) -> None:
    window._start_inspection()
    window._on_robot_task_state(1)
    window.sequencer.amr_move_requested.emit(2)
    assert window._pending_motions

    window._stop_inspection()
    assert not window._pending_motions
    window._on_robot_task_state(3)
    assert window.amr.target == 0.0, "정지한 뒤에는 미뤄 둔 이동이 나가면 안 된다"


def test_motion_runs_at_once_when_the_robot_is_idle(window) -> None:
    window._on_robot_task_state(3)
    window.sequencer.lift_target_requested.emit(167.5)
    assert window.lift.target == 167.5


# ---- 홈 위치 플래그(레지스터 276)까지 확인한다 ----------------------------------------
def _v5_home(window, at_home: bool) -> None:
    """홈 플래그를 쓰는 판(v5)으로 두고 홈 여부를 알려 준다."""
    window._task_version = "dusan_v5"
    window._on_robot_at_home(at_home)


def test_lift_waits_until_the_robot_reaches_home(window) -> None:
    """태스크가 멈췄어도 팔이 벽 앞에 있으면(276 = 0) 리프트를 올리지 않는다."""
    window._on_robot_task_state(3)                      # 태스크는 끝났다
    _v5_home(window, False)                             # 그런데 홈이 아니다
    window.sequencer.lift_target_requested.emit(480.0)

    assert window.lift.target != 480.0
    assert "홈 위치" in window.main_screen.activity_label.text()

    _v5_home(window, True)                              # 홈 도착
    assert window.lift.target == 480.0


def test_home_flag_is_ignored_on_the_v4_task(window) -> None:
    """v4 태스크는 276 을 쓰지 않는다 — 0 이 계속 와도 막으면 안 된다."""
    window._task_version = "dusan_v4"
    window._on_robot_task_state(3)
    window._on_robot_at_home(False)
    window.sequencer.lift_target_requested.emit(300.0)
    assert window.lift.target == 300.0


def test_manual_control_is_locked_while_the_robot_is_away_from_home(window) -> None:
    window.screens["manual"].set_available(True)
    window._on_robot_task_state(3)
    _v5_home(window, False)
    assert not window.screens["manual"].jog_buttons["cmd_mv_fwd"].isEnabled()
    assert "홈 위치" in window.screens["manual"].notice.text()

    _v5_home(window, True)
    assert window.screens["manual"].jog_buttons["cmd_mv_fwd"].isEnabled()


def test_vehicle_ready_needs_outriggers_and_everything_stopped(window) -> None:
    """로봇에 보내는 차량 고정 확인(레지스터 309)의 판단."""
    sent: list[tuple] = []
    window.ros_status.send_vehicle_ready = lambda ready, force=False: sent.append((ready, force))

    assert not window._vehicle_secured(), "아웃트리거를 안 고정했다"
    window.outrigger.move_to(1, " 고정")
    assert not window._vehicle_secured(), "움직이는 중에는 아니다"
    window.outrigger.backend.reset(1)                   # 고정 완료 상태로 둔다
    assert window._vehicle_secured()

    window.lift.move_to(500.0, " mm")
    assert not window._vehicle_secured(), "리프트가 움직이면 내린다"
    window.lift.cancel()

    window._push_vehicle_ready()
    assert sent and sent[-1][0] is True


def test_robot_waiting_for_the_vehicle_is_announced(window) -> None:
    """로봇이 차량 고정을 기다리며 서 있으면(290 = 14) 화면에 이유를 남긴다."""
    said: list[str] = []
    window.main_screen.activity_shown.connect(said.append)
    window._handle_vehicle_wait([14, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    assert any("차량 고정 확인을 기다립니다" in text for text in said), said

    said.clear()
    window._handle_vehicle_wait([14, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    assert said == [], "같은 상태가 10Hz 로 계속 와도 한 번만 알린다"
