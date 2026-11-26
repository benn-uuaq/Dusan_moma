"""「ROS 도메인 ID」를 바꾸면 표시가 바로 바뀌고, 저장하면 바로 새 도메인으로 다시 붙는다 (2026-11-26)."""

from smr_operator_ui.app import OperatorWindow


def _window(qtbot, monkeypatch):
    monkeypatch.setenv("ROS_DOMAIN_ID", "30")
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    return window


def test_label_follows_the_field(qtbot, monkeypatch) -> None:
    window = _window(qtbot, monkeypatch)
    screen = window.screens["connection"]
    screen.set_ros_domain_state()
    assert screen.ros_domain_label.text().startswith("지금 30")
    screen.field("ROS 도메인 ID").setText("31")
    assert "입력 31 — 저장하면 31 로 다시 붙습니다" in screen.ros_domain_label.text()
    screen.field("ROS 도메인 ID").setText("300")
    assert "0~232" in screen.ros_domain_label.text()
    window.close()


def test_save_reconnects_on_the_new_domain(qtbot, monkeypatch) -> None:
    window = _window(qtbot, monkeypatch)
    window._ros_started = True
    calls = []
    window.ros_status.stop = lambda: calls.append("ros stop")
    window.ros_status.start = lambda: calls.append("ros start")
    node = window.robot_node
    node._process = object()
    node.stop = lambda timeout=5.0: calls.append("node stop")
    node.start = lambda already_running=False: calls.append("node start") or True
    screen = window.screens["connection"]
    screen.field("ROS 도메인 ID").setText("31")

    window._apply_ros_domain()
    import os
    assert os.environ["ROS_DOMAIN_ID"] == "31"
    assert calls == ["ros stop", "ros start", "node stop", "node start"]
    assert screen.ros_domain_label.text().startswith("지금 31")
    assert "입력" not in screen.ros_domain_label.text()
    node._process = None
    window.close()


def test_running_job_defers_the_switch(qtbot, monkeypatch) -> None:
    window = _window(qtbot, monkeypatch)
    window._ros_started = True
    window._job_running = lambda: True
    stops = []
    window.ros_status.stop = lambda: stops.append(1)
    screen = window.screens["connection"]
    screen.field("ROS 도메인 ID").setText("31")
    window._apply_ros_domain()
    import os
    assert os.environ["ROS_DOMAIN_ID"] == "30" and stops == []
    assert "작업 중이라" in screen.ros_domain_label.text()
    window.close()


def test_domain_switch_reconnects_the_robot_without_a_link_alarm(qtbot, monkeypatch) -> None:
    """노드를 다시 띄우는 동안 로봇이 잠깐 끊겨도 알람(E9205) 없이 다시 붙인다(2026-11-26)."""
    window = _window(qtbot, monkeypatch)
    window._ros_started = True
    window.ros_status.stop = lambda: None
    window.ros_status.start = lambda: None
    window.robot_node._process = None
    window.robot_node.local_node_pids = lambda: []
    window._robot_endpoint_sent = ("192.168.11.30", 502)
    alarms = []
    window._add_alarm = lambda code, text: alarms.append(code)
    window.screens["connection"].field("ROS 도메인 ID").setText("31")

    window._apply_ros_domain()
    assert window._robot_link_quiet() and window._robot_endpoint_sent is None
    window._robot_link_announced = True
    window._announce_robot_link(False)                 # 노드가 다시 뜨며 끊겼다
    assert alarms == []
    window.close()
