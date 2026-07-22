from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class NumericKeypadDialog(QDialog):
    """Touch-first numeric input with range validation."""

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
        self.display.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.display.setObjectName("KeypadDisplay")
        root.addWidget(self.display)

        range_text = f"허용 범위: {self._format(minimum)} ~ {self._format(maximum)}{unit}"
        self.guide = QLabel(range_text)
        self.guide.setObjectName("Muted")
        root.addWidget(self.guide)
        self.error = QLabel("")
        self.error.setObjectName("StatusDanger")
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
        actions.addWidget(cancel)
        actions.addWidget(confirm)
        root.addLayout(actions)

    def _format(self, value: float) -> str:
        return f"{value:.{self.decimals}f}"

    def _append(self, token: str) -> None:
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
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            event.accept()


class VirtualKeyboardDialog(QDialog):
    """Common on-screen keyboard for editable text settings."""

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
        self.display.setObjectName("KeypadDisplay")
        root.addWidget(self.display)

        for characters in ("1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm"):
            row = QHBoxLayout()
            for character in characters:
                button = QPushButton(character)
                button.setMinimumSize(64, 64)
                button.clicked.connect(lambda _checked=False, char=character: self._append_letter(char))
                if character.isalpha():
                    self._letter_buttons.append(button)
                row.addWidget(button)
            root.addLayout(row)

        symbols = QHBoxLayout()
        for text in (".", "/", "\\", ":", "-", "_", "@"):
            button = QPushButton(text)
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
            edit_row.addWidget(button)
        root.addLayout(edit_row)

        actions = QHBoxLayout()
        cancel = QPushButton("취소")
        confirm = QPushButton("확인")
        cancel.clicked.connect(self.reject)
        confirm.clicked.connect(self._confirm)
        actions.addWidget(cancel)
        actions.addWidget(confirm)
        root.addLayout(actions)

    def _append_letter(self, character: str) -> None:
        self._append(character.upper() if self._uppercase else character)

    def _append(self, text: str) -> None:
        self.display.setText(self.display.text() + text)

    def _toggle_case(self) -> None:
        self._uppercase = not self._uppercase
        for button in self._letter_buttons:
            button.setText(button.text().swapcase())

    def _confirm(self) -> None:
        self.result_text = self.display.text()
        self.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            event.accept()


class _TouchNumericMixin:
    dialog_title = "숫자 입력"

    def _configure_touch_input(self) -> None:
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().installEventFilter(self)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lineEdit().setCursor(Qt.CursorShape.PointingHandCursor)

    def _open_keypad(self) -> None:
        dialog = NumericKeypadDialog(
            value=float(self.value()),
            minimum=float(self.minimum()),
            maximum=float(self.maximum()),
            decimals=self.decimals() if isinstance(self, QDoubleSpinBox) else 0,
            unit=self.suffix(),
            title=self.dialog_title,
            parent=self,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.setValue(dialog.result_value)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self.lineEdit() and event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self._open_keypad()
                return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._open_keypad()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        event.accept()


class TouchSpinBox(_TouchNumericMixin, QSpinBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._configure_touch_input()


class TouchDoubleSpinBox(_TouchNumericMixin, QDoubleSpinBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._configure_touch_input()


class TouchLineEdit(QLineEdit):
    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setReadOnly(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            dialog = VirtualKeyboardDialog(self.text(), parent=self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.setText(dialog.result_text)
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        event.accept()
