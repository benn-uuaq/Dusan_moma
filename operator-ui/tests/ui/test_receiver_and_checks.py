"""연결 설정 — 받는 노드(RCS 내부 프로그램 · 외부 PC) 구분 · 외부 PC 확인 · PLC 포트 확인 (2026-11-24)."""

from smr_operator_ui import app as app_module
from smr_operator_ui.app import OperatorWindow
from smr_operator_ui.services.agv_node_supervisor import AgvNodeSupervisor
from smr_operator_ui.services.link_probe import ProbeResult

EXTERNAL = "외부 PC (그 PC 에서 실행)"
INTERNAL = "RCS 내부 프로그램 (이 PC)"


def _window(qtbot, monkeypatch, local=()):
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    monkeypatch.setattr(AgvNodeSupervisor, "local_vehicle_nodes", classmethod(lambda cls, exclude=None: list(local)))
    return window


def _pick(screen, text):
    box = screen.field("AGV 제어 노드")
    box.setCurrentIndex(box.findText(text))


def test_old_saved_label_maps_to_external_pc_and_fields_follow_the_choice(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    screen = window.screens["connection"]
    screen.apply_values({"AGV 제어 노드": "외부 (다른 PC · 따로 실행)", "외부 PC 주소": "192.168.11.13"})
    assert screen.agv_node_mode() == "external"
    assert screen.field("외부 PC 주소").isEnabled() and screen.external_check_btn.isEnabled()
    assert not screen.field("PLC 명령 쓰기").isEnabled() and not screen.field("AGV 조작 패널").isEnabled()
    assert screen.plc_check_btn.isEnabled(), "PLC 포트 확인은 어느 쪽이든 쓴다"
    _pick(screen, INTERNAL)
    assert not screen.field("외부 PC 주소").isEnabled() and not screen.external_check_btn.isEnabled()
    assert screen.field("PLC 명령 쓰기").isEnabled()
    window.close()


def test_receiver_says_which_node_gets_the_commands(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    screen = window.screens["connection"]
    window._ros_started = True
    _pick(screen, EXTERNAL)
    screen.field("외부 PC 주소").setText("192.168.11.13")
    window._refresh_receiver()
    assert "외부 PC 192.168.11.13 노드가 안 보입니다" in screen.agv_state_label.text()
    assert screen.agv_state_label.objectName() == "StatusDanger"

    window.vehicle._online = True
    window.vehicle.status_publishers = lambda: ["/vehicle/agv_controller"]
    window._refresh_receiver()
    assert "외부 PC 192.168.11.13 — 차량 상태 수신 중" in screen.agv_state_label.text()
    assert screen.agv_state_label.objectName() == "StatusGood"

    # 내부로 골랐는데 외부 PC 노드가 받고 있다 — 알린다.
    _pick(screen, INTERNAL)
    window._refresh_receiver()
    assert "외부 PC 의 노드가 받습니다" in screen.agv_state_label.text()
    # 같은 도메인에 둘 — 명령이 둘 다로 간다.
    window.vehicle.status_publishers = lambda: ["/vehicle/agv_controller", "/vehicle/vehicle_sim"]
    window._refresh_receiver()
    assert "차량 노드가 2개" in screen.agv_state_label.text()
    window.vehicle._online = False
    window.close()


def test_external_mode_warns_when_the_node_is_on_this_pc(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch, local=[(4321, "agv_controller")])
    screen = window.screens["connection"]
    window._ros_started = True
    _pick(screen, EXTERNAL)
    window.vehicle._online = True
    window.vehicle.status_publishers = lambda: ["/vehicle/agv_controller"]
    window._refresh_receiver()
    assert "이 PC 의 노드(agv_controller pid 4321)가 받습니다 — 외부 PC 가 아닙니다" in screen.agv_state_label.text()
    window.vehicle._online = False
    window.close()


def test_plc_port_check_reports_the_tcp_result(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    screen = window.screens["connection"]
    asked: list[tuple] = []
    monkeypatch.setattr(app_module, "probe_tcp", lambda host, port: asked.append((host, port)) or ProbeResult(
        False, "refused", f"닫힘 — {host} 는 응답했지만 {port} 포트를 받지 않습니다"))
    screen.plc_check_btn.click()
    qtbot.waitUntil(lambda: "닫힘" in screen.plc_check_label.text(), timeout=3000)
    assert asked == [("192.168.11.10", 8501)]
    assert screen.plc_check_label.objectName() == "StatusDanger"
    window.close()


def test_external_pc_check_pings_and_explains_when_ros_does_not_see_it(qtbot, monkeypatch):
    window = _window(qtbot, monkeypatch)
    screen = window.screens["connection"]
    _pick(screen, EXTERNAL)
    screen.external_check_btn.click()
    assert "주소를 넣으세요" in screen.external_check_label.text()
    screen.field("외부 PC 주소").setText("192.168.11.13")
    monkeypatch.setattr(app_module, "ping", lambda ip: ProbeResult(True, "reply", f"ping 응답 — {ip} (0.4 ms)", 0.4))
    screen.external_check_btn.click()
    qtbot.waitUntil(lambda: "ping 응답" in screen.external_check_label.text(), timeout=3000)
    text = screen.external_check_label.text()
    assert "ROS 로 그 PC 의 노드가 안 보입니다" in text and "ROS_DOMAIN_ID" in text
    assert screen.external_check_label.objectName() == "StatusWarn"
    window.close()


def test_node_seen_but_status_unreadable_points_at_the_message_definition(qtbot, monkeypatch):
    """2026-11-24 현장: RobotStatus.msg 에 lift_state 를 더한 뒤 외부 PC 의 옛 정의와 어긋나 상태를 못 읽었다 —
    서비스는 오가는데 RCS 만 「노드가 안 보입니다」라고 했다. 노드가 보이면 정의가 다르다고 알린다."""
    window = _window(qtbot, monkeypatch)
    screen = window.screens["connection"]
    window._ros_started = True
    _pick(screen, EXTERNAL)
    screen.field("외부 PC 주소").setText("192.168.11.13")
    window.vehicle.status_publishers = lambda: ["/QUAD_STEER_AGV_CONTROLLER"]
    monkeypatch.setattr(app_module, "ping", lambda ip: ProbeResult(True, "reply", f"ping 응답 — {ip}", 0.4))
    screen.external_check_btn.click()
    qtbot.waitUntil(lambda: "ping 응답" in screen.external_check_label.text(), timeout=3000)
    text = screen.external_check_label.text()
    assert "QUAD_STEER_AGV_CONTROLLER" in text and "메시지 정의가 다릅니다" in text
    assert "ros2 interface show vehicle_interfaces/msg/RobotStatus" in text
    window._refresh_receiver()
    assert "메시지 정의가 다릅니다" in screen.agv_state_label.text()
    assert "메시지 정의가 다릅니다" in window._vehicle_offline_text()
    window.close()
