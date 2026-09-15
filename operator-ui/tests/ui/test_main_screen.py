import pytest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

from smr_operator_ui.services.erut_session import HOME_BUSY_TEXT
from smr_operator_ui.app import OperatorWindow, _scale_stylesheet
from smr_operator_ui.services import GridPlan, MqttServer, MqttTopics
from smr_operator_ui.state import CyclePhase


def test_start_and_pause_cycle(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.show()
    qtbot.mouseClick(window.main_screen.start_button, Qt.MouseButton.LeftButton)
    assert window.simulator.snapshot.cycle.running is True
    assert window.simulator.snapshot.cycle.phase is CyclePhase.SECURING
    qtbot.mouseClick(window.main_screen.pause_button, Qt.MouseButton.LeftButton)
    assert window.simulator.snapshot.cycle.paused is True
    assert window.simulator.snapshot.cycle.phase is CyclePhase.PAUSED
    window.close()


def test_manual_navigation(qtbot) -> None:
    """'수동 제어'는 주요 제어에서 뺐다 — 설정/로그 메뉴를 통해서만 간다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.show()
    window.navigate("manual")
    assert window.stack.currentWidget() is window.screens["manual"]
    window.screens["manual"].back_requested.emit()
    assert window.stack.currentWidget() is window.main_screen
    window.close()


def test_probe_error_reaches_alarm_and_mqtt(qtbot) -> None:
    """센서판이 halt() 전에 남기는 probe_error(레지스터 299, scan_state의

    10번째 값)를 알람 목록 + MQTT evt/error(erut_session.raise_error)로
    내보내야 한다. 같은 코드가 반복되면 다시 안 나가고, 0으로 돌아오면
    -CLEAR 를 한 번 내보낸다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    raised: list[dict] = []
    window.erut_session.raise_error = lambda fields: raised.append(fields)

    base = [4, 1, 3, 0, 1, 0, 237, 0, 0]  # state, row_idx, rows, ... (9 값)
    window.ros_status.scan_state_changed.emit(base + [2])  # probe_l 접촉 실패

    assert len(raised) == 1
    assert raised[0]["code"] == "E-PROBE-L"
    assert raised[0]["level"] == "stop"
    alarms = [window.cobot_manual_screen.alarm_list.item(i).text()
              for i in range(window.cobot_manual_screen.alarm_list.count())]
    assert any("좌측" in text for text in alarms)

    # 같은 코드가 반복 수신돼도 중복으로 나가면 안 된다.
    window.ros_status.scan_state_changed.emit(base + [2])
    assert len(raised) == 1

    # 0으로 되돌아오면(dus_init 재실행) 해제 통보를 한 번 내보낸다.
    window.ros_status.scan_state_changed.emit(base + [0])
    assert len(raised) == 2
    assert raised[-1]["code"] == "E-PROBE-L-CLEAR"
    window.close()


def test_robot_alarm_reaches_alarm_list_and_mqtt(qtbot) -> None:
    """로봇(30001 포트) 실시간 알람 스트림도 알람 목록 + MQTT evt/error 로

    나가야 한다. probe_error 와 달리 상태가 아니라 이벤트라서 중복
    억제/CLEAR 는 ROS 쪽(AlarmManager)이 이미 처리하고 넘어오므로,
    여기서는 받은 그대로 매번 내보내면 된다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    raised: list[dict] = []
    window.erut_session.raise_error = lambda fields: raised.append(fields)

    window.ros_status.alarm_received.emit("[ALARM MSG] PROBE_R: NO IK SOLUTION")

    assert len(raised) == 1
    assert raised[0]["code"] == "E-ROBOT-ALARM"
    assert raised[0]["message"] == "[ALARM MSG] PROBE_R: NO IK SOLUTION"
    alarms = [window.cobot_manual_screen.alarm_list.item(i).text()
              for i in range(window.cobot_manual_screen.alarm_list.count())]
    assert any("PROBE_R" in text for text in alarms)

    # 같은 문구가 다시 와도(ROS 쪽에서 창이 지나 다시 보낸 경우) 여기서는
    # 그대로 다시 내보낸다 — 중복 억제는 ROS 쪽 책임이다.
    window.ros_status.alarm_received.emit("[ALARM MSG] PROBE_R: NO IK SOLUTION")
    assert len(raised) == 2
    window.close()


def test_stop_button_stops_simulator_and_sequencer(qtbot) -> None:
    """'정지' 버튼이 화면 시뮬레이터와 순회(→ 로봇)를 모두 멈춘다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.show()
    qtbot.mouseClick(window.main_screen.start_button, Qt.MouseButton.LeftButton)
    assert window.simulator.snapshot.cycle.running is True
    qtbot.mouseClick(window.main_screen.stop_button, Qt.MouseButton.LeftButton)
    assert window.simulator.snapshot.cycle.running is False
    assert window.simulator.snapshot.cycle.phase is CyclePhase.IDLE
    window.close()


def test_back_returns_to_previous_screen(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.show()
    window.navigate("settings")
    window.navigate("system")
    window.screens["system"].back_requested.emit()
    assert window.stack.currentWidget() is window.screens["settings"]
    window.screens["settings"].back_requested.emit()
    assert window.stack.currentWidget() is window.main_screen
    assert list(window._navigation_history) == []
    window.close()


def test_direct_main_navigation_clears_history(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.show()
    window.navigate("settings")
    window.navigate("system")

    window.navigate("main")

    assert window.stack.currentWidget() is window.main_screen
    assert list(window._navigation_history) == []
    window.close()


def test_mqtt_job_command_updates_target_dimensions(qtbot, monkeypatch) -> None:
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    saved: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        window.settings_service,
        "save",
        lambda scope, values: saved.append((scope, values)),
    )

    mqtt_server.command_received.emit(
        MqttTopics.JOB_COMMAND,
        {
            "timestamp": "1784727779111",
            "job_id": "jb00000001",
            "job_info": {
                "diameter": "2500",
                "height": "6000",
                "target_distance": "8560",
            },
        },
    )

    assert window.main_screen.orbit_view.target_dimensions() == (2.5, 6.0)
    assert saved[0][0] == "inspection_target"
    assert saved[0][1]["job_id"] == "jb00000001"
    assert saved[0][1]["target_distance_m"] == 8.56
    window.close()


def _robot_ready(window) -> None:
    """작업 영역을 로봇에 보낼 수 있는 상태로 만든다.

    Modbus 가 붙어 있고 태스크가 안 도는 동안에만 보내므로, 전송을 확인하는
    테스트는 그 상태를 먼저 만들어야 한다.

    시그널을 쏘지 않고 상태만 세운다 — `connected_changed` 는 TPAC 폴러까지
    함께 켜서 이 테스트와 상관없는 소켓·스레드가 뜬다. 시그널 배선 자체는
    `test_work_area_is_held_back_while_the_robot_task_runs` 가 따로 본다.
    """
    window._robot_link_up = True
    window._robot_task_state = 0


def _job_command_payload(**plan_overrides) -> dict:
    """원통 높이 6000 mm, 셀 높이 800 mm인 작업 계획 payload."""
    plan = {
        "column_count": "12", "row_count": "6",
        "cell_width": "600", "cell_height": "800", "overlap": "20",
    }
    plan.update(plan_overrides)
    return {
        "timestamp": "1784727779111",
        "job_id": "jb00000001",
        "job_info": {
            "diameter": "2500", "height": "6000",
            "target_distance": "8560",
        },
        "plan": plan,
    }


def test_mqtt_arc_geometry_reaches_the_robot(qtbot, monkeypatch) -> None:
    """MQTT 로 온 반지름·두께가 로봇 레지스터까지 내려가야 한다.

    로봇이 호를 스스로 계산하려면 이 둘이 필요하다. 빠지면 0 이 되어
    평면으로 보고 직선으로 훑는다 — 굽은 벽에서는 검사가 성립하지 않는다.
    반지름·두께만 0.1mm 단위로 나간다(834.6 -> 8346).
    """
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _robot_ready(window)
    sent_poses: list[tuple] = []
    monkeypatch.setattr(
        window.ros_status, "send_pose",
        lambda name, values: sent_poses.append((name, list(values))) or True,
    )

    mqtt_server.command_received.emit(MqttTopics.JOB_COMMAND, _job_command_payload(
        column_count="1", row_count="1",
        cell_width="1214", cell_height="500", overlap="20",
        radius="834.6", thickness="10",
    ))

    # 뒤 둘은 EOAT 가로/세로. 여기서는 안 골랐으므로 0 (격자 전체를 훑는다).
    # 마지막 값은 EOAT 종류(레지스터 264). 안 고르면 0 이다.
    assert sent_poses == [
        ("work_area", [1214.0, 500.0, 300, 20.0, 8346, 100, 0, 0, 0.0])]
    window.close()


def test_mqtt_eoat_choice_reaches_the_robot(qtbot, monkeypatch) -> None:
    """MQTT 로 고른 검사장비 종류가 크기로 바뀌어 로봇까지 가야 한다.

    프로브가 EOAT 폭만큼 퍼져 있어 TCP 는 격자 끝까지 갈 필요가 없다 —
    로봇이 이 값으로 이동 거리를 줄인다. 8축은 세로로 길게 달아 186x316.
    """
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _robot_ready(window)
    sent_poses: list[tuple] = []
    monkeypatch.setattr(
        window.ros_status, "send_pose",
        lambda name, values: sent_poses.append((name, list(values))) or True,
    )

    mqtt_server.command_received.emit(MqttTopics.JOB_COMMAND, _job_command_payload(
        column_count="1", row_count="1",
        cell_width="1110", cell_height="500", overlap="20",
        radius="834.6", thickness="10", eoat="8",
    ))

    # 가로, 세로에 이어 종류(8축)까지 간다.
    # 262/263 은 0.1mm 단위다(유효 커버 75 x 167.5 -> 750, 1675).
    assert sent_poses[0][1][6:] == [750, 1675, 8.0]
    window.close()


def test_eoat_height_becomes_the_scanner_band(qtbot, monkeypatch) -> None:
    """EOAT 세로가 곧 한 줄이 덮는 스캐너 밴드가 되어야 한다.

    프로브가 그 높이만큼 퍼져 있으니 한 번 지나가면 그만큼 덮인다.
    EOAT 를 골랐는데도 로컬 스캐너 설정값을 그대로 쓰면 행 수가 틀리고
    리프트가 엉뚱한 높이로 올라간다. 로봇도 같은 규칙이다
    (dus_init 의 `if eoat_h > 0: scan_h = eoat_h`).
    """
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _robot_ready(window)
    sent_poses: list[tuple] = []
    monkeypatch.setattr(
        window.ros_status, "send_pose",
        lambda name, values: sent_poses.append((name, list(values))) or True,
    )

    # 8축 = 186 x 316 -> 밴드 316 (로컬 기본값 257 이 아니라)
    mqtt_server.command_received.emit(MqttTopics.JOB_COMMAND, _job_command_payload(
        column_count="1", row_count="1",
        cell_width="1110", cell_height="500", overlap="20", eoat="8",
    ))

    # 레지스터 258 은 0.1mm 단위다(167.5mm -> 1675). 화면 값은 mm 그대로.
    assert sent_poses[0][1][2] == 1675, "스캐너 밴드가 유효 세로 커버를 안 따라갔다"
    assert window.main_screen.rect_view.work_area()[2] == 167.5
    window.close()


def test_mqtt_job_command_uses_cell_height_not_cylinder_height(qtbot, monkeypatch) -> None:
    """ㄹ자는 셀 하나만 그린다. 원통 전체 높이를 쓰면 안 된다.

    원통 높이 6000 mm를 셀 높이로 잘못 쓰면 한 셀이 6000 mm가 되어 ㄹ자
    패스가 46행까지 늘어난다. 실제로는 로봇이 한 번에 닿는 800 mm짜리
    셀 하나만 스캔하고, 나머지 높이는 리프트가 행(A~F)을 올리며 덮는다.
    """
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _robot_ready(window)
    sent_poses: list[tuple[str, list]] = []
    monkeypatch.setattr(
        window.ros_status, "send_pose",
        lambda name, values: sent_poses.append((name, list(values))) or True,
    )

    mqtt_server.command_received.emit(MqttTopics.JOB_COMMAND, _job_command_payload())

    width_mm, height_mm, _scan_h, _overlap = window.main_screen.rect_view.work_area()
    assert (width_mm, height_mm) == (600.0, 800.0)
    assert height_mm != 6000.0, "원통 전체 높이가 셀 높이로 새어 들어갔다"
    # 로봇에게도 셀 높이가 가야 한다 (레지스터 257 = app_height).
    assert sent_poses[0][1][1] == 800.0
    window.close()


def test_mqtt_job_command_keeps_local_scan_height(qtbot) -> None:
    """스캐너 유효높이는 장비 고유값이라 MQTT가 아니라 로컬 설정을 쓴다."""
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.main_screen.set_work_area(500.0, 700.0, 175.0, 15.0)

    mqtt_server.command_received.emit(MqttTopics.JOB_COMMAND, _job_command_payload())

    # 셀 치수와 겹침은 MQTT 값으로 바뀌고, 스캐너 높이만 로컬 값이 남는다.
    assert window.main_screen.rect_view.work_area() == (600.0, 800.0, 175.0, 20.0)
    window.close()


def test_mqtt_job_command_starts_job_and_applies_plan(qtbot, monkeypatch) -> None:
    """job_cmd가 전체 작업 시작 명령이다.

    plan 블록이 오면 검사 사이클이 시작되고, AMR은 열 1, 리프트는 행 A에서
    출발하도록 OrbitView·RectWorkView가 갱신되며, 값이 로봇(ROS)과
    저장소(DB) 양쪽으로 전달되어야 한다. 지금 몇 번째 셀인지는 이 명령에
    담기지 않는다 — 그 뒤로는 UI/로봇 쪽이 자동으로 순회한다.
    """
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _robot_ready(window)
    saved: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        window.settings_service, "save",
        lambda scope, values: saved.append((scope, values)),
    )
    sent_poses: list[tuple[str, list]] = []
    monkeypatch.setattr(
        window.ros_status, "send_pose",
        lambda name, values: sent_poses.append((name, list(values))) or True,
    )

    mqtt_server.command_received.emit(MqttTopics.JOB_COMMAND, _job_command_payload())

    assert window.simulator.snapshot.cycle.current_segment == 1
    assert window.simulator.snapshot.cycle.total_segments == 12
    assert window.simulator.snapshot.cycle.running is True
    assert window.main_screen.rect_view.work_area() == (600.0, 800.0, 30.0, 20.0)
    # 첫 셀은 1A, 전체 셀 수는 12열 × 6행 = 72.
    assert "1A" in window.main_screen.rect_view._cell_label
    assert "72" in window.main_screen.rect_view._cell_label
    # 반지름·두께는 이 payload 에 없으므로 0 = 평면(직선 스캔)으로 간다.
    assert ("work_area", {"width_mm": 600.0, "height_mm": 800.0,
                          "scan_h_mm": 30.0, "overlap_mm": 20.0,
                          "radius_mm": 0.0, "thickness_mm": 0.0,
                          "eoat_w_mm": 0.0, "eoat_h_mm": 0.0,
                          "eoat_type": 0.0}) in saved
    # 레지스터 256~261. 반지름·두께만 0.1mm 단위라 10 을 곱해 보낸다.
    assert sent_poses == [
        ("work_area", [600.0, 800.0, 300, 20.0, 0, 0, 0, 0, 0.0])]
    window.close()


def test_mqtt_job_command_without_plan_keeps_previous_cell(qtbot) -> None:
    """옛 payload(plan 없음)를 받아도 검사대상 처리는 계속 되고 죽지 않는다."""
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)

    mqtt_server.command_received.emit(
        MqttTopics.JOB_COMMAND,
        {
            "timestamp": "1784727779111",
            "job_id": "jb00000001",
            "job_info": {
                "diameter": "2500", "height": "6000",
                "target_distance": "8560",
            },
        },
    )

    assert window.main_screen.orbit_view.target_dimensions() == (2.5, 6.0)
    window.close()


def test_mqtt_amr_run_starts_inspection(qtbot) -> None:
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)

    mqtt_server.command_received.emit(
        MqttTopics.MC_COMMAND,
        {
            "timestamp": "1784727720000",
            "amr": "run",
            "cobot": "stop",
        },
    )

    assert window.simulator.snapshot.cycle.running is True
    assert window.simulator.snapshot.cycle.phase is CyclePhase.SECURING
    window.close()


@pytest.mark.parametrize("amr_command", ["stop", "ems"])
def test_mqtt_amr_stop_or_ems_pauses_inspection(
    qtbot,
    amr_command,
) -> None:
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.simulator.start_cycle()

    payload = {
        "timestamp": "1784727720000",
        "amr": amr_command,
        "cobot": "stop",
    }
    mqtt_server.command_received.emit(MqttTopics.MC_COMMAND, payload)

    assert window.simulator.snapshot.cycle.running is True
    assert window.simulator.snapshot.cycle.paused is True
    assert window.simulator.snapshot.cycle.phase is CyclePhase.PAUSED

    # QoS 1 재전송으로 같은 명령을 다시 받아도 검사가 재개되지 않아야 한다.
    mqtt_server.command_received.emit(MqttTopics.MC_COMMAND, payload)
    assert window.simulator.snapshot.cycle.paused is True
    assert window.simulator.snapshot.cycle.phase is CyclePhase.PAUSED
    window.close()


def test_robot_restart_stops_before_play(qtbot, monkeypatch) -> None:
    """셀마다 로봇을 stop 한 뒤 play 해야 한다.

    로봇 태스크는 한 셀을 끝낸 뒤에도 RUNNING 으로 남아 있어서 play 만
    보내면 컨트롤러가 거부한다. 게다가 dus_init 은 태스크가 시작될 때만
    작업 영역(256~259)을 읽으므로, 멈췄다 켜지 않으면 이전 셀 치수로
    계속 돈다(원통 전체 높이로 46행을 긁던 문제).
    """
    from smr_operator_ui.app import ROBOT_RESTART_DELAY_MS

    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    calls: list[str] = []
    monkeypatch.setattr(
        window.ros_status, "call_command",
        lambda name: calls.append(name) or True,
    )

    window._start_robot_scan()
    # 원격 제어를 먼저 켜야 play/stop 이 먹는다.
    assert calls == ["remote_control_on", "stop"]

    # play 는 컨트롤러가 태스크를 정리할 시간을 두고 뒤따른다.
    qtbot.waitUntil(lambda: calls == ["remote_control_on", "stop", "play"],
                    timeout=ROBOT_RESTART_DELAY_MS + 2000)
    window.close()


def test_sequencer_drives_cycle_display_not_a_timer(qtbot) -> None:
    """진행 표시는 시퀀서를 따라가야 한다. 자체 타이머로 앞서 나가면 안 된다.

    예전에는 InspectionSimulator 가 1.2초마다 단계를 넘겨서, 로봇이 아직
    첫 셀에 있는데도 화면만 마지막 구간까지 가버렸다.
    """
    from smr_operator_ui.services import SequencerState
    from smr_operator_ui.state import CyclePhase

    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)

    mqtt_server.command_received.emit(MqttTopics.JOB_COMMAND, _job_command_payload())

    # 첫 셀에서는 구간도 1이어야 한다.
    assert window.simulator.snapshot.cycle.current_segment == 1
    assert window.simulator.snapshot.cycle.total_segments == 12

    # 데모 타이머가 살아 있으면 시간이 지나며 구간이 저절로 올라간다.
    qtbot.wait(400)
    assert window.simulator.snapshot.cycle.current_segment == 1, "화면이 혼자 앞서 나갔다"

    # 스캔 중에는 3번 단계(Cobot 검사)가 켜져야 한다.
    window.sequencer._set_state(SequencerState.SCANNING)
    assert window.simulator.snapshot.cycle.phase is CyclePhase.INSPECTING
    window.close()


def test_step_display_advances_in_order(qtbot) -> None:
    """안전 순서 5단계가 1 → 2 → 3 → 4 → 5 로만 흘러야 한다.

    5단계는 구간(열)마다 반복된다. 한 열 안에서 리프트로 셀을 옮기는 것은
    3단계(Cobot 검사) 안이라 표시가 2단계로 되돌아가면 안 된다.
    """
    from smr_operator_ui.services import SequencerState

    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    order = window.main_screen._phase_order

    def active_step() -> int:
        phase = window.simulator.snapshot.cycle.phase
        return order.index(phase) + 1 if phase in order else 0

    mqtt_server.command_received.emit(MqttTopics.JOB_COMMAND, _job_command_payload())

    # 차량은 이미 1구역에 있으므로 1단계(정지·고정)부터 시작한다.
    seen = [active_step()]
    for state in (
        SequencerState.LEVELING,       # 2 수평 보정
        SequencerState.SCANNING,       # 3 검사
        SequencerState.MOVING_LIFT,    # 3 유지 (셀 사이 리프트 이동)
        SequencerState.SCANNING,       # 3 유지
        SequencerState.RETRACTING,     # 4 안전 위치
        SequencerState.MOVING_AMR,     # 5 다음 구간 이동
    ):
        window.sequencer._set_state(state)
        seen.append(active_step())

    assert seen == [1, 2, 3, 3, 3, 4, 5]
    # 5단계가 모두 화면에 있어야 한다.
    assert len(window.main_screen.steps) == 5
    window.close()


def test_mqtt_speed_command_is_sent_to_robot(qtbot, monkeypatch) -> None:
    """외부에서 속도 비율을 바꾸면 로봇으로 전달되어야 한다."""
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    sent: list[tuple[str, int]] = []
    monkeypatch.setattr(
        window.ros_status, "send_value",
        lambda name, value: sent.append((name, value)) or True,
    )

    mqtt_server.command_received.emit(
        MqttTopics.SPEED, {"timestamp": "1", "speed": "45"}
    )

    assert sent == [("speed_ratio", 45)]
    window.close()


def test_mqtt_speed_command_rejects_out_of_range(qtbot, monkeypatch) -> None:
    """범위를 벗어난 값은 로봇으로 보내지 않는다."""
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    sent: list[tuple[str, int]] = []
    monkeypatch.setattr(
        window.ros_status, "send_value",
        lambda name, value: sent.append((name, value)) or True,
    )

    for bad in ("0", "1", "101"):
        mqtt_server.command_received.emit(
            MqttTopics.SPEED, {"timestamp": "1", "speed": bad}
        )
    assert sent == []
    window.close()


def test_linear_speed_is_capped_at_safety_limit(qtbot, monkeypatch) -> None:
    """작업 속도는 안전 기준상 150 mm/s 를 넘겨 보낼 수 없다."""
    from smr_operator_ui.app import MAX_LINEAR_SPEED_MM_S
    from smr_operator_ui.screens import CobotSettingsScreen

    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    sent: list[tuple[str, int]] = []
    monkeypatch.setattr(
        window.ros_status, "send_value",
        lambda name, value: sent.append((name, value)) or True,
    )
    monkeypatch.setattr(window.ros_status, "writable", lambda name: True)

    window._send_linear_speed("cobot", {
        CobotSettingsScreen.SPEED_FIELD: 400,
        CobotSettingsScreen.RATIO_FIELD: 100,
    })

    assert ("linear_speed", MAX_LINEAR_SPEED_MM_S) in sent
    assert MAX_LINEAR_SPEED_MM_S == 150
    window.close()


def test_robot_speed_scale_is_shown(qtbot) -> None:
    """로봇이 실제로 쓰는 속도 비율이 화면에 나와야 한다.

    펜던트에서 직접 바꿔도 Modbus 레지스터 17을 통해 여기로 들어온다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)

    window.ros_status.speed_scale_changed.emit(35)

    # "사이클 요약"을 뺀 뒤로는 속도 바가 실제 비율을 보여 주는 자리다.
    assert "35" in window.main_screen.speed_bar.value_label.text()
    window.close()


def test_speed_bar_sends_ratio_to_robot(qtbot, monkeypatch) -> None:
    """세로 속도 바를 놓으면 로봇으로 속도 비율이 나가야 한다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    sent: list[tuple[str, int]] = []
    monkeypatch.setattr(
        window.ros_status, "send_value",
        lambda name, value: sent.append((name, value)) or True,
    )

    bar = window.main_screen.speed_bar
    bar.slider.setValue(45)
    assert sent == [], "끄는 동안에는 보내지 않는다"
    bar.slider.sliderReleased.emit()

    assert sent == [("speed_ratio", 45)]
    window.close()


def test_speed_bar_follows_robot_without_feedback_loop(qtbot, monkeypatch) -> None:
    """로봇이 알려 온 값으로 바를 맞출 때 다시 명령이 나가면 안 된다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    sent: list[tuple[str, int]] = []
    monkeypatch.setattr(
        window.ros_status, "send_value",
        lambda name, value: sent.append((name, value)) or True,
    )

    window.ros_status.speed_scale_changed.emit(30)

    assert window.main_screen.speed_bar.value() == 30
    assert sent == [], "표시를 갱신하다가 명령이 되돌아 나갔다"
    window.close()


def test_grid_cell_updates_total_cells_and_row_floor(qtbot) -> None:
    """"총 구간 수"는 MQTT(ERUT)가 준 열 수 × 행 수를 따라가야 하고

    (예전엔 생성자 기본값 "12"에서 한 번도 안 바뀌었다), 지금 몇 번째
    행(층)인지도 별도로 보여야 한다("총 구간별 행의 표시가 전혀 없어서
    지금 몇층인지 알 수가 없음").
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    plan = GridPlan(
        column_count=8, row_count=6, cell_width=1110.0, cell_height=500.0,
        scan_overlap=20.0, pitch_x=20.0, pitch_y=20.0,
        radius=834.6, thickness=10.0, eoat_probes=5,
    )
    window.sequencer.start(plan, 257.0)

    window._show_sequencer_cell(2, 3, "C3", 15)

    # 전체 구간 수는 "현재 구간" 타일의 "/ 전체" 자리에 나온다.
    assert window.main_screen.segment_total.text() == "/ 48"  # 8 x 6
    # 값과 "/ 전체"가 각각 별도 라벨이다(네 타일의 슬래시 모양을 맞추려고).
    assert window.main_screen.row_label.text() == "04"
    assert window.main_screen.row_total.text() == "/ 6"
    orbit = window.main_screen.orbit_view
    assert (orbit._current_row, orbit._total_rows) == (4, 6)
    window.close()


def test_orbit_view_diameter_excludes_thickness_shown_separately(qtbot) -> None:
    """검사 대상 지름에는 두께를 섞지 않고 따로 보여줘야 한다

    ("검사 대상의 지름에 두께 포함하지 말고. 두께를 따로 표기").
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window._send_work_area(1110.0, 500.0, 257.0, 20.0, radius_mm=834.6, thickness_mm=10.0)
    assert window.main_screen.orbit_view._target_thickness_mm == 10.0
    window.close()


def test_rect_view_gets_eoat_width_for_tcp_path_inset(qtbot) -> None:
    """프로브 유효 가로 커버가 RectWorkView까지 전달돼야 한다.

    경로 자체는 좌우 끝까지 그리지만(끝도 프로브 중심 기준), 그림이
    값을 들고 있어야 나중에 표시에 쓸 수 있다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window._send_work_area(
        721.0, 500.0, 30.0, 20.0, radius_mm=834.6, thickness_mm=10.0,
        eoat_w_mm=30.0, eoat_h_mm=30.0,
    )
    assert window.main_screen.rect_view._eoat_w_mm == 30.0
    window.close()


def test_scale_stylesheet_scales_every_px_value_uniformly() -> None:
    """전체화면 등으로 창이 커지면 폰트만이 아니라 여백·버튼 높이까지

    같은 비율로 커져야 "화면 비율이 유지"된 것처럼 보인다. 음수 px(예:
    마진)도 부호가 뒤집히면 안 된다.
    """
    qss = "QLabel#Brand { font-size: 20px; } .x { margin: 0 -10px; }"
    scaled = _scale_stylesheet(qss, 1.5)
    assert "font-size: 30px" in scaled
    assert "-15px" in scaled


def test_window_scales_whole_ui_and_topbar_with_window_size(qtbot) -> None:
    """창이 1280x720(기준)보다 커지면(예: 전체화면) 전체 QSS 배율과

    창이 커지면 글자·버튼도 커지되, **창 배수를 그대로 따라가면 전체화면에서
    너무 커진다**("전체화면하면 글씨나 이런게 너무 많이 커져"). 창 배수보다
    완만하게 커지고, 기준 크기로 돌아오면 1.0으로 복귀해야 한다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.show()
    assert window._current_ui_scale == 1.0

    window.resize(1920, 1080)          # 창 배수 1.5
    scale = window._current_ui_scale
    assert 1.0 < scale < 1.5, "커지긴 하되 창 배수보다는 완만해야 한다"
    assert window.top_bar._global_scale == scale
    assert f"font-size: {round(20 * scale)}px" in QApplication.instance().styleSheet()

    window.resize(3840, 2160)          # 아무리 커도 상한을 넘지 않는다
    assert window._current_ui_scale == OperatorWindow._UI_SCALE_MAX

    window.resize(1280, 720)
    assert window._current_ui_scale == 1.0
    assert window.top_bar._global_scale == 1.0
    window.close()


def test_position_marker_parks_at_origin_instead_of_disappearing(qtbot) -> None:
    """현재 위치는 항상 보이고, 스캔이 아닐 때는 영점에 서 있어야 한다.

    예전에는 스캔이 아니면 점을 지웠는데, 좌표(0)와 상태가 따로 도착해
    **영점으로 튀었다가 사라지기**를 반복했다. 생겼다 없어졌다 하는 것보다
    영점에 가만히 있는 편이 읽기 쉽고, 같은 값을 받는 TPAC 과도 어긋나지
    않는다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    rect = window.main_screen.rect_view

    scanning = [6, 1, 3, 0, 1, 0, 237, 0, 0, 0]
    idle = [4, 1, 3, 0, 1, 0, 237, 0, 0, 0]

    window.ros_status.scan_state_changed.emit(scanning)
    window.ros_status.tcp_pose_zero_changed.emit([0.0, 400.0, 0.0, 0.0, 0.0, 0.0])
    assert rect._has_position is True
    assert rect._pos_h_mm == 400.0

    # 스캔이 끝나면 로봇이 0 을 보낸다 — 점은 사라지지 않고 영점에 선다.
    window.ros_status.scan_state_changed.emit(idle)
    window.ros_status.tcp_pose_zero_changed.emit([0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    assert rect._has_position is True
    assert (rect._pos_h_mm, rect._pos_v_mm) == (0.0, 0.0)
    window.close()


def test_position_marker_never_blinks(qtbot) -> None:
    """상태·좌표 토픽이 번갈아 들어와도 표시가 꺼지면 안 된다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    rect = window.main_screen.rect_view

    seen = []
    for state in (6, 6, 4, 6, 4, 4):
        window.ros_status.scan_state_changed.emit(
            [state, 1, 3, 0, 1, 0, 237, 0, 0, 0])
        seen.append(rect._has_position)
        window.ros_status.tcp_pose_zero_changed.emit([0.0, 400.0, 0.0, 0.0, 0.0, 0.0])
        seen.append(rect._has_position)

    assert seen[1:] == [True] * len(seen[1:]), f"표시가 깜빡인다: {seen}"
    window.close()


def test_oversized_work_length_is_clamped_and_alarmed(qtbot) -> None:
    """MQTT 가 안전 한계보다 긴 작업 길이를 보내면 잘라 쓰고 알린다.

    로봇은 자기 안전 한계 안에서만 움직이므로, 긴 값을 그대로 그리면
    **로봇이 따라올 수 없는 경로를 화면에만 그리게 된다**(현재 위치 점이
    경로 왼쪽 끝에 못 닿는다).
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _robot_ready(window)
    raised: list[dict] = []
    notes: list[tuple] = []
    window.erut_session.raise_error = lambda fields: raised.append(fields)
    window.erut_session.notify = lambda code, msg, text, **kw: notes.append((code, msg, text))

    window._send_work_area(1110.0, 500.0, 30.0, 20.0,
                           radius_mm=834.6, thickness_mm=10.0, eoat_w_mm=30.0)

    assert window.main_screen.rect_view.work_area()[0] == 721.0
    # 줄여서 진행하는 것은 장애가 아니다 — evt/error 가 아니라 알림으로 낸다.
    assert raised == []
    assert [n[0] for n in notes] == ["M2001"]
    assert "721" in notes[0][2]
    alarms = [window.cobot_manual_screen.alarm_list.item(i).text()
              for i in range(window.cobot_manual_screen.alarm_list.count())]
    assert any("최대 작업 길이" in text for text in alarms)
    # 실제로 가능한 길이를 함께 알려 줘야 조치할 수 있다.
    assert any("721" in text for text in alarms)
    window.close()


def test_safe_work_length_passes_through_without_alarm(qtbot) -> None:
    """한계 안의 값은 그대로 쓰고 알람도 내지 않는다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _robot_ready(window)
    raised: list[dict] = []
    window.erut_session.raise_error = lambda fields: raised.append(fields)

    # 좌우 끝도 프로브 중심 기준이라 정수 한계는 721 이다 — 그대로 통과한다.
    window._send_work_area(721.0, 500.0, 30.0, 20.0,
                           radius_mm=834.6, thickness_mm=10.0, eoat_w_mm=30.0)

    assert window.main_screen.rect_view.work_area()[0] == 721.0
    assert raised == []
    window.close()


def test_alarm_reset_button_clears_notice_and_errors(qtbot) -> None:
    """장애가 뜬 뒤 화면에서 직접 지울 수 있어야 한다.

    예전에는 알림 문구를 지울 방법이 없어 통보가 계속 남아 있었고,
    해제하려면 ERUT 가 `req/reset` 을 보내 주기를 기다려야 했다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)

    window.erut_session.raise_error({
        "code": "E-ROBOT-ALARM", "message": "thread over max size",
        "level": "warning",
    })
    window.cobot_manual_screen.add_alarm("[ALARM] thread_is_over_max_size:20")
    assert window.erut_session.error_codes() == ["E-ROBOT-ALARM"]

    qtbot.mouseClick(window.main_screen.notice_clear_button, Qt.MouseButton.LeftButton)

    assert window.erut_session.error_codes() == []
    assert "E-ROBOT-ALARM" in window.main_screen.activity_label.text()  # 해제 안내
    alarms = [window.cobot_manual_screen.alarm_list.item(i).text()
              for i in range(window.cobot_manual_screen.alarm_list.count())]
    assert alarms == ["활성 알람 없음"]
    window.close()


def test_position_returns_to_standby_when_the_robot_stops_sending(qtbot) -> None:
    """중간에 태스크를 끊으면 마지막 좌표가 남아 있으면 안 된다.

    로봇의 발행 스레드는 주기마다 생존 카운터(레지스터 293)를 올린다.
    태스크가 멈추면 그 값이 굳으므로, 한동안 안 바뀌면 현재 위치를 대기
    자리로 되돌린다 — 그러지 않으면 마지막 자리에 점이 계속 떠 있어 지금도
    거기 있는 것처럼 보인다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    rect = window.main_screen.rect_view

    def scan_state(alive: int) -> list:
        return [6, 1, 3, alive, 1, 0, 257, 0, 0, 0]

    window.ros_status.scan_state_changed.emit(scan_state(1))
    window.ros_status.tcp_pose_zero_changed.emit([0.0, 849.5, 128.0, 0.0, 0.0, 0.0])
    assert (rect._pos_h_mm, rect._pos_v_mm) == (849.5, 128.0)

    # 카운터가 계속 오르는 동안(정상 동작)은 좌표를 그대로 둔다.
    for alive in range(2, 2 + OperatorWindow._ALIVE_STALL_LIMIT + 2):
        window.ros_status.scan_state_changed.emit(scan_state(alive))
    assert (rect._pos_h_mm, rect._pos_v_mm) == (849.5, 128.0)

    # 카운터가 멈추면 대기 자리(0, 0 = ㄹ자 시작점)로 되돌린다.
    for _ in range(OperatorWindow._ALIVE_STALL_LIMIT + 1):
        window.ros_status.scan_state_changed.emit(scan_state(99))
    assert (rect._pos_h_mm, rect._pos_v_mm) == (0.0, 0.0)
    assert rect._has_position is True   # 사라지지는 않는다
    window.close()


def test_stored_work_area_is_pushed_to_the_robot_on_load(qtbot) -> None:
    """저장된 작업 영역은 화면뿐 아니라 로봇 레지스터에도 실려야 한다.

    예전에는 불러올 때 화면만 맞춰서, 겹침을 바꿔도 로봇은 예전 레지스터
    값(옛 행 수)으로 계속 돌았다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _robot_ready(window)
    sent: list[tuple] = []
    window.ros_status.send_pose = lambda name, values: sent.append(
        (name, list(values))) or True

    # 좌우 끝도 프로브 중심 기준이라 안전 최대 작업 호는 721 이다.
    window._apply_stored_settings("work_area", {
        "width_mm": 721.0, "height_mm": 500.0, "scan_h_mm": 30.0,
        "overlap_mm": 0.0, "radius_mm": 834.6, "thickness_mm": 10.0,
        "eoat_w_mm": 30.0, "eoat_h_mm": 30.0,
    })

    assert sent, "로봇으로 보내지 않았다"
    name, values = sent[-1]
    assert name == "work_area"
    assert values[:4] == [721.0, 500.0, 300, 0.0]   # 258 은 0.1mm
    window.close()


def test_work_area_is_held_back_while_the_robot_task_runs(qtbot) -> None:
    """작업 영역은 태스크가 안 돌고 Modbus 가 붙어 있을 때만 보낸다.

    도는 중에 작업 계획을 바꾸면 진행 중인 검사가 중간에 다른 격자로
    바뀐다. 못 보낼 때는 사유(와 로봇이 보고한 상태값)를 알리고, 조건이
    풀리면 자동으로 다시 보낸다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    sent: list[list] = []
    raised: list[dict] = []
    notes: list[tuple] = []
    window.ros_status.send_pose = lambda name, values: sent.append(list(values)) or True
    window.erut_session.raise_error = lambda fields: raised.append(fields)
    window.erut_session.notify = lambda code, msg, text, **kw: notes.append((code, msg, text))

    def rcs_alarms() -> list[str]:
        lst = window.cobot_manual_screen.alarm_list
        return [lst.item(i).text() for i in range(lst.count())]

    area = dict(radius_mm=834.6, thickness_mm=10.0,
                eoat_w_mm=244.5, eoat_h_mm=244.5, eoat_type=5.0)

    # 연결이 없으면 못 보낸다 — 사유를 알린다.
    window._send_work_area(978.0, 500.0, 257.0, 0.0, **area)
    assert sent == []
    # ERUT 에는 알림(M2002)만 — 내부 통신 사정은 RCS 알람에만 남긴다.
    assert notes[-1][0] == "M2002"
    assert "연결" not in notes[-1][2]
    assert any("연결" in a for a in rcs_alarms())

    # 연결되면 보류분이 자동으로 나간다.
    window.ros_status.connected_changed.emit(True)
    assert len(sent) == 1
    assert len(sent[0]) == 9

    # 태스크가 도는 중이면 보류하고, 로봇이 보고한 값을 함께 알린다.
    window.ros_status.task_state_changed.emit(1)
    notes.clear()
    window._send_work_area(600.0, 500.0, 257.0, 20.0, **area)
    assert len(sent) == 1, "태스크 실행 중에는 보내면 안 된다"
    assert notes[-1][0] == "M2002"
    assert any("실행 중" in a and "태스크 상태 = 1" in a for a in rcs_alarms())
    assert raised == [], "보류는 장애가 아니다"

    # 멈추면 보류해 둔 값이 나간다.
    window.ros_status.task_state_changed.emit(3)
    assert len(sent) == 2
    assert sent[-1][0] == 600.0
    window.close()


def test_ui_launches_the_robot_node_unless_one_is_running(qtbot) -> None:
    """프로그램 하나만 켜면 로봇 제어 노드도 같이 떠야 한다.

    다만 이미 떠 있으면 다시 띄우지 않는다 — 같은 로봇에 두 노드가 붙으면
    Modbus 소켓을 서로 뺏는다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    supervisor = window.robot_node

    assert supervisor.start(already_running=True) is False
    assert supervisor.owns_process is False
    window.close()


def test_work_area_edited_on_screen_reaches_the_robot(qtbot) -> None:
    """화면에서 고친 값도 바로 로봇으로 가야 한다.

    예전에는 저장만 해서, 겹침을 20 으로 바꿔도 로봇은 0 그대로였다.
    게다가 다이얼로그가 묻는 네 항목만 저장해 반지름·두께·EOAT 가 통째로
    지워졌고, 다음에 불러올 때 반지름 0(=평면)이 로봇에 내려갔다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _robot_ready(window)
    sent: list[list] = []
    saved: list[tuple] = []
    window.ros_status.send_pose = lambda name, values: sent.append(list(values)) or True
    window.settings_service.save = lambda scope, values: saved.append(
        (scope, dict(values)))

    # MQTT 가 먼저 호 정보와 EOAT 를 준 상태.
    window._send_work_area(721.0, 500.0, 30.0, 0.0, radius_mm=834.6,
                           thickness_mm=10.0, eoat_w_mm=30.0,
                           eoat_h_mm=30.0, eoat_type=5.0)
    assert sent[-1][3] == 0.0

    # 화면 다이얼로그에서 겹침만 20 으로 바꾼다.
    window.main_screen.work_area_changed.emit(721.0, 500.0, 30.0, 20.0)

    assert sent[-1][3] == 20.0, "겹침이 로봇까지 가지 않았다"
    # 다이얼로그가 안 묻는 값은 지워지지 않고 그대로 이어져야 한다.
    assert sent[-1][4] == 8346          # 반지름 834.6 (0.1mm 단위)
    assert sent[-1][6:] == [300, 300, 5.0]   # 262/263 은 0.1mm (30.0mm)
    assert "radius_mm" in saved[-1][1] and saved[-1][1]["radius_mm"] == 834.6
    window.close()


def test_zero_arc_probe_error_is_reported(qtbot) -> None:
    """호가 0 이면(반지름/호 길이 0) 로봇이 남기는 코드 4 를 알려야 한다.

    그 상태로 진행하면 movec 이 "Target pose ... coincident with auxiliary
    pose" 로 죽는데, 그 문구만으로는 원인을 알 수 없다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    raised: list[dict] = []
    window.erut_session.raise_error = lambda fields: raised.append(fields)

    base = [4, 1, 3, 0, 1, 0, 257, 0, 0]
    window.ros_status.scan_state_changed.emit(base + [4])

    assert raised[-1]["code"] == "E-ARC-ZERO"
    alarms = [window.cobot_manual_screen.alarm_list.item(i).text()
              for i in range(window.cobot_manual_screen.alarm_list.count())]
    assert any("반지름" in text for text in alarms)
    window.close()


def test_tpac_pose_source_defaults_to_scan_value(qtbot) -> None:
    """TPAC 은 훑는 동안의 이동 거리를 받아 평면으로 펴는 장치다.

    그러니 400 자리에 실을 값의 기본은 베이스 좌표가 아니라 제로점 기준
    스캔 값이어야 한다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    combo = window.screens["tpac_bridge"].srv_pose_source
    assert combo.currentData() == "scan"
    assert combo.currentText() == "제로점 기준 스캔 값"
    window.close()


# ---------------------------------------------------------------------------
# 원점 도착 -> ERUT 확인 -> 적심 -> 스캔
# ---------------------------------------------------------------------------
def _scanning(window) -> None:
    """스캔 구간이 도는 중으로 세운다. 원점 대기는 이때만 뜻이 있다."""
    from smr_operator_ui.services import SequencerState
    window.sequencer._state = SequencerState.SCANNING


def _scan_state(state: int) -> list[int]:
    """레지스터 290~299 를 흉내 낸다. 앞자리만 의미 있는 시험용."""
    values = [0] * 10
    values[0] = state
    return values


def test_robot_waiting_at_origin_is_announced_once(qtbot) -> None:
    """로봇이 원점에서 멈춰 서면(290 = 7) ERUT 에 한 번만 알린다.

    상태는 10Hz 로 계속 들어오므로, 값이 **바뀔 때만** 알려야 도착 통보가
    초당 열 번씩 쌓이지 않는다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _scanning(window)
    told: list[int] = []
    window.erut_session.notify_at_origin = lambda: told.append(1)

    for _ in range(5):
        window._handle_origin_wait(_scan_state(7))

    assert told == [1], "도착 통보가 한 번이 아니다"
    window.close()


def test_leaving_the_origin_arms_the_next_arrival(qtbot) -> None:
    """대기가 풀리면 다음 도착 때 다시 알려야 한다(루프판)."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _scanning(window)
    told: list[int] = []
    window.erut_session.notify_at_origin = lambda: told.append(1)
    window.erut_session.clear_at_origin = lambda: None

    window._handle_origin_wait(_scan_state(7))
    window._handle_origin_wait(_scan_state(8))   # 적심 구간
    window._handle_origin_wait(_scan_state(6))   # 스캔
    window._handle_origin_wait(_scan_state(7))   # 다음 셀에서 다시 도착

    assert told == [1, 1]
    window.close()


def test_erut_start_while_waiting_releases_the_robot(qtbot) -> None:
    """원점에서 기다리는 중에 온 ERUT start 는 새 작업이 아니라 **해제**다.

    순회는 이미 돌고 있으므로 BUSY 로 되돌리면 로봇이 영영 못 움직인다.
    해제는 레지스터 267 에 1 을 쓰는 것으로 나간다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    sent: list[tuple] = []
    window.ros_status.send_value = lambda name, value: sent.append(
        (name, value)) or True

    window._handle_origin_wait(_scan_state(7))
    window.erut_session.scan_go_requested.emit()

    assert sent == [("scan_go", 1)]
    window.close()


def test_failed_release_is_alarmed(qtbot) -> None:
    """허가를 못 보냈으면 조용히 넘어가지 말고 알린다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.ros_status.send_value = lambda name, value: False

    assert window._release_scan_gate() is False
    alarms = [window.cobot_manual_screen.alarm_list.item(i).text()
              for i in range(window.cobot_manual_screen.alarm_list.count())]
    assert any("스캔 시작 허가" in text for text in alarms)
    window.close()


def test_probe_gate_is_announced_to_mc(qtbot) -> None:
    """원점 대기 상태를 사내 MC 쪽으로도 알린다.

    ERUT 규격에서는 evt/ready(stage=at_origin)가 그 자리지만, 사내 MC
    규격에는 대응하는 동작이 없어 probe_gate 통로를 따로 둔다.
    """
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _scanning(window)
    gates: list[tuple] = []
    mqtt_server.publish_probe_gate = lambda waiting, cell="": gates.append(
        (waiting, cell)) or True

    window._handle_origin_wait(_scan_state(7))
    window._handle_origin_wait(_scan_state(6))

    assert [g[0] for g in gates] == [True, False]
    window.close()


def test_probe_ack_releases_only_when_pressed(qtbot) -> None:
    """확인이 참일 때만 로봇을 푼다 — 프로브가 안 붙었으면 계속 세워 둔다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _scanning(window)
    sent: list[tuple] = []
    window.ros_status.send_value = lambda name, value: sent.append(
        (name, value)) or True

    window._handle_origin_wait(_scan_state(7))
    window._handle_probe_ack({"pressed": False, "reason": "PROBE_NOT_PRESSED"})
    assert sent == [], "눌림 불량인데 스캔을 시작했다"

    window._handle_probe_ack({"pressed": True})
    assert sent == [("scan_go", 1)]
    window.close()


def test_probe_ack_outside_the_wait_is_ignored(qtbot) -> None:
    """대기 중이 아닐 때 온 확인은 무시한다 — 스캔을 앞당기면 안 된다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    sent: list[tuple] = []
    window.ros_status.send_value = lambda name, value: sent.append(
        (name, value)) or True

    window._handle_probe_ack({"pressed": True})

    assert sent == []
    window.close()


def test_mqtt_overlap_moves_the_vehicle_and_lift(qtbot, monkeypatch) -> None:
    """MQTT 로 받은 겹침은 차량·리프트 이동량에서 빠져야 한다.

    겹침은 격자 **끼리**의 값이다. 그만큼 덜 이동해야 옆·위 격자와 겹치고,
    로봇이 그 겹친 자리에서 다시 영점을 잡는다. 격자 **안** ㄹ자 줄 겹침은
    로봇이 프로브 커버로 스스로 정하므로 여기서 채우지 않는다.
    """
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _robot_ready(window)

    mqtt_server.command_received.emit(MqttTopics.JOB_COMMAND, _job_command_payload(
        column_count="2", row_count="3",
        cell_width="600", cell_height="800", overlap="20",
    ))

    plan = window.sequencer.plan
    assert plan is not None, "작업 계획이 적용되지 않았다"
    assert (plan.pitch_x, plan.pitch_y) == (20.0, 20.0)
    assert plan.scan_overlap == 0.0
    assert plan.column_pitch == 600.0 - 20.0     # 차량이 20 덜 간다
    assert plan.lift_pitch == 800.0 - 20.0       # 리프트가 20 덜 오른다
    window.close()


def test_overlap_larger_than_the_cell_is_refused(qtbot) -> None:
    """겹침이 격자보다 크면 차량·리프트가 뒤로 가거나 제자리를 맴돈다."""
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _robot_ready(window)

    mqtt_server.command_received.emit(MqttTopics.JOB_COMMAND, _job_command_payload(
        column_count="1", row_count="1",
        cell_width="600", cell_height="800", overlap="900",
    ))

    assert window.sequencer.plan is None
    window.close()


def test_zero_travel_distance_is_named_in_the_message(qtbot) -> None:
    """이동거리도 있어야 하는 값이다 — 다만 어느 값인지 짚어 줘야 한다.

    예전에는 지름·높이와 뭉뚱그려 "검사대상 치수는 0보다 큰 값이어야"
    라고만 해서, 지름·높이가 멀쩡한데도 무엇이 문제인지 알 수 없었다.
    """
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _robot_ready(window)

    payload = _job_command_payload(column_count="1", row_count="1",
                                   cell_width="721", cell_height="500")
    payload["job_info"]["target_distance"] = "0"
    mqtt_server.command_received.emit(MqttTopics.JOB_COMMAND, payload)

    assert window.sequencer.plan is None
    assert "이동거리" in window.main_screen.activity_label.text()
    window.close()


def test_a_bad_dimension_is_named_in_the_message(qtbot) -> None:
    """어느 값이 문제인지 짚어 준다 — 뭉뚱그리면 고칠 데를 못 찾는다."""
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _robot_ready(window)

    payload = _job_command_payload(column_count="1", row_count="1",
                                   cell_width="721", cell_height="500")
    payload["job_info"]["diameter"] = "0"
    mqtt_server.command_received.emit(MqttTopics.JOB_COMMAND, payload)

    assert "지름" in window.main_screen.activity_label.text()
    window.close()


def test_multi_cell_job_moves_the_vehicle_and_lift(qtbot) -> None:
    """여러 격자를 받으면 차량과 리프트가 실제로 자리를 옮겨야 한다.

    격자가 1x1 이면 둘 다 제자리라 순회가 도는지 확인할 수 없다.
    이동량은 겹침만큼 줄어든다 (격자끼리 겹치게).
    """
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _robot_ready(window)

    mqtt_server.command_received.emit(MqttTopics.JOB_COMMAND, _job_command_payload(
        column_count="3", row_count="4",
        cell_width="721", cell_height="500", overlap="20",
    ))
    plan = window.sequencer.plan
    assert plan is not None and plan.total_cells == 12

    # 3열이면 차량이 두 번 옮겨 간다(1 -> 2 -> 3).
    window._move_amr_to_column(3)
    assert window.amr.target == 2 * (721.0 - 20.0)
    # 리프트는 행마다 (셀 세로 - 겹침) 만큼 오른다.
    assert plan.lift_pitch == 500.0 - 20.0
    window.close()


def test_cell_status_carries_the_grid_name(qtbot) -> None:
    """job_state 의 job_id 는 **격자 이름**이다 (사내 MC 규격 T-009).

    전체 작업 ID(jb00000001)가 아니다 — 규격이 `1A` ~ `12F` 로 못박아 뒀다.
    바깥은 이걸로 어느 영역이 끝났는지 추적한다.
    """
    mqtt_server = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt_server, start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    sent: list[tuple] = []
    mqtt_server.publish_job_state = lambda cell, state: sent.append(
        (cell, state)) or True

    window._publish_cell_status("2C", "executing")

    assert sent == [("2C", "executing")]
    window.close()


def test_hero_values_shrink_instead_of_being_clipped(qtbot) -> None:
    """폭이 모자라면 요약 칸 글자가 먼저 줄어야 한다.

    글자 크기가 고정이면 칸 넷이 들어갈 폭이 안 나와 칸이 통째로 창 밖으로
    밀려 잘렸다 — 전체화면에서만 멀쩡했던 이유다.
    """
    from PyQt6.QtGui import QFontMetrics

    from smr_operator_ui.components import FitLabel

    label = FitLabel("구간 검사 중", base_px=34, min_px=15)
    qtbot.addWidget(label)
    label.show()

    label.resize(90, 40)          # 34px 로는 도저히 안 들어가는 폭
    assert label.font().pixelSize() < 34
    assert QFontMetrics(label.font()).horizontalAdvance(label.text()) <= 90

    label.resize(400, 40)         # 넉넉하면 원래 크기로 돌아온다
    assert label.font().pixelSize() == 34


def test_hero_values_never_shrink_below_the_floor(qtbot) -> None:
    """아무리 좁아도 하한 아래로는 안 줄인다 — 읽을 수 없으면 소용없다."""
    from smr_operator_ui.components import FitLabel

    label = FitLabel("구간 검사 중", base_px=34, min_px=15)
    qtbot.addWidget(label)
    label.show()
    label.resize(10, 40)

    assert label.font().pixelSize() == 15


def test_hero_values_follow_the_window_scale(qtbot) -> None:
    """전체화면에서는 기준 크기도 같이 커져야 나머지 글자와 어울린다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    label = window.main_screen.phase_label

    window.main_screen.set_global_scale(1.3)
    assert label.font().pixelSize() == round(34 * 1.3)
    window.main_screen.set_global_scale(1.0)
    assert label.font().pixelSize() == 34
    window.close()


def test_robot_start_failure_is_shown_on_the_main_screen(qtbot) -> None:
    """로봇을 못 띄우면 그 이유가 작업 화면에 보여야 한다.

    예전에는 play/stop 실패가 코봇 수동 화면에만 남아서, 메인 화면에는
    "로봇 스캔을 시작합니다" 만 뜨고 로봇은 가만히 있는데 이유를 알 수
    없었다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)

    window._show_command_result("play", False, "로봇 제어 노드가 응답하지 않습니다.")

    notice = window.main_screen.activity_label.text()
    assert "play" in notice and "응답하지 않습니다" in notice
    alarms = [window.cobot_manual_screen.alarm_list.item(i).text()
              for i in range(window.cobot_manual_screen.alarm_list.count())]
    assert any("play" in text for text in alarms)
    window.close()


def test_successful_robot_command_does_not_shout(qtbot) -> None:
    """성공한 명령까지 메인 화면에 쏟아내면 정작 볼 것이 묻힌다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.main_screen.show_activity("1A: 로봇 스캔을 시작합니다.")

    window._show_command_result("play", True, "play 전송 완료")

    assert window.main_screen.activity_label.text() == "1A: 로봇 스캔을 시작합니다."
    window.close()


def test_erut_job_keeps_the_local_arc_and_probe_setup(qtbot) -> None:
    """20260812 ERUT 요청에는 반지름·두께·EOAT 가 없다 — RCS 설정으로 채운다.

    그대로 두면 0 이 되어 로봇이 굽은 벽을 평면으로 훑고, 그 0 이 설정에
    저장까지 되어 다음 작업에도 남았다.
    """
    from smr_operator_ui.services import GridPlan

    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window._work_area_extra = {"radius_mm": 834.6, "thickness_mm": 10.0,
                               "eoat_w_mm": 30.0, "eoat_h_mm": 30.0,
                               "eoat_type": 5.0}
    bare = GridPlan(column_count=1, row_count=1,
                    cell_width=721.0, cell_height=500.0)

    filled = window._fill_from_local_setup(bare)

    assert (filled.radius, filled.thickness, filled.eoat_probes) == (834.6, 10.0, 5)
    window.close()


def test_values_sent_by_erut_win_over_the_local_setup(qtbot) -> None:
    """ERUT 가 값을 주면 그 값을 쓴다 — 로컬 설정은 빈자리만 채운다."""
    from smr_operator_ui.services import GridPlan

    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window._work_area_extra = {"radius_mm": 834.6, "thickness_mm": 10.0,
                               "eoat_type": 5.0}
    given = GridPlan(column_count=1, row_count=1, cell_width=721.0,
                     cell_height=500.0, radius=1200.0, eoat_probes=8)

    filled = window._fill_from_local_setup(given)

    assert (filled.radius, filled.eoat_probes) == (1200.0, 8)
    assert filled.thickness == 10.0
    window.close()


def test_abort_backs_off_and_goes_home(qtbot) -> None:
    """ERUT abort: 걸려 있던 play 를 취소하고, 잠시 뒤 홈 이동을 보낸다.

    홈 이동은 노드에서 TCP -Z 로 먼저 물러난 뒤 올라가므로 곡면을 긁지 않는다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    calls: list[str] = []
    window.ros_status.call_command = lambda name: calls.append(name) or True

    window._start_robot_scan()            # play 가 1.2초 뒤로 걸려 있다
    window._abort_job()
    qtbot.wait(1500)

    assert "home" in calls
    assert "play" not in calls, "abort 뒤에 play 가 날아가 로봇이 다시 출발했다"
    window.close()


def test_origin_wait_is_ignored_when_no_section_is_running(qtbot) -> None:
    """스캔 구간이 안 도는데 290 == 7 이 남아 있어도 대기로 잡지 않는다.

    로봇은 멈춰도 7 을 들고 있어서, 안 거르면 abort 뒤 evt/ready 가 또 나간다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    told: list[int] = []
    window.erut_session.notify_at_origin = lambda: told.append(1)

    window._handle_origin_wait(_scan_state(7))

    assert told == []
    window.close()


def _task_spy(window):
    """태스크 경로 설정·명령 호출을 가로챈다."""
    pushed: list[tuple] = []
    calls: list[str] = []
    window.ros_status.set_task_paths = lambda scan, mark: pushed.append(
        (scan, mark)) or True
    window.ros_status.call_command = lambda name: calls.append(name) or True
    return pushed, calls


def test_sensor_tasks_are_the_default(qtbot) -> None:
    """기본은 센서판이다 — 체크 해제 상태로 뜬다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)

    assert window.screens["cobot"].nosensor_check.isChecked() is False
    scan, mark = window._task_paths()
    assert scan.endswith("/dusan_v4.task") and mark.endswith("/dusan_v4_mark.task")
    window.close()


def test_checking_nosensor_switches_and_loads_the_scan_task(qtbot) -> None:
    """체크하면 논센서 경로를 노드에 넘기고, 한가하면 바로 불러온다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    pushed, calls = _task_spy(window)
    saved: list[tuple] = []
    window.settings_service.save = lambda scope, values: saved.append((scope, dict(values)))

    window.screens["cobot"].nosensor_check.setChecked(True)

    assert pushed[-1] == ("Dusan/dusan_v4/dusan_v4_nosensor_seq.task",
                          "Dusan/dusan_v4/dusan_v4_nosensor_mark.task")
    assert calls[-1] == "load_scan_task"
    # 판과 버전을 늘 함께 저장한다 — 파일 저장소는 범위를 통째로 바꿔 쓴다.
    assert ("robot_task", {"nosensor": True, "task_version": "dusan_v4"}) in saved
    window.close()


def test_switching_mid_job_waits_for_the_next_job(qtbot) -> None:
    """작업 중에 바꾸면 지금 구간을 흔들지 않는다 — 불러오지 않는다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    pushed, calls = _task_spy(window)
    _scanning(window)

    window.screens["cobot"].nosensor_check.setChecked(True)

    assert pushed, "경로는 넘겨 둬야 다음 작업에 쓴다"
    assert "load_scan_task" not in calls
    window.close()


def test_stored_nosensor_choice_is_restored(qtbot) -> None:
    """저장해 둔 판이 다시 켤 때 그대로 돌아온다(신호 없이)."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    pushed, calls = _task_spy(window)

    window._apply_stored_settings("robot_task", {"nosensor": True})

    assert window.screens["cobot"].nosensor_check.isChecked() is True
    assert pushed[-1][0].endswith("dusan_v4_nosensor_seq.task")
    assert "load_scan_task" not in calls, "불러오기만으로 로봇을 건드리면 안 된다"
    window.close()


def test_erut_job_loads_the_chosen_scan_task_first(qtbot) -> None:
    """ERUT 구간 작업은 체크한 판의 스캔 태스크를 먼저 불러 두고 시작한다."""
    from smr_operator_ui.services import GridPlan

    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    pushed, calls = _task_spy(window)
    window._nosensor = True

    window._start_erut_job(GridPlan(column_count=1, row_count=1,
                                    cell_width=721.0, cell_height=500.0))

    assert pushed[-1][0].endswith("dusan_v4_nosensor_seq.task")
    assert calls[:3] == ["remote_control_on", "stop", "load_scan_task"]
    window.close()


def test_rcs_start_runs_the_same_job_as_mqtt(qtbot) -> None:
    """RCS '검사 시작'도 MQTT job_cmd 처럼 실제 순회를 시작한다.

    예전에는 데모 사이클에만 이어져 안전 순서 표시만 돌고 차량·리프트·
    로봇은 움직이지 않았다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    pushed, calls = _task_spy(window)
    window.main_screen.rect_view.set_work_area(721.0, 500.0, 30.0, 20.0)
    window.main_screen.orbit_view.set_target_dimensions(1.6692, 2.0)

    window.main_screen.start_button.click()

    plan = window.sequencer.plan
    assert plan is not None, "순회가 시작되지 않았다"
    # 원주 π x 1669.2 = 5244 / (721-20) -> 8 열, 높이 2000 / (500-20) -> 5 행
    assert (plan.column_count, plan.row_count) == (8, 5)
    assert (plan.pitch_x, plan.pitch_y) == (20.0, 20.0)
    assert calls[:3] == ["remote_control_on", "stop", "load_scan_task"]
    window.close()


def test_rcs_start_is_refused_while_a_job_runs(qtbot) -> None:
    """돌고 있는 작업을 버튼 한 번에 갈아엎지 않는다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _task_spy(window)
    _scanning(window)
    before = window.sequencer.plan

    window._start_inspection()

    assert window.sequencer.plan is before
    assert "이미 작업 중" in window.main_screen.activity_label.text()
    window.close()


def test_rcs_pause_pauses_and_resumes_the_real_job(qtbot) -> None:
    """RCS '일시정지'는 실제 순회를 멈추고, 다시 누르면 이어간다."""
    from smr_operator_ui.services import SequencerState

    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _task_spy(window)
    window._start_inspection()
    assert window.sequencer.state not in (SequencerState.IDLE, SequencerState.PAUSED)

    window._toggle_pause()
    assert window.sequencer.state is SequencerState.PAUSED
    window._toggle_pause()
    assert window.sequencer.state is not SequencerState.PAUSED
    window.close()


def test_home_button_sends_home_when_idle(qtbot) -> None:
    """메인 화면 '로봇 홈' — 작업이 없으면 바로 홈 명령을 보낸다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _pushed, calls = _task_spy(window)

    window.main_screen.home_button.click()

    assert calls == ["home"]
    window.close()


def test_home_is_refused_while_a_job_runs(qtbot) -> None:
    """작업 중(스캔·프로브·마킹 등)에는 홈 명령을 보내지 않고 로그에만 남긴다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _pushed, calls = _task_spy(window)
    window._start_inspection()
    calls.clear()

    window.main_screen.home_button.click()
    # 알림 문구는 바로 본다 — 기다리면 더미 장비 알림이 덮어쓴다.
    assert HOME_BUSY_TEXT in window.main_screen.activity_label.text()
    qtbot.wait(700)

    assert "home" not in calls, "동작 중인데 홈 명령이 나갔다"
    assert window.findChildren(QMessageBox) == [], "팝업은 띄우지 않는다"
    window.close()


def test_home_is_refused_while_the_robot_task_runs(qtbot) -> None:
    """RCS 작업이 없어도 로봇 태스크가 돌면(펜던트에서 튼 경우) 거절한다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _pushed, calls = _task_spy(window)

    window._on_robot_task_state(1)          # 레지스터 500: 실행 중
    window._request_home()
    assert calls == []

    window._on_robot_task_state(3)          # 중지됨
    window._request_home()
    assert calls == ["home"]
    window.close()


def test_manual_screen_home_follows_the_same_rule(qtbot) -> None:
    """Cobot 수동 제어의 '홈 이동'도 로봇이 동작 중이면 거절하고 사유를 남긴다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _pushed, calls = _task_spy(window)
    window._on_robot_task_state(1)

    window._handle_cobot_command("home")

    assert calls == []
    assert window.cobot_manual_screen.activity_label.text() == HOME_BUSY_TEXT
    window.close()


def test_home_parks_the_position_at_the_origin(qtbot) -> None:
    """정지 후 홈으로 보내면 현재 위치를 영점으로 되돌린다.

    태스크가 멈추면 로봇은 좌표를 더 쓰지 않아 레지스터에 마지막 스캔
    좌표가 굳어 남는다. 그 값이 계속 들어와도 다시 그리지 않고, 로봇이
    다시 좌표를 내기 시작하면(생존 카운터가 움직이면) 그때부터 그린다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _task_spy(window)
    rect = window.main_screen.rect_view
    stale = [0.0, 600.0, 300.0, 0.0, 0.0, 0.0]

    def scan_state(alive: int) -> list:
        return [6, 1, 3, alive, 1, 0, 257, 0, 0, 0]

    window.ros_status.scan_state_changed.emit(scan_state(1))
    window.ros_status.tcp_pose_zero_changed.emit(stale)
    assert (rect._pos_h_mm, rect._pos_v_mm) == (600.0, 300.0)

    window._request_home()
    assert (rect._pos_h_mm, rect._pos_v_mm) == (0.0, 0.0)
    # 굳은 좌표가 계속 들어와도 영점에 머문다.
    window.ros_status.scan_state_changed.emit(scan_state(1))
    window.ros_status.tcp_pose_zero_changed.emit(stale)
    assert (rect._pos_h_mm, rect._pos_v_mm) == (0.0, 0.0)

    # 다음 작업에서 로봇이 다시 좌표를 내면 그대로 그린다.
    window.ros_status.scan_state_changed.emit(scan_state(2))
    window.ros_status.tcp_pose_zero_changed.emit([0.0, 50.0, 0.0, 0.0, 0.0, 0.0])
    assert rect._pos_h_mm == 50.0
    window.close()


def test_mqtt_cobot_home_follows_the_home_rule(qtbot) -> None:
    """MC 의 mc_cmd cobot=home — 쉬고 있으면 홈, 동작 중이면 거절."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _pushed, calls = _task_spy(window)

    window._handle_mqtt_command(MqttTopics.MC_COMMAND, {"cobot": "home"})
    assert calls == ["home"]

    calls.clear()
    window._on_robot_task_state(1)
    window._handle_mqtt_command(MqttTopics.MC_COMMAND, {"cobot": "home"})
    assert calls == []
    assert HOME_BUSY_TEXT in window.main_screen.activity_label.text()
    window.close()


def test_mqtt_job_clear_stops_the_robot_too(qtbot) -> None:
    """MC 의 job_clear(정지)는 RCS '정지'와 같이 로봇 태스크까지 멈춘다.

    예전에는 순회만 멈춰 로봇이 계속 돌았고, 이어 누른 홈이 거절됐다.
    """
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _pushed, calls = _task_spy(window)
    window._start_inspection()
    calls.clear()

    window._handle_mqtt_command(MqttTopics.JOB_CLEAR, {"request": "true"})

    assert "stop" in calls
    assert window._job_running() is False
    window.close()


def test_hero_totals_show_counts_only(qtbot) -> None:
    """현재 구간/행 칸에는 전체 수만 — mm 값은 칸이 좁아 안 붙인다."""
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    window.main_screen.set_motion_values(960.0, 1234.0)
    assert "mm" not in window.main_screen.segment_total.text()
    assert "mm" not in window.main_screen.row_total.text()
    window.close()


def test_operation_records_follow_a_real_cell(qtbot, tmp_path, monkeypatch) -> None:
    """구간 하나를 돌면 작업기록·스캔좌표·알람이벤트·통신 파일이 남는다."""
    from pathlib import Path
    from openpyxl import load_workbook
    from smr_operator_ui.services import data_recorder as dr
    monkeypatch.setenv("SMR_DATA_DIR", str(tmp_path))
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    _task_spy(window)
    assert window.data_recorder.root == tmp_path

    window._start_inspection()                          # RCS 검사 시작
    window.sequencer.cell_status_changed.emit("1A", "executing")
    window.ros_status.scan_state_changed.emit([6, 1, 3, 1, 1, 0, 257, 0, 0, 0])
    window.ros_status.tcp_pose_zero_changed.emit([0.0, 40.0, 0.0, 0.0, 0.0, 180.0])
    window.ros_status.tcp_pose_zero_changed.emit([0.0, 80.0, 0.0, 0.0, 0.0, 180.0])
    window.sequencer.cell_status_changed.emit("1A", "completed")
    window.erut._note_traffic("수신", "doosan/robot/req/query", b'{"content":{"req_id":"q1"}}')
    window._stop_inspection()

    (book,) = (tmp_path / dr.JOBS).rglob("*.xlsx")
    rows = list(load_workbook(book).active.iter_rows(min_row=2, values_only=True))
    assert rows[0][4] == "RCS" and rows[0][6] == "1A" and rows[0][7] == "완료"
    assert rows[0][16] == 2, "스캔 중 좌표 2개"
    (scan,) = (tmp_path / dr.SCAN).rglob("*.txt")
    assert len(scan.read_text(encoding="utf-8").splitlines()) == 3 + 2
    events = next((tmp_path / dr.EVENTS).rglob("*.txt")).read_text(encoding="utf-8")
    assert "RCS 검사 시작" in events and "작업을 정지했습니다" in events
    comms = next((tmp_path / dr.COMMS).rglob("*.txt")).read_text(encoding="utf-8")
    assert "doosan/robot/req/query" in comms
    window.close()


def test_saving_system_settings_moves_the_record_folder(qtbot, tmp_path, monkeypatch) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)   # 시험용 임시 폴더로 뜬다
    qtbot.addWidget(window)
    # 환경변수 고정을 풀어야 설정값이 먹는다 — 창을 만든 뒤에 풀어서
    # 기본값(D:/SMR/Data)으로는 한 번도 쓰지 않게 한다.
    monkeypatch.delenv("SMR_DATA_DIR", raising=False)
    target = tmp_path / "records"
    window._apply_system_settings("system", {"데이터 저장 위치": str(target),
                                             "로그 보존 기간": 30})
    assert window.data_recorder.root == target
    assert window.data_recorder._retention_days == 30
    assert str(target) in window.screens["system"].field("데이터 저장 위치").toolTip()
    window.close()


# ---- 태스크 선택 (dusan_v4 / dusan_v5) ---------------------------------------------
def test_task_selector_lists_versions_with_v4_default(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    cobot = window.screens["cobot"]
    combo = cobot.task_version_combo
    assert [combo.itemText(i) for i in range(combo.count())] == ["dusan_v4", "dusan_v5"]
    assert cobot.task_version() == "dusan_v4"
    assert window._task_paths() == ("Dusan/dusan_v4/dusan_v4.task",
                                    "Dusan/dusan_v4/dusan_v4_mark.task")
    # 폼 필드가 아니다 — Cobot '저장' 때 cobot 설정에 따로 들어가지 않는다.
    assert "태스크 선택" not in cobot.values()
    window.close()


def test_choosing_v5_switches_paths_saves_and_loads(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    pushed, calls = _task_spy(window)
    saved: list[tuple] = []
    window.settings_service.save = lambda scope, values: saved.append((scope, dict(values)))
    combo = window.screens["cobot"].task_version_combo

    combo.setCurrentIndex(1)
    combo.activated.emit(1)                     # 사용자가 목록에서 고른 것

    assert pushed[-1] == ("Dusan/dusan_v5/dusan_v5.task", "Dusan/dusan_v5/dusan_v5_mark.task")
    assert calls[-1] == "load_scan_task"
    assert saved[-1] == ("robot_task", {"nosensor": False, "task_version": "dusan_v5"})

    # 태스크 판(논센서)과 함께 쓴다.
    window.screens["cobot"].nosensor_check.setChecked(True)
    assert pushed[-1] == ("Dusan/dusan_v5/dusan_v5_nosensor_seq.task",
                          "Dusan/dusan_v5/dusan_v5_nosensor_mark.task")
    assert saved[-1] == ("robot_task", {"nosensor": True, "task_version": "dusan_v5"})
    window.close()


def test_stored_task_version_is_restored_without_loading(qtbot) -> None:
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    pushed, calls = _task_spy(window)

    window._apply_stored_settings("robot_task", {"nosensor": False, "task_version": "dusan_v5"})
    assert window.screens["cobot"].task_version() == "dusan_v5"
    assert pushed[-1][0] == "Dusan/dusan_v5/dusan_v5.task"
    assert "load_scan_task" not in calls, "불러오기만으로 로봇을 건드리면 안 된다"

    # 예전 설정(버전 없음)이나 목록에 없는 값이면 기본 v4 로 둔다.
    window._apply_stored_settings("robot_task", {"nosensor": False})
    assert window.screens["cobot"].task_version() == "dusan_v4"
    window._apply_stored_settings("robot_task", {"task_version": "dusan_v9"})
    assert window.screens["cobot"].task_version() == "dusan_v4"
    assert window._task_paths()[0] == "Dusan/dusan_v4/dusan_v4.task"
    window.close()
