"""벽면 작업 영역(ㄹ자 스캔)을 사각형으로 그리는 위젯이다."""

from __future__ import annotations

from math import asin, ceil, floor, sin

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from smr_operator_ui.styles.tokens import COLORS


# 로봇이 좌우로 실제 이동하는 **현(弦)** 길이의 한계 [mm]. 호를 길게 잡을수록
# 양 끝 사이 직선거리가 멀어지는데, 이 값을 넘으면 굽은 벽에서 닿는다.
# 현은 벽 **바깥면**(반지름 + 두께) 기준으로 잰다.
MAX_PROBE_CHORD_MM = 700.0



def probe_chord_mm(arc_run_mm: float, radius_mm: float, thickness_mm: float) -> float:
    """TCP 가 호를 따라 arc_run 만큼 갔을 때 양 끝 사이의 실제 직선거리.

    프로브는 벽 **바깥면**(반지름 + 두께)을 타므로 그 반지름으로 잰다.
    """
    r = radius_mm + thickness_mm
    if r <= 0 or arc_run_mm <= 0:
        return 0.0
    return 2.0 * r * sin(arc_run_mm / (2.0 * r))


def max_safe_arc_mm(radius_mm: float, thickness_mm: float,
                    eoat_w_mm: float = 0.0) -> float:
    """현이 안전 한계를 넘지 않는 최대 호 길이(작업 영역 폭) [mm].

    chord = 2R·sin(arc_run / 2R) <= 한계  =>  arc_run <= 2R·asin(한계 / 2R).

    가운데 프로브 중심(= TCP)이 작업 영역 좌우 **끝까지** 가므로, 이동
    호 길이가 곧 작업 영역 폭이다. 예전에는 TCP 가 양 끝에서 EOAT 절반씩
    안쪽만 지난다고 보고 EOAT 폭을 더했지만 이제는 더하지 않는다
    (`eoat_w_mm` 은 옛 호출부 호환용으로 받기만 하고 쓰지 않는다).

    반지름을 모르면(0) 한계를 두지 않는다.
    """
    r = radius_mm + thickness_mm
    if r <= 0:
        return 0.0
    ratio = MAX_PROBE_CHORD_MM / (2.0 * r)
    if ratio >= 1.0:
        return 0.0          # 반지름이 아주 작으면 호 전체가 한계 안이다
    # 작업 호 길이는 레지스터 256 에 **정수 mm** 로 나가므로 버림한다.
    return float(floor(2.0 * r * asin(ratio)))


def plan_dimensions(
    width_mm: float, height_mm: float, scan_h_mm: float, overlap_mm: float,
    eoat_w_mm: float = 0.0, radius_mm: float = 0.0, thickness_mm: float = 0.0,
) -> dict:
    """로봇 태스크(dus_init.script)와 같은 규칙으로 피치·행수를 계산한다.

    상승 피치는 밑면(0)과 윗면(height)을 균등 분할해서 나온다 — 십자
    프로브의 가운데 프로브가 두 면을 직접 훑어야 하기 때문이다.
    격자 **안**의 겹침은 그 결과로 나오고, `overlap_mm`(격자끼리의 겹침)은
    쓰지 않는다.
    이 값은 화면 미리보기 전용이며, 실제 계산은 로봇 태스크가 한다.

    `eoat_w_mm` 은 이제 경로에 쓰지 않는다 — 좌우 끝도 프로브 중심
    기준이라 TCP 가 격자 폭을 그대로 오간다. 옛 호출부 호환으로 받기만
    한다.
    """
    width = width_mm if width_mm > 0 else 500.0
    height = max(height_mm, 0.0)
    scan_h = scan_h_mm if scan_h_mm > 0 else 200.0
    # 십자(+) 프로브는 **가운데 프로브**가 검사면을 훑는다. 그래서 맨 아랫줄
    # 에서는 ㅗ 자세로 가운데 프로브가 밑면(v=0)을, 맨 윗줄에서는 ㅜ 자세로
    # 윗면(v=height)을 지나야 한다. 줄 위치가 0..height 를 균등 분할한
    # 자리로 먼저 정해지고, **겹침은 그 결과로 나온다** — 더 이상 밖에서
    # 받은 겹침으로 피치를 정하지 않는다.
    #
    # 피치는 프로브 유효 세로 커버(scan_h)를 넘을 수 없다 — 넘으면 줄
    # 사이에 검사 안 된 띠가 남는다.
    #   5축 십자 : scan_h 30    -> 500mm 면 17칸(18줄), 피치 29.4
    #   8축 직사 : scan_h 167.5 -> 500mm 면  3칸( 4줄), 피치 166.7
    #
    # **`overlap_mm` 은 여기에 안 쓴다.** 그 값은 격자 안 줄 겹침이 아니라
    # **격자끼리**의 겹침 허용도라, 리프트가 다음 격자로 올라갈 때 얼마를
    # 덜 올라가는지를 정한다(JobSequencer.lift_pitch). 로봇도 같은 규칙이다
    # (dus_init.script). 옛 호출부 호환으로 받기만 한다.
    max_pitch = scan_h
    if height <= 0:
        rows, pitch = 1, 0.0
    else:
        # 줄 사이 간격 수. 이만큼 나눠야 피치가 한계 아래로 내려온다.
        gaps = int(ceil(height / max_pitch))
        rows = gaps + 1
        pitch = height / gaps
    overlap = max(scan_h - pitch, 0.0) if rows > 1 else 0.0
    top = (rows - 1) * pitch
    # TCP(= 가운데 프로브 중심)가 작업 영역 좌우 **끝까지** 간다. 예전에는
    # 프로브가 EOAT 폭만큼 퍼져 있다고 보고 양 끝에서 절반씩 물러났지만,
    # 이제 좌우 끝도 프로브 중심 기준이라 여백이 없다.
    arc_run = width
    # 로봇이 내보내는 가로 좌표는 호 길이가 아니라 **현**(두 끝점 사이
    # 직선거리 = cur-zero 의 Y 변위)이다. 굽은 벽을 폈을 때의 길이가
    # 아니라서 호보다 짧다(721 -> 699.3).
    chord = probe_chord_mm(arc_run, radius_mm, thickness_mm) or arc_run
    # 그림은 격자 폭을 꽉 채워 그린다. 로봇 좌표는 현 기준이므로 화면에
    # 얹을 때 width/chord 로 늘려 준다 — TPAC 이 호를 평면으로 펴서 보는
    # 것과 같은 그림이다. 값 계산·전달은 그대로 두고 그림만 맞춘다.
    draw_run = width
    return dict(width=width, height=height, scan_h=scan_h, overlap=overlap,
                pitch=pitch, rows=rows, top=top,
                covered=top + scan_h / 2.0,
                arc_run=arc_run, arc_margin=0.0,
                draw_run=draw_run, chord=chord)


def ideal_path(
    width_mm: float, height_mm: float, scan_h_mm: float, overlap_mm: float,
    eoat_w_mm: float = 0.0, radius_mm: float = 0.0, thickness_mm: float = 0.0,
):
    """원점(0,0) 기준 이상적인 ㄹ자 경로 [(가로 mm, 세로 mm), ...] 를 만든다.
    가로 값 자체는 원점(0)에서 잰 거리일 뿐이고, 화면에 그릴 때 어느 쪽이
    되는지는 RectWorkView.paintEvent()의 to_px()가 정한다(반시계 방향으로
    바꾼 뒤로는 원점이 **왼쪽 아래** 구석이다).
    세로 + = 위. dusan_map.py의 ideal_path()와 같은 규칙이다.

    가로는 격자 폭을 **끝에서 끝까지** 오간다 — 좌우 끝도 가운데 프로브
    중심 기준이라 TCP 가 직접 거기까지 간다.
    """
    p = plan_dimensions(width_mm, height_mm, scan_h_mm, overlap_mm, eoat_w_mm,
                        radius_mm, thickness_mm)
    margin = p["arc_margin"]
    # 세로는 **가운데 프로브**가 지나는 높이다. 맨 아랫줄은 ㅗ 자세로
    # 밑면을 직접 훑으므로 바닥(0)에서 시작하고, 맨 윗줄은 ㅜ 자세로
    # 윗면(height)에서 끝난다. 밴드(scan_h)는 그 선에서 위아래로 퍼지며
    # 격자 밖으로 조금 넘어간다 — 프로브가 모서리를 물고 있는 상태다.
    base_v = 0.0
    pts = [(margin, base_v)]
    h, v, sign = margin, base_v, 1
    for i in range(p["rows"]):
        h = margin + p["draw_run"] if sign > 0 else margin
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
        # 시험용 외벽 한 장을 격자 하나로 본다 (R834.6 / 호 978 / 높이 500 / 두께 10).
        # 가로는 **호 길이**다 — 로봇이 movec 로 호를 따라가므로 실제 이동
        # 거리이기도 하다. 예전엔 1110(현 1031.8)이었는데 양 끝점이 너무
        # 벌어져 충돌 위험이 있어 줄였다 — 좌우 끝점 사이 실제 현이 700mm
        # 를 안 넘는 최대 정수 호 길이(721 -> 현 699.31mm). 좌우 끝도
        # 프로브 중심 기준이라 TCP 가 이 폭을 그대로 오간다.
        # 로봇 쪽 app_arc 샘플·MQTT 시뮬레이터 샘플과 같은 값이다.
        self._width_mm = 721.0
        self._height_mm = 500.0
        # 한 줄이 덮는 밴드 = 기본 EOAT(5축 십자형)의 **가운데 프로브 센서
        # 지름** 30mm. 이 값이 곧 up 동작의 최대 상승량이다.
        # EOAT 를 고르면 그 세로로 덮어쓴다 (app.py `_scan_band_mm`).
        self._scan_h_mm = 30.0
        self._overlap_mm = 0.0
        # 프로브 유효 가로 커버. 경로에는 쓰지 않고(좌우 끝도 프로브 중심
        # 기준이다) 값만 들고 있는다.
        self._eoat_w_mm = 30.0
        # 경로를 **현** 으로 그리는 데 쓴다(plan_dimensions 의 draw_run 참고).
        self._radius_mm = 834.6
        self._thickness_mm = 10.0
        self._pos_h_mm = 0.0
        self._pos_v_mm = 0.0
        self._has_position = False
        self._cell_label = ""
        # 최소 폭은 실제로 쓸 수 있는 공간에 맞춘다.
        # 메인 화면은 1280px 고정인데 우측 레일(340)·속도 바(112)·여백을
        # 빼면 원통·작업영역 둘이 나눠 쓸 폭이 780 밖에 없다. 둘 다 500을
        # 요구하면 1014 가 되어 234px 이 창 밖으로 밀려 오른쪽이 잘린다.
        self.setMinimumSize(360, 330)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("작업 영역을 클릭하여 크기를 설정하세요.")

    def set_work_area(
        self, width_mm: float, height_mm: float, scan_h_mm: float, overlap_mm: float,
        eoat_w_mm: float | None = None,
        radius_mm: float | None = None, thickness_mm: float | None = None,
    ) -> None:
        """작업 영역 치수를 갱신한다. 단위는 mm이며 Modbus 256~259와 같다.

        `eoat_w_mm`을 주지 않으면(None) 이전 값을 그대로 유지한다 — 이
        위젯을 부르는 곳 중에는 아직 EOAT 폭을 모르는 경로(예: 설정 저장소
        복원)도 있어서, 매번 0으로 리셋되면 안 되기 때문이다.
        """
        self._width_mm = width_mm
        self._height_mm = height_mm
        self._scan_h_mm = scan_h_mm
        self._overlap_mm = overlap_mm
        if eoat_w_mm is not None:
            self._eoat_w_mm = eoat_w_mm
        if radius_mm is not None:
            self._radius_mm = radius_mm
        if thickness_mm is not None:
            self._thickness_mm = thickness_mm
        self.update()

    def work_area(self) -> tuple[float, float, float, float]:
        """현재 너비/높이/스캐너 높이/겹침을 mm로 반환한다."""
        return self._width_mm, self._height_mm, self._scan_h_mm, self._overlap_mm

    def set_position(self, horizontal_mm: float, vertical_mm: float) -> None:
        """원점 기준 현재 위치를 표시한다. 가로는 원점에서 잰 거리(화면상 어느
        쪽인지는 to_px()가 정한다 — 지금은 원점이 오른쪽 끝), 세로 + = 위."""
        self._pos_h_mm, self._pos_v_mm = horizontal_mm, vertical_mm
        self._has_position = True
        self.update()

    def set_cell_label(self, text: str) -> None:
        """현재 스캔 중인 격자 이름을 표시한다. 예: "A0 (1/9)"."""
        self._cell_label = text
        self.update()

    def park_position(self) -> None:
        """대기 자리(ㄹ자 시작점)로 되돌린다.

        로봇이 좌표를 그만 보내면(태스크 중단·정지) 마지막 값이 화면에
        그대로 남아 지금도 거기 있는 것처럼 보인다. 멈춘 것을 알아챈 쪽이
        이걸 불러 준다(app.py 의 alive 감시). (0, 0) 은 "스캔 중이 아님"을
        뜻하고, 그리는 쪽이 그 값을 ㄹ자 시작점에 세운다.
        """
        self._pos_h_mm = 0.0
        self._pos_v_mm = 0.0
        self.update()

    def clear_position(self) -> None:
        """스캔 중이 아닐 때는 위치 표시를 지운다."""
        self._has_position = False
        self.update()

    def _plot_rect(self) -> QRectF:
        """작업 영역을 그릴 사각형. **주어진 칸을 그대로 채운다.**

        실제 치수 비율(예: 1110 × 500 = 2.2:1)을 그대로 지키면 격자 크기가
        바뀔 때마다 그림이 널뛰고, 가로로 긴 영역은 패널을 넘어가 잘린다.
        이 그림은 ㄹ자가 몇 줄인지·겹침이 어디인지 읽으라고 있는 것이지
        실제 형상을 재라고 있는 게 아니다. 그래서 가로세로를 **따로**
        늘려 칸을 채운다(축척이 서로 다르다 — 길이를 눈대중하면 안 된다).
        """
        top_margin, bottom_margin, side_margin = 74, 26, 20
        return QRectF(
            side_margin,
            top_margin,
            max(self.width() - side_margin * 2, 10),
            max(self.height() - top_margin - bottom_margin, 10),
        )

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

        _pts, plan = ideal_path(
            self._width_mm, self._height_mm, self._scan_h_mm, self._overlap_mm,
            self._eoat_w_mm, self._radius_mm, self._thickness_mm)
        rect = self._plot_rect()
        # 가로세로 축척이 다르다. 칸을 꽉 채우려는 것이라 의도한 것이다.
        # 밴드는 격자 위아래로 scan_h/2 씩 넘어가지만 사각형은 격자
        # 그대로 그린다 — 넘어가는 부분은 검사 대상이 아니다.
        extent_v = max(plan["height"], 1.0)
        scale_x = rect.width() / plan["width"] if plan["width"] else 1.0
        scale_y = rect.height() / extent_v

        def to_px(h_mm: float, v_mm: float) -> tuple[float, float]:
            # 세로는 위로 갈수록 값이 커지므로 화면 y와는 반대 방향이다.
            #
            # 가로는 **뒤집지 않는다.** 스캔을 반시계 방향으로 바꾸면서
            # 원점이 호의 반대쪽 끝(베이스 Y 가 큰 쪽)으로 옮겨 갔고
            # (dus_probe_l.script), 로봇도 거기서 반대 방향으로 훑는다.
            # 그래서 h_mm=0(원점)이 화면 **왼쪽 아래** 구석이고 ㄹ자는
            # 왼쪽에서 오른쪽으로 뻗어 나간다. 여기 하나만 두면 원점
            # 표시·경로·실시간 위치 점이 모두 같은 기준으로 그려진다.
            x = rect.left() + h_mm * scale_x
            y = rect.bottom() - v_mm * scale_y
            return x, y

        # 제목 + 계산값. 제목은 굵게 크게, 수치는 폭이 좁아도 잘리지 않도록
        # 작은 글꼴에 줄바꿈을 허용해 따로 그린다.
        text_area = QRectF(6, 8, max(self.width() - 12, 10), 60)
        painter.setPen(QColor(COLORS["text"]))
        painter.setFont(QFont("Malgun Gothic", 12, 600))
        title = f"작업 영역  {self._cell_label}" if self._cell_label else "작업 영역"
        painter.drawText(
            QRectF(text_area.x(), text_area.y(), text_area.width(), 20),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, title,
        )
        painter.setFont(QFont("Malgun Gothic", 9))
        painter.drawText(
            QRectF(text_area.x(), text_area.y() + 22, text_area.width(), 40),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
            f"{plan['width']:.0f} × {plan['height']:.0f} mm  "
            f"(스캐너 {plan['scan_h']:.0f} / 겹침 {plan['overlap']:.1f} mm 자동)\n"
            f"피치 {plan['pitch']:.1f} mm  {plan['rows']} 행",
        )

        # 작업 영역 사각형
        painter.setPen(QPen(QColor("#AAB3BC"), 1.5))
        painter.setBrush(QColor("#E4E7EA"))
        painter.drawRect(rect)

        # 겹침 구간 음영: 인접한 두 패스의 스캐너 밴드가 겹치는 부분
        if plan["overlap"] > 0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLORS["pending"]))
            half = plan["scan_h"] / 2.0
            for row in range(1, plan["rows"]):
                # 아래 줄 밴드의 위 끝과 위 줄 밴드의 아래 끝이 겹치는 구간.
                band_bottom = row * plan["pitch"] - half
                band_top = (row - 1) * plan["pitch"] + half
                band_bottom = max(band_bottom, 0.0)
                band_top = min(band_top, plan["height"])
                if band_top <= band_bottom:
                    continue
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

        def robot_to_px(h_mm: float, v_mm: float) -> tuple[float, float]:
            """로봇이 알려 주는 원점 기준 좌표를 화면 좌표로 바꾼다.

            가로: 로봇은 제로점 기준 Y 변위, 즉 **현**(두 끝점 사이 직선
            거리)을 보낸다. 그림은 격자 폭(호를 편 길이)으로 그리므로
            width/chord 만큼 늘려 얹는다 — TPAC 이 호를 평면으로 펴서
            보는 것과 같다. 값 계산·전달은 그대로 둔다.

            세로: 로봇의 0 은 첫 줄(ㅗ 자세로 밑면을 훑는 줄)의 스캔선
            이고, 그림에서도 그 줄이 바닥(0)이다. 그대로 쓴다.

            스캔이 아닐 때 로봇은 (0, 0) 을 보내는데, 그 자리가 곧 ㄹ자가
            시작하는 자리(오른쪽 아래 구석)라 따로 손댈 것이 없다.
            """
            chord = plan["chord"]
            if chord > 0:
                h_mm = h_mm * plan["draw_run"] / chord
            return to_px(h_mm, v_mm)

        # 원점 표시. 로봇이 알려 주는 좌표가 아니라 **작업 영역 자체의
        # 기준점**이라 그림 좌표 (0, 0) 을 그대로 쓴다 — 화면에서는
        # 왼쪽 아래 구석이다("스캔 영역의 원점이 원점").
        ox, oy = to_px(0, 0)
        # 구석에 정확히 중심을 두면 원이 절반 잘려 나간다. 반지름만큼
        # 안쪽으로 붙여 구석에 닿은 채로 온전히 보이게 한다.
        radius = 4.0
        ox = max(ox, rect.left() + radius)
        oy = min(oy, rect.bottom() - radius)
        painter.setPen(QPen(QColor(COLORS["text"]), 1))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(QRectF(ox - radius, oy - radius, radius * 2, radius * 2))

        # 현재 위치
        if self._has_position:
            px, py = robot_to_px(self._pos_h_mm, self._pos_v_mm)
            # 치수가 바뀐 직후 등 옛 좌표가 범위를 벗어나면 그림 밖(제목
            # 위)까지 그려진다. 점이 통째로 보이도록 반지름만큼 안쪽으로
            # 눌러 둔다.
            mark_r = 8.0
            px = min(max(px, rect.left() + mark_r), rect.right() - mark_r)
            py = min(max(py, rect.top() + mark_r), rect.bottom() - mark_r)
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
