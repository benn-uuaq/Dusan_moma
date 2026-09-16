"""차량 제어 선택(더미 / ROS 차량)과 작업 시작 시 안내 시험.

실제로 겪은 문제: 차량 제어가 기본 '더미'인 채로 MQTT 작업을 시작하면 화면
순서는 다 흐르는데 차량은 가만히 있었다. 무엇이 잘못됐는지 알기 어려웠다.
"""

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


def test_choosing_from_the_list_applies_without_saving(window) -> None:
    combo = window.screens["connection"].field("차량 제어")
    assert window.amr.active == "dummy"

    combo.setCurrentIndex(1)
    combo.activated.emit(1)                      # 목록에서 고른 것 — 저장은 안 누른다

    assert window.amr.active == "vehicle"
    assert window.lift.active == "vehicle" and window.outrigger.active == "vehicle"


def test_starting_a_job_says_the_vehicle_is_only_a_dummy(window) -> None:
    from datetime import date
    window._start_inspection()
    # 시작할 때 여러 줄이 이어 나와 화면 문구는 덮인다 — 기록으로 확인한다.
    lines = [r[4] for r in window.data_recorder.read_events(date.today())]
    assert any("더미" in text and "실제로 움직이지 않습니다" in text for text in lines), lines[-3:]
    window._stop_inspection()


def test_starting_a_job_warns_when_the_vehicle_is_not_reachable(window) -> None:
    combo = window.screens["connection"].field("차량 제어")
    combo.setCurrentIndex(1)
    combo.activated.emit(1)

    window._start_inspection()

    # ROS·vehicle_interfaces 가 없는 환경이면 그 사실을, 있으면 상태 없음을 알린다.
    assert any("차량" in text for text in _alarms(window)), _alarms(window)
    window._stop_inspection()


def test_mode_is_not_switched_while_a_job_runs(window) -> None:
    window._start_inspection()
    combo = window.screens["connection"].field("차량 제어")
    combo.setCurrentIndex(1)
    combo.activated.emit(1)
    assert window.amr.active == "dummy", "작업 중에는 바꾸지 않는다"
    assert "작업 중" in window.main_screen.activity_label.text()
    window._stop_inspection()
