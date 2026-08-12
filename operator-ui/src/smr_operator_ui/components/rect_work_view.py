"""벽면 작업 영역(ㄹ자 스캔)을 사각형으로 그리는 위젯이다."""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from smr_operator_ui.styles.tokens import COLORS


def plan_dimensions(width_mm: float, height_mm: float, scan_h_mm: float, overlap_mm: float) -> dict:
    """로봇 태스크(dus_init.script)와 같은 규칙으로 피치·행수를 계산한다.

    상승 피치는 항상 (스캐너 높이 - 겹침) 고정이다. 균등 재분배하지 않는다.
    이 값은 화면 미리보기 전용이며, 실제 계산은 로봇 태스크가 한다.
    """
    width = width_mm if width_mm > 0 else 500.0
    height = max(height_mm, 0.0)
    scan_h = scan_h_mm if scan_h_mm > 0 else 200.0
    overlap = max(overlap_mm, 0.0)
    if overlap >= scan_h:
        overlap = scan_h - 10.0
    pitch = scan_h - overlap
    if pitch <= 0:
        pitch = 10.0
    rows = 1
    if height > scan_h:
        while (rows - 1) * pitch + scan_h < height:
            rows += 1
    top = (rows - 1) * pitch
    return dict(width=width, height=height, scan_h=scan_h, overlap=overlap,
                pitch=pitch, rows=rows, top=top, covered=top + scan_h)


def ideal_path(width_mm: float, height_mm: float, scan_h_mm: float, overlap_mm: float):
    """원점(0,0) 기준 이상적인 ㄹ자 경로 [(가로 mm, 세로 mm), ...] 를 만든다.
    가로 + = 오른쪽, 세로 + = 위. dusan_map.py의 ideal_path()와 같은 규칙이다."""
    p = plan_dimensions(width_mm, height_mm, scan_h_mm, overlap_mm)
    pts = [(0.0, 0.0)]
    h, v, sign = 0.0, 0.0, 1
    for i in range(p["rows"]):
        h = p["width"] if sign > 0 else 0.0
        pts.append((h, v))
        sign = -sign
        if i < p["rows"] - 1:
            v += p["pitch"]
            pts.append((h, v))
    return pts, p


class RectWorkView(QWidget):
    """작업 영역 사각형과 ㄹ자 이동 경로, 현재 위치를 표시한다."""

    target_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._width_mm = 600.0
        self._height_mm = 800.0
        self._scan_h_mm = 150.0
        self._overlap_mm = 20.0
        self._pos_h_mm = 0.0
        self._pos_v_mm = 0.0
        self._has_position = False
        self._cell_label = ""
        self.setMinimumSize(500, 330)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("작업 영역을 클릭하여 크기를 설정하세요.")

    def set_work_area(self, width_mm: float, height_mm: float, scan_h_mm: float, overlap_mm: float) -> None:
        """작업 영역 치수를 갱신한다. 단위는 mm이며 Modbus 256~259와 같다."""
        self._width_mm = width_mm
        self._height_mm = height_mm
        self._scan_h_mm = scan_h_mm
        self._overlap_mm = overlap_mm
        self.update()

    def work_area(self) -> tuple[float, float, float, float]:
        """현재 너비/높이/스캐너 높이/겹침을 mm로 반환한다."""
        return self._width_mm, self._height_mm, self._scan_h_mm, self._overlap_mm

    def set_position(self, horizontal_mm: float, vertical_mm: float) -> None:
        """원점 기준 현재 위치를 표시한다. 가로 + = 오른쪽, 세로 + = 위."""
        self._pos_h_mm, self._pos_v_mm = horizontal_mm, vertical_mm
        self._has_position = True
        self.update()

    def set_cell_label(self, text: str) -> None:
        """현재 스캔 중인 격자 이름을 표시한다. 예: "A0 (1/9)"."""
        self._cell_label = text
        self.update()

    def clear_position(self) -> None:
        """스캔 중이 아닐 때는 위치 표시를 지운다."""
        self._has_position = False
        self.update()

    def _plot_rect(self, plan: dict) -> QRectF:
        """작업 영역을 그릴 사각형 영역(위젯 좌표)을 계산한다."""
        top_margin, bottom_margin, side_margin = 74, 26, 20
        avail_w = max(self.width() - side_margin * 2, 10)
        avail_h = max(self.height() - top_margin - bottom_margin, 10)
        extent_v = max(plan["height"], plan["covered"], 1.0)
        scale = min(avail_w / plan["width"], avail_h / extent_v)
        w = plan["width"] * scale
        h = extent_v * scale
        x = side_margin + (avail_w - w) / 2
        y = top_margin + (avail_h - h)
        return QRectF(x, y, w, h)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        """빈 곳 어디를 눌러도 크기 입력 대화상자를 연다."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.target_clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        _pts, plan = ideal_path(self._width_mm, self._height_mm, self._scan_h_mm, self._overlap_mm)
        rect = self._plot_rect(plan)
        scale = rect.width() / plan["width"] if plan["width"] else 1.0

        def to_px(h_mm: float, v_mm: float) -> tuple[float, float]:
            # 세로는 위로 갈수록 값이 커지므로 화면 y와는 반대 방향이다.
            x = rect.left() + h_mm * scale
            y = rect.bottom() - v_mm * scale
            return x, y

        # 제목 + 계산값
        title_rect = QRectF(0, 8, self.width(), 60)
        painter.setPen(QColor(COLORS["text"]))
        painter.setFont(QFont("Malgun Gothic", 12, 600))
        title = f"작업 영역  {self._cell_label}" if self._cell_label else "작업 영역"
        painter.drawText(
            title_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            f"{title}\n"
            f"{plan['width']:.0f} × {plan['height']:.0f} mm  "
            f"(스캐너 {plan['scan_h']:.0f} / 겹침 {plan['overlap']:.0f} mm)\n"
            f"피치 {plan['pitch']:.0f} mm  {plan['rows']} 행",
        )

        # 작업 영역 사각형
        painter.setPen(QPen(QColor("#AAB3BC"), 1.5))
        painter.setBrush(QColor("#E4E7EA"))
        painter.drawRect(rect)

        # 겹침 구간 음영: 인접한 두 패스의 스캐너 밴드가 겹치는 부분
        if plan["overlap"] > 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLORS["pending"]))
            for row in range(1, plan["rows"]):
                band_bottom = row * plan["pitch"]
                band_top = band_bottom + plan["overlap"]
                x0, y0 = to_px(0, band_top)
                x1, y1 = to_px(plan["width"], band_bottom)
                painter.drawRect(QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)))

        # 이상적인 ㄹ자 경로
        path = QPainterPath()
        for index, (h_mm, v_mm) in enumerate(_pts):
            x, y = to_px(h_mm, v_mm)
            path.moveTo(x, y) if index == 0 else path.lineTo(x, y)
        pen = QPen(QColor(COLORS["primary"]), 3, Qt.PenStyle.SolidLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        # 원점 표시
        ox, oy = to_px(0, 0)
        painter.setPen(QPen(QColor(COLORS["text"]), 1))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(QRectF(ox - 4, oy - 4, 8, 8))

        # 현재 위치
        if self._has_position:
            px, py = to_px(self._pos_h_mm, self._pos_v_mm)
            painter.setPen(QPen(QColor("#FFFFFF"), 2))
            painter.setBrush(QColor(COLORS["warning"]))
            painter.drawEllipse(QRectF(px - 8, py - 8, 16, 16))

        # 범례
        legend_y = self.height() - 22
        painter.setFont(QFont("Malgun Gothic", 9))
        painter.setPen(QColor(COLORS["primary"]))
        painter.drawText(QRectF(18, legend_y - 3, 70, 18), Qt.AlignmentFlag.AlignLeft, "─ 경로")
        painter.setPen(QColor(COLORS["text"]))
        painter.drawText(QRectF(94, legend_y - 3, 90, 18), Qt.AlignmentFlag.AlignLeft, "○ 원점")
        if self._has_position:
            painter.setPen(QColor(COLORS["warning"]))
            painter.drawText(QRectF(170, legend_y - 3, 90, 18), Qt.AlignmentFlag.AlignLeft, "● 현재 위치")
