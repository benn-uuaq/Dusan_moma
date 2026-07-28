"""간단한 보조 페이지에서 재사용하는 정보 화면이다."""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget


class InfoScreen(QWidget):
    """제목과 설명 및 공통 이전 화면 버튼을 표시한다."""

    back_requested = pyqtSignal()

    def __init__(self, title: str, description: str, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 40, 48, 40)
        heading = QLabel(title)
        heading.setObjectName("HeroValue")
        body = QLabel(description)
        body.setWordWrap(True)
        body.setObjectName("Muted")
        back = QPushButton("이전")
        back.setObjectName("BackButton")
        back.clicked.connect(self.back_requested.emit)
        layout.addWidget(heading)
        layout.addWidget(body)
        layout.addStretch()
        layout.addWidget(back)
