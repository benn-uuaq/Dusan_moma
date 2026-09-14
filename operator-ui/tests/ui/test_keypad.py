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


# ---- 마우스 휠로 값이 바뀌지 않는다 ------------------------------------------
def _wheel(widget, up: bool = True) -> None:
    from PyQt6.QtCore import QPoint, QPointF
    from PyQt6.QtGui import QWheelEvent
    pos = QPointF(widget.width() / 2, widget.height() / 2)
    ev = QWheelEvent(pos, widget.mapToGlobal(pos), QPoint(0, 0),
                     QPoint(0, 120 if up else -120), Qt.MouseButton.NoButton,
                     Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, ev)


def test_wheel_never_changes_input_values_but_still_scrolls_the_page(qtbot) -> None:
    """입력칸 위에서 휠을 굴려도 값은 그대로이고, 페이지는 스크롤된다."""
    from PyQt6.QtWidgets import QScrollArea, QSlider, QVBoxLayout, QWidget
    from smr_operator_ui.keypad import TouchComboBox, TouchSpinBox, install_wheel_guard
    install_wheel_guard()

    area = QScrollArea(); area.setWidgetResizable(True)
    page = QWidget(); col = QVBoxLayout(page)
    spin = TouchSpinBox(); spin.setRange(0, 1000); spin.setValue(200)
    combo = TouchComboBox(); combo.addItems(["한국어", "English"])
    slider = QSlider(Qt.Orientation.Horizontal); slider.setRange(0, 100); slider.setValue(50)
    for w in (spin, combo, slider):
        col.addWidget(w)
    col.addSpacing(3000)                     # 스크롤할 거리
    area.setWidget(page); area.resize(300, 200)
    qtbot.addWidget(area); area.show()

    bar = area.verticalScrollBar()
    for target in (spin.lineEdit(), spin, combo, slider):
        _wheel(target, up=False)
    assert spin.value() == 200
    assert combo.currentIndex() == 0
    assert slider.value() == 50
    assert bar.value() > 0, "입력칸 위에서도 페이지는 스크롤돼야 한다"


def test_speed_bar_is_not_changed_by_the_wheel(qtbot) -> None:
    """로봇 속도 슬라이더도 휠로 안 바뀐다 — 스크롤하다 속도가 바뀌면 위험하다."""
    from smr_operator_ui.app import OperatorWindow
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    slider = window.main_screen.speed_bar.slider
    slider.setValue(50)                  # 끝값이면 휠이 더 못 가 시험이 안 된다
    _wheel(slider, up=False)
    assert slider.value() == 50
    _wheel(slider, up=True)
    assert slider.value() == 50
    window.close()


# ---- 데이터 저장 위치: 폴더 선택 창 --------------------------------------------
def test_data_folder_opens_a_folder_picker_not_the_keyboard(qtbot) -> None:
    from smr_operator_ui.app import OperatorWindow
    from smr_operator_ui.folder_picker import FolderPathEdit
    window = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(window)
    field = window.screens["system"].field("데이터 저장 위치")
    assert isinstance(field, FolderPathEdit)
    assert field.isReadOnly()
    asked: list[str] = []
    field.chooser = lambda current: asked.append(current) or "E:/UT/Data"

    qtbot.mouseClick(field, Qt.MouseButton.LeftButton)

    assert asked == ["D:/SMR/Data"]              # 지금 값에서 연다
    assert field.text() == "E:/UT/Data"
    assert window.screens["system"].values()["데이터 저장 위치"] == "E:/UT/Data"
    window.close()


def test_cancelled_folder_picker_keeps_the_old_path(qtbot) -> None:
    from smr_operator_ui.folder_picker import FolderPathEdit
    field = FolderPathEdit("D:/SMR/Data")
    qtbot.addWidget(field)
    field.chooser = lambda current: None
    qtbot.mouseClick(field, Qt.MouseButton.LeftButton)
    assert field.text() == "D:/SMR/Data"


def test_windows_picker_paths_and_arguments() -> None:
    import base64
    from smr_operator_ui import folder_picker as fp
    assert fp.to_windows_path("D:/SMR/Data") == "D:\\SMR\\Data"
    assert fp.from_windows_path("D:\\SMR\\데이터") == "D:/SMR/데이터"
    args = fp.windows_picker_args("데이터 저장 위치 선택", "D:\\SMR\\Data")
    assert "-STA" in args
    script = base64.b64decode(args[-1]).decode("utf-16-le")
    assert "'데이터 저장 위치 선택'" in script
    assert "'D:\\SMR\\Data'" in script
    # 작은따옴표가 든 경로도 PowerShell 문자열이 깨지지 않는다.
    assert fp._ps_quote("D:\\it's") == "'D:\\it''s'"
