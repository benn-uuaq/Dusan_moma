#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""단독 TPAC 브릿지 끝까지 시험 — 가짜 로봇(Modbus 서버) → 브릿지 → TPAC 역할 클라이언트.

    python test_bridge_standalone.py        (pytest 로도 돈다)
실제 로봇 · TPAC 없이 높은 포트(15xxx)에서 돈다.
"""

import queue
import time

from pyModbusTCP.client import ModbusClient
from pyModbusTCP.server import DataBank, ModbusServer

import json

from bridge_core import plan_blocks
from tpac_bridge_app import DEFAULTS, BridgeRunner, load_settings

ROBOT_PORT, TPAC_PORT = 15502, 15503


def _u16(v):
    return int(v) & 0xFFFF


def _s16(v):
    return v - 0x10000 if v >= 0x8000 else v


def _wait(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def _robot():
    bank = DataBank()
    server = ModbusServer(host="127.0.0.1", port=ROBOT_PORT, no_block=True, data_bank=bank)
    server.start()
    bank.set_holding_registers(63, [2, 7, 0, 7, 1, 0, 0, 0, 2, 0])            # 버전 · 모드 7 · 전원
    bank.set_holding_registers(384, [_u16(v) for v in (4500, -1200, 3300, 3141, 0, -1571)])
    bank.set_holding_registers(400, [_u16(v) for v in (25, 0, -3, 0, 0, 0)])  # 실제 속도
    bank.set_holding_registers(280, [0] * 8)
    bank.set_holding_registers(2, [0])
    return server, bank


def _settings(**over):
    values = dict(DEFAULTS)
    values.update(robot_ip="127.0.0.1", robot_port=ROBOT_PORT, tpac_bind="127.0.0.1",
                  tpac_port=TPAC_PORT, interval_ms=50, latch_ms=50)
    values.update(over)
    return values


def test_coordinates_and_do0_reach_tpac():
    robot, bank = _robot()
    runner = BridgeRunner(queue.Queue())
    tpac = ModbusClient(host="127.0.0.1", port=TPAC_PORT, auto_open=True, timeout=1.0)
    try:
        assert runner.start(_settings())
        assert _wait(lambda: runner.last_data is not None)
        # 스캔 전 — CSCAN 은 0, 포즈(400)도 CSCAN(0), 속도 0, DO 0
        assert _wait(lambda: tpac.read_holding_registers(400, 6) == [0] * 6)
        assert tpac.read_holding_registers(410, 6) == [0] * 6
        assert tpac.read_holding_registers(1, 1) == [0]
        # 기본 좌표는 데이터 블록(관절 6 다음 6워드)에 늘 있다
        assert [_s16(v) for v in tpac.read_holding_registers(6, 6)] == [4500, -1200, 3300, 3141, 0, -1571]

        # 로봇 프로그램이 스캔을 시작했다 — CSCAN 좌표 · C-scan 위치를 쓰고 DO0 을 켠다
        bank.set_holding_registers(280, [_u16(v) for v in (1234, 0, -56, 0, 0, 0, 789, 42)])
        bank.set_holding_registers(2, [0b1001])            # DO0 · DO3 — 기본은 DO0 만 넘긴다
        assert _wait(lambda: tpac.read_holding_registers(1, 1) == [1])
        assert tpac.read_coils(16, 3) == [True, False, False]
        assert [_s16(v) for v in tpac.read_holding_registers(400, 6)] == [1234, 0, -56, 0, 0, 0]
        assert [_s16(v) for v in tpac.read_holding_registers(420, 6)] == [1234, 0, -56, 0, 0, 0]
        assert tpac.read_holding_registers(431, 1) == [789]       # C-scan 위치 0.1mm
        assert tpac.read_holding_registers(430, 1) == [42]        # 누적 mm
        assert [_s16(v) for v in tpac.read_holding_registers(410, 3)] == [25, 0, -3]   # 스캔 중 속도

        # 짧게 껐다 켜도 TPAC 은 꺼진 것을 본다(latch)
        bank.set_holding_registers(2, [0])
        assert _wait(lambda: tpac.read_holding_registers(1, 1) == [0])
        assert tpac.read_coils(16, 1) == [False]
    finally:
        runner.stop()
        tpac.close()
        robot.stop()


def test_pose_source_and_do_range_options():
    robot, bank = _robot()
    runner = BridgeRunner(queue.Queue())
    tpac = ModbusClient(host="127.0.0.1", port=TPAC_PORT, auto_open=True, timeout=1.0)
    try:
        assert runner.start(_settings(pose_source="pose", signal_mode="do0_2"))
        bank.set_holding_registers(2, [0b1111])
        assert _wait(lambda: tpac.read_holding_registers(1, 1) == [0b111])     # DO0~DO2 만
        assert tpac.read_coils(16, 3) == [True, True, True]
        # 포즈(400) = 기본 좌표, 속도는 늘 낸다
        assert [_s16(v) for v in tpac.read_holding_registers(400, 6)] == [4500, -1200, 3300, 3141, 0, -1571]
        assert [_s16(v) for v in tpac.read_holding_registers(410, 3)] == [25, 0, -3]
    finally:
        runner.stop()
        tpac.close()
        robot.stop()


def test_robot_reconnects_after_it_comes_up_late():
    runner = BridgeRunner(queue.Queue())
    try:
        assert runner.start(_settings())                       # 로봇이 아직 없다 — 다시 시도한다
        time.sleep(0.5)
        robot, _bank = _robot()
        try:
            assert _wait(lambda: runner.last_data is not None, timeout=8.0)
        finally:
            robot.stop()
    finally:
        runner.stop()


def test_coordinate_block_is_read_last_and_status_is_not_in_the_cycle():
    """지연 검토(2026-11-18) — 좌표를 맨 뒤에 읽고 바로 낸다. 상태 블록은 매 주기 읽지 않는다."""
    fast, slow = plan_blocks()
    assert fast[-1] == (280, 8)                        # CSCAN 좌표가 맨 뒤
    assert (2, 1) in fast and (384, 22) in fast         # DO · 기본 좌표 + 실제 속도
    assert all(start not in (63, 500, 277) for start, _n in fast)
    assert (63, 48) in slow and (500, 1) in slow
    fast_task, _slow = plan_blocks(use_signal=True)
    assert fast_task[-1] == (277, 14)                   # 두산 태스크 방식은 277~290 과 한 블록


def test_old_default_interval_moves_to_the_new_default(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"interval_ms": 100}), encoding="utf-8")
    assert load_settings(str(path))["interval_ms"] == 20
    path.write_text(json.dumps({"interval_ms": 50}), encoding="utf-8")
    assert load_settings(str(path))["interval_ms"] == 50           # 사람이 바꾼 값은 그대로
    path.write_text(json.dumps({"interval_ms": 100, "settings_version": 2}), encoding="utf-8")
    assert load_settings(str(path))["interval_ms"] == 100


if __name__ == "__main__":
    import inspect
    import pathlib
    import tempfile
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            if "tmp_path" in inspect.signature(fn).parameters:
                with tempfile.TemporaryDirectory() as tmp:
                    fn(pathlib.Path(tmp))
            else:
                fn()
            print("통과:", name)
