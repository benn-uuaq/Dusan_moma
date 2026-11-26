"""TPAC 화면 「주소 매핑」 드롭다운 — ELITE / UR (2026-11-25).

ELITE 를 고르면 아래 주소가 ELITE 로봇 자리로 바뀐다 — 포즈 값 실제 TCP · 포즈 384 · 속도 400 ·
평면 280 · Cscan 286 · Odometer 287, UR 배치는 꺼지고 ELITE 배치가 켜진다. TPAC 동기 신호는 그대로.
"""

import json
import os
import socket

from smr_operator_ui.app import OperatorWindow
from smr_operator_ui.services.tpac_bridge.bridge_core import MAPPING_ELITE, MAPPING_UR
from smr_operator_ui.services.tpac_bridge.robot_map import RobotData

ROBOT_SCAN = [1234, -567, 890, 10, -20, 30, 4321, 1500]     # 로봇 280~287
TCP = [5000, -1200, 3300, 3141, -10, 20]                    # 로봇 384~389
SPEED = [-150, 80, -20, 5, -3, 1]                           # 로봇 400~405

ELITE_FIELDS = {"pose_source": "pose", "pose_addr": 384, "speed_addr": 400, "plane_addr": 280,
                "cscan_addr": 286, "odometer_addr": 287, "ur_mode": False, "elite_status": True}
UR_FIELDS = {"pose_source": "scan", "pose_addr": 400, "speed_addr": 410, "plane_addr": 420,
             "cscan_addr": 431, "odometer_addr": 430, "ur_mode": True, "elite_status": False}


def _u(values):
    return [v & 0xFFFF for v in values]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _robot_data(state=6) -> RobotData:
    regs = {277: 1, 290: state, 500: 1, 66: 7, 67: 1}
    regs.update({280 + i: v & 0xFFFF for i, v in enumerate(ROBOT_SCAN)})
    regs.update({384 + i: v & 0xFFFF for i, v in enumerate(TCP)})
    regs.update({400 + i: v & 0xFFFF for i, v in enumerate(SPEED)})
    regs.update({73 + i: 100 * (i + 1) for i in range(6)})
    return RobotData.from_registers(regs)


def _window(qtbot) -> OperatorWindow:
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: "tpac_bridge" not in (window._initial_scopes or set()), timeout=3000)
    return window


def _fields(screen) -> dict:
    values = screen.server_settings()
    return {key: values[key] for key in ELITE_FIELDS}


def _choose(screen, mapping) -> None:
    screen.srv_mapping.setCurrentIndex(screen.srv_mapping.findData(mapping))


def _start(screen) -> None:
    screen.srv_bind_ip.setText("127.0.0.1")
    screen.srv_port.setValue(_free_port())
    screen._start_server()
    assert screen.server.running


def test_dropdown_defaults_to_ur(qtbot) -> None:
    window = _window(qtbot)
    screen = window.screens["tpac_bridge"]
    combo = screen.srv_mapping
    assert [combo.itemData(i) for i in range(combo.count())] == [MAPPING_ELITE, MAPPING_UR]
    assert combo.currentData() == MAPPING_UR and not hasattr(screen, "srv_ur_addr")
    assert _fields(screen) == UR_FIELDS
    assert screen.address_overlaps() == []
    window.close()


def test_choosing_elite_moves_addresses_and_layout(qtbot) -> None:
    window = _window(qtbot)
    screen = window.screens["tpac_bridge"]
    _choose(screen, MAPPING_ELITE)
    assert _fields(screen) == ELITE_FIELDS
    assert screen.srv_signals.isChecked()                       # TPAC 동기 신호는 그대로
    assert screen.address_overlaps() == []
    # 주소는 그 뒤 손으로 바꿀 수 있다 — 매핑을 다시 고르기 전까지 남는다
    screen.srv_pose_addr.setValue(600)
    assert screen.srv_pose_addr.value() == 600
    _choose(screen, MAPPING_UR)
    assert _fields(screen) == UR_FIELDS
    window.close()


def test_elite_server_mirrors_robot_registers(qtbot) -> None:
    window = _window(qtbot)
    screen = window.screens["tpac_bridge"]
    _choose(screen, MAPPING_ELITE)
    _start(screen)
    try:
        assert screen.server.mapping == MAPPING_ELITE and screen.server.elite_status
        assert not screen.server.ur_mode and screen.server.signals is not None
        screen._on_data(_robot_data())
        bank = screen.server.bridge.bank
        assert bank.get_holding_registers(280, 8) == _u(ROBOT_SCAN)   # 평면 · Cscan · Odometer
        assert bank.get_holding_registers(384, 6) == _u(TCP)          # pose = 베이스 TCP
        assert bank.get_holding_registers(400, 6) == _u(SPEED)        # 속도
        assert bank.get_holding_registers(66, 2) == [7, 1]            # 모드 · 전원
        assert bank.get_holding_registers(73, 6) == [100, 200, 300, 400, 500, 600]
        assert bank.get_holding_registers(270, 6) == [0] * 6          # UR 자리는 비운다
        assert bank.get_holding_registers(410, 6) == [0] * 6
        assert bank.get_holding_registers(256, 3) == [0] * 3
        assert screen.server.covers(286, 1) == "Cscan"
        assert screen.server.covers(280, 8) == "작업 평면 + Cscan + Odometer"     # 한 번에 읽어도 채워짐
        assert screen.server.covers(280, 9) is None                               # 288 은 비어 있다
        rows = {screen.serve_table.item(r, 0).text(): screen.serve_table.item(r, 1).text()
                for r in range(screen.serve_table.rowCount())}
        assert rows["384"] == "[ELITE] TCP X" and rows["400"].startswith("[ELITE] 속도")
        assert rows["280"] == "[평면] TCP X" and rows["66"] == "[ELITE] Robot Mode"
        assert "270" not in rows and "256" not in rows
    finally:
        screen._stop_server()
    window.close()


def test_scan_zero_hold_also_zeroes_elite_scan_block(qtbot) -> None:
    window = _window(qtbot)
    screen = window.screens["tpac_bridge"]
    _choose(screen, MAPPING_ELITE)
    _start(screen)
    try:
        screen._on_data(_robot_data())
        screen.hold_scan_zero("시험")
        bank = screen.server.bridge.bank
        assert bank.get_holding_registers(280, 8) == [0] * 8
        assert bank.get_holding_registers(384, 6) == _u(TCP)          # 실제 TCP 는 그대로
    finally:
        screen._stop_server()
    window.close()


def test_overlap_is_reported(qtbot) -> None:
    window = _window(qtbot)
    screen = window.screens["tpac_bridge"]
    _choose(screen, MAPPING_ELITE)
    screen.srv_pose_addr.setValue(400)            # ELITE 에서 pose 를 속도 자리에 두면
    warnings = screen.address_overlaps()
    assert len(warnings) == 1 and "포즈(400~405)" in warnings[0] and "속도(400~405)" in warnings[0]
    window.close()


def test_mapping_choice_survives_restart(qtbot) -> None:
    window = _window(qtbot)
    screen = window.screens["tpac_bridge"]
    _choose(screen, MAPPING_ELITE)
    screen.cscan_addr.setValue(296)
    path = os.environ["SMR_SETTINGS_FILE"]

    def saved() -> dict:
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle).get("tpac_bridge", {})
        except (OSError, ValueError):
            return {}

    qtbot.waitUntil(lambda: saved().get("mapping") == MAPPING_ELITE and saved().get("cscan_addr") == 296,
                    timeout=3000)
    window.close()

    again = _window(qtbot)
    screen = again.screens["tpac_bridge"]
    qtbot.waitUntil(lambda: screen.srv_mapping.currentData() == MAPPING_ELITE, timeout=3000)
    assert _fields(screen) == {**ELITE_FIELDS, "cscan_addr": 296}
    again.close()


def test_old_saved_elite_with_ur_addresses_gets_elite_addresses(qtbot) -> None:
    """앞 판은 ELITE 를 골라도 주소가 UR(400 …) 그대로 저장됐다 — 불러올 때 ELITE 자리로 맞춘다."""
    window = _window(qtbot)
    screen = window.screens["tpac_bridge"]
    screen.apply_server_settings({"mapping": MAPPING_ELITE, "pose_source": "scan", "pose_addr": 400,
                                  "plane_addr": 420, "odometer_addr": 430, "cscan_addr": 431,
                                  "ur_mode": True, "scan_signals": True, "latch_ms": 70})
    assert _fields(screen) == ELITE_FIELDS and screen.srv_latch_ms.value() == 70
    window.close()


def test_loaded_settings_restart_a_running_server(qtbot) -> None:
    """로봇이 먼저 붙어 서버가 기본(UR)으로 떴어도, 저장값이 오면 그 배치로 다시 켠다."""
    window = _window(qtbot)
    screen = window.screens["tpac_bridge"]
    _start(screen)
    try:
        assert screen.server.mapping == MAPPING_UR
        screen.apply_server_settings({"version": 2, "mapping": MAPPING_ELITE})
        assert screen.server.running and screen.server.mapping == MAPPING_ELITE
        assert screen.server.mirror_map["pose"] == 384 and screen.server.mirror_map["speed"] == 400
        assert screen.server.elite_status and not screen.server.ur_mode
    finally:
        screen._stop_server()
    window.close()
