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
    QWidget,
)

from smr_operator_ui.components import MetricRow, OrbitView, RectWorkView, SequenceStep
from smr_operator_ui.keypad import TouchDoubleSpinBox, TouchSpinBox
from smr_operator_ui.state import AppSnapshot, CyclePhase


class MainScreen(QWidget):
    """검사 사이클을 표시하고 사용자 동작은 시그널로 위임한다."""

    # 화면은 장비나 저장 서비스를 직접 호출하지 않고 동작을 요청한다.
    # OperatorWindow가 아래 시그널을 알맞은 서비스에 연결한다.
    start_requested = pyqtSignal()
    pause_requested = pyqtSignal()
    manual_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    target_dimensions_changed = pyqtSignal(float, float)
    # 너비/높이/스캐너 높이/겹침. Modbus 256~259와 같은 mm 단위다.
    work_area_changed = pyqtSignal(float, float, float, float)

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
        self.segment_label = QLabel("01")
        self.segment_label.setObjectName("HeroCurrent")
        segment_box = self._hero_box("현재 구간", self.segment_label, "/ 12")
        self.progress_label = QLabel("0 %")
        self.progress_label.setObjectName("HeroValue")
        progress_box = self._hero_box("원주 진행률", self.progress_label)
        self.phase_label = QLabel("대기")
        self.phase_label.setObjectName("HeroValue")
        phase_box = self._hero_box("현재 단계", self.phase_label)
        hero.addWidget(segment_box, 1)
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
        rail.setMinimumWidth(340)
        rail.setMaximumWidth(380)
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
        manual = QPushButton("수동 제어")
        manual.clicked.connect(self.manual_requested)
        controls.addWidget(self.pause_button)
        controls.addWidget(manual)
        rail_layout.addLayout(controls)
        cycle_title = QLabel("사이클 요약")
        cycle_title.setObjectName("SectionTitle")
        rail_layout.addWidget(cycle_title)
        self.total = MetricRow("총 구간 수", "12")
        self.current = MetricRow("현재 구간", "01 / 12")
        self.summary_progress = MetricRow("원주 진행률", "0 %")
        self.completed = MetricRow("완료 구간", "0")
        self.pending = MetricRow("대기 구간", "12")
        self.next_phase = MetricRow("다음 단계", "정지·고정")
        for row in (self.total, self.current, self.summary_progress, self.completed, self.pending, self.next_phase):
            rail_layout.addWidget(row)
        settings = QPushButton("설정 / 로그")
        settings.clicked.connect(self.settings_requested)
        rail_layout.addWidget(settings)
        self.activity_label = QLabel("검사 시작을 기다리고 있습니다.")
        self.activity_label.setObjectName("Muted")
        self.activity_label.setWordWrap(True)
        rail_layout.addWidget(self.activity_label)
        root.addWidget(rail)

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

        def mm_field(value: float, title: str) -> TouchSpinBox:
            field = TouchSpinBox()
            field.dialog_title = title
            field.setRange(1, 20000)
            field.setSuffix(" mm")
            field.setValue(int(value))
            return field

        width_input = mm_field(width_mm, "작업 영역 너비 입력")
        height_input = mm_field(height_mm, "작업 영역 높이 입력")
        scan_h_input = mm_field(scan_h_mm, "스캐너 세로 유효높이 입력")
        overlap_input = mm_field(overlap_mm, "세로 겹침 입력")

        form.addRow("너비", width_input)
        form.addRow("높이", height_input)
        form.addRow("스캐너 높이", scan_h_input)
        form.addRow("겹침", overlap_input)
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

    def set_work_area(self, width_mm: float, height_mm: float, scan_h_mm: float, overlap_mm: float) -> None:
        """외부 설정 또는 사용자 입력으로 작업 영역 치수를 변경한다."""
        self.rect_view.set_work_area(width_mm, height_mm, scan_h_mm, overlap_mm)

    def apply_wall_position(self, horizontal_mm: float, vertical_mm: float) -> None:
        """원점 기준 상대좌표(가로/세로)를 사각형 작업 모델에 표시한다."""
        self.rect_view.set_position(horizontal_mm, vertical_mm)

    def _hero_box(self, label: str, value: QLabel, suffix: str = "") -> QFrame:
        """대시보드 상단에서 재사용할 요약 카드를 만든다."""
        frame = QFrame()
        frame.setObjectName("Surface")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 8, 16, 8)
        title = QLabel(label)
        title.setObjectName("MetricLabel")
        line = QHBoxLayout()
        line.addWidget(value)
        if suffix:
            suffix_label = QLabel(suffix)
            suffix_label.setObjectName("SectionTitle")
            line.addWidget(suffix_label)
        line.addStretch()
        layout.addWidget(title)
        layout.addLayout(line)
        return frame

    def update_snapshot(self, snapshot: AppSnapshot) -> None:
        """하나의 일관된 상태 스냅샷을 대시보드 전체에 반영한다."""
        cycle = snapshot.cycle
        self.segment_label.setText(f"{cycle.current_segment:02d}")
        self.progress_label.setText(f"{cycle.progress_percent} %")
        self.phase_label.setText(cycle.phase.value)
        self.orbit_view.set_state(cycle)
        self.current.set_value(f"{cycle.current_segment:02d} / {cycle.total_segments}")
        self.summary_progress.set_value(f"{cycle.progress_percent} %")
        self.completed.set_value(str(cycle.completed_segments))
        self.pending.set_value(str(cycle.total_segments - cycle.completed_segments))

        # 일반 검사 순서 안에서는 다음 단계가 순환한다. 완료나 대기처럼
        # 순서 밖의 상태는 별도의 문구를 명시해야 한다.
        if cycle.phase in self._phase_order:
            index = self._phase_order.index(cycle.phase)
            next_phase = self._phase_order[(index + 1) % len(self._phase_order)].value
        elif cycle.phase is CyclePhase.COMPLETE:
            next_phase = "사이클 종료"
        else:
            next_phase = "정지·고정"
        self.next_phase.set_value(next_phase)
        for step, phase in zip(self.steps, self._phase_order):
            step.set_active(cycle.phase is phase)

        self.start_button.setText("새 검사 시작" if cycle.phase is CyclePhase.COMPLETE else "검사 시작")
        self.start_button.setEnabled(not cycle.running or cycle.paused)
        self.pause_button.setEnabled(cycle.running)
        self.pause_button.setText("재개" if cycle.paused else "일시정지")

    def show_activity(self, message: str) -> None:
        """서비스 또는 시뮬레이터의 최근 활동 메시지를 표시한다."""
        self.activity_label.setText(message)
