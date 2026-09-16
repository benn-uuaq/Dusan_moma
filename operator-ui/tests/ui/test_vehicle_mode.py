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
