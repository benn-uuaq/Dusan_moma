"""실장비 없이 UI를 개발하기 위한 비동기 검사 시뮬레이터."""

from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from smr_operator_ui.state import AppSnapshot, CyclePhase, initial_snapshot


class InspectionSimulator(QObject):
    """명확한 검사 생명주기를 가진 비동기 장비 시뮬레이터."""

    snapshot_changed = pyqtSignal(object)
    activity = pyqtSignal(str)

    _sequence = (
        CyclePhase.SECURING,
        CyclePhase.LEVELING,
        CyclePhase.INSPECTING,
        CyclePhase.RETRACTING,
        CyclePhase.MOVING,
    )

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.snapshot: AppSnapshot = initial_snapshot()
        self._phase_index = 0
        self._timer = QTimer(self)
        self._timer.setInterval(1200)
        self._timer.timeout.connect(self._advance)

    def start_cycle(self) -> None:
        """새 검사 사이클을 시작하거나 완료된 사이클을 다시 시작한다."""
        cycle = self.snapshot.cycle
        if cycle.running and not cycle.paused:
            return
        if cycle.phase is CyclePhase.COMPLETE:
            cycle = replace(cycle, current_segment=1, completed_segments=0)
        self._phase_index = 0
        cycle = replace(cycle, running=True, paused=False, phase=self._sequence[0], velocity_mps=0.0)
        self._publish(cycle, "원주 검사 사이클을 시작했습니다.")
        self._timer.start()

    def toggle_pause(self) -> None:
        """중단된 단계를 보존하면서 검사를 일시정지하거나 재개한다."""
        cycle = self.snapshot.cycle
        if not cycle.running:
            return
        if cycle.paused:
            cycle = replace(cycle, paused=False, phase=self._sequence[self._phase_index])
            self._timer.start()
            self._publish(cycle, "검사를 재개했습니다.")
            return
        self.pause_cycle()

    def pause_cycle(self) -> None:
        """실행 중인 검사를 일시정지하며 반복 요청은 상태를 바꾸지 않는다."""
        cycle = self.snapshot.cycle
        if not cycle.running or cycle.paused:
            return
        self._timer.stop()
        cycle = replace(
            cycle,
            paused=True,
            phase=CyclePhase.PAUSED,
            velocity_mps=0.0,
        )
        self._publish(cycle, "검사를 안전하게 일시정지했습니다.")

    def stop_cycle(self) -> None:
        """타이머 진행을 멈추고 안전한 대기 상태를 발행한다."""
        self._timer.stop()
        cycle = replace(self.snapshot.cycle, running=False, paused=False, phase=CyclePhase.IDLE, velocity_mps=0.0)
        self._publish(cycle, "사이클을 정지했습니다.")

    def _advance(self) -> None:
        """검사 단계를 한 번 진행하고 필요하면 다음 구간으로 이동한다."""
        cycle = self.snapshot.cycle
        if cycle.paused:
            return
        self._phase_index += 1
        if self._phase_index >= len(self._sequence):
            # 이동 단계가 끝나면 현재 구간이 완료된다. 다음 타이머 호출부터
            # 다음 원주 구간의 안전 순서를 처음부터 다시 수행한다.
            completed = cycle.completed_segments + 1
            if completed >= cycle.total_segments:
                self._timer.stop()
                cycle = replace(
                    cycle,
                    completed_segments=cycle.total_segments,
                    running=False,
                    phase=CyclePhase.COMPLETE,
                    velocity_mps=0.0,
                )
                self._publish(cycle, "SMR 원주 전체 검사를 완료했습니다.")
                return
            cycle = replace(cycle, completed_segments=completed, current_segment=completed + 1)
            self._phase_index = 0

        phase = self._sequence[self._phase_index]
        velocity = 0.15 if phase is CyclePhase.MOVING else 0.0
        cycle = replace(cycle, phase=phase, velocity_mps=velocity)
        self._publish(cycle, f"{cycle.current_segment:02d} 구간: {phase.value}")

    def _publish(self, cycle, message: str) -> None:
        """검사 상태를 한 번에 교체한 뒤 상태 및 활동 구독자에게 알린다."""
        self.snapshot = replace(self.snapshot, cycle=cycle)
        self.snapshot_changed.emit(self.snapshot)
        self.activity.emit(message)
