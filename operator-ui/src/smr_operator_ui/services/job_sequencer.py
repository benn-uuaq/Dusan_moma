"""격자(셀)를 하나씩 자동으로 순회하는 작업 시퀀서.

원통을 편 직사각형을 격자로 나눈다. 열(1~12)은 AMR이 정차하는 원주 구역,
행(A~F)은 리프트 높이다. 셀 하나(예: `1A`)가 Cobot이 ㄹ자로 훑는 영역이며
`1A → 1B → … → 1F → 2A → … → 12F` 순으로 진행한다.

리프트가 올라가면 로봇 기준 좌표계도 함께 올라가므로 **로봇 입장에서는
모든 셀이 같은 동작**이다. 그래서 셀마다 하는 일은 두 가지뿐이다.

1. 리프트를 한 칸 올린다(행이 바뀔 때) 또는 AMR을 옮긴다(열이 바뀔 때)
2. 제로점에서 로봇 프로그램을 play한다

셀 완료는 로봇 태스크가 레지스터에 쓰는 값으로 판정한다
(`state == 5 && finished == 1`). 자세한 모델은
`docs/grid_sequencer_design.md` 참고.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PyQt6.QtCore import QObject, pyqtSignal


class SequencerState(str, Enum):
    """시퀀서가 지금 무엇을 기다리고 있는지."""

    IDLE = "대기"
    SECURING = "정지·고정"          # 1. AMR 정차 후 아웃트리거 고정
    LEVELING = "수평 보정"          # 2. 열의 첫 리프트 위치잡기
    MOVING_LIFT = "리프트 이동 중"   # 3-중간. 셀과 셀 사이 리프트 상승
    SCANNING = "스캔 중"            # 3. ㄹ자 스캔
    RETRACTING = "안전 위치"        # 4. 열을 다 끝내고 물러남
    MOVING_AMR = "다음 구간 이동"    # 5. 다음 열로 AMR 이동
    PAUSED = "일시정지"
    DONE = "전체 완료"
    STOPPED = "정지"


class CellStatus(str, Enum):
    """셀 하나의 진행 상태. MQTT `job_state`로 외부에 알린다."""

    WAITING = "waiting"
    EXECUTING = "executing"
    COMPLETED = "completed"


#: 십자(5축) 프로브의 **가운데 프로브 센서 지름** [mm].
#: 지금은 십자에서 이 하나만 유효 스캔으로 친다 — 그래서 ㄹ자 up 동작의
#: 최대 상승량도 이 값이다. 500mm 샘플이면 17칸(18줄)이 된다.
CROSS_CENTRE_PROBE_MM = 30.0

#: 십자에서 **위·아래 프로브까지** 유효로 칠 때 쓸 값 [mm] — 도면의
#: 프로브 중심 간격 dY. 아직 안 쓴다. 위·아래를 포함하기로 하면
#: PROBE_COVERAGE_MM[5] 의 세로값을 이걸로 바꾸면 된다.
CROSS_OUTER_PROBE_SPAN_MM = 167.5

# 프로브 종류별 **유효 스캔 커버**(가로합 mm, 세로합 mm).
# 가로는 호 방향, 세로는 리프트 방향이다.
#
# 몸통 크기도, 프로브 뭉치 전체 크기도 아니다 — **한 번 지날 때 실제로
# 검사되는 범위**다. 좌우 끝과 상승 모두 **가운데 프로브 중심**을 기준으로
# 잡으므로(로봇 TCP 가 곧 그 중심), 이 값은 경로의 좌우 여백이 아니라
# 줄 간격(= up 상승량)의 상한을 정하는 데 쓴다.
#
#   5축 십자(+)  : 가운데 프로브 하나만 유효 -> 30 x 30
#   8축 직사각   : 프로브가 벽면을 빈틈없이 덮는 크기 -> 좌우합 75, 상하합 167.5
#
# 십자의 위·아래 프로브까지 유효로 치기로 하면 5번 항목의 **세로값만**
# CROSS_OUTER_PROBE_SPAN_MM 으로 바꾸면 된다. 좌·우 프로브까지 포함하면
# 가로값도 75.0(도면 dX)이 된다.
PROBE_COVERAGE_MM: dict[int, tuple[float, float]] = {
    5: (CROSS_CENTRE_PROBE_MM, CROSS_CENTRE_PROBE_MM),
    8: (75.0, 167.5),
}


@dataclass(frozen=True)
class GridPlan:
    """격자 분할 계획. 단위는 mm.

    **겹침이 세 종류라 섞으면 안 된다** (ERUT 규격 20260818 `scan` 블록).

    | 값 | 무엇의 겹침 | 쓰이는 곳 |
    |---|---|---|
    | `scan_overlap` | area **안**의 ㄹ자 줄 간격 | 로봇 레지스터 259 |
    | `pitch_y` | area **끼리**의 세로 겹침 | 리프트 상승량 |
    | `pitch_x` | area **끼리**의 가로 겹침 | AMR 이동 거리 |

    셀 크기(`cell_width`/`cell_height`)는 요청의 `area.end - area.start` 로
    정한다. `area` 가 곧 한 셀의 사각형이다.
    """

    column_count: int
    row_count: int
    cell_width: float
    cell_height: float
    scan_overlap: float = 0.0   # area 안 ㄹ자 줄 겹침 → 로봇으로 전달
    pitch_x: float = 0.0        # area 끼리 가로 겹침 → AMR 이동
    pitch_y: float = 0.0        # area 끼리 세로 겹침 → 리프트 상승
    # 로봇이 호를 직접 계산하는 데 쓴다 (레지스터 260/261).
    # cell_width 는 **호 길이**다 — 현이 아니다. 로봇이 반지름과 함께
    # 현을 계산한다. 반지름 0 이면 평면으로 보고 직선으로 훑는다.
    radius: float = 0.0         # 훑는 면의 반지름 [mm]
    thickness: float = 0.0      # 제품 두께 [mm] — UT 기록용
    # 검사장비(EOAT) 종류. 프로브 축 수로 고른다 (5 = 십자형, 8 = 직사각).
    # 0 이면 EOAT 를 안 쓰는 것으로 보고 로봇이 격자 전체를 훑는다.
    eoat_probes: int = 0
    # 이 구간의 원점 — 검사면 좌표 [mm] (ERUT area.start). 외주면에서
    # x 는 원주 전개 거리, y 는 높이다. 차량은 x, 리프트는 y 로 정렬한다.
    # 사내 MC job_cmd 처럼 격자를 우리가 도는 경우는 0 이다.
    origin_x: float = 0.0
    origin_y: float = 0.0

    @property
    def eoat_size(self) -> tuple[float, float]:
        """프로브 **유효 스캔 커버**의 (가로, 세로) [mm]. 모르는 종류면 (0, 0).

        가로는 호 방향, 세로는 리프트 방향이다. 세로값이 곧 ㄹ자 up 동작의
        **최대 상승량**이다 — 자세한 건 PROBE_COVERAGE_MM 주석 참고.
        """
        return PROBE_COVERAGE_MM.get(int(self.eoat_probes), (0.0, 0.0))

    @property
    def total_cells(self) -> int:
        return self.column_count * self.row_count

    @property
    def lift_pitch(self) -> float:
        """행 하나를 올라갈 때 리프트가 상승하는 양.

        셀 높이에서 **area 사이 세로 겹침**(`pitch_y`)을 뺀 값이다.
        ㄹ자 줄 겹침(`scan_overlap`)이 아니다 — 그건 로봇이 area 안에서 쓴다.
        겹침이 셀 높이 이상이면 리프트가 안 올라가 같은 자리를 맴돌므로
        최소값을 둔다.
        """
        return max(self.cell_height - self.pitch_y, 1.0)

    @property
    def column_pitch(self) -> float:
        """열 하나를 넘어갈 때 AMR 이 이동하는 거리."""
        return max(self.cell_width - self.pitch_x, 1.0)


def cell_label(column_index: int, row_index: int) -> str:
    """0부터 세는 좌표를 `1A`, `12F` 같은 셀 이름으로 바꾼다.

    열은 1부터 세는 숫자, 행은 A부터 세는 문자다. 행이 26개를 넘으면
    문자가 바닥나므로 그때는 숫자를 그대로 쓴다(A0 형태가 아니라 1-27).
    """
    if row_index < 26:
        return f"{column_index + 1}{chr(ord('A') + row_index)}"
    return f"{column_index + 1}-{row_index + 1}"


class JobSequencer(QObject):
    """셀 순회 상태 머신. 지금 어느 셀인지에 대한 유일한 소유자다."""

    # (열 0-base, 행 0-base, 셀 이름, 전체 셀 중 몇 번째인지 1-base)
    cell_changed = pyqtSignal(int, int, str, int)
    # 셀 이름과 상태. MQTT job_state 발행에 그대로 쓴다.
    cell_status_changed = pyqtSignal(str, str)
    # 리프트를 이 높이(mm)로 옮겨 달라는 요청. 더미 어댑터가 받는다.
    lift_target_requested = pyqtSignal(float)
    # AMR을 이 열(1-base)로 옮겨 달라는 요청. 더미 어댑터가 받는다.
    amr_move_requested = pyqtSignal(int)
    # 로봇 프로그램을 제로점에서 play해 달라는 요청.
    robot_start_requested = pyqtSignal()
    # 로봇 프로그램을 멈춰 달라는 요청.
    #
    # 순회를 멈추는 것만으로는 로봇이 서지 않는다 — 로봇은 자기 태스크를
    # 계속 돌린다. 차량·리프트·배터리 쪽 장애로 작업을 세울 때도 로봇 팔은
    # 그대로 벽을 훑고 있게 되므로, 멈춤은 반드시 로봇까지 내려가야 한다.
    robot_stop_requested = pyqtSignal()
    # 아웃트리거로 차량을 고정해 달라는 요청. 더미 어댑터가 받는다.
    secure_requested = pyqtSignal()
    # 로봇을 안전 위치로 물리라는 요청. 더미 어댑터가 받는다.
    retract_requested = pyqtSignal()
    # 작업 영역(셀 치수)을 로봇에 써 달라는 요청. 작업 시작 때 한 번만 나간다.
    # 셀 치수 + 호 정보. 순서는 레지스터 256~264 와 같다:
    #   호 길이, 격자 높이, 스캐너 높이, 겹침, 반지름, 두께,
    #   EOAT 가로, EOAT 세로, EOAT 종류(0/5/8)
    work_area_requested = pyqtSignal(float, float, float, float, float, float,
                                     float, float, float)
    job_complete = pyqtSignal()
    activity = pyqtSignal(str)
    # 시퀀서 상태가 바뀔 때마다 이름을 보낸다. 화면의 진행 단계 표시가
    # 자체 타이머가 아니라 실제 진행을 따라가도록 하기 위한 것이다.
    state_changed = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._plan: GridPlan | None = None
        self._scan_h_mm = 150.0
        self._base_lift_mm = 0.0
        self._column = 0
        self._row = 0
        self._state = SequencerState.IDLE
        self._resume_state = SequencerState.IDLE
        # 로봇은 다음 셀을 시작할 때까지 완료 상태(state=5, finished=1)를
        # 계속 들고 있다. play 직후에도 직전 셀의 완료 상태가 그대로 남아
        # 있으므로, **로봇이 완료 아닌 상태를 한 번 거친 뒤**에야 완료로
        # 인정한다. 시간이 아니라 상태 변화로 판정하므로 로봇이 느리게
        # 출발해도 안전하다.
        self._saw_robot_busy = False

    def _set_state(self, state: SequencerState) -> None:
        """상태를 바꾸고 알린다. 상태 변경은 반드시 여기를 거친다."""
        if self._state is state:
            return
        self._state = state
        self.state_changed.emit(state.name)

    # ------------------------------------------------------------- 조회
    @property
    def state(self) -> SequencerState:
        return self._state

    @property
    def plan(self) -> GridPlan | None:
        return self._plan

    def current_cell(self) -> str:
        """지금 셀 이름. 계획이 없으면 빈 문자열."""
        if self._plan is None:
            return ""
        return cell_label(self._column, self._row)

    def cell_ordinal(self) -> int:
        """전체 셀 중 몇 번째인지(1-base). 계획이 없으면 0."""
        if self._plan is None:
            return 0
        return self._column * self._plan.row_count + self._row + 1

    # ------------------------------------------------------------- 입력
    def start(self, plan: GridPlan, scan_h_mm: float, base_lift_mm: float = 0.0,
              move_first: bool = False) -> None:
        """작업 계획을 받아 첫 셀(1A)부터 순회를 시작한다.

        `move_first` 면 **차량 정렬부터** 한다. ERUT 는 구간마다 따로
        요청하므로(구간 하나 = job 하나) 매번 그 구간 자리로 차량을
        옮겨야 한다. 사내 MC 처럼 차량이 이미 1구역에 서 있는 경우는 끈다.
        """
        self._plan = plan
        self._scan_h_mm = scan_h_mm
        self._base_lift_mm = base_lift_mm
        self._column = 0
        self._row = 0
        self._saw_robot_busy = False
        self.activity.emit(
            f"작업 시작: {plan.column_count}열 × {plan.row_count}행 "
            f"= {plan.total_cells}개 셀"
        )
        # 셀 치수는 작업 내내 같다. 리프트가 올라가도 로봇 좌표계가 함께
        # 올라가므로 셀마다 다시 쓸 필요가 없다.
        # 로봇에게 주는 겹침(레지스터 259)은 **격자끼리**의 세로 겹침이다.
        # 격자 안 ㄹ자 줄 겹침이 아니다 — 그건 로봇이 프로브 커버로 스스로
        # 정한다(dus_init.script). 리프트가 다음 격자로 올라갈 때 이만큼
        # 덜 올라가고(lift_pitch), 그래서 격자끼리 겹친다.
        eoat_w, eoat_h = plan.eoat_size
        self.work_area_requested.emit(
            plan.cell_width, plan.cell_height, scan_h_mm, plan.pitch_y,
            plan.radius, plan.thickness, eoat_w, eoat_h,
            float(plan.eoat_probes),
        )
        if move_first:
            # 구간 자리로 차량부터 옮긴다. 도착하면(amr_arrived) 고정 ->
            # 수평 보정(리프트 정렬) -> 검사 순서로 이어진다.
            self._set_state(SequencerState.MOVING_AMR)
            self.activity.emit("구간 자리로 차량을 정렬합니다.")
            self.amr_move_requested.emit(1)
            return
        # 차량은 이미 1구역 중앙에 서 있다. 이동(5단계)이 아니라 고정(1단계)
        # 부터 시작해야 화면이 1 → 2 → 3 → 4 → 5 순서로 흐른다.
        self._begin_column()

    def pause(self) -> None:
        """진행 중인 순회를 멈춘다. 재개하면 하던 자리에서 이어간다."""
        if self._state in (SequencerState.IDLE, SequencerState.PAUSED,
                           SequencerState.DONE, SequencerState.STOPPED):
            return
        self._resume_state = self._state
        self._set_state(SequencerState.PAUSED)
        # 로봇도 같이 세운다. 재개하면 resume() 이 제로점부터 다시 play 한다.
        self.robot_stop_requested.emit()
        self.activity.emit(f"{self.current_cell()} 에서 일시정지했습니다.")

    def resume(self) -> None:
        """일시정지한 자리에서 다시 진행한다."""
        if self._state is not SequencerState.PAUSED:
            return
        self._set_state(self._resume_state)
        self.activity.emit(f"{self.current_cell()} 에서 재개했습니다.")
        # 스캔 도중 멈췄으면 로봇은 제로점에서 다시 시작해야 한다.
        if self._state is SequencerState.SCANNING:
            self._start_scan()

    def stop(self) -> None:
        """순회를 완전히 중단한다."""
        if self._plan is None:
            return
        self._set_state(SequencerState.STOPPED)
        self.robot_stop_requested.emit()
        self.activity.emit("작업을 정지했습니다.")

    def lift_arrived(self) -> None:
        """리프트가 목표 높이에 도착했다는 신호. 수평 보정이든 셀 사이 이동이든 스캔으로 간다."""
        if self._state not in (SequencerState.LEVELING, SequencerState.MOVING_LIFT):
            return
        self._start_scan()

    def amr_arrived(self) -> None:
        """AMR이 다음 열에 도착했다는 신호. 그 열의 1단계부터 다시 시작한다."""
        if self._state is not SequencerState.MOVING_AMR:
            return
        self._begin_column()

    def secured(self) -> None:
        """아웃트리거 고정이 끝났다(1단계 완료). 수평 보정으로 넘어간다."""
        if self._state is not SequencerState.SECURING:
            return
        self._request_lift(SequencerState.LEVELING)

    def retracted(self) -> None:
        """안전 위치 복귀가 끝났다(4단계 완료). 다음 열로 넘어간다."""
        if self._state is not SequencerState.RETRACTING:
            return
        plan = self._plan
        if plan is not None and self._column + 1 < plan.column_count:
            self._column += 1
            self._row = 0
            self._set_state(SequencerState.MOVING_AMR)
            self.activity.emit(f"{self._column + 1}구역으로 이동합니다.")
            self.amr_move_requested.emit(self._column + 1)
            return
        self._set_state(SequencerState.DONE)
        self.activity.emit("전체 격자 스캔을 완료했습니다.")
        self.job_complete.emit()

    def handle_scan_state(self, values: list[int]) -> None:
        """로봇의 스캔 진행 상태(레지스터 290~298)를 받아 완료를 판정한다.

        완료 상태는 다음 셀을 시작할 때까지 계속 들어오고, play 직후에도
        직전 셀의 완료 상태가 남아 있다. 그래서 **로봇이 완료 아닌 상태를
        한 번 거친 뒤 다시 완료가 되는 변화**를 잡는다. 이렇게 하면 한 번의
        완료로 여러 셀이 우르르 넘어가는 일도, 로봇이 늦게 출발해 완료를
        놓치는 일도 없다.
        """
        if self._state is not SequencerState.SCANNING:
            return
        if len(values) <= _FINISHED_INDEX:
            # 레지스터를 다 못 읽었다. 잘린 값으로 판정하지 않는다.
            return

        done = (
            values[_STATE_INDEX] == _STATE_DONE
            and values[_FINISHED_INDEX] == 1
        )
        if not done:
            # 로봇이 실제로 움직이기 시작했다. 이제부터 완료를 인정한다.
            self._saw_robot_busy = True
            return
        if self._saw_robot_busy:
            self._saw_robot_busy = False
            self._complete_cell()

    # ------------------------------------------------------------- 내부
    def _begin_column(self) -> None:
        """열 하나의 안전 순서를 1단계(정지·고정)부터 시작한다.

        5단계는 **구간(열)마다** 반복된다. 열 안에서 리프트로 셀을 옮기는
        것은 3단계(Cobot 검사) 안에서 일어나므로 단계 표시가 뒤로 가지 않는다.
        """
        self._announce_cell()
        self._set_state(SequencerState.SECURING)
        self.activity.emit(f"{self._column + 1}구역: 차량을 고정합니다.")
        self.secure_requested.emit()

    def _announce_cell(self) -> None:
        """지금 셀을 화면과 외부에 알린다."""
        label = self.current_cell()
        self.cell_changed.emit(self._column, self._row, label, self.cell_ordinal())
        self.cell_status_changed.emit(label, CellStatus.WAITING.value)

    def _request_lift(self, state: SequencerState) -> None:
        """이번 행 높이로 리프트를 옮겨 달라고 요청한다.

        열의 첫 행이면 수평 보정(2단계), 그 뒤 행이면 검사 중의 셀 이동이다.
        """
        plan = self._plan
        if plan is None:
            return
        self._set_state(state)
        height = self._base_lift_mm + self._row * plan.lift_pitch
        self.activity.emit(
            f"{self.current_cell()}: 리프트를 {height:.0f} mm 로 올립니다."
        )
        self.lift_target_requested.emit(height)

    def _start_scan(self) -> None:
        """로봇을 제로점에서 play해 이번 셀을 스캔시킨다."""
        label = self.current_cell()
        self._set_state(SequencerState.SCANNING)
        # 로봇이 아직 직전 셀의 완료 상태를 들고 있을 수 있다. 움직이는 걸
        # 한 번 본 뒤에야 완료를 인정한다.
        self._saw_robot_busy = False
        self.cell_status_changed.emit(label, CellStatus.EXECUTING.value)
        self.activity.emit(f"{label}: 로봇 스캔을 시작합니다.")
        self.robot_start_requested.emit()

    def _complete_cell(self) -> None:
        """셀 하나의 스캔을 마쳤다. 같은 열에 남은 행이 있으면 이어서 한다."""
        plan = self._plan
        if plan is None:
            return
        label = self.current_cell()
        self.cell_status_changed.emit(label, CellStatus.COMPLETED.value)
        self.activity.emit(f"{label}: 스캔을 완료했습니다.")

        if self._row + 1 < plan.row_count:
            # 같은 열의 다음 행. 아직 3단계(검사) 안이라 단계 표시는 그대로다.
            self._row += 1
            self._announce_cell()
            self._request_lift(SequencerState.MOVING_LIFT)
            return

        # 열을 다 끝냈다. 4단계로 물러난다.
        self._set_state(SequencerState.RETRACTING)
        self.activity.emit(f"{self._column + 1}구역 완료. 안전 위치로 물러납니다.")
        self.retract_requested.emit()

# 레지스터 290~298 중 순회에 쓰는 항목의 위치와 완료 상태 값.
# 로봇 태스크(dus_finish.script)가 state=5, finished=1을 쓴다.
_STATE_INDEX = 0
_FINISHED_INDEX = 5
_STATE_DONE = 5
