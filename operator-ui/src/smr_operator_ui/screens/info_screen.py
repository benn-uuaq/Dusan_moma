from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget


class InfoScreen(QWidget):
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
