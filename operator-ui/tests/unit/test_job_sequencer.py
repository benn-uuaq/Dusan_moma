"""격자 순회 상태 머신 테스트.

리프트/AMR 도착과 로봇 스캔 완료를 직접 흘려 넣어 `1A`부터 마지막 셀까지
순회가 끝나는지, 각 셀의 상태가 순서대로 나오는지를 본다.
"""

import pytest

from smr_operator_ui.services import (
    CellStatus,
    GridPlan,
    JobSequencer,
    SequencerState,
    cell_label,
)

# 로봇 태스크가 한 셀을 끝냈을 때 레지스터 290~298에 남기는 값.
SCAN_DONE = [5, 0, 6, 12, 1, 1, 130, 0, 100]
SCAN_RUNNING = [4, 2, 6, 11, 1, 0, 130, 0, 40]


def _plan(columns: int = 2, rows: int = 3) -> GridPlan:
    return GridPlan(
        column_count=columns, row_count=rows,
        cell_width=600.0, cell_height=800.0,
        scan_overlap=10.0, pitch_x=5.0, pitch_y=20.0,
    )


class Harness:
    """시퀀서를 붙잡고 더미 장비 역할을 대신해 주는 도우미."""

    def __init__(self, plan: GridPlan) -> None:
        self.seq = JobSequencer()
        self.plan = plan
        self.cells: list[str] = []
        self.statuses: list[tuple[str, str]] = []
        self.lift_targets: list[float] = []
        self.amr_targets: list[int] = []
        self.work_areas: list[tuple] = []
        self.plays = 0
        self.done = False

        self.seq.cell_changed.connect(lambda c, r, label, n: self.cells.append(label))
        self.seq.cell_status_changed.connect(
            lambda cell, state: self.statuses.append((cell, state))
        )
        self.seq.lift_target_requested.connect(self.lift_targets.append)
        self.seq.amr_move_requested.connect(self.amr_targets.append)
        self.seq.work_area_requested.connect(
            lambda *values: self.work_areas.append(values)
        )
        self.seq.robot_start_requested.connect(self._on_play)
        self.stops = 0
        self.seq.robot_stop_requested.connect(self._on_stop)
        self.seq.job_complete.connect(self._on_done)
        self.secures = 0
        self.retracts = 0
        self.seq.secure_requested.connect(self._on_secure)
        self.seq.retract_requested.connect(self._on_retract)

    def _on_secure(self) -> None:
        self.secures += 1

    def _on_retract(self) -> None:
        self.retracts += 1

    def _on_play(self) -> None:
        self.plays += 1

    def _on_stop(self) -> None:
        self.stops += 1

    def _on_done(self) -> None:
        self.done = True

    def start(self) -> None:
        self.seq.start(self.plan, scan_h_mm=150.0)

    def settle(self) -> None:
        """장비 동작(고정/리프트/AMR/복귀)이 끝났다고 계속 답해 준다."""
        for _ in range(20):
            state = self.seq.state
            if state is SequencerState.MOVING_AMR:
                self.seq.amr_arrived()
            elif state is SequencerState.SECURING:
                self.seq.secured()
            elif state in (SequencerState.LEVELING, SequencerState.MOVING_LIFT):
                self.seq.lift_arrived()
            elif state is SequencerState.RETRACTING:
                self.seq.retracted()
            else:
                return
        raise AssertionError("이동 상태에서 빠져나오지 못했습니다.")

    def finish_cell(self) -> None:
        """지금 셀의 스캔을 끝내고 다음 셀 이동까지 마친다.

        실제 로봇처럼 "동작 중"을 한 번 거친 뒤 "완료"를 보낸다. 시퀀서는
        이 변화를 보고 완료를 인정한다.
        """
        assert self.seq.state is SequencerState.SCANNING
        self.seq.handle_scan_state(SCAN_RUNNING)
        self.seq.handle_scan_state(SCAN_DONE)
        self.settle()


def test_cell_label_uses_column_number_and_row_letter() -> None:
    assert cell_label(0, 0) == "1A"
    assert cell_label(0, 1) == "1B"
    assert cell_label(11, 5) == "12F"


def test_full_traversal_visits_every_cell_in_order(qtbot) -> None:
    """1A → 1B → 1C → 2A → 2B → 2C 순으로 전부 돈다.

    같은 열 안에서는 리프트만 올리고, 열이 바뀔 때만 AMR이 움직인다.
    """
    h = Harness(_plan(columns=2, rows=3))
    h.start()
    h.settle()
    for _ in range(h.plan.total_cells):
        h.finish_cell()

    assert h.cells == ["1A", "1B", "1C", "2A", "2B", "2C"]
    assert h.done is True
    assert h.seq.state is SequencerState.DONE
    # 차량은 이미 1구역에 서 있으므로 2열로 넘어갈 때 한 번만 움직인다.
    assert h.amr_targets == [2]
    # 셀마다 로봇을 제로점에서 다시 play한다.
    assert h.plays == 6


def test_work_area_is_sent_once_for_whole_job(qtbot) -> None:
    """셀 치수는 작업 시작 때 한 번만 로봇에 쓴다.

    리프트가 올라가면 로봇 기준 좌표계도 함께 올라가므로, 모든 셀에서
    로봇 동작이 같다. 셀마다 다시 쓸 필요가 없다.
    """
    h = Harness(_plan(columns=2, rows=3))
    h.start()
    h.settle()
    for _ in range(h.plan.total_cells):
        h.finish_cell()

    # 로봇에 가는 겹침(레지스터 259)은 **격자끼리**의 세로 겹침(pitch_y)이다.
    # 격자 안 ㄹ자 줄 겹침(scan_overlap=10)이 아니다 — 그건 로봇이 프로브
    # 커버로 스스로 정한다.
    # 뒤 두 개는 호 계산용 반지름·두께 (여기서는 안 줬으므로 0 = 평면).
    # 마지막 값은 EOAT 종류(레지스터 264). 안 고르면 0 이다.
    assert h.work_areas == [(600.0, 800.0, 150.0, 20.0, 0.0, 0.0, 0.0, 0.0, 0.0)]


def test_arc_geometry_reaches_the_robot(qtbot) -> None:
    """반지름·두께는 로봇이 호를 직접 계산하는 데 필요하다.

    셀 가로는 **호 길이**로 넘어간다(현이 아니다). 로봇이 반지름과 함께
    현을 계산하므로, 이 두 값이 빠지면 호를 못 그리고 직선으로 훑는다.
    """
    plan = GridPlan(column_count=1, row_count=1,
                    cell_width=1214.0, cell_height=500.0,
                    scan_overlap=20.0, pitch_y=30.0,
                    radius=834.6, thickness=10.0)
    h = Harness(plan)
    h.start()
    h.settle()

    # 네 번째 값은 격자간 세로 겹침(pitch_y=30)이다. scan_overlap(20)이 아니다.
    assert h.work_areas == [
        (1214.0, 500.0, 150.0, 30.0, 834.6, 10.0, 0.0, 0.0, 0.0)]


def test_eoat_size_reaches_the_robot(qtbot) -> None:
    """프로브 유효 커버가 로봇까지 가야 줄 간격을 정할 수 있다.

    값은 몸통도 프로브 뭉치 전체 크기도 아니고 **한 번 지날 때 실제로
    검사되는 범위**다. 세로값이 곧 ㄹ자 up 동작의 최대 상승량이 된다.
      5축 십자 : 가운데 프로브 센서 지름 30 x 30
      8축 직사 : 좌우합 75, 상하합 167.5
    """
    for probes, (w, h) in ((5, (30.0, 30.0)), (8, (75.0, 167.5))):
        plan = GridPlan(column_count=1, row_count=1,
                        cell_width=1110.0, cell_height=500.0,
                        scan_overlap=20.0, radius=834.6, thickness=10.0,
                        eoat_probes=probes)
        assert plan.eoat_size == (w, h)
        seq = Harness(plan)
        seq.start()
        seq.settle()
        # 가로, 세로에 이어 종류(레지스터 264)까지 간다.
        assert seq.work_areas[0][6:] == (w, h, float(probes))


def test_unknown_eoat_falls_back_to_no_tool(qtbot) -> None:
    """모르는 EOAT 종류면 0 을 보내 로봇이 격자 전체를 훑게 둔다."""
    plan = GridPlan(column_count=1, row_count=1,
                    cell_width=1110.0, cell_height=500.0, eoat_probes=99)
    assert plan.eoat_size == (0.0, 0.0)


def test_lift_rises_by_cell_height_minus_overlap(qtbot) -> None:
    """행이 하나 올라갈 때마다 리프트는 (셀 높이 - 겹침)만큼 오른다."""
    h = Harness(_plan(columns=2, rows=3))
    h.start()
    h.settle()
    for _ in range(h.plan.total_cells):
        h.finish_cell()

    # 리프트는 area 사이 세로 겹침(pitch_y)만큼만 덜 오른다.
    pitch = 800.0 - 20.0
    # 열마다 0, 1, 2행 높이를 거치고 다음 열에서 다시 0행으로 내려온다.
    assert h.lift_targets == [0.0, pitch, 2 * pitch, 0.0, pitch, 2 * pitch]
    # 고정과 안전 위치는 열마다 한 번씩이다 (2열 → 2번).
    assert (h.secures, h.retracts) == (2, 2)


def test_cell_status_goes_waiting_executing_completed(qtbot) -> None:
    """각 셀은 작업대기 → 작업중 → 작업완료 순서로 상태를 낸다."""
    h = Harness(_plan(columns=1, rows=2))
    h.start()
    h.settle()
    h.finish_cell()

    assert h.statuses[:3] == [
        ("1A", CellStatus.WAITING.value),
        ("1A", CellStatus.EXECUTING.value),
        ("1A", CellStatus.COMPLETED.value),
    ]
    # 다음 셀은 곧바로 대기 상태로 넘어간다.
    assert h.statuses[3] == ("1B", CellStatus.WAITING.value)


def test_completion_signal_is_handled_only_once_per_cell(qtbot) -> None:
    """완료 신호는 계속 들어오지만 셀은 한 번만 넘어가야 한다.

    로봇은 다음 셀을 시작할 때까지 state=5를 계속 쓰고 있으므로, 빗장이
    없으면 한 번의 완료로 여러 셀이 우르르 넘어간다.
    """
    h = Harness(_plan(columns=1, rows=3))
    h.start()
    h.settle()
    h.seq.handle_scan_state(SCAN_RUNNING)
    for _ in range(5):
        h.seq.handle_scan_state(SCAN_DONE)
    h.settle()

    assert h.cells == ["1A", "1B"]
    assert h.done is False


def test_scan_in_progress_does_not_advance(qtbot) -> None:
    """스캔 중(state=4)에는 셀이 넘어가지 않는다."""
    h = Harness(_plan(columns=1, rows=2))
    h.start()
    h.settle()
    h.seq.handle_scan_state(SCAN_RUNNING)

    assert h.seq.state is SequencerState.SCANNING
    assert h.cells == ["1A"]


def test_pause_blocks_advance_and_resume_restarts_scan(qtbot) -> None:
    """일시정지 중에는 완료 신호를 받아도 넘어가지 않는다.

    재개하면 로봇은 제로점에서 다시 play되어야 한다.
    """
    h = Harness(_plan(columns=1, rows=2))
    h.start()
    h.settle()
    plays_before = h.plays

    h.seq.handle_scan_state(SCAN_RUNNING)
    h.seq.pause()
    assert h.seq.state is SequencerState.PAUSED
    h.seq.handle_scan_state(SCAN_DONE)
    assert h.cells == ["1A"]

    h.seq.resume()
    assert h.seq.state is SequencerState.SCANNING
    assert h.plays == plays_before + 1


def test_pause_also_stops_the_robot(qtbot) -> None:
    """일시정지는 로봇 태스크까지 세워야 한다.

    순회만 멈추면 로봇은 자기 태스크를 계속 돌려 벽을 훑는다. 장애가
    차량·리프트·배터리 쪽에서 나도 마찬가지이므로 멈춤은 로봇까지 내려가야 한다.
    """
    h = Harness(_plan(columns=1, rows=2))
    h.start()
    h.settle()
    h.seq.handle_scan_state(SCAN_RUNNING)
    stops_before = h.stops

    h.seq.pause()
    assert h.stops == stops_before + 1, "일시정지가 로봇까지 내려가지 않았다"


def test_stop_also_stops_the_robot(qtbot) -> None:
    """작업 정지도 로봇 태스크를 세워야 한다."""
    h = Harness(_plan(columns=1, rows=2))
    h.start()
    h.settle()
    stops_before = h.stops

    h.seq.stop()
    assert h.stops == stops_before + 1, "정지가 로봇까지 내려가지 않았다"


def test_stop_halts_traversal(qtbot) -> None:
    h = Harness(_plan(columns=2, rows=2))
    h.start()
    h.settle()
    h.seq.handle_scan_state(SCAN_RUNNING)
    h.seq.stop()

    assert h.seq.state is SequencerState.STOPPED
    h.seq.handle_scan_state(SCAN_DONE)
    assert h.cells == ["1A"]


def test_short_scan_state_payload_is_ignored(qtbot) -> None:
    """레지스터를 다 못 읽었을 때 잘린 값으로 완료 판정하지 않는다."""
    h = Harness(_plan(columns=1, rows=2))
    h.start()
    h.settle()
    h.seq.handle_scan_state(SCAN_RUNNING)
    h.seq.handle_scan_state([5, 0, 6])

    assert h.seq.state is SequencerState.SCANNING
    assert h.cells == ["1A"]


def test_lift_pitch_stays_positive_when_overlap_exceeds_cell(qtbot) -> None:
    """겹침이 셀 높이보다 크면 리프트가 안 올라가 같은 자리를 맴돈다."""
    plan = GridPlan(
        column_count=1, row_count=2,
        cell_width=600.0, cell_height=100.0, pitch_y=200.0,
    )
    assert plan.lift_pitch > 0


def test_lift_pitch_uses_the_cell_to_cell_overlap(qtbot) -> None:
    """격자끼리의 겹침(pitch_y)은 리프트가 덜 올라가는 양이다.

    로봇에 넘기는 겹침(레지스터 259)과 같은 값이어야 격자 안 스캔과
    격자끼리의 겹침이 어긋나지 않는다.
    """
    plan = GridPlan(column_count=1, row_count=2,
                    cell_width=600.0, cell_height=800.0,
                    scan_overlap=10.0, pitch_y=20.0)
    assert plan.lift_pitch == 780.0

    h = Harness(plan)
    h.start()
    h.settle()
    assert h.work_areas[0][3] == plan.pitch_y


def test_erut_section_aligns_vehicle_then_lift_then_robot(qtbot) -> None:
    """ERUT 구간 하나의 물리 순서: 차량 정렬 -> 고정 -> 리프트 정렬 -> 로봇.

    prepare 가 오면 이 순서로 준비하고, 로봇은 프로브 3점을 잡은 뒤 원점에
    서서 start 를 기다린다. 차량보다 로봇이 먼저 움직이면 벽에 닿는다.
    """
    plan = GridPlan(column_count=1, row_count=1,
                    cell_width=721.0, cell_height=500.0,
                    origin_x=2000.0, origin_y=1000.0)
    h = Harness(plan)
    order: list[str] = []
    h.seq.amr_move_requested.connect(lambda _c: order.append("차량"))
    h.seq.secure_requested.connect(lambda: order.append("고정"))
    h.seq.lift_target_requested.connect(lambda mm: order.append(f"리프트 {mm:.0f}"))
    h.seq.robot_start_requested.connect(lambda: order.append("로봇"))

    h.seq.start(plan, 30.0, base_lift_mm=plan.origin_y, move_first=True)
    h.seq.amr_arrived()
    h.seq.secured()
    h.seq.lift_arrived()

    assert order == ["차량", "고정", "리프트 1000", "로봇"]


def test_mc_job_keeps_the_vehicle_where_it_is(qtbot) -> None:
    """사내 MC 작업은 차량이 이미 1구역에 서 있다 — 처음에 옮기지 않는다."""
    h = Harness(_plan(columns=2, rows=1))
    h.seq.start(h.plan, 150.0)

    assert h.amr_targets == []
    assert h.secures == 1
