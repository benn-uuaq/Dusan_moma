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

from smr_operator_ui.components import MetricRow, OrbitView, SequenceStep
from smr_operator_ui.keypad import TouchDoubleSpinBox
from smr_operator_ui.state import AppSnapshot, CyclePhase


class MainScreen(QWidget):
    start_requested = pyqtSignal()
    pause_requested = pyqtSignal()
    manual_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    target_dimensions_changed = pyqtSignal(float, float)

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
        sequence.setSpacing(8)
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
        metrics = QVBoxLayout()
        metrics.setSpacing(0)
        self.lift = MetricRow("리프트 높이", "1.20 m")
        self.tcp = MetricRow("TCP 위치", "X 0.00 / Y 0.00 / Z 0.00 m")
        self.velocity = MetricRow("이동 속도", "0.0 m/s")
        self.mode = MetricRow("모드", "수동")
        self.state = MetricRow("상태", "대기")
        self.safety = MetricRow("안전 상태", "정상")
        for item in (self.lift, self.tcp, self.velocity, self.mode, self.state, self.safety):
            metrics.addWidget(item)
        metrics.addStretch()
        metric_frame = QFrame()
        metric_frame.setObjectName("Surface")
        metric_frame.setLayout(metrics)
        metric_frame.setMinimumWidth(265)
        workspace.addWidget(metric_frame)
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
            diameter_m = diameter_input.value()
            height_m = height_input.value()
            self.orbit_view.set_target_dimensions(diameter_m, height_m)
            self.target_dimensions_changed.emit(diameter_m, height_m)

    def _hero_box(self, label: str, value: QLabel, suffix: str = "") -> QFrame:
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
        cycle = snapshot.cycle
        self.segment_label.setText(f"{cycle.current_segment:02d}")
        self.progress_label.setText(f"{cycle.progress_percent} %")
        self.phase_label.setText(cycle.phase.value)
        self.orbit_view.set_state(cycle)
        self.lift.set_value(f"{cycle.lift_height_m:.2f} m")
        self.tcp.set_value(f"X {cycle.tcp_x_m:.2f} / Y {cycle.tcp_y_m:.2f} / Z {cycle.tcp_z_m:.2f} m")
        self.velocity.set_value(f"{cycle.velocity_mps:.1f} m/s")
        self.mode.set_value("자동" if cycle.running else "수동")
        self.state.set_value(cycle.phase.value)
        self.safety.set_value("정상" if cycle.safe else "안전 정지")
        self.current.set_value(f"{cycle.current_segment:02d} / {cycle.total_segments}")
        self.summary_progress.set_value(f"{cycle.progress_percent} %")
        self.completed.set_value(str(cycle.completed_segments))
        self.pending.set_value(str(cycle.total_segments - cycle.completed_segments))

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
        self.activity_label.setText(message)
