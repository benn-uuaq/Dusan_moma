from PyQt6.QtWidgets import QDialog

from smr_operator_ui.keypad import NumericKeypadDialog, TouchDoubleSpinBox, TouchLineEdit


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


def test_touch_fields_block_direct_keyboard_editing(qtbot) -> None:
    number = TouchDoubleSpinBox()
    text = TouchLineEdit("SMR")
    qtbot.addWidget(number)
    qtbot.addWidget(text)
    assert number.lineEdit().isReadOnly()
    assert text.isReadOnly()
