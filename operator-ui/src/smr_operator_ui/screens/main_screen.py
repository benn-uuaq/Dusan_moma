"""검사 상태, 주요 제어, 검사 대상 설정을 제공하는 메인 화면이다."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QScrollArea,
    QWidget,
)

from smr_operator_ui.components import (
    FitLabel, OrbitView, RectWorkView, SequenceStep, SpeedBar,
)
from smr_operator_ui.keypad import TouchDoubleSpinBox, TouchSpinBox
from smr_operator_ui.state import AppSnapshot, CyclePhase


class MainScreen(QWidget):
    """검사 사이클을 표시하고 사용자 동작은 시그널로 위임한다."""

    # 화면은 장비나 저장 서비스를 직접 호출하지 않고 동작을 요청한다.
    # OperatorWindow가 아래 시그널을 알맞은 서비스에 연결한다.
    start_requested = pyqtSignal()
    pause_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    IDLE_NOTICE = "검사 시작을 기다리고 있습니다."

    settings_requested = pyqtSignal()
    alarm_reset_requested = pyqtSignal()
    target_dimensions_changed = pyqtSignal(float, float)
    # 너비/높이/스캐너 높이/겹침. Modbus 256~259와 같은 mm 단위다.
    work_area_changed = pyqtSignal(float, float, float, float)
    # 세로 속도 바에서 로봇 동작 속도 비율(2~100 %)을 바꿨다.
    speed_changed = pyqtSignal(int)

    # 화면에 늘어놓는 안전 순서. 한 구간에서 이 5단계를 순환한다.
    _phase_order = (
        CyclePhase.SECURING,
        CyclePhase.LEVELING,
        CyclePhase.INSPECTING,
        CyclePhase.RETRACTING,
        CyclePhase.MOVING,
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QHBoxLayout(self)
        root.setContentsMargins(20, 14, 0, 14)
        root.setSpacing(14)

        content = QVBoxLayout()
        content.setSpacing(10)
        root.addLayout(content, 1)

        hero = QHBoxLayout()
        # 상단 네 칸은 같은 규칙으로 만든다: 큰 값 한 개 + (있으면) 같은
        # 모양의 "/ 전체" 접미사. 예전에는 구간만 값·접미사를 나누고 행은
        # "01 / 6" 을 통째로 한 라벨에 넣어, 같은 자리인데 슬래시의 크기와
        # 색이 서로 달라 보였다.
        self.segment_label = FitLabel("01", base_px=40)
        self.segment_label.setObjectName("HeroCurrent")
        segment_box, self.segment_total = self._hero_box(
            "현재 구간", self.segment_label, "/ 12")
        # 열(구간)만으로는 리프트가 몇 칸 올라갔는지 알 수 없어 행도 함께 보여준다.
        self.row_label = FitLabel("01", base_px=40)
        self.row_label.setObjectName("HeroCurrent")
        row_box, self.row_total = self._hero_box(
            "현재 행 (리프트)", self.row_label, "/ 6")
        self.progress_label = FitLabel("0 %")
        self.progress_label.setObjectName("HeroValue")
        progress_box, _ = self._hero_box("원주 진행률", self.progress_label)
        # 여기 문구가 가장 길다("구간 검사 중"). 창이 좁아지면 먼저 줄어야
        # 네 칸이 다 들어간다.
        self.phase_label = FitLabel("대기")
        self.phase_label.setObjectName("HeroValue")
        phase_box, _ = self._hero_box("현재 단계", self.phase_label)
        hero.addWidget(segment_box, 1)
        hero.addWidget(row_box, 1)
        hero.addWidget(progress_box, 1)
        hero.addWidget(phase_box, 2)
        content.addLayout(hero)

        sequence_label = QLabel("안전 순서 (구간마다 반복)")
        sequence_label.setObjectName("SectionTitle")
        content.addWidget(sequence_label)
        sequence = QHBoxLayout()
        # sequence.setSpacing(8)
        names = ("정지·고정", "수평 보정", "Cobot 검사", "안전 위치", "다음 구간 이동")
        self.steps: list[SequenceStep] = []
        for index, name in enumerate(names, 1):
            step = SequenceStep(index, name)
            self.steps.append(step)
            sequence.addWidget(step, 1)
        content.addLayout(sequence)

        safety_note = QLabel("Cobot 검사 단계와 AMR 이동 단계는 동시에 수행되지 않습니다.")
        safety_note.setObjectName("Muted")
        safety_note.setWordWrap(True)
        content.addWidget(safety_note)

        workspace = QHBoxLayout()
        self.orbit_view = OrbitView()
        self.orbit_view.target_clicked.connect(self._edit_target_dimensions)
        workspace.addWidget(self.orbit_view, 1)
        self.rect_view = RectWorkView()
        self.rect_view.target_clicked.connect(self._edit_work_area)
        workspace.addWidget(self.rect_view, 1)
        content.addLayout(workspace, 1)

        rail = QFrame()
        rail.setObjectName("RightRail")
        # 폭은 app.qss의 QFrame#RightRail min/max-width로 고정한다(전체
        # UI 배율에 맞춰 같이 커지도록 — speed_bar.py의 SpeedBar와 같은 이유).
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(24, 10, 24, 10)
        rail_layout.setSpacing(7)
        title = QLabel("주요 제어")
        title.setObjectName("SectionTitle")
        rail_layout.addWidget(title)
        self.start_button = QPushButton("검사 시작")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.clicked.connect(self.start_requested)
        rail_layout.addWidget(self.start_button)
        controls = QHBoxLayout()
        self.pause_button = QPushButton("일시정지")
        self.pause_button.setObjectName("PauseButton")
        self.pause_button.setEnabled(False)
        self.pause_button.clicked.connect(self.pause_requested)
        # 수동 제어는 설정 / 로그 메뉴에 이미 있으므로 여기서는 뺀다.
        self.stop_button = QPushButton("정지")
        self.stop_button.setObjectName("DangerButton")
        self.stop_button.clicked.connect(self.stop_requested)
        controls.addWidget(self.pause_button)
        controls.addWidget(self.stop_button)
        rail_layout.addLayout(controls)
        # "사이클 요약"은 상단 타일(현재 구간·현재 행·원주 진행률·현재 단계)과
        # 내용이 겹쳐 뺐다. 남은 자리는 아래 알림 상자가 받는다.
        rail_layout.addSpacing(12)
        settings = QPushButton("설정 / 로그")
        settings.clicked.connect(self.settings_requested)
        rail_layout.addWidget(settings)
        # 알림·장애 통보 자리. 예전에는 남는 공간에 얹혀 있어 다른 항목에
        # 밀리면 글자가 잘렸다("알람 띄우는 영역이 너무 좁음"). 자리를 미리
        # 잡아 두는 상자로 만들어 메시지가 없어도 높이가 유지되게 한다.
        notice = QFrame()
        notice.setObjectName("NoticeBox")
        notice_layout = QVBoxLayout(notice)
        notice_layout.setContentsMargins(12, 10, 12, 10)
        notice_layout.setSpacing(4)
        notice_title = QLabel("알림")
        notice_title.setObjectName("NoticeTitle")
        notice_layout.addWidget(notice_title)
        self.activity_label = QLabel(self.IDLE_NOTICE)
        self.activity_label.setObjectName("NoticeText")
        self.activity_label.setWordWrap(True)
        self.activity_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        notice_layout.addWidget(self.activity_label, 1)
        # 장애·알람이 뜬 뒤 지울 방법이 없어서 문구가 계속 남아 있었다.
        # 자주 쓰는 조작이라 목록 아래에 제 크기로 둔다.
        self.notice_clear_button = QPushButton("알람 리셋")
        self.notice_clear_button.setObjectName("NoticeClear")
        self.notice_clear_button.clicked.connect(self.alarm_reset_requested)
        notice_layout.addWidget(self.notice_clear_button)
        # 남는 세로는 알림 상자가 쓴다 — 주요 제어와 아래 버튼 사이가
        # 휑하게 비지 않고, 문구도 여러 줄 들어간다.
        rail_layout.addWidget(notice, 1)
        root.addWidget(rail)

        # 로봇 속도는 작업 중에도 자주 만지므로 화면 오른쪽 끝에 세로 바로 둔다.
        self.speed_bar = SpeedBar()
        self.speed_bar.speed_changed.connect(self.speed_changed)
        root.addWidget(self.speed_bar)

    def _edit_target_dimensions(self) -> None:
        """터치 전용 숫자 필드로 검사 대상의 지름과 높이를 입력받는다."""
        dialog = QDialog(self)
        dialog.setWindowTitle("검사 대상 설정")
        dialog.setModal(True)
        dialog.setMinimumWidth(360)

        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        diameter_m, height_m = self.orbit_view.target_dimensions()

        diameter_input = TouchDoubleSpinBox()
        diameter_input.dialog_title = "검사 대상 지름 입력"
        diameter_input.setRange(0.10, 100.00)
        diameter_input.setDecimals(2)
        diameter_input.setSingleStep(0.10)
        diameter_input.setSuffix(" m")
        diameter_input.setValue(diameter_m)

        height_input = TouchDoubleSpinBox()
        height_input.dialog_title = "검사 대상 높이 입력"
        height_input.setRange(0.10, 100.00)
        height_input.setDecimals(2)
        height_input.setSingleStep(0.10)
        height_input.setSuffix(" m")
        height_input.setValue(height_m)

        form.addRow("지름", diameter_input)
        form.addRow("높이", height_input)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            # 화면에는 값을 즉시 반영하고, 같은 값의 비동기 저장은
            # 애플리케이션 계층에 요청한다.
            diameter_m = diameter_input.value()
            height_m = height_input.value()
            self.set_target_dimensions(diameter_m, height_m)
            self.target_dimensions_changed.emit(diameter_m, height_m)

    def set_target_dimensions(self, diameter_m: float, height_m: float) -> None:
        """외부 설정 또는 사용자 입력으로 검사 대상 크기를 변경한다."""
        self.orbit_view.set_target_dimensions(diameter_m, height_m)

    def _edit_work_area(self) -> None:
        """터치 전용 숫자 필드로 작업 영역 치수를 입력받는다. 단위는 mm이다."""
        dialog = QDialog(self)
        dialog.setWindowTitle("작업 영역 설정")
        dialog.setModal(True)
        dialog.setMinimumWidth(360)

        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        width_mm, height_mm, scan_h_mm, overlap_mm = self.rect_view.work_area()

        def mm_field(value: float, title: str, minimum: int = 1) -> TouchSpinBox:
            field = TouchSpinBox()
            field.dialog_title = title
            field.setRange(minimum, 20000)
            field.setSuffix(" mm")
            field.setValue(int(value))
            return field

        width_input = mm_field(width_mm, "작업 영역 너비 입력")
        height_input = mm_field(height_mm, "작업 영역 높이 입력")
        scan_h_input = mm_field(scan_h_mm, "스캐너 세로 유효높이 입력")
        # 겹침만 0 을 허용한다 — 바깥(MQTT)에서 0 을 보내는데 화면에서만
        # 1 부터라면 같은 값을 다시 넣을 수 없다. 나머지는 0 이면 계산이
        # 성립하지 않아 1 이 하한이다.
        overlap_input = mm_field(overlap_mm, "세로 최소 겹침 입력", minimum=0)

        form.addRow("너비", width_input)
        form.addRow("높이", height_input)
        form.addRow("스캐너 높이", scan_h_input)
        # 실제 겹침은 로봇이 계산한다 — 가운데 프로브가 밑면과 윗면을
        # 모두 지나야 해서 줄 위치가 먼저 정해지고, 겹침은 그 결과다.
        # 여기 값은 "최소 이만큼은 겹쳐라"는 하한일 뿐이다.
        form.addRow("최소 겹침", overlap_input)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            values = (width_input.value(), height_input.value(),
                      scan_h_input.value(), overlap_input.value())
            self.set_work_area(*values)
            self.work_area_changed.emit(*(float(v) for v in values))

    def set_work_area(
        self, width_mm: float, height_mm: float, scan_h_mm: float, overlap_mm: float,
        eoat_w_mm: float | None = None,
        radius_mm: float | None = None, thickness_mm: float | None = None,
    ) -> None:
        """외부 설정 또는 사용자 입력으로 작업 영역 치수를 변경한다.

        반지름·두께는 그림에서 경로를 **현**으로 그리는 데 쓴다 — 로봇이
        내보내는 가로 좌표가 현 기준이라 그래야 마커가 경로 위에 놓인다.
        """
        self.rect_view.set_work_area(width_mm, height_mm, scan_h_mm, overlap_mm,
                                     eoat_w_mm, radius_mm, thickness_mm)

    def apply_wall_position(self, horizontal_mm: float, vertical_mm: float) -> None:
        """원점 기준 상대좌표(가로/세로)를 사각형 작업 모델에 표시한다."""
        self.rect_view.set_position(horizontal_mm, vertical_mm)

    def set_grid_position(self, column: int, columns: int,
                          row: int, rows: int) -> None:
        """지금 열(구간)과 행(리프트 단계)을 함께 표시한다.

        열은 AMR 정차 구역, 행은 리프트 높이다. 리프트가 한 칸 오를 때마다
        행이 올라가고, 열이 바뀌면 행은 다시 01 로 내려온다.
        """
        self._columns, self._rows = columns, rows
        self.segment_label.setText(f"{column:02d}")
        self.row_label.setText(f"{row:02d}")
        self._refresh_hero_totals()
        self.orbit_view.set_row_position(row, rows)

    def set_global_scale(self, scale: float) -> None:
        """창 배율을 받아 요약 칸 글자 기준 크기에 반영한다.

        스타일시트는 배율만큼 커진 크기를 주는데, FitLabel 은 자기 폰트를
        직접 잡고 있어 그 변화를 못 받는다. 여기서 같은 배율을 넘겨 준다.
        """
        for label in (self.segment_label, self.row_label,
                      self.progress_label, self.phase_label,
                      self.segment_total, self.row_total):
            if isinstance(label, FitLabel):
                label.set_scale(scale)

    def set_motion_values(self, lift_mm: float, amr_mm: float) -> None:
        """가상 차량 이동 거리와 리프트 높이를 헤드라인 옆에 붙인다.

        열·행 번호만으로는 지금 얼마나 갔는지 알 수 없다. 칸 수 옆에 실제
        값을 같이 두면 이동 중인지 선 건지도 바로 보인다 (값이 흐른다).
        """
        self._lift_mm, self._amr_mm = float(lift_mm), float(amr_mm)
        self._refresh_hero_totals()

    def _refresh_hero_totals(self) -> None:
        columns = getattr(self, "_columns", 12)
        rows = getattr(self, "_rows", 6)
        amr = getattr(self, "_amr_mm", None)
        lift = getattr(self, "_lift_mm", None)
        self.segment_total.setText(
            f"/ {columns}" if amr is None else f"/ {columns} · {amr:,.0f} mm")
        self.row_total.setText(
            f"/ {rows}" if lift is None else f"/ {rows} · {lift:,.0f} mm")

    def set_base_speed(self, mm_s: float) -> None:
        """속도 바가 % 를 mm/s 로 환산할 기준값을 갱신한다."""
        self.speed_bar.set_base_speed(mm_s)

    def set_speed_scale(self, percent: int) -> None:
        """로봇이 실제로 쓰고 있는 동작 속도 비율[%]을 표시한다."""
        self.speed_bar.set_actual(percent)

    def clear_activity(self) -> None:
        """알림 상자를 비운다(기본 문구로 되돌린다)."""
        self.activity_label.setText(self.IDLE_NOTICE)

    def set_work_cell_label(self, text: str) -> None:
        """현재 스캔 중인 격자 이름(예: "A0 (1/9)")을 표시한다."""
        self.rect_view.set_cell_label(text)

    def set_total_cells(self, total: int) -> None:
        """MQTT(ERUT)가 보낸 열 수 × 행 수(전체 셀 수)를 상단 타일에 반영한다.

        "사이클 요약"을 뺀 뒤로는 "현재 구간" 타일의 "/ 전체" 자리가 이 값을
        보여 준다.
        """
        self.segment_total.setText(f"/ {total}")

    def _hero_box(self, label: str, value: QLabel,
                  suffix: str = "") -> tuple[QFrame, QLabel | None]:
        """대시보드 상단에서 재사용할 요약 카드를 만든다.

        접미사("/ 12")도 돌려준다 — 격자 크기가 바뀌면 갱신해야 하는데,
        예전에는 만들어만 두고 잡아 두지 않아 12·6 이 그대로 굳어 있었다.
        """
        frame = QFrame()
        frame.setObjectName("Surface")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 8, 16, 8)
        title = QLabel(label)
        title.setObjectName("MetricLabel")
        line = QHBoxLayout()
        line.setSpacing(8)
        line.addWidget(value)
        suffix_label = None
        if suffix:
            suffix_label = FitLabel(suffix, base_px=22, min_px=11)
            suffix_label.setObjectName("HeroSuffix")
            line.addWidget(suffix_label)
        line.addStretch()
        layout.addWidget(title)
        layout.addLayout(line)
        return frame, suffix_label

    def update_snapshot(self, snapshot: AppSnapshot) -> None:
        """하나의 일관된 상태 스냅샷을 대시보드 전체에 반영한다."""
        cycle = snapshot.cycle
        self.segment_label.setText(f"{cycle.current_segment:02d}")
        self.progress_label.setText(f"{cycle.progress_percent} %")
        self.phase_label.setText(cycle.phase.value)
        self.orbit_view.set_state(cycle)

        # 일반 검사 순서 안에서는 다음 단계가 순환한다. 완료나 대기처럼
        # 순서 밖의 상태는 별도의 문구를 명시해야 한다.
        if cycle.phase in self._phase_order:
            index = self._phase_order.index(cycle.phase)
            next_phase = self._phase_order[(index + 1) % len(self._phase_order)].value
        elif cycle.phase is CyclePhase.COMPLETE:
            next_phase = "사이클 종료"
        else:
            next_phase = "정지·고정"
        for step, phase in zip(self.steps, self._phase_order):
            step.set_active(cycle.phase is phase)

        self.start_button.setText("새 검사 시작" if cycle.phase is CyclePhase.COMPLETE else "검사 시작")
        self.start_button.setEnabled(not cycle.running or cycle.paused)
        self.pause_button.setEnabled(cycle.running)
        self.pause_button.setText("재개" if cycle.paused else "일시정지")

    def show_activity(self, message: str) -> None:
        """서비스 또는 시뮬레이터의 최근 활동 메시지를 표시한다."""
        self.activity_label.setText(message)
