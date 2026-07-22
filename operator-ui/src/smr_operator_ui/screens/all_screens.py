from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QListWidget, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from smr_operator_ui.keypad import TouchDoubleSpinBox, TouchLineEdit, TouchSpinBox


class BaseScreen(QWidget):
    navigate = pyqtSignal(str)
    back_requested = pyqtSignal()

    def __init__(self, title: str, subtitle: str = "", parent=None) -> None:
        super().__init__(parent)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(24, 16, 24, 18)
        self.body.setSpacing(12)
        head = QHBoxLayout()
        title_box = QVBoxLayout()
        heading = QLabel(title); heading.setObjectName("HeroValue")
        title_box.addWidget(heading)
        if subtitle:
            sub = QLabel(subtitle); sub.setObjectName("Muted"); title_box.addWidget(sub)
        head.addLayout(title_box); head.addStretch()
        back = QPushButton("이전")
        back.setObjectName("BackButton")
        back.clicked.connect(self.back_requested.emit)
        head.addWidget(back)
        self.body.addLayout(head)

    def surface(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame(); frame.setObjectName("Surface")
        layout = QVBoxLayout(frame); layout.setContentsMargins(16, 12, 16, 12); layout.setSpacing(9)
        label = QLabel(title); label.setObjectName("SectionTitle"); layout.addWidget(label)
        return frame, layout


class ManualScreen(BaseScreen):
    def __init__(self) -> None:
        super().__init__("수동 제어", "점검 모드에서만 사용할 수 있으며, 버튼을 누르는 동안에만 동작합니다.")
        grid = QGridLayout(); grid.setSpacing(12); self.body.addLayout(grid, 1)
        amr, a = self.surface("AMR 조그 제어")
        pad = QGridLayout()
        for text, r, c in (("전진",0,1),("좌회전",1,0),("정지",1,1),("우회전",1,2),("후진",2,1)):
            b=QPushButton(text); b.setMinimumHeight(64); pad.addWidget(b,r,c)
        a.addLayout(pad); a.addWidget(QLabel("속도 설정  0.10 m/s   |   Cobot 안전 위치 확인됨"))
        grid.addWidget(amr,0,0,2,1)
        lift,l=self.surface("리프트")
        l.addWidget(QLabel("현재 높이")); val=QLabel("1.20 m"); val.setObjectName("HeroValue"); l.addWidget(val)
        row=QHBoxLayout(); row.addWidget(QPushButton("상승")); row.addWidget(QPushButton("하강")); l.addLayout(row)
        grid.addWidget(lift,0,1)
        out,o=self.surface("아웃트리거")
        og=QGridLayout()
        for i in range(3):
            og.addWidget(QLabel(f"Outrigger {i+1}\n접지 · 정상"),0,i)
            og.addWidget(QPushButton("전개"),1,i); og.addWidget(QPushButton("회수"),2,i)
        o.addLayout(og); level=QPushButton("자동 수평 보정"); level.setMinimumHeight(56); o.addWidget(level)
        grid.addWidget(out,1,1)
        alert=QLabel("안전 인터락: Cobot 검사 중에는 AMR 이동과 아웃트리거 회수가 비활성화됩니다.")
        alert.setObjectName("StatusWarn"); self.body.addWidget(alert)


class RunScreen(BaseScreen):
    def __init__(self) -> None:
        super().__init__("검사 실행", "검사 계획을 선택하고 사전 조건을 확인한 뒤 원주 사이클을 시작합니다.")
        row=QHBoxLayout(); self.body.addLayout(row,1)
        plan,p=self.surface("검사 계획")
        combo=QComboBox(); combo.addItems(["SMR Shell UT · 12구간", "교정 시편 · 4구간"]); p.addWidget(combo)
        p.addWidget(QLabel("대상: Ø 2.0 m\n시작 구간: 01\n회전 방향: 시계 방향\n구간당 검사 폭: 설정값 사용"))
        row.addWidget(plan,1)
        checks,c=self.surface("사전 조건")
        for text in ("비상정지 정상","AMR 정지","아웃트리거 접지","수평 허용 범위","Cobot 준비","UT 준비"):
            cb=QCheckBox(text); cb.setChecked(True); cb.setEnabled(False); c.addWidget(cb)
        row.addWidget(checks,1)
        action,ac=self.surface("실행")
        progress=QProgressBar(); progress.setValue(0); ac.addWidget(progress)
        ac.addWidget(QLabel("현재 구간 01 / 12\n상태: 대기\n예상 데이터 파일: SMR_20260716_001"))
        start=QPushButton("검사 사이클 시작"); start.setObjectName("PrimaryButton"); ac.addWidget(start)
        row.addWidget(action,1)


class SettingsMenuScreen(BaseScreen):
    def __init__(self) -> None:
        super().__init__("설정 / 진단", "장비 설정과 운전 기록을 관리합니다.")
        self.setObjectName("SettingsScreen")
        grid=QGridLayout(); grid.setSpacing(14); self.body.addLayout(grid,1)
        items=(("manual","수동 제어","AMR·리프트·아웃트리거"),("io","I/O 상태","PLC 입출력 진단"),("system","시스템 설정","시간·단위·로그"),("ut","UT 시스템 설정","검사 조건과 트리거"),("cobot","Cobot 설정","연결과 작업 슬롯"),("errors","오류 로그","활성 및 과거 오류"),("logs","로그 파일","날짜별 기록 관리"),("modes","운전 모드 저장","설정 슬롯 관리"))
        for i,(key,title,desc) in enumerate(items):
            b=QPushButton(f"{title}\n{desc}"); b.setObjectName("SettingsTile"); b.clicked.connect(lambda _,k=key:self.navigate.emit(k)); grid.addWidget(b,i//4,i%4)


class IOStatusScreen(BaseScreen):
    def __init__(self) -> None:
        super().__init__("I/O 상태", "PLC와 장비별 디지털·아날로그 신호를 조회합니다.")
        table=QTableWidget(10,5); table.setHorizontalHeaderLabels(["주소","신호명","방향","값","상태"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        data=[("X000","Emergency Stop","IN","OFF","정상"),("X001","Safety Scanner","IN","ON","정상"),("X010","Outrigger 1 Contact","IN","ON","정상"),("X011","Outrigger 2 Contact","IN","ON","정상"),("X012","Outrigger 3 Contact","IN","ON","정상"),("Y000","AMR Drive Enable","OUT","OFF","정상"),("Y010","Lift Brake","OUT","ON","정상"),("D100","Lift Height","IN","1200 mm","정상"),("D110","Roll","IN","0.00°","정상"),("D111","Pitch","IN","0.00°","정상")]
        for r,row in enumerate(data):
            for c,v in enumerate(row): table.setItem(r,c,QTableWidgetItem(v))
        table.horizontalHeader().setStretchLastSection(True); self.body.addWidget(table,1)


class FormScreen(BaseScreen):
    save_requested = pyqtSignal(str, dict)

    def __init__(self,scope:str,title:str,subtitle:str,fields:list[tuple[str,QWidget]]) -> None:
        super().__init__(title,subtitle)
        self.settings_scope = scope
        self._fields: dict[str, QWidget] = {}
        self._saved_values: dict[str, object] = {}
        self.setObjectName("SettingsScreen")
        surface,layout=self.surface("설정값")
        form=QFormLayout(); form.setSpacing(16)
        for label,widget in fields:
            field_label = QLabel(label)
            field_label.setObjectName("SettingsFieldLabel")
            widget.setObjectName("SettingsInput")
            self._fields[label] = widget
            form.addRow(field_label,widget)
        layout.addLayout(form)
        row=QHBoxLayout(); row.addStretch()
        cancel=QPushButton("변경 취소"); cancel.setObjectName("SettingsButton"); cancel.clicked.connect(self.restore_saved_values); row.addWidget(cancel)
        save=QPushButton("저장"); save.setObjectName("PrimarySettingsButton"); save.clicked.connect(self._request_save); row.addWidget(save)
        layout.addLayout(row)
        self.save_status = QLabel("PostgreSQL에서 설정을 불러오는 중입니다.")
        self.save_status.setObjectName("Muted")
        layout.addWidget(self.save_status)
        self.body.addWidget(surface,1)
        self._saved_values = self.values()

    def values(self) -> dict[str, object]:
        values: dict[str, object] = {}
        for key, widget in self._fields.items():
            if isinstance(widget, TouchLineEdit):
                values[key] = widget.text()
            elif isinstance(widget, (TouchSpinBox, TouchDoubleSpinBox)):
                values[key] = widget.value()
            elif isinstance(widget, QComboBox):
                values[key] = widget.currentText()
            elif isinstance(widget, QCheckBox):
                values[key] = widget.isChecked()
        return values

    def apply_values(self, values: dict[str, object]) -> None:
        for key, value in values.items():
            widget = self._fields.get(key)
            if isinstance(widget, TouchLineEdit):
                widget.setText(str(value))
            elif isinstance(widget, TouchSpinBox):
                widget.setValue(int(value))
            elif isinstance(widget, TouchDoubleSpinBox):
                widget.setValue(float(value))
            elif isinstance(widget, QComboBox):
                index = widget.findText(str(value))
                if index >= 0:
                    widget.setCurrentIndex(index)
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(value))
        self._saved_values = self.values()
        self.save_status.setObjectName("Muted")
        self.save_status.setText("PostgreSQL 저장값을 불러왔습니다.")
        self.save_status.style().unpolish(self.save_status)
        self.save_status.style().polish(self.save_status)

    def restore_saved_values(self) -> None:
        self.apply_values(self._saved_values)
        self.save_status.setText("저장된 값으로 되돌렸습니다.")

    def _request_save(self) -> None:
        self.save_status.setText("PostgreSQL에 저장하는 중입니다.")
        self.save_requested.emit(self.settings_scope, self.values())

    def mark_saved(self) -> None:
        self._saved_values = self.values()
        self.save_status.setObjectName("StatusGood")
        self.save_status.setText("PostgreSQL에 저장했습니다.")
        self.save_status.style().unpolish(self.save_status)
        self.save_status.style().polish(self.save_status)

    def show_storage_error(self, message: str) -> None:
        self.save_status.setObjectName("StatusDanger")
        self.save_status.setText(f"저장소 오류: {message}")
        self.save_status.style().unpolish(self.save_status)
        self.save_status.style().polish(self.save_status)


def line(text:str)->TouchLineEdit: return TouchLineEdit(text)
def spin(value:int,lo:int=0,hi:int=9999)->TouchSpinBox:
    w=TouchSpinBox(); w.setRange(lo,hi); w.setValue(value); return w
def dspin(value:float,suffix:str)->TouchDoubleSpinBox:
    w=TouchDoubleSpinBox(); w.setRange(-9999,9999); w.setDecimals(2); w.setValue(value); w.setSuffix(suffix); return w


class SystemSettingsScreen(FormScreen):
    def __init__(self):
        lang=QComboBox(); lang.addItems(["한국어","English"])
        super().__init__("system","시스템 설정","운영 환경과 로그 정책을 설정합니다.",[("장비 이름",line("SMR Operator Console")),("언어",lang),("상태 갱신 주기",spin(200,50,5000)),("로그 보존 기간",spin(365,1,3650)),("데이터 저장 위치",line("D:/SMR/Data")),("안전 설정",QLabel("PLC 관리 · 읽기 전용"))])

class UTSettingsScreen(FormScreen):
    def __init__(self):
        super().__init__("ut","UT 시스템 설정","검사 중에는 품질 관련 설정이 잠깁니다.",[("UT 주소",line("192.168.0.50")),("통신 포트",spin(5000)),("검사 조건",line("SMR_SHELL_A")),("주사 속도",dspin(150," mm/s")),("게인",dspin(26," dB")),("마킹 트리거",QCheckBox("기준 초과 시 출력"))])

class CobotSettingsScreen(FormScreen):
    def __init__(self):
        tasks=QComboBox(); tasks.addItems([f"TASK {i:02d}" for i in range(1,25)])
        super().__init__("cobot","Cobot 설정","연결 설정과 검사 작업 슬롯을 관리합니다.",[("IP 주소",line("192.168.0.40")),("포트",spin(500)),("장치 ID",spin(1)),("선택 작업",tasks),("연결 상태",QLabel("● 연결됨")),("마지막 응답",QLabel("12 ms"))])


class ErrorLogScreen(BaseScreen):
    def __init__(self):
        super().__init__("오류 로그","활성 오류를 먼저 확인하고 원인을 해소한 뒤 리셋합니다.")
        table=QTableWidget(4,6); table.setHorizontalHeaderLabels(["발생 시각","코드","장비","심각도","메시지","상태"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        rows=[("2026-07-16 08:20:14","ER000","PLC","주의","Safety stop test","해제"),("2026-07-15 17:42:10","ER112","Cobot","오류","Response timeout","복구"),("2026-07-15 10:11:08","WR021","Lift","주의","Height deviation","복구"),("2026-07-14 15:02:33","IF005","UT","정보","Connection restored","확인")]
        for r,row in enumerate(rows):
            for c,v in enumerate(row):table.setItem(r,c,QTableWidgetItem(v))
        table.horizontalHeader().setStretchLastSection(True); self.body.addWidget(table,1)
        buttons=QHBoxLayout(); buttons.addStretch(); buttons.addWidget(QPushButton("선택 오류 해제")); buttons.addWidget(QPushButton("다운로드")); self.body.addLayout(buttons)


class LogFilesScreen(BaseScreen):
    def __init__(self):
        super().__init__("로그 파일","검사 작업과 연결된 기록을 날짜별로 관리합니다.")
        table=QTableWidget(5,5); table.setHorizontalHeaderLabels(["생성 시각","작업 ID","종류","크기","파일명"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        rows=[("2026-07-16 08:30","SMR-001","검사","18.4 MB","inspection_001.log"),("2026-07-16 08:20","-","오류","320 KB","error_20260716.log"),("2026-07-15 16:44","SMR-000","검사","22.1 MB","inspection_000.log"),("2026-07-15 09:00","-","시스템","1.3 MB","system_20260715.log"),("2026-07-14 09:00","-","시스템","1.1 MB","system_20260714.log")]
        for r,row in enumerate(rows):
            for c,v in enumerate(row):table.setItem(r,c,QTableWidgetItem(v))
        table.horizontalHeader().setStretchLastSection(True); self.body.addWidget(table,1)
        row=QHBoxLayout(); row.addStretch(); row.addWidget(QPushButton("선택 다운로드")); delete=QPushButton("선택 삭제"); delete.setObjectName("DangerButton"); row.addWidget(delete); self.body.addLayout(row)


class ModeSlotsScreen(BaseScreen):
    def __init__(self):
        super().__init__("운전 모드 저장","검사 조건과 장비 위치를 슬롯으로 관리합니다.")
        grid=QGridLayout(); self.body.addLayout(grid,1)
        for i in range(1,9):
            text=f"슬롯 {i}\n" + ("SMR Shell 기본\n2026-07-16" if i==1 else "비어 있음")
            b=QPushButton(text); b.setMinimumHeight(105); grid.addWidget(b,(i-1)//4,(i-1)%4)
        row=QHBoxLayout(); row.addStretch(); row.addWidget(QPushButton("불러오기")); row.addWidget(QPushButton("현재 설정 저장")); self.body.addLayout(row)
