"""운영 화면에서 공통으로 사용하는 소형 위젯을 제공한다."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


class FitLabel(QLabel):
    """폭이 모자라면 글자를 줄여 잘리지 않게 하는 라벨.

    스타일시트가 정한 크기(`base_px`)를 최대로 삼고, 지금 폭에 안 들어가면
    `min_px` 까지 줄인다. 전체화면에서는 창 배율(`set_scale`)만큼 기준이
    커진다 — 나머지 글자와 같이 커져야 어색하지 않다.

    창을 작게 줄이면 상단 요약 칸 넷이 들어갈 폭이 안 나오는데, 글자
    크기가 고정이라 칸이 통째로 창 밖으로 밀려 잘렸다("전체화면에서만
    글자가 안 잘리네"). 글자가 먼저 줄어들면 칸도 같이 좁아진다.
    """

    def __init__(self, text: str = "", base_px: int = 34, min_px: int = 15,
                 parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._base_px = base_px
        self._min_px = min_px
        self._scale = 1.0
        # sizeHint 는 그대로 두고(그래야 자기 글자 폭만큼 자리를 받는다)
        # **최소** 폭만 0 으로 연다 — minimumSizeHint 를 손대지 않으면
        # QLabel 이 글자 전체 폭을 최소로 요구해서 칸이 줄어들 수가 없다.
        #
        # 예전에 SizePolicy 를 Ignored 로 뒀더니 sizeHint 까지 무시되어,
        # 옆의 addStretch() 가 공간을 다 가져가고 라벨 폭이 0 이 됐다
        # (요약 칸 값이 통째로 안 보였다).
        self.setMinimumWidth(0)
        self._refit()

    def minimumSizeHint(self):  # noqa: N802 - Qt 이름 규칙
        """폭은 0 까지 줄어도 된다 — 글자가 대신 작아진다."""
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())

    def set_scale(self, scale: float) -> None:
        """창 배율을 받는다. 기준 글자 크기가 그만큼 커진다."""
        self._scale = max(0.1, float(scale))
        self._refit()

    def setText(self, text: str) -> None:  # noqa: N802 - Qt 이름 규칙
        super().setText(text)
        self._refit()

    def resizeEvent(self, event) -> None:  # noqa: N802, ANN001
        super().resizeEvent(event)
        self._refit()

    def _refit(self) -> None:
        text = self.text()
        ceiling = max(self._min_px, round(self._base_px * self._scale))
        available = self.width()
        font = self.font()
        if not text or available <= 0:
            font.setPixelSize(ceiling)
            self.setFont(font)
            return
        for px in range(ceiling, self._min_px - 1, -1):
            font.setPixelSize(px)
            if QFontMetrics(font).horizontalAdvance(text) <= available:
                break
        self.setFont(font)
        self.updateGeometry()


class ConnectionBadge(QFrame):
    """장비 이름과 연결 상태를 간결하게 표시한다.

    대부분은 아직 실제 장비 상태에 연결되어 있지 않아 표시만 하는
    자리표시자다(항상 "연결됨"). `set_connected()`로 실제 상태를 반영할
    수 있게 해 뒀다 — 실제 장비가 붙어 있는 항목(예: TPAC)부터 쓴다.
    """

    #: 폭을 맞출 때 기준으로 삼는 상태 문구. 어떤 상태가 들어와도 배지
    #: 크기가 변하지 않게 **가장 긴 문구**로 폭을 잡아 둔다.
    STATE_TEXTS = ("연결됨", "연결 안 됨")

    def __init__(self, name: str, initial_connected: bool = True,
                parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(1)
        self.name_label = QLabel(name)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_label = QLabel()
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.name_label)
        layout.addWidget(self.state_label)
        self._state_text = ""
        self.set_connected(initial_connected)

    def set_state(self, text: str, kind: str = "StatusWarn") -> None:
        """연결 여부가 아닌 상태를 직접 쓴다(예: 차량이 '더미'일 때)."""
        self._show(text, kind)

    def set_connected(self, connected: bool, label: str | None = None) -> None:
        """연결 여부를 반영한다. `label`을 주면 문구를 대신 쓴다(예: 상태 불명)."""
        self._show(label or ("연결됨" if connected else "연결 안 됨"),
                   "StatusGood" if connected else "StatusDanger")

    def _show(self, text: str, kind: str) -> None:
        self._state_text = text
        self.state_label.setText(f"● {text}")
        self.state_label.setObjectName(kind)
        self.state_label.style().unpolish(self.state_label)
        self.state_label.style().polish(self.state_label)
        self.sync_width()

    def sync_width(self) -> None:
        """배지 폭을 장비마다 똑같이 맞춘다.

        글자 길이대로 두면 "연결됨"과 "연결 안 됨"의 폭이 달라 배지가
        들쭉날쭉하고, 상태가 바뀔 때마다 옆 배지가 밀린다. 이름과 상태
        문구 중 **가장 넓은 것**에 맞춰 고정 폭을 준다 — 상태 문구가
        이름보다 늘 넓으므로 결과적으로 모든 배지가 같은 폭이 된다.
        글자 크기가 바뀌면(상단바 축소) 다시 불러야 한다.
        """
        margins = self.layout().contentsMargins()
        state = max(self.state_label.fontMetrics().horizontalAdvance(f"● {text}")
                    for text in (*self.STATE_TEXTS, self._state_text))
        name = self.name_label.fontMetrics().horizontalAdvance(self.name_label.text())
        self.setFixedWidth(max(state, name) + margins.left() + margins.right() + 2)


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
