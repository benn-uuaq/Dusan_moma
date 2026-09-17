"""TPAC 엔코더 보드 동기 신호 DO[0..2] (Coil 16~18 / Register 1 bit 0~2).

요구사항(3S 개발 범위 · DO 신호 제어 시퀀스):
  1. 격자 준비 : DO[2]=1 (리셋) -> 시작 위치 이동 -> DO[2]=0
  2. 전진 스캔 : DO[0]=1 -> DO[1]=1 -> 끝점에서 DO[1]=0
  3. 줄 바꿈   : DO[1]=0 유지
  4. 후진 스캔 : DO[0]=0 -> DO[1]=1 -> 끝점에서 DO[1]=0
  5. 다음 전진 : DO[0]=1 (0->1 = 라인 리셋) -> DO[1]=1
  격자 사이(리프트·차량 이동)는 리셋으로 구분한다. 신호는 바뀔 때마다 50 ms 이상 유지.
"""

import socket
import threading
import time

import pytest

from smr_operator_ui.services.tpac_bridge.scan_signals import (
    INITIAL, SEG_BACKWARD, SEG_FORWARD, SEG_INDEX, SEG_NONE, Outputs,
    ScanSignalOutput, plan_steps, target_outputs,
)


class Recorder:
    """ScanSignalOutput 이 흘려보내는 순서를 그대로 따라가 본다(스레드 없이)."""

    def __init__(self):
        self.current = INITIAL
        self.planned = INITIAL
        self.last_segment = SEG_NONE
        self.history = []

    def feed(self, state, segment, task_state=0):
        new_forward = segment == SEG_FORWARD and self.last_segment != SEG_FORWARD
        self.last_segment = segment
        target = target_outputs(self.planned, state, segment, task_state)
        steps = plan_steps(self.planned, target, new_forward_line=new_forward)
        self.history += [s.bits for s in steps]
        if steps:
            self.planned = steps[-1]
        return [s.bits for s in steps]


def test_one_grid_follows_the_encoder_board_sequence() -> None:
    r = Recorder()
    # 1. 준비: 프로브·원점 복귀 동안 리셋 유지, 원점에서 해제
    assert r.feed(2, SEG_NONE) == []                      # 이미 리셋(0,0,1)
    assert r.feed(3, SEG_NONE) == []
    assert r.feed(7, SEG_NONE) == [[0, 0, 0]]             # DO[2]=0 준비 완료
    assert r.feed(8, SEG_NONE) == []                      # 적심 중 그대로
    # 2. 전진 스캔: 방향 먼저, 스캔 나중
    assert r.feed(6, SEG_FORWARD) == [[1, 0, 0], [1, 1, 0]]
    assert r.feed(6, SEG_NONE) == [[1, 0, 0]]             # 끝점 -> 동결
    # 3. 줄 바꿈: 동결 유지
    assert r.feed(6, SEG_INDEX) == []
    # 4. 후진 스캔
    assert r.feed(6, SEG_BACKWARD) == [[0, 0, 0], [0, 1, 0]]
    assert r.feed(6, SEG_NONE) == [[0, 0, 0]]
    assert r.feed(6, SEG_INDEX) == []
    # 5. 다음 전진 — 0 -> 1 전환이 라인 리셋
    assert r.feed(6, SEG_FORWARD) == [[1, 0, 0], [1, 1, 0]]
    assert r.feed(6, SEG_NONE) == [[1, 0, 0]]
    # 격자 끝 -> 리셋으로 구분(리프트·차량 이동 동안 유지)
    assert r.feed(5, SEG_NONE) == [[1, 0, 1], [0, 0, 1]]
    assert r.feed(5, SEG_NONE) == []
    # 다음 격자: 준비 동안 리셋 유지, 차량 대기(14)는 그대로, 원점에서 해제
    assert r.feed(0, SEG_NONE) == []
    assert r.feed(14, SEG_NONE) == []
    assert r.feed(7, SEG_NONE) == [[0, 0, 0]]


def test_scan_is_never_on_while_direction_or_reset_changes() -> None:
    r = Recorder()
    for state, seg in ((7, 0), (6, 1), (6, 0), (6, 2), (6, 3), (6, 0), (6, 2), (6, 1), (5, 0), (7, 0)):
        r.feed(state, seg)
    prev = INITIAL.bits
    for bits in r.history:
        changed = [i for i in range(3) if bits[i] != prev[i]]
        assert len(changed) == 1, f"한 번에 한 비트만 바뀐다: {prev} -> {bits}"
        if changed[0] in (0, 2):
            assert bits[1] == 0 and prev[1] == 0, f"스캔 중에 방향·리셋이 바뀌면 안 된다: {prev} -> {bits}"
        prev = bits


def test_new_forward_line_with_direction_already_high_gets_a_line_reset_edge() -> None:
    current = Outputs(direction=1, scan=0, reset=0)
    target = Outputs(direction=1, scan=1, reset=0)
    steps = plan_steps(current, target, new_forward_line=True)
    assert [s.bits for s in steps] == [[0, 0, 0], [1, 0, 0], [1, 1, 0]]


def test_pause_mid_line_freezes_and_resume_does_not_reset_the_line() -> None:
    r = Recorder()
    r.feed(7, SEG_NONE)
    r.feed(6, SEG_FORWARD)
    assert r.feed(6, SEG_FORWARD, task_state=2) == [[1, 0, 0]], "일시정지 -> 동결"
    assert r.feed(6, SEG_FORWARD, task_state=1) == [[1, 1, 0]], "재개 — 방향 0->1 을 다시 만들지 않는다"


def test_error_freezes_without_wiping_counters() -> None:
    r = Recorder()
    r.feed(7, SEG_NONE)
    r.feed(6, SEG_BACKWARD)
    assert r.feed(9, SEG_BACKWARD) == [[0, 0, 0]]
    assert r.planned.reset == 0, "오류는 동결만 — 리셋하면 그 격자 데이터가 지워진다"


def test_output_thread_keeps_each_state_for_the_latch_time() -> None:
    written = []
    out = ScanSignalOutput(lambda o: written.append((time.monotonic(), o.bits)), latch_ms=50)
    out.start()
    try:
        out.observe(7, SEG_NONE)
        out.observe(6, SEG_FORWARD)          # 두 단계
        out.observe(6, SEG_NONE)
        out.observe(5, SEG_NONE)             # 두 단계
        deadline = time.monotonic() + 2.0
        while len(written) < 7 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        out.stop()
    assert [b for _, b in written] == [
        [0, 0, 1], [0, 0, 0], [1, 0, 0], [1, 1, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]]
    gaps = [b[0] - a[0] for a, b in zip(written, written[1:])]
    assert min(gaps) >= 0.049, gaps
    assert out.current.bits == [0, 0, 1]


# ---- 실제 Modbus 서버로 왕복 -----------------------------------------------------
def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_bridge_serves_coils_and_register_one_to_several_clients() -> None:
    from pyModbusTCP.client import ModbusClient

    from smr_operator_ui.services.tpac_bridge.bridge_core import ExternalServer
    from smr_operator_ui.services.tpac_bridge.robot_map import RobotData

    port = _free_port()
    server = ExternalServer()
    # 화면 기본값 그대로: 데이터 블록이 주소 0 에서 시작해 레지스터 1 과 겹친다.
    assert server.start(host="127.0.0.1", port=port, start_addr=0, mirror=True,
                        fmt="float32", ur_mode=True, pose_source="scan", signal_latch_ms=50)
    tpac = ModbusClient(host="127.0.0.1", port=port, auto_open=True, timeout=1.0)
    monitor = ModbusClient(host="127.0.0.1", port=port, auto_open=True, timeout=1.0)
    try:
        def feed(state, segment, scan=(1234, -567, 890, 10, 20, 30)):
            regs = {277: segment, 290: state, 500: 1}
            regs.update({280 + i: v & 0xFFFF for i, v in enumerate(scan)})
            server.update(RobotData.from_registers(regs))

        def settled(bits):
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                feed(*last)
                coils = tpac.read_coils(16, 3)
                word = monitor.read_holding_registers(1, 1)
                if coils and word and [int(c) for c in coils] == bits \
                        and word[0] == bits[0] | bits[1] << 1 | bits[2] << 2:
                    return True
                time.sleep(0.02)
            return False

        last = (7, 0)
        assert settled([0, 0, 0]), "원점 도착 -> 리셋 해제"
        last = (6, 1)
        assert settled([1, 1, 0]), "전진 스캔"
        # 두 클라이언트가 같은 값을 본다 — TPAC 과 모의기가 동시에 붙어도 된다.
        assert [int(c) for c in monitor.read_coils(16, 3)] == [1, 1, 0]
        assert tpac.read_holding_registers(1, 1) == [3]
        # 좌표는 400~405 에 그대로(스캔 좌표 기본).
        pose = tpac.read_holding_registers(400, 6)
        assert [v - 65536 if v > 32767 else v for v in pose] == [1234, -567, 890, 10, 20, 30]
        last = (6, 3)
        assert settled([0, 1, 0]), "후진 스캔"
        last = (5, 0)
        assert settled([0, 0, 1]), "격자 끝 -> 리셋"
    finally:
        tpac.close()
        monitor.close()
        server.stop()
