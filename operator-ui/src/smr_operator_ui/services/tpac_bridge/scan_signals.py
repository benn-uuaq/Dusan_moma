"""TPAC 엔코더 보드 동기 신호 — DO[0..2] (Coil 16~18 / Register 1 bit 0~2).

TPAC 엔코더 보드가 요구하는 신호다(3S 개발 범위 · Modbus 메모리 맵 요구사항).

  DO[0] 스캔 방향     Coil 16 / Reg 1 bit 0   1 = Forward, 0 = Backward
                                              (0 -> 1 로 바뀌는 순간 = 라인 리셋)
  DO[1] 스캔 플래그   Coil 17 / Reg 1 bit 1   1 = 유효 검사 구간(펄스 출력), 0 = 동결
  DO[2] 호밍/초기화   Coil 18 / Reg 1 bit 2   1 = 전체 카운터 리셋, 0 = 준비 완료

TPAC 펌웨어는 비트(FC1)로도, 레지스터(FC3)로도 물으므로 두 곳에 같은 값을 쓴다.
신호가 바뀌면 **최소 50 ms 는 그 상태를 유지**해야 보드가 놓치지 않는다 —
그래서 바뀔 값을 한 비트씩 순서대로 큐에 넣고, 전용 스레드가 하나씩 내보내며
사이마다 latch 시간을 지킨다.

무엇을 내보낼지는 로봇 레지스터 두 개로 정한다.
  290  진행 상태  — 0 대기 · 2 3점측정 · 3 원점복귀 · 5 완료 · 6 스캔 구간 ·
                    7 원점 대기 · 8 적심 · 9 오류 · 10~13 마킹 · 14 차량 고정 대기
  277  스캔 구간  — 0 없음 · 1 전진 스캔 · 2 줄 바꿈(상승) · 3 후진 스캔
  500  태스크 상태 — 2 일시 중지 / 3 중지됨 이면 스캔 중이어도 동결

  격자 준비(홈·프로브·원점 복귀)와 격자 사이(완료 뒤 리프트·차량 이동) : 리셋 (0, 0, 1)
  원점 도착·적심 (스캔 직전)                                        : 준비 (0, 0, 0)
  전진 스캔 줄                                                      : (1, 1, 0)
  줄 바꿈 상승 · 줄 끝                                              : 동결 (방향 유지, 0, 0)
  후진 스캔 줄                                                      : (0, 1, 0)
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, replace

#: Coil 16~18 = DO[0..2]
COIL_BASE = 16
#: Register 1 의 bit 0~2 = DO[0..2]
STATUS_REGISTER = 1
#: 신호 하나를 붙잡아 두는 최소 시간 [ms]
LATCH_MS = 50

# 로봇 레지스터 277 — 스캔 구간 (robot_task dus_pass_r/l · dus_up · dus_finish)
SEG_NONE, SEG_FORWARD, SEG_INDEX, SEG_BACKWARD = 0, 1, 2, 3

# 로봇 레지스터 290 — 진행 상태
STATE_SCAN = 6
READY_STATES = frozenset({7, 8})                       # 원점 도착 · 적심
RESET_STATES = frozenset({0, 2, 3, 5, 10, 11, 12, 13})  # 준비 · 격자 끝 · 마킹
HOLD_STATES = frozenset({14})                          # 차량 고정 대기 — 그대로 둔다
# 500 — 태스크가 멈춰 있으면 스캔 구간이어도 동결한다(0 은 모름 -> 막지 않는다)
TASK_PAUSED_OR_STOPPED = frozenset({2, 3})


@dataclass(frozen=True)
class Outputs:
    """DO[0] 방향, DO[1] 스캔, DO[2] 리셋."""

    direction: int = 0
    scan: int = 0
    reset: int = 1

    @property
    def bits(self) -> list[int]:
        return [self.direction, self.scan, self.reset]

    @property
    def word(self) -> int:
        return self.direction | (self.scan << 1) | (self.reset << 2)

    def describe(self) -> str:
        return (f"방향 {'Forward' if self.direction else 'Backward'} · "
                f"스캔 {'유효' if self.scan else '동결'} · "
                f"리셋 {'1' if self.reset else '0'}")


#: 브리지를 켰을 때 — 로봇 상태를 모르므로 카운터를 묶어 둔다.
INITIAL = Outputs(direction=0, scan=0, reset=1)


def target_outputs(current: Outputs, state: int, segment: int,
                   task_state: int = 0) -> Outputs:
    """로봇 상태로 지금 내보내야 할 신호를 정한다."""
    if state == STATE_SCAN:
        if task_state in TASK_PAUSED_OR_STOPPED:
            return replace(current, scan=0)
        if segment == SEG_FORWARD:
            return Outputs(direction=1, scan=1, reset=0)
        if segment == SEG_BACKWARD:
            return Outputs(direction=0, scan=1, reset=0)
        return Outputs(direction=current.direction, scan=0, reset=0)
    if state in READY_STATES:
        return Outputs(direction=0, scan=0, reset=0)
    if state in RESET_STATES:
        return Outputs(direction=0, scan=0, reset=1)
    if state in HOLD_STATES:
        return current
    # 오류(9)·알 수 없는 값 — 데이터는 지우지 않고 동결만 한다.
    return replace(current, scan=0)


def plan_steps(current: Outputs, target: Outputs, new_forward_line: bool = False) -> list[Outputs]:
    """현재 -> 목표를 **한 비트씩** 바꾸는 순서.

    스캔 끄기가 먼저, 켜기가 마지막이다 — 방향·리셋이 바뀌는 동안 펄스가
    나가면 안 된다. 새 전진 줄인데 방향이 이미 1 이면 0 을 한 번 거쳐
    0 -> 1 전환(라인 리셋)을 만든다.
    """
    steps: list[Outputs] = []
    s = current
    if s.scan and not target.scan:
        s = replace(s, scan=0)
        steps.append(s)
    if s.reset != target.reset:
        s = replace(s, reset=target.reset)
        steps.append(s)
    if s.direction != target.direction:
        s = replace(s, direction=target.direction)
        steps.append(s)
    elif new_forward_line and target.direction == 1 and target.scan and not s.scan:
        s = replace(s, direction=0)
        steps.append(s)
        s = replace(s, direction=1)
        steps.append(s)
    if target.scan and not s.scan:
        s = replace(s, scan=1)
        steps.append(s)
    return steps


class ScanSignalOutput:
    """로봇 상태를 받아 DO 신호를 latch 간격으로 내보낸다.

    write(outputs) 는 실제로 Coil·Register 에 쓰는 함수다(서버가 넘겨준다).
    on_change(outputs, reason) 는 화면·로그용 알림이다(스레드에서 불린다).
    """

    def __init__(self, write, latch_ms: int = LATCH_MS, on_change=None) -> None:
        self._write = write
        self.latch_s = max(0, int(latch_ms)) / 1000.0
        self._on_change = on_change or (lambda outputs, reason: None)
        self._queue: deque[tuple[Outputs, str]] = deque()
        self._cond = threading.Condition()
        self._current = INITIAL          # 실제로 내보낸 값
        self._planned = INITIAL          # 큐 끝까지 반영한 값
        self._last_segment = SEG_NONE
        self._last_change = 0.0
        self._thread: threading.Thread | None = None
        self._running = False
        self.changes = 0

    @property
    def current(self) -> Outputs:
        return self._current

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._current = self._planned = INITIAL
        self._write(INITIAL)
        self._last_change = time.monotonic()
        self._on_change(INITIAL, "시작 — 리셋")
        self._thread = threading.Thread(target=self._run, name="tpac-do", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._running = False
            self._queue.clear()
            self._cond.notify_all()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def observe(self, state: int, segment: int, task_state: int = 0) -> None:
        """폴링한 로봇 값 한 벌. 바뀐 게 있으면 순서대로 큐에 넣는다."""
        with self._cond:
            new_forward = segment == SEG_FORWARD and self._last_segment != SEG_FORWARD
            self._last_segment = segment
            target = target_outputs(self._planned, int(state), int(segment), int(task_state))
            steps = plan_steps(self._planned, target, new_forward_line=new_forward)
            if not steps:
                return
            reason = f"로봇 상태 {state} · 구간 {segment}"
            for step in steps:
                self._queue.append((step, reason))
            self._planned = steps[-1]
            self._cond.notify_all()

    # ------------------------------------------------------------------
    def _run(self) -> None:
        while True:
            with self._cond:
                while self._running and not self._queue:
                    self._cond.wait(0.5)
                if not self._running:
                    return
                outputs, reason = self._queue.popleft()
            # 앞 신호가 latch 시간만큼 유지된 뒤에 바꾼다.
            wait = self.latch_s - (time.monotonic() - self._last_change)
            if wait > 0:
                time.sleep(wait)
            self._write(outputs)
            self._current = outputs
            self._last_change = time.monotonic()
            self.changes += 1
            self._on_change(outputs, reason)
