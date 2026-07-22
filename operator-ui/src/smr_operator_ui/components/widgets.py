from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


class ConnectionBadge(QFrame):
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
        self.value_label.setText(value)


class SequenceStep(QFrame):
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
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)
