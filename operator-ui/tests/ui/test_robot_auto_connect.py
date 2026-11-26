"""RCS 를 켜면 저장된 로봇 주소로 저절로 붙는다 · Fast DDS 설정을 건다 (2026-11-24).

현장(WSL 미러 네트워크)에서 RCS 가 「연결 안 됨」이었다 — 노드를 주소 없이 띄웠고(기본값 192.168.227.134),
같은 PC 의 노드끼리 ROS 탐색이 안 됐다(127.0.0.1 로 가는 1.4 KB 넘는 UDP 가 사라진다).
"""

import os

from smr_operator_ui import app as app_module
from smr_operator_ui.app import OperatorWindow


def _window(qtbot):
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    window._ros_started = True                      # 시험: ROS 를 켠 것처럼
    calls: list[tuple] = []
    window.ros_status.set_robot_endpoint = lambda ip, port=None: calls.append(("endpoint", ip, port)) or True
    window._handle_cobot_command = lambda name: calls.append(("command", name))
    window.screens["tpac_bridge"]._start_robot = lambda: None
    conn = window.screens["connection"]
    conn.field(conn.ROBOT_IP_FIELD).setText("192.168.11.30")
    return window, calls


def test_loaded_settings_connect_the_robot_once_the_node_answers(qtbot, monkeypatch):
    window, calls = _window(qtbot)
    monkeypatch.setattr(window, "ROBOT_AUTO_CONNECT_RETRY_MS", 50)
    ready = {"value": False}
    window.ros_status.robot_params_ready = lambda: ready["value"]

    window._auto_connect_robot()
    qtbot.wait(120)
    assert calls == [], "노드가 아직 안 떴으면 기다린다"
    ready["value"] = True
    qtbot.waitUntil(lambda: ("command", "connect") in calls, timeout=2000)
    assert ("endpoint", "192.168.11.30", window.screens["connection"].robot_port()) in calls
    window.close()


def test_no_auto_connect_after_the_operator_disconnects_or_when_already_linked(qtbot):
    window, calls = _window(qtbot)
    window.ros_status.robot_params_ready = lambda: True
    window._robot_endpoint_sent = ("192.168.11.30", window.screens["connection"].robot_port())
    window._robot_link_up = True
    window._auto_connect_robot()
    assert calls == [], "같은 주소로 이미 붙어 있다"

    window._robot_link_up = False
    window._robot_manual_disconnect = True
    window._auto_connect_robot()
    assert calls == [], "작업자가 「연결 해제」를 눌렀다"
    window._connect_robot()                     # 「연결」을 다시 누르면 풀린다
    assert ("command", "connect") in calls and window._robot_manual_disconnect is False
    window.close()


def test_dds_profile_is_applied_only_when_the_shell_did_not_set_one(monkeypatch):
    monkeypatch.delenv("FASTRTPS_DEFAULT_PROFILES_FILE", raising=False)
    monkeypatch.delenv("RMW_IMPLEMENTATION", raising=False)
    path = app_module.apply_dds_profile()
    assert path and os.environ["FASTRTPS_DEFAULT_PROFILES_FILE"] == path
    text = open(path, encoding="utf-8").read()
    assert "<maxMessageSize>1400</maxMessageSize>" in text

    monkeypatch.setenv("FASTRTPS_DEFAULT_PROFILES_FILE", "/elsewhere.xml")
    assert app_module.apply_dds_profile() == ""
    assert os.environ["FASTRTPS_DEFAULT_PROFILES_FILE"] == "/elsewhere.xml"

    monkeypatch.delenv("FASTRTPS_DEFAULT_PROFILES_FILE")
    monkeypatch.setenv("RMW_IMPLEMENTATION", "rmw_cyclonedds_cpp")
    assert app_module.apply_dds_profile() == ""


def test_any_robot_port_drop_is_announced_once_with_the_port(qtbot):
    """29999 · 30001 · 502 중 하나라도 끊기면 어느 포트인지 알린다 — 알람은 한 번(2026-11-24)."""
    window, _calls = _window(qtbot)
    said: list[str] = []
    window.main_screen.show_activity = said.append
    window.ros_status.connected_changed.emit(True)                 # 붙어 있었다
    window.ros_status.link_detail_changed.emit("끊김 30001(Primary): 로봇이 30001 연결을 닫았습니다")
    window.ros_status.connected_changed.emit(False)
    alarms = window.cobot_manual_screen.alarm_list
    texts = [alarms.item(i).text() for i in range(alarms.count())]
    e9205 = [t for t in texts if "E9205" in t]
    assert len(e9205) == 1 and "30001(Primary)" in e9205[0]
    assert "30001(Primary)" in window.screens["connection"].link_message.text()

    window.ros_status.link_detail_changed.emit("다시 연결됨 30001(Primary)")
    window.ros_status.connected_changed.emit(True)
    assert any("30001(Primary) 다시 연결됐습니다" in t for t in said)
    window.close()


def test_connect_failures_before_the_first_link_are_not_alarms(qtbot):
    window, _calls = _window(qtbot)
    window.ros_status.link_detail_changed.emit("끊김 29999(Dashboard) · 30001(Primary) · 502(Modbus): 연결 실패")
    alarms = window.cobot_manual_screen.alarm_list
    assert not any("E9205" in alarms.item(i).text() for i in range(alarms.count()))
    assert "연결 실패" in window.screens["connection"].link_message.text()
    window.close()
