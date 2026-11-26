"""조작 화면의 직접 입력란에 값을 옮겨 주는 비모달 터치 키패드."""
from PyQt5 import QtCore, QtWidgets as W


class InputKeypad(W.QDialog):
    def __init__(self, field, text_mode, parent):
        super().__init__(parent)
        self.field = field
        self.setWindowTitle(field.accessibleName())
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        layout = W.QVBoxLayout(self)
        self.editor = W.QLineEdit(field.text())
        self.editor.setAccessibleName(field.accessibleName())
        layout.addWidget(self.editor)
        grid = W.QGridLayout()
        layout.addLayout(grid)

        # 버튼을 터치해도 편집란이 포커스를 유지해 커서·선택 영역을 그대로 사용한다.
        rows = ("1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm-_.") if text_mode else ("789", "456", "123", "-0.")
        self.letters = []
        for row, keys in enumerate(rows):
            for column, key in enumerate(keys):
                button = self.make_button(key)
                button.clicked.connect(lambda checked, b=button: self.editor.insert(b.text()))
                grid.addWidget(button, row, column)
                if key.isalpha():
                    self.letters.append(button)

        actions = W.QHBoxLayout()
        layout.addLayout(actions)
        if text_mode:
            shift = self.make_button("대문자")
            shift.setCheckable(True)
            shift.toggled.connect(self.set_uppercase)
            actions.addWidget(shift)
        for label, action in (("⌫", self.editor.backspace), ("지우기", self.editor.clear),
                              ("취소", self.reject), ("입력", self.accept)):
            button = self.make_button(label)
            button.clicked.connect(action)
            actions.addWidget(button)
        button.setDefault(True)
        self.editor.selectAll()
        self.editor.setFocus()

    @staticmethod
    def make_button(label):
        button = W.QPushButton(label)
        button.setMinimumSize(44, 42)
        button.setSizePolicy(W.QSizePolicy.Ignored, W.QSizePolicy.Preferred)
        button.setFocusPolicy(QtCore.Qt.NoFocus)
        button.setAutoDefault(False)
        return button

    def set_uppercase(self, enabled):
        for button in self.letters:
            text = button.text()
            button.setText(text.upper() if enabled else text.lower())

    def accept(self):
        # 입력 확정은 화면 값만 바꾼다. 서비스 호출은 조작 화면의 명령 버튼에서 한다.
        self.field.setText(self.editor.text())
        super().accept()
