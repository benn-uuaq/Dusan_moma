"""검사 대상, 구간 링, 로봇 위치를 직접 그리는 위젯이다."""

from __future__ import annotations

import math
from importlib.resources import files

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QWidget

from smr_operator_ui.state import CycleState
from smr_operator_ui.styles.tokens import COLORS


class OrbitView(QWidget):
    """진행 중인 검사를 12개 원주 구간으로 표시한다."""

    target_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = CycleState()
        # 시험용 외벽 기준. 검사 대상 자체의 지름(안쪽 면 기준, R834.6 ->
        # 1.6692 m)과 두께(10mm)는 따로 표시한다 — 로봇이 훑는 바깥 면
        # 반지름(834.6+10=844.6)은 로봇 경로 계산에만 쓰는 값이지 "검사
        # 대상의 지름"이 아니다(예전엔 여기 합쳐서 보여줬다).
        self._target_diameter_m = 1.6692
        self._target_height_m = 0.5
        self._target_thickness_mm = 10.0
        # 지금 몇 번째 행(층)을 도는지. 열(구간)만으로는 알 수 없다.
        self._current_row = 1
        self._total_rows = 1
        source = QPixmap(str(files("smr_operator_ui.resources").joinpath("amr-cobot2.png")))
        if source.isNull():
            self._robot_pixmap = source
        else:
            # 원본 이미지의 불필요한 여백을 잘라낸다. 이렇게 해야 이미지를
            # 확대·축소하여 원주 위에 배치할 때 로봇이 중앙에 맞는다.
            crop = QRect(
                int(source.width() * 0.15),
                int(source.height() * 0.05),
                int(source.width() * 0.70),
                int(source.height() * 0.85),
            )
            self._robot_pixmap = source.copy(crop)
        # 최소 폭은 실제로 쓸 수 있는 공간에 맞춘다.
        # 메인 화면은 1280px 고정인데 우측 레일(340)·속도 바(112)·여백을
        # 빼면 원통·작업영역 둘이 나눠 쓸 폭이 780 밖에 없다. 둘 다 500을
        # 요구하면 1014 가 되어 234px 이 창 밖으로 밀려 오른쪽이 잘린다.
        self.setMinimumSize(360, 330)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("검사 대상을 클릭하여 크기를 설정하세요.")

    def set_state(self, state: CycleState) -> None:
        """표시할 검사 상태를 교체하고 다시 그리도록 요청한다."""
        self._state = state
        self.update()

    def set_target_dimensions(self, diameter_m: float, height_m: float) -> None:
        """중앙에 표시되는 검사 대상의 지름과 높이를 갱신한다."""
        self._target_diameter_m = diameter_m
        self._target_height_m = height_m
        self.update()

    def set_target_thickness_mm(self, thickness_mm: float) -> None:
        """검사 대상의 두께를 지름과 별도로 갱신한다(단위 mm)."""
        self._target_thickness_mm = thickness_mm
        self.update()

    def set_row_position(self, row: int, rows: int) -> None:
        """지금 몇 번째 행(층)을 도는지 갱신한다. 열(구간) 진행만으로는

        AMR이 원주를 몇 바퀴째 도는지(=리프트가 몇 층에 있는지) 알 수
        없어서("총 구간별 행의 표시가 전혀 없어서 몇층인지 알 수가 없음")
        따로 표시한다.
        """
        self._current_row = row
        self._total_rows = rows
        self.update()

    def target_dimensions(self) -> tuple[float, float]:
        """현재 검사 대상의 지름과 높이를 미터 단위로 반환한다."""
        return self._target_diameter_m, self._target_height_m

    def _target_rect(self) -> QRectF:
        """현재 위젯 크기를 기준으로 클릭 가능한 중앙 원 영역을 계산한다."""
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
        """중앙 검사 대상 원을 직접 눌렀을 때만 클릭 시그널을 발생시킨다."""
        if event.button() == Qt.MouseButton.LeftButton and self._target_rect().contains(event.position()):
            self.target_clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        """검사 대상, 진행 구간, 번호, 로봇, 범례를 그린다."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        center = QPointF(w * 0.44, h * 0.48)
        radius = min(w * 0.34, h * 0.36)

        target = self._target_rect()
        # 지름에는 두께를 포함하지 않는다(검사 대상 자체의 크기) — 두께는
        # 따로 표기한다. 행(층)은 열(구간) 진행만으로는 알 수 없는
        # 정보라 같이 보여준다("몇층인지 알 수가 없음" 대응).
        target_text = (
            "검사 대상\n"
            f"Ø {self._target_diameter_m:.2f} m\n"
            f"두께 {self._target_thickness_mm:.0f} mm\n"
            f"H {self._target_height_m:.2f} m\n"
            f"{self._current_row} / {self._total_rows} 층"
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

        # Qt의 drawPie는 1/16도 단위를 쓰고, 각도는 3시 방향이 0 이며
        # **반시계**가 + 다. 차량이 반시계로 도니 조각도 그쪽으로 돌린다
        # (예전에는 `90 - index*30` 이라 조각만 시계 방향이었다 — 숫자와
        #  로봇은 반시계인데 조각만 반대로 가서 어긋나 보였다).
        #
        # 조각 수·간격은 **실제 열 수**를 따른다. 12 로 못박아 두면 3 열
        # 짜리 작업에서 12 칸 중 3 칸만 칠해져 나머지가 비어 보인다.
        total = max(1, self._state.total_segments)
        step = 360.0 / total
        # 조각 사이 간격 [도]. 칸이 많아지면 간격도 같이 줄여야 조각이
        # 사라지지 않는다.
        gap_deg = min(4.0, step * 0.2)
        span = (step - gap_deg) * 16
        gap = gap_deg * 16
        for index in range(total):
            start_deg = 90 + index * step - (step - gap_deg) / 2.0
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
                int(span),
            )

        # 채워진 부채꼴의 안쪽을 배경색 원으로 가려 검사 대상 주변에
        # 고리 모양의 진행 표시만 남긴다.
        painter.setBrush(QColor(COLORS["background"]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(center.x() - radius * 1.03, center.y() - radius * 1.03, radius * 2.06, radius * 2.06))
        painter.setBrush(QColor("#E4E7EA"))
        painter.setPen(QPen(QColor("#AAB3BC"), 1.5))
        painter.drawEllipse(target)
        painter.setPen(QColor(COLORS["text"]))
        painter.drawText(target, Qt.AlignmentFlag.AlignCenter, target_text)

        painter.setFont(QFont("Malgun Gothic", 9, 600))
        for index in range(total):
            # 화면 Y 가 아래로 커지므로 각도를 **빼야** 반시계 방향이다.
            # 차량은 원통을 반시계로 돈다 (1번이 12시, 2번이 그 왼쪽 …).
            angle = math.radians(-90 - index * step)
            x = center.x() + math.cos(angle) * radius * 1.28
            y = center.y() + math.sin(angle) * radius * 1.28
            painter.setPen(QColor(COLORS["text"]))
            painter.drawText(QRectF(x - 17, y - 12, 34, 24), Qt.AlignmentFlag.AlignCenter, f"{index + 1:02d}")

        # 1번 구간은 12시 방향에서 시작하며 이후 구간은 **반시계 방향**으로
        # 30도씩 동일하게 이동한다.
        robot_angle = math.radians(-90 - (self._state.current_segment - 1) * step)
        rx = center.x() + math.cos(robot_angle) * radius*0.9
        ry = center.y() + math.sin(robot_angle) * radius*0.9
        if not self._robot_pixmap.isNull():
            size = int(radius * 0.6)
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
