from __future__ import annotations

import math
from importlib.resources import files

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QWidget

from smr_operator_ui.state import CycleState
from smr_operator_ui.styles.tokens import COLORS


class OrbitView(QWidget):
    """Twelve-segment circumference view for the active inspection cycle."""

    target_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = CycleState()
        self._target_diameter_m = 2.0
        self._target_height_m = 4.0
        source = QPixmap(str(files("smr_operator_ui.resources").joinpath("amr-cobot.png")))
        if source.isNull():
            self._robot_pixmap = source
        else:
            crop = QRect(
                int(source.width() * 0.15),
                int(source.height() * 0.05),
                int(source.width() * 0.70),
                int(source.height() * 0.85),
            )
            self._robot_pixmap = source.copy(crop)
        self.setMinimumSize(500, 330)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("검사 대상을 클릭하여 크기를 설정하세요.")

    def set_state(self, state: CycleState) -> None:
        self._state = state
        self.update()

    def set_target_dimensions(self, diameter_m: float, height_m: float) -> None:
        self._target_diameter_m = diameter_m
        self._target_height_m = height_m
        self.update()

    def target_dimensions(self) -> tuple[float, float]:
        return self._target_diameter_m, self._target_height_m

    def _target_rect(self) -> QRectF:
        center = QPointF(self.width() * 0.44, self.height() * 0.48)
        radius = min(self.width() * 0.34, self.height() * 0.36)
        target_diameter = radius * 1.24
        return QRectF(
            center.x() - target_diameter * 0.5,
            center.y() - target_diameter * 0.5,
            target_diameter,
            target_diameter,
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._target_rect().contains(event.position()):
            self.target_clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        center = QPointF(w * 0.44, h * 0.48)
        radius = min(w * 0.34, h * 0.36)

        target = self._target_rect()
        target_text = (
            "검사 대상\n"
            f"Ø {self._target_diameter_m:.2f} m\n"
            f"H {self._target_height_m:.2f} m"
        )
        painter.setPen(QPen(QColor("#AAB3BC"), 1.5))
        painter.setBrush(QColor("#E4E7EA"))
        painter.drawEllipse(target)
        painter.setPen(QColor(COLORS["text"]))
        painter.setFont(QFont("Malgun Gothic", 12, 600))
        painter.drawText(target, Qt.AlignmentFlag.AlignCenter, target_text)

        orbit = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
        pen = QPen(QColor(COLORS["secondary"]), 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(orbit)

        gap = 4 * 16
        for index in range(self._state.total_segments):
            start_deg = 90 - index * 30 - 13
            if index < self._state.completed_segments:
                color = COLORS["success"]
            elif index + 1 == self._state.current_segment:
                color = COLORS["warning"]
            else:
                color = COLORS["pending"]
            painter.setPen(QPen(QColor("#FFFFFF"), 2))
            painter.setBrush(QColor(color))
            painter.drawPie(
                QRectF(center.x() - radius * 1.16, center.y() - radius * 1.16, radius * 2.32, radius * 2.32),
                int(start_deg * 16),
                int((30 * 16) - gap),
            )

        painter.setBrush(QColor(COLORS["background"]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(center.x() - radius * 1.03, center.y() - radius * 1.03, radius * 2.06, radius * 2.06))
        painter.setBrush(QColor("#E4E7EA"))
        painter.setPen(QPen(QColor("#AAB3BC"), 1.5))
        painter.drawEllipse(target)
        painter.setPen(QColor(COLORS["text"]))
        painter.drawText(target, Qt.AlignmentFlag.AlignCenter, target_text)

        painter.setFont(QFont("Malgun Gothic", 9, 600))
        for index in range(12):
            angle = math.radians(-90 + index * 30)
            x = center.x() + math.cos(angle) * radius * 1.28
            y = center.y() + math.sin(angle) * radius * 1.28
            painter.setPen(QColor(COLORS["text"]))
            painter.drawText(QRectF(x - 17, y - 12, 34, 24), Qt.AlignmentFlag.AlignCenter, f"{index + 1:02d}")

        robot_angle = math.radians(-90 + (self._state.current_segment - 1) * 30)
        rx = center.x() + math.cos(robot_angle) * radius
        ry = center.y() + math.sin(robot_angle) * radius
        if not self._robot_pixmap.isNull():
            size = int(radius * 1.08)
            robot = self._robot_pixmap.scaled(
                size,
                size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap(QPoint(int(rx - robot.width() * 0.5), int(ry - robot.height() * 0.52)), robot)

        legend_y = h - 24
        items = ((COLORS["success"], "완료"), (COLORS["warning"], "현재"), (COLORS["pending"], "대기"))
        x = 18
        painter.setFont(QFont("Malgun Gothic", 9))
        for color, label in items:
            painter.fillRect(QRectF(x, legend_y, 16, 12), QColor(color))
            painter.setPen(QColor(COLORS["text"]))
            painter.drawText(QRectF(x + 22, legend_y - 3, 50, 18), Qt.AlignmentFlag.AlignLeft, label)
            x += 76
