"""TPAC 엔코더 보드 모의기(mqtt_test/tpac_encoder_sim.py) 시험.

  1. 모의기의 보드 흉내(EncoderBoard) — 라인·C-scan·리셋·latch 위반 판정
  2. 끝까지: 로봇 격자 하나를 흉내 내 **실제 RCS 브리지 서버**에 흘리고, 모의기의
     폴러(표준 라이브러리 Modbus 클라이언트)로 읽어 보드가 센 결과를 본다.
"""

import importlib.util
import queue
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

SIM_PATH = Path(__file__).resolve().parents[3] / "mqtt_test" / "tpac_encoder_sim.py"


@pytest.fixture(scope="module")
def sim():
    spec = importlib.util.spec_from_file_location("tpac_encoder_sim", SIM_PATH)
    module = importlib.util.module_from_spec(spec)
    # dataclass 가 자기 모듈을 sys.modules 에서 찾으므로 먼저 등록한다.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _pose(x_mm=0.0, z_mm=0.0):
    return [int(round(x_mm * 10)) & 0xFFFF, 0, int(round(z_mm * 10)) & 0xFFFF, 0, 0, 0]


def test_board_counts_lines_and_resets(sim) -> None:
    board = sim.EncoderBoard(latch_ms=50, poll_ms=10)
    t = 0.0

    def step(bits, x=0.0, z=0.0, dt=0.06):
        nonlocal t
        t += dt
        word = bits[0] | bits[1] << 1 | bits[2] << 2
        return board.update(t, bits, word, _pose(x, z))

    step([0, 0, 1])                         # 리셋
    step([0, 0, 0])                         # 준비
    step([1, 0, 0])                         # 방향 전진 (0->1 = 라인 리셋)
    step([1, 1, 0])                         # 스캔 시작
    for x in (100, 200, 300):
        step([1, 1, 0], x=x)
    step([1, 0, 0], x=300)                  # 동결
    step([1, 0, 0], x=300, z=30)            # 줄 바꿈 — 안 센다
    step([0, 0, 0], x=300, z=30)            # 방향 후진
    step([0, 1, 0], x=300, z=30)
    for x in (200, 100, 0):
        step([0, 1, 0], x=x, z=30)
    step([0, 0, 0], z=30)

    assert board.lines == 2
    assert [r.direction for r in board.line_records] == ["전진", "후진"]
    assert board.line_records[0].length_mm == pytest.approx(300)
    assert board.line_records[1].length_mm == pytest.approx(300)
    assert board.cscan_mm == pytest.approx(0), "전진 +300, 후진 -300"
    assert board.odometer_mm == pytest.approx(600)
    assert board.frozen_move_mm == pytest.approx(30)
    assert board.violations == 0 and not board.mismatch

    step([0, 0, 1], z=30)                   # 격자 끝 리셋
    assert board.lines == 0 and board.odometer_mm == 0 and board.line_records == []


def test_board_flags_short_latches_and_channel_mismatch(sim) -> None:
    board = sim.EncoderBoard(latch_ms=50, poll_ms=10)
    board.update(0.000, [0, 0, 0], 0, _pose())
    events = board.update(0.020, [1, 0, 0], 1, _pose())       # 20 ms 만에 바뀜
    assert board.violations == 1 and events[0].warn
    board.update(0.100, [1, 1, 1], 3, _pose())                  # 두 비트 동시 + FC3 불일치
    board.update(0.110, [1, 1, 1], 3, _pose())
    assert board.violations == 2
    assert board.mismatch, "FC1 과 FC3 가 두 번 연속 다르면 불일치"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_end_to_end_one_grid_through_the_real_bridge(sim) -> None:
    from smr_operator_ui.services.tpac_bridge.bridge_core import ExternalServer
    from smr_operator_ui.services.tpac_bridge.robot_map import RobotData

    port = _free_port()
    server = ExternalServer()
    assert server.start(host="127.0.0.1", port=port, mirror=True, ur_mode=True,
                        pose_source="scan", signal_latch_ms=50)

    # 로봇 흉내 — dus_pass_r/l · dus_up · dus_finish 가 쓰는 순서 그대로.
    robot = {"state": 2, "seg": 0, "x": 0.0, "z": 0.0}
    stop = threading.Event()

    def publish():
        while not stop.is_set():
            regs = {277: robot["seg"], 290: robot["state"], 500: 1}
            regs.update({280 + i: v for i, v in enumerate(_pose(robot["x"], robot["z"]))})
            server.update(RobotData.from_registers(regs))
            time.sleep(0.02)

    def hold(seconds):
        time.sleep(seconds)

    def move(key, target, seconds):
        start, steps = robot[key], max(1, int(seconds / 0.01))
        for k in range(1, steps + 1):
            robot[key] = start + (target - start) * k / steps
            time.sleep(seconds / steps)

    feeder = threading.Thread(target=publish, daemon=True)
    feeder.start()
    out = queue.Queue()
    poller = sim.Poller("127.0.0.1", port, 1, 10, out)
    poller.start()
    board = sim.EncoderBoard(latch_ms=50, poll_ms=10)
    events = []
    snapshot = {}
    try:
        hold(0.3)                                             # 3점 측정(리셋 유지)
        robot["state"] = 7; hold(0.2)                         # 원점 도착 -> 준비
        robot["state"] = 8; hold(0.2)                         # 적심
        robot["state"] = 6
        for line, (seg, target) in enumerate(((1, 400.0), (3, 0.0), (1, 400.0))):
            robot["seg"] = seg; hold(0.25)                    # sig_hold
            move("x", target, 0.5)
            robot["seg"] = 0; hold(0.25)                      # 끝점 동결
            if line < 2:
                robot["seg"] = 2; move("z", robot["z"] + 30, 0.2)   # 줄 바꿈
        hold(0.2)
        # 격자 끝 직전 — 보드가 센 값을 떠 둔다(리셋되면 지워진다).
        _drain(out, board, events)
        snapshot = {"lines": board.lines, "lengths": [r.length_mm for r in board.line_records],
                    "dirs": [r.direction for r in board.line_records],
                    "frozen": board.frozen_move_mm, "violations": board.violations,
                    "mismatch": board.mismatch}
        robot["state"] = 5; hold(0.4)                         # 격자 끝 -> 리셋
        _drain(out, board, events)
    finally:
        poller.stop()
        stop.set()
        feeder.join(timeout=1)
        poller.join(timeout=2)
        server.stop()

    assert snapshot["lines"] == 3, [e.text for e in events]
    assert snapshot["dirs"] == ["전진", "후진", "전진"]
    for length in snapshot["lengths"]:
        assert length == pytest.approx(400, abs=2), snapshot["lengths"]
    assert snapshot["frozen"] == pytest.approx(60, abs=2), "줄 바꿈 두 번은 동결 중에 움직였다"
    assert snapshot["violations"] == 0, [e.text for e in events if e.warn]
    assert not snapshot["mismatch"]
    assert board.do == [0, 0, 1] and board.lines == 0, "격자 끝에는 리셋으로 구분"


def _drain(out, board, events):
    while True:
        try:
            item = out.get_nowait()
        except queue.Empty:
            return
        if item[0] == "data":
            _, t, coils, word, pose = item
            events.extend(board.update(t, coils, word, pose))
