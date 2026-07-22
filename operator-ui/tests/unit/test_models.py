from dataclasses import replace

from smr_operator_ui.state import CyclePhase, CycleState


def test_cycle_progress_uses_completed_segments() -> None:
    state = replace(CycleState(), completed_segments=3, current_segment=4)
    assert state.progress_percent == 25


def test_new_cycle_starts_idle_and_safe() -> None:
    state = CycleState()
    assert state.phase is CyclePhase.IDLE
    assert state.safe is True
    assert state.velocity_mps == 0.0
