"""터치 전용 숫자 키패드와 가상 키보드 공통 위젯을 제공한다."""

from __future__ import annotations

import atexit

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollBar,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class WheelGuard(QObject):
    """마우스 휠로 입력값이 바뀌지 않게 막는다 (앱 전체).

    값은 **클릭해서 키패드·키보드로 넣는 것만** 허용한다. Qt 기본값으로는
    스핀박스·콤보박스·슬라이더 위에서 휠을 굴리면 값이 바뀌어, 화면을
    스크롤하려다 설정값이나 로봇 속도(속도 바 슬라이더)가 슬쩍 바뀌었다.

    휠은 버리지 않고 **바깥 스크롤 영역으로 넘긴다** — 입력칸 위에 마우스가
    있어도 페이지는 그대로 스크롤된다. 스크롤바 자체는 막지 않는다.
    """

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.Wheel or not isinstance(watched, QWidget):
            return False
        if not _wheel_changes_value(watched):
            return False
        area = _scroll_area_of(watched)
        if area is not None:
            QApplication.sendEvent(area.verticalScrollBar(), event)
        return True     # 값은 안 바꾼다


def _wheel_changes_value(widget: QWidget) -> bool:
    """휠이 값을 바꾸는 위젯(또는 그 안쪽 입력부)인가."""
    w: QWidget | None = widget
    # 자기 자신과 바로 위 하나만 본다 — 스핀박스·콤보의 안쪽 QLineEdit 까지.
    # 더 올라가면 펼친 콤보 목록 안의 스크롤까지 막힌다.
    for _ in range(2):
        if w is None:
            return False
        if isinstance(w, (QAbstractSpinBox, QComboBox)):
            return True
        if isinstance(w, QAbstractSlider) and not isinstance(w, QScrollBar):
            return True
        w = w.parentWidget()
    return False


def _scroll_area_of(widget: QWidget) -> QAbstractScrollArea | None:
    w = widget.parentWidget()
    while w is not None:
        if isinstance(w, QAbstractScrollArea) and not isinstance(w, QComboBox):
            return w
        w = w.parentWidget()
    return None


def install_wheel_guard(app: QApplication | None = None) -> None:
    """앱에 WheelGuard 를 한 번만 건다. 여러 번 불러도 된다."""
    app = app or QApplication.instance()
    if app is None or getattr(app, "_smr_wheel_guard", None) is not None:
        return
    guard = WheelGuard(app)
    app.installEventFilter(guard)
    app._smr_wheel_guard = guard
    # 파이썬이 끝날 때 Qt 가 앱을 허무는 도중에도 이벤트가 오는데, 그때
    # 이미 반쯤 정리된 파이썬 필터를 부르면 죽는다(세그폴트). 파이썬이
    # 정리를 시작하기 전에 떼어 둔다.
    atexit.register(_remove_wheel_guard, app, guard)


def _remove_wheel_guard(app: QApplication, guard: QObject) -> None:
    try:
        app.removeEventFilter(guard)
    except RuntimeError:        # 앱이 이미 없어졌다
        pass


def _disarm_enter(button: QPushButton) -> None:
    """물리 Enter 가 이 버튼을 대신 눌러 버리는 것을 막는다.

    QDialog 안의 QPushButton 은 autoDefault 가 기본으로 켜져 있어서, 포커스를
    가진 버튼이 Enter 를 가로채 스스로를 누른다. 액션 버튼(취소/확인)만
    포커스를 받을 수 있게 남아 있었던 탓에 **첫 포커스가 "취소"에 가서
    Enter 를 누르면 입력이 반영되지 않은 채 창만 닫혔다.** 포커스도 받지
    않고 autoDefault 도 끄면 Enter 가 다이얼로그의 keyPressEvent 까지
    내려와 "확인"과 같은 동작(_confirm)을 한다.
    """
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    button.setAutoDefault(False)
    button.setDefault(False)


class NumericKeypadDialog(QDialog):
    """허용 범위를 검증하는 터치 중심 숫자 입력 창."""

    def __init__(
        self,
        value: float,
        minimum: float,
        maximum: float,
        decimals: int,
        unit: str = "",
        title: str = "숫자 입력",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.minimum = minimum
        self.maximum = maximum
        self.decimals = decimals
        self.result_value = value
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)

        root = QVBoxLayout(self)
        self.display = QLineEdit(self._format(value))
        self.display.setReadOnly(True)
        # display가 초기 포커스를 가져가면(읽기 전용이라도 기본 포커스
        # 정책은 그대로 남는다) 물리 Backspace가 이 QLineEdit로 먼저
        # 전달돼 아래 dialog.keyPressEvent(실제 _backspace 처리)까지
        # 오지 않는 경우가 있었다("입력은 되는데 백스페이스만 안 먹힘").
        # 애초에 커서를 둘 일이 없는 표시 전용 칸이므로 포커스 자체를
        # 받지 않게 해, 모든 키 입력이 항상 다이얼로그로 온다.
        self.display.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.display.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.display.setObjectName("KeypadDisplay")
        root.addWidget(self.display)

        range_text = f"허용 범위: {self._format(minimum)} ~ {self._format(maximum)}{unit}"
        self.guide = QLabel(range_text)
        self.guide.setObjectName("Muted")
        self.guide.setWordWrap(True)
        root.addWidget(self.guide)
        self.error = QLabel("")
        self.error.setObjectName("StatusDanger")
        self.error.setWordWrap(True)
        root.addWidget(self.error)

        grid = QGridLayout()
        grid.setSpacing(8)
        for text, row, column in (
            ("7", 0, 0), ("8", 0, 1), ("9", 0, 2), ("⌫", 0, 3),
            ("4", 1, 0), ("5", 1, 1), ("6", 1, 2), ("전체 지우기", 1, 3),
            ("1", 2, 0), ("2", 2, 1), ("3", 2, 2), ("±", 2, 3),
            ("0", 3, 0), ("00", 3, 1), (".", 3, 2),
        ):
            button = QPushButton(text)
            button.setMinimumSize(72, 64)
            # 버튼을 마우스로 눌러 포커스를 가져간 뒤에도 같은 이유로
            # 물리 키보드 입력이 그 버튼에 막히지 않게 한다.
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            if text == "⌫":
                button.clicked.connect(self._backspace)
            elif text == "전체 지우기":
                button.clicked.connect(self._clear)
            elif text == "±":
                button.clicked.connect(self._toggle_sign)
            else:
                button.clicked.connect(lambda _checked=False, token=text: self._append(token))
            grid.addWidget(button, row, column)
        root.addLayout(grid)

        actions = QHBoxLayout()
        cancel = QPushButton("취소")
        confirm = QPushButton("확인")
        confirm.setObjectName("PrimaryAction")
        cancel.clicked.connect(self.reject)
        confirm.clicked.connect(self._confirm)
        for button in (cancel, confirm):
            _disarm_enter(button)
        actions.addWidget(cancel)
        actions.addWidget(confirm)
        root.addLayout(actions)

    def _format(self, value: float) -> str:
        return f"{value:.{self.decimals}f}"

    def _append(self, token: str) -> None:
        """소수점 중복을 막으면서 유효한 숫자 토큰을 추가한다."""
        current = self.display.text()
        if token == "." and (self.decimals == 0 or "." in current):
            return
        if current in ("0", "-0") and token != ".":
            current = "-" if current.startswith("-") else ""
        self.display.setText(current + token)
        self.error.clear()

    def _backspace(self) -> None:
        self.display.setText(self.display.text()[:-1])
        self.error.clear()

    def _clear(self) -> None:
        self.display.clear()
        self.error.clear()

    def _toggle_sign(self) -> None:
        text = self.display.text()
        self.display.setText(text[1:] if text.startswith("-") else f"-{text}")
        self.error.clear()

    def _confirm(self) -> None:
        """입력 창을 확정하기 전에 표시된 값과 허용 범위를 검증한다."""
        try:
            value = float(self.display.text())
        except ValueError:
            self.error.setText("값을 입력하세요.")
            return
        if not self.minimum <= value <= self.maximum:
            self.error.setText("허용 범위를 벗어난 값입니다.")
            return
        self.result_value = round(value, self.decimals)
        self.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """터치 버튼과 같은 동작을 물리 키보드로도 할 수 있게 한다.

        숫자/부호/소수점은 버튼을 누른 것과 똑같이 처리하고, Enter는
        확인, Backspace는 지우기, Esc는 취소다. 검증(_confirm)은 그대로
        거치므로 화면을 거치지 않고 값이 바로 반영되는 일은 없다.
        """
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.reject()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._confirm()
            return
        if key == Qt.Key.Key_Backspace:
            self._backspace()
            return
        text = event.text()
        if text == "-":
            self._toggle_sign()
        elif text.isdigit() or text == ".":
            self._append(text)
        else:
            event.accept()


class VirtualKeyboardDialog(QDialog):
    """텍스트 설정 입력에 사용하는 공통 화면 키보드."""

    def __init__(self, value: str, title: str = "텍스트 입력", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.result_text = value
        self._uppercase = False
        self._letter_buttons: list[QPushButton] = []
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(760)

        root = QVBoxLayout(self)
        self.display = QLineEdit(value)
        self.display.setReadOnly(True)
        # NumericKeypadDialog와 같은 이유: 표시 전용 칸이 초기 포커스를
        # 가져가 물리 Backspace가 여기서 막히지 않게, 아예 포커스를
        # 받지 않게 한다.
        self.display.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.display.setObjectName("KeypadDisplay")
        root.addWidget(self.display)

        for characters in ("1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm"):
            row = QHBoxLayout()
            for character in characters:
                button = QPushButton(character)
                button.setMinimumSize(64, 64)
                button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                button.clicked.connect(lambda _checked=False, char=character: self._append_letter(char))
                if character.isalpha():
                    self._letter_buttons.append(button)
                row.addWidget(button)
            root.addLayout(row)

        symbols = QHBoxLayout()
        for text in (".", "/", "\\", ":", "-", "_", "@"):
            button = QPushButton(text)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _checked=False, token=text: self._append(token))
            symbols.addWidget(button)
        root.addLayout(symbols)

        edit_row = QHBoxLayout()
        shift = QPushButton("대/소문자")
        space = QPushButton("공백")
        backspace = QPushButton("한 글자 지우기")
        clear = QPushButton("전체 지우기")
        shift.clicked.connect(self._toggle_case)
        space.clicked.connect(lambda: self._append(" "))
        backspace.clicked.connect(lambda: self.display.setText(self.display.text()[:-1]))
        clear.clicked.connect(self.display.clear)
        for button in (shift, space, backspace, clear):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            edit_row.addWidget(button)
        root.addLayout(edit_row)

        actions = QHBoxLayout()
        cancel = QPushButton("취소")
        confirm = QPushButton("확인")
        cancel.clicked.connect(self.reject)
        confirm.clicked.connect(self._confirm)
        for button in (cancel, confirm):
            _disarm_enter(button)
        actions.addWidget(cancel)
        actions.addWidget(confirm)
        root.addLayout(actions)

    def _append_letter(self, character: str) -> None:
        self._append(character.upper() if self._uppercase else character)

    def _append(self, text: str) -> None:
        self.display.setText(self.display.text() + text)

    def _toggle_case(self) -> None:
        """이후 입력 문자와 키 표시를 대문자 또는 소문자로 전환한다."""
        self._uppercase = not self._uppercase
        for button in self._letter_buttons:
            button.setText(button.text().swapcase())

    def _confirm(self) -> None:
        self.result_text = self.display.text()
        self.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """화면 키보드 버튼과 같은 동작을 물리 키보드로도 할 수 있게 한다.

        인쇄 가능한 문자는 버튼을 누른 것과 똑같이 이어붙이고, Enter는
        확인, Backspace는 한 글자 지우기, Esc는 취소다.
        """
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.reject()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._confirm()
            return
        if key == Qt.Key.Key_Backspace:
            self.display.setText(self.display.text()[:-1])
            return
        text = event.text()
        if text.isprintable() and text:
            self._append(text)
        else:
            event.accept()


class TouchSelectDialog(QDialog):
    """콤보박스 항목을 큰 목록으로 고르는 터치용 선택 창.

    숫자 키패드·가상 키보드와 같은 방식(모달 창)이다. 항목 문구가 길어도
    (예: "FLOAT32 — 관절 deg, TCP XYZ mm, R deg — IEEE754 실수") 잘리지
    않도록 줄바꿈을 허용한다.
    """

    def __init__(self, items: list[str], current: int,
                 title: str = "선택", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.selected_index = -1
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setObjectName("TouchSelectList")
        self.list.setWordWrap(True)
        self.list.addItems(items)
        if 0 <= current < len(items):
            self.list.setCurrentRow(current)
        # 한 번 누르면 바로 고르고 닫는다 — 터치에서 더블클릭은 어렵다.
        self.list.itemClicked.connect(self._choose)
        root.addWidget(self.list)

        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("취소")
        cancel.clicked.connect(self.reject)
        _disarm_enter(cancel)
        cancel.setMinimumWidth(160)
        row.addWidget(cancel)
        root.addLayout(row)

    def _choose(self, item) -> None:
        self.selected_index = self.list.row(item)
        self.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Enter 는 지금 짚은 항목을 고르고, Esc 는 취소다."""
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.reject()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            row = self.list.currentRow()
            if row >= 0:
                self.selected_index = row
                self.accept()
            return
        super().keyPressEvent(event)


class TouchComboBox(QComboBox):
    """터치 환경용 콤보박스 — 네이티브 펼침 목록 대신 선택 창을 연다.

    Wayland(WSLg 포함)에서는 Qt 의 팝업(Qt::Popup)이 창과 동떨어진 자리에
    뜨고, 항목을 골라도 화면에 그대로 남는다. XWayland(xcb)로 옮기면 팝업은
    정상이지만 이번엔 **마우스 커서가 창 위에서 안 보인다** — 둘 다 실제로
    겪었고, 커서 쪽이 훨씬 치명적이라 되돌렸다.

    그래서 팝업을 아예 쓰지 않는다. 숫자 키패드·가상 키보드와 같은 모달
    선택 창으로 고르면 플랫폼의 팝업 처리에 기대지 않아도 되고, 터치
    화면에서는 항목이 커져서 누르기도 쉽다.
    """

    def showPopup(self) -> None:  # noqa: N802
        if self.count() == 0:
            return
        dialog = TouchSelectDialog(
            [self.itemText(i) for i in range(self.count())],
            self.currentIndex(),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        row = dialog.selected_index
        if row < 0:
            return
        if row != self.currentIndex():
            self.setCurrentIndex(row)
        self.activated.emit(row)


class _TouchNumericMixin:
    """Qt 스핀박스 전체 영역을 터치 키패드 실행 영역으로 바꾼다.

    클릭(터치)하면 큰 버튼짜리 숫자 키패드가 뜬다 — 이건 그대로 둔다.
    다만 예전에는 물리 키보드 입력을 아예 막아 놨었다(포커스 자체를
    못 받게, lineEdit도 읽기 전용으로). Tab으로 옮겨 와 직접 타이핑하는
    사용자(마우스+키보드로 쓰는 개발/시험 환경 등)를 위해 포커스와
    직접 입력은 열어 두고, 클릭했을 때의 키패드 팝업만 그대로 유지한다.
    """

    dialog_title = "숫자 입력"

    def _configure_touch_input(self) -> None:
        """기본 화살표를 숨기고 테두리 클릭도 키패드 팝업 영역으로 만든다."""
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.lineEdit().installEventFilter(self)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lineEdit().setCursor(Qt.CursorShape.PointingHandCursor)

    def _open_keypad(self) -> None:
        """현재 스핀박스의 범위와 정밀도에 맞춘 키패드를 연다."""
        is_double = isinstance(self, QDoubleSpinBox)
        dialog = NumericKeypadDialog(
            value=float(self.value()),
            minimum=float(self.minimum()),
            maximum=float(self.maximum()),
            decimals=self.decimals() if is_double else 0,
            unit=self.suffix(),
            title=self.dialog_title,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # QSpinBox.setValue는 int만 받는다. float(예: 502.0)를 그대로
            # 넘기면 PyQt6가 시그니처를 못 맞춰 조용히 무시해 버려서,
            # 확인을 눌러도 값이 반영되지 않는 것처럼 보인다.
            value = dialog.result_value
            self.setValue(value if is_double else int(round(value)))

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        """QAbstractSpinBox 내부 입력부가 처리하는 클릭을 가로챈다."""
        if watched is self.lineEdit() and event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self._open_keypad()
                return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        """스핀박스 테두리 영역을 터치해도 키패드를 연다."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._open_keypad()
            event.accept()
            return
        super().mousePressEvent(event)


class TouchSpinBox(_TouchNumericMixin, QSpinBox):
    """숫자 키패드로만 수정할 수 있는 정수 입력 필드."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._configure_touch_input()


class TouchDoubleSpinBox(_TouchNumericMixin, QDoubleSpinBox):
    """숫자 키패드로만 수정할 수 있는 실수 입력 필드."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._configure_touch_input()


class TouchLineEdit(QLineEdit):
    """클릭하면 공통 가상 키보드가 뜨는 텍스트 입력 필드.

    예전에는 물리 키보드 입력을 아예 막아 놨었다(읽기 전용 + 포커스
    불가). 클릭했을 때 뜨는 가상 키보드 팝업은 그대로 두되, Tab으로
    옮겨 와 직접 타이핑하는 것도 되게 열어 둔다 — 포커스와 직접 입력을
    막지 않는다.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        """텍스트 입력 상자 전체를 가상 키보드 터치 영역으로 사용한다."""
        if event.button() == Qt.MouseButton.LeftButton:
            dialog = VirtualKeyboardDialog(self.text(), parent=self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.setText(dialog.result_text)
            event.accept()
            return
        super().mousePressEvent(event)
