"""운영 화면에서 공통으로 사용하는 소형 위젯을 제공한다."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


class ConnectionBadge(QFrame):
    """장비 이름과 연결 상태를 간결하게 표시한다."""

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(1)
        name_label = QLabel(name)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        state = QLabel("● 연결됨")
        state.setObjectName("StatusGood")
        state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name_label)
        layout.addWidget(state)


class MetricRow(QFrame):
    """검사 및 장비의 실시간 수치를 항목명과 값으로 표시한다."""

    def __init__(self, label: str, value: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CycleRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        label_widget = QLabel(label)
        label_widget.setObjectName("MetricLabel")
        self.value_label = QLabel(value)
        self.value_label.setObjectName("MetricValue")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(label_widget)
        layout.addStretch()
        layout.addWidget(self.value_label)
        self.setMinimumHeight(38)

    def set_value(self, value: str) -> None:
        """행을 다시 만들지 않고 표시값만 변경한다."""
        self.value_label.setText(value)


class SequenceStep(QFrame):
    """구간마다 반복되는 안전 순서의 한 단계를 표시한다."""

    def __init__(self, number: int, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SequenceStep")
        self.setProperty("active", False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        number_label = QLabel(str(number))
        number_label.setObjectName("Muted")
        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setWordWrap(True)
        layout.addWidget(number_label, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(title_label)
        self.setMinimumHeight(66)

    def set_active(self, active: bool) -> None:
        """동적 QSS 속성을 변경하고 스타일을 즉시 다시 계산한다."""
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)
