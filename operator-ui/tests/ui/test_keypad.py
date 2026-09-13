from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication, QDialog

from smr_operator_ui.keypad import (
    NumericKeypadDialog, TouchComboBox, TouchDoubleSpinBox, TouchLineEdit,
    TouchSelectDialog, VirtualKeyboardDialog,
)


def test_numeric_keypad_accepts_valid_value(qtbot) -> None:
    dialog = NumericKeypadDialog(2.0, 0.1, 100.0, 2, " m")
    qtbot.addWidget(dialog)
    dialog.display.setText("3.25")
    dialog._confirm()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.result_value == 3.25


def test_numeric_keypad_rejects_out_of_range_value(qtbot) -> None:
    dialog = NumericKeypadDialog(2.0, 0.1, 100.0, 2, " m")
    qtbot.addWidget(dialog)
    dialog.display.setText("100.01")
    dialog._confirm()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.error.text() == "허용 범위를 벗어난 값입니다."


def test_touch_fields_also_accept_physical_keyboard(qtbot) -> None:
    """클릭하면 뜨는 키패드/키보드 팝업은 그대로 두되(mousePressEvent는

    안 건드림), Tab으로 옮겨 와 물리 키보드로 직접 타이핑하는 것도
    막지 않아야 한다 — 예전에는 읽기 전용 + 포커스 불가로 아예 막아
    놨었다.
    """
    number = TouchDoubleSpinBox()
    text = TouchLineEdit("")
    qtbot.addWidget(number)
    qtbot.addWidget(text)

    assert not number.lineEdit().isReadOnly()
    assert not text.isReadOnly()
    assert number.focusPolicy() != Qt.FocusPolicy.NoFocus
    assert text.focusPolicy() != Qt.FocusPolicy.NoFocus

    text.setFocus()
    qtbot.keyClicks(text, "SMR")
    assert text.text() == "SMR"


def test_keypad_dialogs_backspace_reaches_dialog_without_focused_display(qtbot) -> None:
    """읽기 전용 display가 초기 포커스를 가져가면 물리 Backspace가

    거기서 막혀 다이얼로그의 실제 지우기 로직까지 오지 않던 문제 —
    display/버튼을 모두 포커스 불가로 만들어, 다이얼로그 자신이 키를
    받게 한다.
    """
    dialog = NumericKeypadDialog(2.0, 0.1, 100.0, 2, " m")
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.display.focusPolicy() == Qt.FocusPolicy.NoFocus
    dialog.display.setText("325")
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Backspace, Qt.KeyboardModifier.NoModifier)
    dialog.keyPressEvent(event)
    assert dialog.display.text() == "32"

    kb = VirtualKeyboardDialog("SMR")
    qtbot.addWidget(kb)
    kb.show()
    assert kb.display.focusPolicy() == Qt.FocusPolicy.NoFocus
    event2 = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Backspace, Qt.KeyboardModifier.NoModifier)
    kb.keyPressEvent(event2)
    assert kb.display.text() == "SM"


def test_enter_key_confirms_virtual_keyboard(qtbot) -> None:
    """물리 Enter 는 "확인"과 같아야 한다 — 입력이 반영된 채로 닫혀야 한다.

    예전에는 취소/확인 버튼만 포커스를 받을 수 있어서 첫 포커스가 "취소"에
    갔고, autoDefault 때문에 Enter 가 그 버튼을 눌러 **입력이 사라진 채
    창만 닫혔다**.
    """
    dialog = VirtualKeyboardDialog("", parent=None)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.keyClicks(dialog, "192.168.0.5")
    qtbot.keyClick(dialog, Qt.Key.Key_Return)

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.result_text == "192.168.0.5"


def test_enter_key_confirms_numeric_keypad(qtbot) -> None:
    """숫자 키패드도 같은 이유로 Enter 가 확인이어야 한다."""
    dialog = NumericKeypadDialog(0.0, 0.0, 65535.0, 0, parent=None)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.keyClicks(dialog, "502")
    qtbot.keyClick(dialog, Qt.Key.Key_Return)

    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.result_value == 502


def test_touch_combo_uses_a_dialog_instead_of_a_native_popup(qtbot) -> None:
    """콤보박스는 네이티브 펼침 목록을 쓰지 않는다.

    Wayland 에서는 그 팝업이 창과 동떨어진 자리에 뜨고 골라도 남는다.
    XWayland 로 옮기면 팝업은 낫지만 마우스 커서가 창 위에서 안 보인다 —
    둘 다 실제로 겪어서, 아예 모달 선택 창으로 고르게 했다.
    """
    combo = TouchComboBox()
    qtbot.addWidget(combo)
    combo.addItems(["실제 TCP", "제로점 기준 스캔 값", "자동"])

    opened: list[TouchSelectDialog] = []
    original = TouchSelectDialog.exec

    def choose_second(dialog):
        opened.append(dialog)
        dialog.selected_index = 1
        return QDialog.DialogCode.Accepted

    picked: list[int] = []
    combo.activated.connect(picked.append)
    TouchSelectDialog.exec = choose_second
    try:
        combo.showPopup()
    finally:
        TouchSelectDialog.exec = original

    assert opened, "선택 창이 뜨지 않았다"
    assert combo.currentIndex() == 1
    assert picked == [1]
    # 네이티브 팝업은 아예 열리지 않아야 한다.
    assert not combo.view().window().isVisible()


def test_touch_select_dialog_enter_and_escape(qtbot) -> None:
    """Enter 는 짚은 항목을 고르고, Esc 는 아무것도 고르지 않는다."""
    dialog = TouchSelectDialog(["A", "B", "C"], current=2)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.keyClick(dialog, Qt.Key.Key_Return)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.selected_index == 2

    other = TouchSelectDialog(["A", "B"], current=0)
    qtbot.addWidget(other)
    other.show()
    qtbot.keyClick(other, Qt.Key.Key_Escape)
    assert other.result() == QDialog.DialogCode.Rejected
    assert other.selected_index == -1
