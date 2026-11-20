"""PLC 연결 표시 · 다시 연결 (2026-11-20 — AGV 판 인터페이스에 맞춤).

PLC 와는 차량(AGV) 노드만 통신한다. AGV 판 vehicle_interfaces 에는 PLC 전용 필드 · 서비스가 없어서
RCS 는 차량 상태로 본다: 상태가 안 오면 「모름」, 오면 「연결됨」, error_code 701(PLC_COMM_LOST)이면
「연결 실패」. 「PLC 다시 연결」은 차량에 리셋(RobotControl reset=1)을 보낸다 — 차량 노드가 PLC 에 다시
붙으면 701 을 스스로 푼다.
"""

import threading
import time

import pytest

from smr_operator_ui.app import OperatorWindow


def _window(qtbot):
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    window.erut.publish_event = lambda *a, **k: True
    window.erut.publish_res = lambda *a, **k: True
    window.erut.publish_error = lambda *a, **k: True
    return window


def test_plc_badge_and_connection_screen_follow_the_vehicle_status(qtbot) -> None:
    window = _window(qtbot)
    badge = window.top_bar.badges["PLC"]
    connection = window.screens["connection"]
    said: list[str] = []
    window.main_screen.show_activity = said.append
    assert "모름" in badge.state_label.text(), "차량 상태가 오기 전에는 모른다"
    assert not connection.plc_reconnect_btn.isEnabled()

    window._show_plc_status({"state": "STOP", "error_code": 0})
    assert "연결됨" in badge.state_label.text()
    assert "연결됨" in connection.plc_state_label.text()
    assert not connection.plc_reconnect_btn.isEnabled(), "붙어 있으면 다시 연결할 것이 없다"

    window._show_plc_status({"state": "ERROR", "error_code": 21, "error_msg": "모의 오류 21"})
    assert "연결됨" in badge.state_label.text(), "다른 차량 오류는 PLC 끊김이 아니다"

    window._show_plc_status({"state": "ERROR", "error_code": 701,
                             "error_msg": "701 PLC_COMM_LOST — timeout 10.0.0.5:502"})
    assert "연결 실패" in badge.state_label.text()
    assert "timeout 10.0.0.5:502" in connection.plc_state_label.text()
    assert connection.plc_reconnect_btn.isEnabled()
    assert any("PLC 다시 연결" in text for text in said), "실패하면 다시 시도하는 법을 알려 준다"

    window._show_plc_status({"state": "STOP", "error_code": 0})
    assert said[-1] == "PLC 가 다시 연결됐습니다."
    assert "다시 연결됐습니다" in connection.plc_message.text()

    window._show_plc_status(None)                      # 차량 상태가 끊김
    assert "모름" in badge.state_label.text()
    assert not connection.plc_reconnect_btn.isEnabled()
    assert "차량 상태가 들어오지 않습니다" in connection.plc_message.text()
    window.close()


def test_plc_reconnect_sends_a_vehicle_reset_only_when_the_plc_is_lost(qtbot) -> None:
    window = _window(qtbot)
    connection = window.screens["connection"]
    sent: list[dict] = []
    window.vehicle.control = lambda **kw: sent.append(kw) or True
    window._show_plc_status({"state": "STOP", "error_code": 0})
    window._request_plc_reconnect()
    assert sent == [] and "끊겨 있지 않습니다" in connection.plc_message.text()

    window._show_plc_status({"state": "ERROR", "error_code": 701, "error_msg": "701 PLC_COMM_LOST"})
    connection.plc_reconnect_btn.click()
    assert sent == [{"reset": True}]
    window.vehicle.command_result.emit("robot_control", False, "Rejected: busy")
    assert "PLC 다시 연결을 못 했습니다 — Rejected: busy" in connection.plc_message.text()
    window.vehicle.command_result.emit("robot_control", True, "Done")
    assert "못 했습니다" in connection.plc_message.text(), "기다리던 응답 하나만 PLC 결과로 본다"
    window.close()


def test_plc_reconnect_without_the_vehicle_node_says_why(qtbot) -> None:
    window = _window(qtbot)
    connection = window.screens["connection"]
    window._show_plc_status({"state": "ERROR", "error_code": 701, "error_msg": "701 PLC_COMM_LOST"})
    window._request_plc_reconnect()                    # ROS 없음 — robot_control 클라이언트가 없다
    assert "PLC 다시 연결을 못 했습니다" in connection.plc_message.text()
    window.close()


def test_plc_failure_and_reconnect_with_the_simulator(qtbot, tmp_path, monkeypatch) -> None:
    """모의기: PLC 끊김 → 701 · 알람 · 「연결 실패」 → 「PLC 다시 연결」(리셋) → 「연결됨」 · 701 풀림."""
    pytest.importorskip("rclpy")
    vehicle_sim_node = pytest.importorskip("vehicle_sim.vehicle_sim_node")
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.parameter import Parameter
    ns = f"vplc{int(time.time() * 1000) % 100000}"
    monkeypatch.setenv("SMR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SMR_VEHICLE_NS", ns)
    window = _window(qtbot)
    window.ros_status.start()
    sim = vehicle_sim_node.VehicleSim(parameter_overrides=[
        Parameter("namespace", value=ns), Parameter("plc_connect_s", value=0.3),
        Parameter("plc_retry_s", value=0.0)])           # 스스로 다시 시도하지 않게 — 버튼으로만
    executor = SingleThreadedExecutor()
    executor.add_node(sim)
    threading.Thread(target=executor.spin, daemon=True).start()
    badge = window.top_bar.badges["PLC"]
    connection = window.screens["connection"]
    alarms = window.cobot_manual_screen.alarm_list
    try:
        qtbot.waitUntil(lambda: "연결됨" in badge.state_label.text(), timeout=5000)

        sim.set_parameters([Parameter("plc_fail", value=True)])
        qtbot.waitUntil(lambda: "연결 실패" in badge.state_label.text(), timeout=3000)
        status = window.vehicle.last_status
        assert status["error_code"] == 701 and status["state"] == "ERROR"
        qtbot.waitUntil(lambda: alarms.count() > 0 and "701" in alarms.item(0).text(), timeout=3000)
        assert "[E2001]" in alarms.item(0).text()
        assert connection.plc_reconnect_btn.isEnabled()

        # 아직 PLC 가 안 붙는다 — 리셋은 받지만 701 이 남는다.
        connection.plc_reconnect_btn.click()
        qtbot.waitUntil(lambda: "리셋을 받았습니다" in connection.plc_message.text(), timeout=3000)
        qtbot.wait(600)
        assert "연결 실패" in badge.state_label.text()

        sim.set_parameters([Parameter("plc_fail", value=False)])
        connection.plc_reconnect_btn.click()
        qtbot.waitUntil(lambda: "연결됨" in badge.state_label.text(), timeout=5000)
        status = window.vehicle.last_status
        assert status["error_code"] == 0 and status["state"] in ("STOP", "HOLD"), "다시 붙으면 701 이 풀린다"
        assert "다시 연결됐습니다" in connection.plc_message.text()
        assert not connection.plc_reconnect_btn.isEnabled()
    finally:
        executor.shutdown()
        sim.destroy_node()
        window.ros_status.stop()
        window.close()
