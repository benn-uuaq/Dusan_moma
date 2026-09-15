"""기록·설정 묶음을 다루는 화면: 오류 로그, 로그 파일, 운전 모드 저장.

세 화면이 겹치지 않게 역할을 나눈다.

  시스템 설정   : 데이터 저장 위치·로그 보존 기간을 **바꾸는 곳** (여기뿐)
  로그 파일     : 저장 위치 아래 네 가지 기록 파일을 보고 내보내거나 지운다
  오류 로그     : 지금 걸린 오류를 해제하고, 알람·이벤트 기록을 날짜별로 본다
  운전 모드 저장 : 작업 조건 묶음(작업 영역·검사 대상·Cobot 속도·태스크·UT)을
                  슬롯에 저장하고 불러온다. 연결·시스템 설정은 장비 고유값이라
                  넣지 않는다.

화면은 파일을 직접 만들지 않는다 — 기록기(DataRecorder)와 앱이 준 함수를
부른다. 확인 창·이름 입력·폴더 열기는 시험에서 바꿔 끼울 수 있게 속성으로 둔다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
)

from smr_operator_ui.folder_picker import FolderPicker, open_in_file_manager, to_local_path
from smr_operator_ui.keypad import TouchComboBox, VirtualKeyboardDialog
from smr_operator_ui.screens.all_screens import BaseScreen
from smr_operator_ui.services.data_recorder import CATEGORIES, EVENTS, write_table_xlsx


def _ask_yes(parent, title: str, text: str) -> bool:
    answer = QMessageBox.question(parent, title, text,
                                  QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                  QMessageBox.StandardButton.No)
    return answer == QMessageBox.StandardButton.Yes


def _table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _size_text(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


def _status(label: QLabel, text: str, kind: str = "Muted") -> None:
    label.setObjectName(kind)
    label.setText(text)
    label.style().unpolish(label)
    label.style().polish(label)


# =============================================================================
class ErrorLogScreen(BaseScreen):
    """지금 걸린 오류 해제 + 날짜별 알람·이벤트 기록."""

    #: 해제할 오류 코드들. 빈 목록이면 전체 해제(메인 화면 '알람 리셋'과 같다).
    clear_requested = pyqtSignal(list)

    #: (보기 이름, 보여 줄 구분들 — None 이면 전부)
    KIND_FILTERS = (
        ("알람·장애·해제·알림", ("알람", "장애", "해제", "알림")),
        ("전체 (진행 알림 포함)", None),
        ("알람", ("알람",)),
        ("장애", ("장애",)),
        ("해제", ("해제",)),
        ("알림", ("알림",)),
    )
    HEADERS = ["시각", "구분", "코드", "수준", "내용"]

    def __init__(self) -> None:
        super().__init__("오류 로그", "지금 걸린 오류를 확인·해제하고, 날짜별 알람·이벤트 기록을 봅니다.")
        self.recorder = None
        self.active_errors: Callable[[], list[str]] = list
        self._days: list[date] = []

        active, al = self.surface("활성 오류")
        self.active_list = QListWidget()
        self.active_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.active_list.setMaximumHeight(110)
        al.addWidget(self.active_list)
        row = QHBoxLayout(); row.addStretch()
        self.clear_selected_button = QPushButton("선택 오류 해제")
        self.clear_all_button = QPushButton("전체 해제 (알람 리셋)")
        self.clear_all_button.setObjectName("DangerButton")
        row.addWidget(self.clear_selected_button); row.addWidget(self.clear_all_button)
        al.addLayout(row)
        self.body.addWidget(active)

        records, rl = self.surface("알람·이벤트 기록")
        filters = QHBoxLayout()
        filters.addWidget(QLabel("날짜"))
        self.day_combo = TouchComboBox()
        filters.addWidget(self.day_combo, 1)
        filters.addWidget(QLabel("구분"))
        self.kind_combo = TouchComboBox()
        self.kind_combo.addItems([name for name, _ in self.KIND_FILTERS])
        filters.addWidget(self.kind_combo, 1)
        self.refresh_button = QPushButton("새로고침")
        self.export_button = QPushButton("엑셀로 내보내기")
        filters.addWidget(self.refresh_button); filters.addWidget(self.export_button)
        rl.addLayout(filters)
        self.table = _table(self.HEADERS)
        # 날짜는 위에서 고르므로 표에는 시각만 둔다(내보낼 때는 날짜까지).
        for col, width in ((0, 120), (1, 60), (2, 120), (3, 70)):
            self.table.setColumnWidth(col, width)
        rl.addWidget(self.table, 1)
        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        self.status_label.setWordWrap(True)
        rl.addWidget(self.status_label)
        self.body.addWidget(records, 1)

        self.picker = FolderPicker("오류 로그를 내보낼 폴더", self)
        self.picker.chosen.connect(self._export_to)
        self.clear_selected_button.clicked.connect(self._clear_selected)
        self.clear_all_button.clicked.connect(lambda: self.clear_requested.emit([]))
        self.refresh_button.clicked.connect(self.refresh)
        self.export_button.clicked.connect(self._export_clicked)
        self.day_combo.activated.connect(lambda _i: self._load_table())
        self.kind_combo.activated.connect(lambda _i: self._load_table())

    def bind(self, recorder, active_errors: Callable[[], list[str]]) -> None:
        self.recorder = recorder
        self.active_errors = active_errors

    # ---- 활성 오류 ---------------------------------------------------------
    def refresh_active(self, *_args) -> None:
        codes = list(self.active_errors() or [])
        self.active_list.clear()
        self.active_list.addItems(codes or [])
        self.clear_selected_button.setEnabled(bool(codes))
        if not codes:
            self.active_list.addItem("걸려 있는 오류 없음")
            self.active_list.item(0).setFlags(Qt.ItemFlag.NoItemFlags)

    def _clear_selected(self) -> None:
        codes = [item.text() for item in self.active_list.selectedItems()]
        if codes:
            self.clear_requested.emit(codes)

    # ---- 기록 --------------------------------------------------------------
    def refresh(self) -> None:
        """활성 오류와 기록 표를 다시 읽는다(화면을 열 때마다)."""
        self.refresh_active()
        if self.recorder is None:
            return
        keep = self.selected_day()
        self._days = self.recorder.event_days()
        today = date.today()
        if today not in self._days:
            self._days.insert(0, today)
        self.day_combo.clear()
        self.day_combo.addItems([f"{d:%Y-%m-%d}" for d in self._days])
        self.day_combo.setCurrentIndex(self._days.index(keep) if keep in self._days else 0)
        self._load_table()

    def selected_day(self) -> date | None:
        index = self.day_combo.currentIndex()
        return self._days[index] if 0 <= index < len(self._days) else None

    def _kinds(self):
        return self.KIND_FILTERS[max(self.kind_combo.currentIndex(), 0)][1]

    def _rows(self) -> list[list[str]]:
        day = self.selected_day()
        if self.recorder is None or day is None:
            return []
        kinds = self._kinds()
        rows = [r for r in self.recorder.read_events(day) if kinds is None or r[1] in kinds]
        rows.reverse()                               # 최근 것이 위
        return rows

    def _load_table(self) -> None:
        rows = self._rows()
        self.table.setRowCount(0)
        for row in rows:
            self._insert_row(self.table.rowCount(), row)
        day = self.selected_day()
        _status(self.status_label, f"{day:%Y-%m-%d} 기록 {len(rows)}줄" if day else "")

    def _insert_row(self, at: int, row: list[str]) -> None:
        self.table.insertRow(at)
        for col, value in enumerate(row[:5]):
            if col == 0 and len(value) > 11:
                value = value[11:]                   # "2026-09-15 18:13:33.522" -> 시각만
            item = QTableWidgetItem(value)
            if col == 4:
                item.setToolTip(value)               # 긴 내용은 잘리므로 전체를 띄워 준다
            self.table.setItem(at, col, item)

    def on_event_logged(self, fields: list) -> None:
        """새 기록이 남으면, 오늘을 보고 있고 구분이 맞으면 표 맨 위에 붙인다."""
        if self.selected_day() != date.today():
            return
        kinds = self._kinds()
        if kinds is None or fields[1] in kinds:
            self._insert_row(0, [str(v) for v in fields])
            _status(self.status_label, f"{date.today():%Y-%m-%d} 기록 {self.table.rowCount()}줄")

    def _export_clicked(self) -> None:
        if self.recorder is None or self.selected_day() is None:
            return
        self.picker.open(str(self.recorder.root))

    def _export_to(self, chosen: str) -> None:
        day = self.selected_day()
        rows = self._rows()
        target = Path(to_local_path(chosen)) / f"{day:%Y%m%d}_오류로그.xlsx"
        try:
            write_table_xlsx(target, "오류로그", self.HEADERS, rows, [22, 8, 14, 8, 80])
        except OSError as exc:
            _status(self.status_label, f"내보내지 못했습니다: {exc}", "StatusDanger")
            return
        _status(self.status_label, f"{len(rows)}줄을 내보냈습니다: {chosen}/{target.name}", "StatusGood")


# =============================================================================
class LogFilesScreen(BaseScreen):
    """데이터 저장 위치 아래의 기록 파일 — 보기·내보내기·지우기."""

    #: 저장 위치·보존 기간을 바꾸러 시스템 설정으로 간다.
    system_settings_requested = pyqtSignal()

    ALL = "전체"
    HEADERS = ["날짜", "종류", "파일명", "크기"]

    def __init__(self) -> None:
        super().__init__("로그 파일", "데이터 저장 위치의 기록 파일을 날짜별로 보고, 내보내거나 지웁니다.")
        self.recorder = None
        self._files: list = []
        # 시험에서 바꿔 끼운다.
        self.confirm: Callable[[str], bool] = lambda text: _ask_yes(self, "기록 파일 삭제", text)
        self.opener: Callable[[str], None] = open_in_file_manager

        where, wl = self.surface("저장 위치")
        line = QHBoxLayout()
        self.location_label = QLabel("-")
        self.location_label.setWordWrap(True)
        line.addWidget(self.location_label, 1)
        self.open_button = QPushButton("폴더 열기")
        self.settings_button = QPushButton("시스템 설정에서 변경")
        line.addWidget(self.open_button); line.addWidget(self.settings_button)
        wl.addLayout(line)
        self.body.addWidget(where)

        files, fl = self.surface("기록 파일")
        filters = QHBoxLayout()
        filters.addWidget(QLabel("종류"))
        self.kind_combo = TouchComboBox()
        self.kind_combo.addItems([self.ALL, *CATEGORIES])
        filters.addWidget(self.kind_combo, 1)
        self.refresh_button = QPushButton("새로고침")
        filters.addWidget(self.refresh_button)
        fl.addLayout(filters)
        self.table = _table(self.HEADERS)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for col in (0, 1, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        fl.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        self.status_label.setWordWrap(True)
        actions.addWidget(self.status_label, 1)
        self.export_button = QPushButton("선택 내보내기")
        self.delete_button = QPushButton("선택 삭제")
        self.delete_button.setObjectName("DangerButton")
        actions.addWidget(self.export_button); actions.addWidget(self.delete_button)
        fl.addLayout(actions)
        self.body.addWidget(files, 1)

        self.picker = FolderPicker("기록 파일을 내보낼 폴더", self)
        self.picker.chosen.connect(self._export_to)
        self.refresh_button.clicked.connect(self.refresh)
        self.kind_combo.activated.connect(lambda _i: self.refresh())
        self.settings_button.clicked.connect(self.system_settings_requested)
        self.open_button.clicked.connect(self._open_folder)
        self.export_button.clicked.connect(self._export_clicked)
        self.delete_button.clicked.connect(self._delete_selected)

    def bind(self, recorder) -> None:
        self.recorder = recorder

    def refresh(self) -> None:
        if self.recorder is None:
            return
        root = self.recorder.root
        self.location_label.setText(
            f"{root}\n보존 기간 {self.recorder.retention_days}일 — 지난 파일은 자동으로 지웁니다.")
        kind = self.kind_combo.currentText()
        self._files = self.recorder.list_records(None if kind == self.ALL else kind)
        self.table.setRowCount(0)
        for record in self._files:
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, value in enumerate((f"{record.day:%Y-%m-%d}", record.category,
                                         record.path.name, _size_text(record.size))):
                item = QTableWidgetItem(value)
                if col == 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, col, item)
        total = sum(r.size for r in self._files)
        _status(self.status_label, f"파일 {len(self._files)}개 · {_size_text(total)}")

    def selected_paths(self) -> list[Path]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [self._files[r].path for r in rows if r < len(self._files)]

    def _open_folder(self) -> None:
        if self.recorder is not None:
            self.recorder.root.mkdir(parents=True, exist_ok=True)
            self.opener(str(self.recorder.root))

    def _export_clicked(self) -> None:
        if not self.selected_paths():
            _status(self.status_label, "내보낼 파일을 먼저 고르세요.", "StatusWarn")
            return
        self.picker.open(str(self.recorder.root))

    def _export_to(self, chosen: str) -> None:
        copied = self.recorder.export_files(self.selected_paths(), Path(to_local_path(chosen)))
        _status(self.status_label, f"{len(copied)}개를 내보냈습니다: {chosen}", "StatusGood")

    def _delete_selected(self) -> None:
        paths = self.selected_paths()
        if not paths:
            _status(self.status_label, "지울 파일을 먼저 고르세요.", "StatusWarn")
            return
        if not self.confirm(f"선택한 기록 파일 {len(paths)}개를 지웁니다. 되돌릴 수 없습니다."):
            return
        removed = self.recorder.delete_files(paths)
        self.refresh()
        _status(self.status_label, f"{len(removed)}개를 지웠습니다.", "StatusGood")


# =============================================================================
class ModeSlotsScreen(BaseScreen):
    """작업 조건 묶음을 슬롯에 저장하고 불러온다."""

    load_requested = pyqtSignal(int)
    save_requested = pyqtSignal(int, str)
    clear_requested = pyqtSignal(int)

    SLOT_COUNT = 8

    def __init__(self) -> None:
        super().__init__(
            "운전 모드 저장",
            "작업 영역·검사 대상·Cobot 속도·태스크·UT 조건을 슬롯에 묶어 저장하고 불러옵니다. "
            "연결·시스템 설정은 장비마다 다른 값이라 넣지 않습니다.")
        self._slots: dict[str, dict] = {}
        # 시험에서 바꿔 끼운다.
        self.confirm: Callable[[str], bool] = lambda text: _ask_yes(self, "운전 모드", text)
        self.ask_name: Callable[[str], str | None] = self._ask_name_dialog

        row = QHBoxLayout(); self.body.addLayout(row, 1)
        slots, sl = self.surface("슬롯")
        grid = QGridLayout(); sl.addLayout(grid, 1)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.slot_buttons: list[QPushButton] = []
        for i in range(1, self.SLOT_COUNT + 1):
            button = QPushButton()
            button.setObjectName("ModeSlotButton")
            button.setCheckable(True)
            button.setMinimumHeight(96)
            # QSS 의 min-height 때문에 한 줄 기준으로 고정되는 것을 풀어 준다.
            button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
            self.group.addButton(button, i)
            self.slot_buttons.append(button)
            grid.addWidget(button, (i - 1) // 4, (i - 1) % 4)
        self.slot_buttons[0].setChecked(True)
        actions = QHBoxLayout(); actions.addStretch()
        self.load_button = QPushButton("불러오기")
        self.load_button.setObjectName("ModeLoadButton")
        self.save_button = QPushButton("현재 설정 저장")
        self.clear_button = QPushButton("비우기")
        self.clear_button.setObjectName("DangerButton")
        for button in (self.load_button, self.save_button, self.clear_button):
            actions.addWidget(button)
        sl.addLayout(actions)
        row.addWidget(slots, 3)

        detail, dl = self.surface("슬롯 내용")
        self.detail_label = QLabel("")
        self.detail_label.setWordWrap(True)
        self.detail_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        dl.addWidget(self.detail_label, 1)
        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        self.status_label.setWordWrap(True)
        dl.addWidget(self.status_label)
        row.addWidget(detail, 2)

        self.group.idClicked.connect(lambda _id: self._show_detail())
        self.load_button.clicked.connect(self._load_clicked)
        self.save_button.clicked.connect(self._save_clicked)
        self.clear_button.clicked.connect(self._clear_clicked)
        self.set_slots({})

    # ---- 표시 --------------------------------------------------------------
    def selected_slot(self) -> int:
        return max(self.group.checkedId(), 1)

    def slot(self, number: int) -> dict:
        return dict(self._slots.get(str(number)) or {})

    def set_slots(self, slots: dict) -> None:
        self._slots = {str(k): dict(v or {}) for k, v in (slots or {}).items()}
        for i, button in enumerate(self.slot_buttons, start=1):
            snap = self._slots.get(str(i)) or {}
            if snap:
                button.setText(f"슬롯 {i}\n{snap.get('name', '')}\n{snap.get('saved_at', '')}")
            else:
                button.setText(f"슬롯 {i}\n비어 있음")
        self._show_detail()

    def _show_detail(self) -> None:
        number = self.selected_slot()
        snap = self.slot(number)
        empty = not snap
        self.load_button.setEnabled(not empty)
        self.clear_button.setEnabled(not empty)
        self.detail_label.setText(f"슬롯 {number} — 비어 있음" if empty else describe_mode(snap))

    def show_status(self, text: str, kind: str = "Muted") -> None:
        _status(self.status_label, text, kind)

    # ---- 동작 --------------------------------------------------------------
    def _ask_name_dialog(self, default: str) -> str | None:
        dialog = VirtualKeyboardDialog(default, title="운전 모드 이름", parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.result_text.strip()
        return None

    def _load_clicked(self) -> None:
        number = self.selected_slot()
        if self.slot(number):
            self.load_requested.emit(number)

    def _save_clicked(self) -> None:
        number = self.selected_slot()
        current = self.slot(number)
        if current and not self.confirm(
                f"슬롯 {number}({current.get('name', '')})을(를) 지금 설정으로 덮어씁니다."):
            return
        name = self.ask_name(current.get("name") or f"운전 모드 {number}")
        if name is None:
            return
        self.save_requested.emit(number, name or f"운전 모드 {number}")

    def _clear_clicked(self) -> None:
        number = self.selected_slot()
        current = self.slot(number)
        if current and self.confirm(f"슬롯 {number}({current.get('name', '')})을(를) 비웁니다."):
            self.clear_requested.emit(number)


def describe_mode(snap: dict[str, Any]) -> str:
    """슬롯 내용을 사람이 읽는 몇 줄로."""
    def num(value, digits=0):
        try:
            return f"{float(value):,.{digits}f}"
        except (TypeError, ValueError):
            return "-"

    lines = [f"■ {snap.get('name', '')}", f"저장: {snap.get('saved_at', '-')}", ""]
    area = snap.get("work_area") or {}
    if area:
        probe = {5: "5축 십자", 8: "8축 직사각"}.get(int(float(area.get("eoat_type") or 0)), "-")
        lines.append(f"작업 영역: 호 {num(area.get('width_mm'))} × 높이 {num(area.get('height_mm'))} mm, "
                     f"격자간 겹침 {num(area.get('overlap_mm'))} mm")
        lines.append(f"  반지름 {num(area.get('radius_mm'), 1)} mm, 두께 {num(area.get('thickness_mm'), 1)} mm, "
                     f"프로브 {probe}")
    target = snap.get("inspection_target") or {}
    if target:
        lines.append(f"검사 대상: 지름 {num(target.get('diameter_m'), 3)} m × 높이 {num(target.get('height_m'), 3)} m"
                     + (f", 이동거리 {num(target.get('target_distance_m'), 2)} m"
                        if target.get("target_distance_m") else ""))
    cobot = snap.get("cobot") or {}
    if cobot:
        lines.append(f"Cobot: 작업 속도 {num(cobot.get('작업 속도'))} mm/s, 속도 비율 {num(cobot.get('속도 비율'))} %")
    task = snap.get("robot_task") or {}
    if task:
        lines.append(f"태스크: {task.get('task_version', '-')} · "
                     f"{'논센서판' if task.get('nosensor') else '센서판'}")
    ut = snap.get("ut") or {}
    if ut:
        lines.append("UT: " + ", ".join(f"{k} {v}" for k, v in ut.items()))
    return "\n".join(lines)


def mode_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")
