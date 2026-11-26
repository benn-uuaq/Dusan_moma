"""RCS 안의 AGV 제어 노드 · 차량 없이 시작 막기 · ROS 도메인 설정 (2026-11-24)."""

import json
import os

from smr_operator_ui.app import OperatorWindow, apply_ros_domain_setting
from smr_operator_ui.services.job_sequencer import SequencerState


def _window(qtbot):
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    window.erut.publish_event = lambda *a, **k: True
    window.erut.publish_res = lambda *a, **k: True
    window.erut.publish_error = lambda *a, **k: True
    return window


def _pick(screen, field, text):
    box = screen.field(field)
    box.setCurrentIndex(box.findText(text))


def test_internal_agv_node_follows_the_connection_settings(qtbot, monkeypatch):
    window = _window(qtbot)
    screen = window.screens["connection"]
    calls: list[str] = []
    node = window.agv_node
    node.start = lambda: calls.append(f"start {node.plc_host}:{node.plc_port}") or True
    node.restart = lambda: calls.append(f"restart {node.plc_host}:{node.plc_port}") or True
    node.stop = lambda timeout=5.0: calls.append("stop")
    window._apply_agv_node()
    assert calls == [] and "ROS 없이" in screen.agv_state_label.text()

    window._ros_started = True
    window._apply_agv_node()
    assert calls == [] and "ROS 차량 노드 고정" in screen.agv_state_label.text(), "자동 · 더미면 띄우지 않는다"

    _pick(screen, "차량 제어", "ROS 차량 노드 고정")
    window._apply_agv_node()
    assert calls == ["start 192.168.11.10:8501"]

    monkeypatch.setattr(type(node), "running", property(lambda self: True))
    screen.field("PLC 주소").setText("127.0.0.1")
    screen.field("PLC 포트").setValue(8501)
    window._apply_agv_node()
    assert calls[-1] == "restart 127.0.0.1:8501", "PLC 주소가 바뀌면 다시 띄운다"
    vehicle = window.screens["vehicle"]
    vehicle.field(vehicle.LIFT_MAX_FIELD).setValue(5000)
    vehicle.save_requested.emit("vehicle", vehicle.values())
    assert calls[-1] == "restart 127.0.0.1:8501" and node.max_lift_m == 5.0, \
        "리프트 한계 높이가 바뀌면 AGV 노드 max_lift_height 로 다시 띄운다"
    _pick(screen, "AGV 제어 노드", "외부 PC (그 PC 에서 실행)")
    window._apply_agv_node()
    assert calls[-1] == "stop" and "외부" in screen.agv_state_label.text()
    window.close()


def test_start_is_refused_when_the_vehicle_node_is_not_seen(qtbot):
    """차량 제어가 「ROS 차량 노드 고정」인데 차량 상태가 안 오면 검사 · 계측을 시작하지 않는다 —
    시작하면 첫 차량 동작에서 「차량 서비스가 없습니다」로 멈춘다(2026-11-24 현장)."""
    window = _window(qtbot)
    _pick(window.screens["connection"], "차량 제어", "ROS 차량 노드 고정")
    window._apply_vehicle_mode()
    said: list[str] = []
    window.main_screen.show_activity = said.append
    window._start_inspection()
    assert window.sequencer.state is SequencerState.IDLE
    assert "소싱" in said[-1], "ROS 가 없으면 그 까닭"
    window.vehicle._node = object()                      # ROS 에는 붙었다 — 차량 상태만 안 온다
    window._start_inspection()
    assert window.sequencer.state is SequencerState.IDLE
    assert said[-1].startswith("검사 시작 거절") and "ROS 도메인" in said[-1]
    alarms = window.cobot_manual_screen.alarm_list
    assert any("E9307" in alarms.item(i).text() for i in range(alarms.count()))
    window._start_local_calibration()
    assert said[-1].startswith("계측 시작 거절")
    window.close()


def test_ros_domain_setting_is_applied_before_ros(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"connection": {"ROS 도메인 ID": "77"}}, ensure_ascii=False))
    monkeypatch.setenv("SMR_SETTINGS_FILE", str(settings))
    monkeypatch.delenv("SMR_DATABASE_URL", raising=False)
    monkeypatch.setenv("ROS_DOMAIN_ID", "0")
    assert apply_ros_domain_setting() == "77" and os.environ["ROS_DOMAIN_ID"] == "77"
    settings.write_text(json.dumps({"connection": {"ROS 도메인 ID": ""}}))
    monkeypatch.setenv("ROS_DOMAIN_ID", "5")
    assert apply_ros_domain_setting() == "" and os.environ["ROS_DOMAIN_ID"] == "5", "비우면 환경 그대로"
