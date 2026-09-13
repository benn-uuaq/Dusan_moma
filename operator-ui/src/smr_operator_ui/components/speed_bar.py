"""로봇 동작 속도 비율을 조절하는 세로 슬라이더."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QLabel, QSlider, QVBoxLayout, QWidget

# 로봇 컨트롤러가 받는 범위. 0 %는 정지가 아니라 거부이므로 2가 하한이다.
SPEED_MIN, SPEED_MAX = 2, 100
# 안전 기준상 TCP 직선 속도 상한. 로봇 태스크(dus_init.script)도 같은 값으로 자른다.
MAX_LINEAR_SPEED_MM_S = 150


class SpeedBar(QFrame):
    """세로 슬라이더로 로봇 전체 동작 속도 비율(2~100 %)을 조절한다.

    슬라이더를 놓았을 때만 값을 내보낸다. 끄는 동안 매 픽셀마다 명령을
    보내면 29999가 밀리기 때문이다.

    로봇이 실제로 쓰고 있는 값은 `set_actual()`로 되돌려 받는다. 이때는
    시그널을 막아, 표시를 갱신하다가 다시 명령이 나가는 되먹임을 끊는다.
    """

    speed_changed = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SpeedBar")
        # 폭은 app.qss의 QFrame#SpeedBar min/max-width로 고정한다. 여기서
        # setFixedWidth로 못박으면 전체 UI 배율(전체화면 등)이 커져도 이
        # 폭만 그대로라 "150 mm/s" 글자가 패널 밖으로 잘린다.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(8)

        caption = QLabel("로봇\n속도")
        caption.setObjectName("SectionTitle")
        caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(caption)

        self.slider = QSlider(Qt.Orientation.Vertical)
        self.slider.setObjectName("SpeedSlider")
        self.slider.setRange(SPEED_MIN, SPEED_MAX)
        self.slider.setValue(SPEED_MAX)
        self.slider.setPageStep(10)
        # 100 % 일 때의 TCP 직선 속도 [mm/s]. 작업 속도 설정에서 받아 갱신한다.
        self._base_mm_s = float(MAX_LINEAR_SPEED_MM_S)
        self.slider.setTickPosition(QSlider.TickPosition.TicksLeft)
        self.slider.setTickInterval(10)
        layout.addWidget(self.slider, 1, Qt.AlignmentFlag.AlignHCenter)

        self.value_label = QLabel(f"{SPEED_MAX} %")
        self.value_label.setObjectName("SpeedValue")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.value_label)

        # 안전 상한(150 mm/s)을 곱해 실제 몇 mm/s 로 도는지 함께 보여준다.
        # % 만으로는 안전 기준을 넘는지 아닌지 눈으로 알 수 없다.
        self.mms_label = QLabel("- mm/s")
        self.mms_label.setObjectName("SectionTitle")
        self.mms_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.mms_label)

        # 끄는 동안은 숫자만 따라오고, 놓을 때 한 번 명령을 보낸다.
        self.slider.valueChanged.connect(self._show_value)
        self.slider.sliderReleased.connect(self._emit_value)
        # 초기 setValue 는 연결 전에 일어나 라벨이 갱신되지 않는다. 한 번 채운다.
        self._show_value(self.slider.value())

    def _show_value(self, value: int) -> None:
        self.value_label.setText(f"{value} %")
        self.mms_label.setText(f"{self._base_mm_s * value / 100:.0f} mm/s")

    def _emit_value(self) -> None:
        self.speed_changed.emit(self.slider.value())

    def set_base_speed(self, mm_s: float) -> None:
        """100 % 일 때의 TCP 속도(mm/s). 작업 속도 설정이 바뀌면 불린다."""
        self._base_mm_s = max(0.0, min(float(mm_s), MAX_LINEAR_SPEED_MM_S))
        self._show_value(self.slider.value())

    def value(self) -> int:
        """지금 슬라이더가 가리키는 비율."""
        return self.slider.value()

    def set_actual(self, percent: int) -> None:
        """로봇이 실제로 쓰고 있는 비율을 반영한다.

        펜던트나 외부 MQTT로 바뀐 값도 여기로 들어온다. 슬라이더를 끄는
        중이면 손을 방해하지 않도록 갱신을 건너뛴다. 우측 레일의 "로봇 속도"
        항목을 뺀 뒤로는 이 바가 실제 비율을 보여 주는 유일한 자리다.
        """
        percent = int(percent)
        if not SPEED_MIN <= percent <= SPEED_MAX:
            # 범위 밖은 "값 없음"이다. 하한으로 눌러 버리면 로봇이 2 % 로
            # 도는 것처럼 보인다.
            return
        if self.slider.isSliderDown():
            return
        blocked = self.slider.blockSignals(True)
        self.slider.setValue(percent)
        self.slider.blockSignals(blocked)
        self._show_value(percent)
