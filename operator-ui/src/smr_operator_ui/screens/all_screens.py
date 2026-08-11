"""보조 운영 화면과 PostgreSQL 기반 공통 설정 폼을 제공한다."""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QListWidget, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from smr_operator_ui.components import MetricRow
from smr_operator_ui.keypad import TouchDoubleSpinBox, TouchLineEdit, TouchSpinBox


class BaseScreen(QWidget):
    """제목, 본문, 이전 화면 요청을 제공하는 공통 보조 화면 틀."""

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
        """스타일이 적용된 내용 카드를 만들고 내부 레이아웃을 반환한다."""
        frame = QFrame(); frame.setObjectName("Surface")
        layout = QVBoxLayout(frame); layout.setContentsMargins(16, 12, 16, 12); layout.setSpacing(9)
        label = QLabel(title); label.setObjectName("SectionTitle"); layout.addWidget(label)
        return frame, layout


class ManualScreen(BaseScreen):
    """AMR, 리프트, 아웃트리거 수동 제어 화면."""

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


class StatusBlock(QWidget):
    """항목명 아래에 값을 두는 좁은 상태 표시 블록.

    `MetricRow`는 항목명과 값을 한 줄에 놓아 값이 길면 항목명을 덮는다.
    상태 문구는 길어질 수 있으므로 여기서는 두 줄로 나눈다.
    """

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(2)
        name=QLabel(label); name.setObjectName("MetricLabel")
        self.value_label=QLabel("-"); self.value_label.setObjectName("MetricValue")
        layout.addWidget(name); layout.addWidget(self.value_label)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


class CobotManualScreen(BaseScreen):
    """엘리트 협동로봇의 연결, 전원, 프로그램 제어와 상태를 다루는 화면.

    화면은 장비를 직접 호출하지 않고 `command_requested`로 요청만 알린다.
    실제 통신 연결 시 이 시그널을 Dashboard 명령이나 ROS 2 서비스에 잇는다.
    """

    command_requested = pyqtSignal(str)

    # 버튼 문구와 명령 키를 함께 둔다. 명령 키는 robot_control_node가 제공하는
    # robot/dashboard/* 서비스 이름과 일치시켜 이후 연결을 단순하게 만든다.
    _CONNECTION = (("연결", "connect"), ("연결 해제", "disconnect"))
    _POWER = (("전원 ON", "power_on"), ("전원 OFF", "power_off"), ("브레이크 해제", "brake_release"))
    _PROGRAM = (("재생", "play"), ("일시정지", "pause"), ("정지", "stop"))
    _MOTION = (("홈 이동", "home"),)

    # 알람은 계속 쌓이므로 표시 개수를 제한한다.
    MAX_ALARMS = 50

    # TCP 자세를 구성하는 6개 성분. 위치와 회전 성분의 단위가 서로 다르므로
    # 표시 순서와 단위를 이 한 곳에서만 정의한다.
    _POSE_AXES = (
        ("x", "mm"), ("y", "mm"), ("z", "mm"),
        ("rx", "mrad"), ("ry", "mrad"), ("rz", "mrad"),
    )

    def __init__(self) -> None:
        super().__init__("Cobot 수동 제어", "점검 모드에서만 사용합니다. 검사 사이클 진행 중에는 사용하지 마십시오.")
        # 720 px 높이 안에서 카드가 서로 밀리지 않도록 조작부는 4열 격자에
        # 담고, TCP 6개 성분은 아래쪽 영역 전체를 사용한다.
        top=QGridLayout(); top.setSpacing(12); self.body.addLayout(top,3)

        conn,c=self.surface("연결")
        self.endpoint_label=QLabel(); self.endpoint_label.setObjectName("Muted"); c.addWidget(self.endpoint_label)
        self.connection_state=QLabel("● 연결 안 됨"); self.connection_state.setObjectName("StatusDanger"); c.addWidget(self.connection_state)
        c.addLayout(self._button_row(self._CONNECTION))
        c.addStretch()
        top.addWidget(conn,0,0)

        power,p=self.surface("전원 · 브레이크")
        p.addLayout(self._button_row(self._POWER))
        p.addWidget(self._note("브레이크 해제 전 로봇 주변을 확인하십시오."))
        p.addStretch()
        top.addWidget(power,0,1)

        program,pg=self.surface("프로그램 제어")
        pg.addLayout(self._button_row(self._PROGRAM))
        pg.addWidget(self._note("정지는 프로그램을 처음으로 되돌립니다."))
        pg.addStretch()
        top.addWidget(program,0,2)

        motion,mo=self.surface("이동")
        mo.addLayout(self._button_row(self._MOTION))
        mo.addWidget(self._note("경로 확인 후 실행"))
        mo.addStretch()
        top.addWidget(motion,0,3)

        alarms,al=self.surface("알람")
        self.alarm_list=QListWidget(); self.alarm_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.alarm_list.setMinimumHeight(44)
        al.addWidget(self.alarm_list,1)
        # 알람 문구는 길어서 좁은 열에서는 읽을 수 없다. 아래 행을 상태와
        # 절반씩 나눠 쓴다.
        top.addWidget(alarms,1,0,1,2)

        status,s=self.surface("로봇 상태")
        self.metrics={
            "robot_mode": StatusBlock("로봇 모드"),
            "control_method": StatusBlock("제어 방식"),
            "operation_mode": StatusBlock("운전 모드"),
        }
        # 세로로 쌓으면 아래 카드를 밀어내므로 세 항목을 가로로 배치한다.
        status_row=QHBoxLayout(); status_row.setSpacing(12)
        # 로봇 모드 값(CONFIRM_SAFETY 등)이 가장 길어 폭을 더 준다.
        for row,stretch in zip(self.metrics.values(),(3,2,2)):
            status_row.addWidget(row,stretch)
        s.addLayout(status_row)
        s.addStretch()
        top.addWidget(status,1,2,1,2)
        # 버튼 수와 문구 길이가 열마다 다르므로 너비를 같게 나누지 않는다.
        for column,stretch in enumerate((3,4,4,2)):
            top.setColumnStretch(column,stretch)

        bottom=QHBoxLayout(); bottom.setSpacing(12); self.body.addLayout(bottom,2)
        self.tcp_rows=self._pose_card(bottom,"TCP 현재값")
        self.zero_rows=self._pose_card(bottom,"제로점 기준")

        self.activity_label=QLabel("장비에 연결되어 있지 않습니다. 화면 동작만 확인할 수 있습니다.")
        self.activity_label.setObjectName("Muted"); self.activity_label.setWordWrap(True)
        self.body.addWidget(self.activity_label)
        self._alarm_empty = True
        self.set_endpoint("192.168.227.134")
        self.set_alarms([])

    def _note(self, text: str) -> QLabel:
        """카드 폭을 넘지 않도록 줄바꿈되는 안내 문구를 만든다."""
        label=QLabel(text); label.setObjectName("Muted"); label.setWordWrap(True)
        return label

    def _pose_card(self, parent: QHBoxLayout, title: str) -> dict[str, MetricRow]:
        """TCP 6개 성분을 개별 행으로 보여주는 카드를 만든다."""
        card,layout=self.surface(title)
        grid=QGridLayout(); grid.setSpacing(6); grid.setContentsMargins(0,0,0,0)
        rows: dict[str, MetricRow] = {}
        for index,(axis,unit) in enumerate(self._POSE_AXES):
            # 위치 성분과 회전 성분을 각각 한 줄에 두어 3열 2행으로 배치한다.
            row=MetricRow(f"{axis.upper()} ({unit})","-")
            rows[axis]=row
            grid.addWidget(row,index//3,index%3)
        for column in range(3):
            grid.setColumnStretch(column,1)
        layout.addLayout(grid)
        layout.addStretch()
        parent.addWidget(card,1)
        return rows

    def _button_row(self, items: tuple[tuple[str, str], ...]) -> QHBoxLayout:
        """명령 버튼을 한 줄로 배치하고 눌림을 시그널로 전달한다."""
        row=QHBoxLayout()
        for text,command in items:
            button=QPushButton(text); button.setMinimumHeight(52)
            # command를 기본 인자로 고정하지 않으면 모든 버튼이 반복문의
            # 마지막 명령만 전달하게 된다.
            button.clicked.connect(lambda _,key=command,label=text:self._request(key,label))
            row.addWidget(button)
        return row

    def _request(self, command: str, label: str) -> None:
        """명령 요청을 기록하고 상위 계층에 전달한다."""
        self.activity_label.setText(f"'{label}' 명령을 요청했습니다. (장비 미연결)")
        self.command_requested.emit(command)

    def set_endpoint(self, ip: str) -> None:
        """연결 설정 화면에서 지정한 대상 주소를 표시한다."""
        self.endpoint_label.setText(f"대상  {ip}")

    def set_connected(self, connected: bool) -> None:
        """세 채널 연결 여부를 화면 상단에 표시한다."""
        self.connection_state.setText("● 연결됨" if connected else "● 연결 안 됨")
        self.connection_state.setObjectName("StatusGood" if connected else "StatusDanger")
        self.connection_state.style().unpolish(self.connection_state)
        self.connection_state.style().polish(self.connection_state)

    def apply_status(self, values: dict[str, str]) -> None:
        """robot/status/* 토픽에 대응하는 표시값을 갱신한다."""
        for key, row in self.metrics.items():
            if key in values:
                row.set_value(values[key])

    def apply_tcp(self, values: dict[str, str]) -> None:
        """현재 TCP 자세를 성분별로 표시한다. 키는 x, y, z, rx, ry, rz이다."""
        self._apply_pose(self.tcp_rows, values)

    def apply_zero_point(self, values: dict[str, str]) -> None:
        """제로점 기준 TCP 자세를 성분별로 표시한다."""
        self._apply_pose(self.zero_rows, values)

    def _apply_pose(self, rows: dict[str, MetricRow], values: dict[str, str]) -> None:
        """전달된 성분만 갱신하고 나머지는 이전 값을 유지한다."""
        for axis, row in rows.items():
            if axis in values:
                row.set_value(values[axis])

    def set_alarms(self, alarms: list[str]) -> None:
        """알람 목록 전체를 지정한 내용으로 교체한다."""
        self.alarm_list.clear()
        self._alarm_empty = not alarms
        self.alarm_list.addItems(alarms or ["활성 알람 없음"])

    def add_alarm(self, text: str) -> None:
        """수신한 알람을 최신 항목이 위에 오도록 추가한다."""
        if self._alarm_empty:
            # 비어 있음 안내 문구를 실제 알람과 섞이지 않게 먼저 지운다.
            self.alarm_list.clear()
            self._alarm_empty = False
        stamp = datetime.now().strftime("%H:%M:%S")
        self.alarm_list.insertItem(0, f"{stamp}  {text}")
        while self.alarm_list.count() > self.MAX_ALARMS:
            self.alarm_list.takeItem(self.alarm_list.count() - 1)
        self.alarm_list.scrollToTop()


class RunScreen(BaseScreen):
    """검사 계획과 사전 조건을 확인하는 화면."""

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
    """설정, 진단, 로그, 수동 제어 화면으로 이동하는 타일 메뉴."""

    def __init__(self) -> None:
        super().__init__("설정 / 진단", "장비 설정과 운전 기록을 관리합니다.")
        self.setObjectName("SettingsScreen")
        grid=QGridLayout(); grid.setSpacing(14); self.body.addLayout(grid,1)
        items=(("manual","수동 제어","AMR·리프트·아웃트리거"),("cobot_manual","Cobot 수동 제어","연결·전원·프로그램 제어"),("io","I/O 상태","PLC 입출력 진단"),("connection","연결 설정","협동로봇·PLC·MQTT IP"),("system","시스템 설정","시간·단위·로그"),("ut","UT 시스템 설정","검사 조건과 트리거"),("cobot","Cobot 설정","검사 작업 슬롯"),("errors","오류 로그","활성 및 과거 오류"),("logs","로그 파일","날짜별 기록 관리"),("modes","운전 모드 저장","설정 슬롯 관리"))
        for i,(key,title,desc) in enumerate(items):
            # key를 기본 인자로 고정한다. 그렇지 않으면 모든 lambda가
            # 반복문의 마지막 key만 참조하게 된다.
            b=QPushButton(f"{title}\n{desc}"); b.setObjectName("SettingsTile"); b.clicked.connect(lambda _,k=key:self.navigate.emit(k)); grid.addWidget(b,i//4,i%4)


class IOStatusScreen(BaseScreen):
    """PLC와 장비의 입출력을 표시하는 읽기 전용 진단 표."""

    def __init__(self) -> None:
        super().__init__("I/O 상태", "PLC와 장비별 디지털·아날로그 신호를 조회합니다.")
        table=QTableWidget(10,5); table.setHorizontalHeaderLabels(["주소","신호명","방향","값","상태"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # PLC 어댑터 연결 전에도 화면을 개발할 수 있도록 임시 데이터를
        # 사용한다. 운영자가 값을 바꾸지 못하도록 표는 읽기 전용이다.
        data=[("X000","Emergency Stop","IN","OFF","정상"),("X001","Safety Scanner","IN","ON","정상"),("X010","Outrigger 1 Contact","IN","ON","정상"),("X011","Outrigger 2 Contact","IN","ON","정상"),("X012","Outrigger 3 Contact","IN","ON","정상"),("Y000","AMR Drive Enable","OUT","OFF","정상"),("Y010","Lift Brake","OUT","ON","정상"),("D100","Lift Height","IN","1200 mm","정상"),("D110","Roll","IN","0.00°","정상"),("D111","Pitch","IN","0.00°","정상")]
        for r,row in enumerate(data):
            for c,v in enumerate(row): table.setItem(r,c,QTableWidgetItem(v))
        table.horizontalHeader().setStretchLastSection(True); self.body.addWidget(table,1)


class FormScreen(BaseScreen):
    """입력 위젯 값을 항목명 기준으로 직렬화하는 공통 설정 폼."""

    save_requested = pyqtSignal(str, dict)

    def __init__(self,scope:str,title:str,subtitle:str,fields:list[tuple[str,QWidget|None]],columns:int=1) -> None:
        super().__init__(title,subtitle)
        self.settings_scope = scope
        self._fields: dict[str, QWidget] = {}
        self._saved_values: dict[str, object] = {}
        self.setObjectName("SettingsScreen")
        surface,layout=self.surface("설정값")

        # 위젯이 없는 항목은 입력이 아니라 구역 제목이다. 구역을 기준으로
        # 항목을 묶어 두면 열 배치와 단일 폼 배치를 같은 정의로 처리할 수 있다.
        groups: list[tuple[str, list[tuple[str, QWidget]]]] = []
        for label,widget in fields:
            if widget is None:
                groups.append((label, []))
                continue
            if not groups:
                groups.append(("", []))
            groups[-1][1].append((label, widget))

        # 화면 높이는 720 px로 고정되어 있어 항목이 많으면 세로 한 줄로는
        # 넘친다. 구역을 열로 나누어 배치한다.
        columns_layout=QHBoxLayout(); columns_layout.setSpacing(24)
        per_column=max(1,-(-len(groups)//max(1,columns)))
        column: QVBoxLayout | None = None
        for index,(section_title,entries) in enumerate(groups):
            if index % per_column == 0:
                column=QVBoxLayout(); column.setSpacing(8); columns_layout.addLayout(column,1)
            if section_title:
                section=QLabel(section_title); section.setObjectName("SectionTitle"); column.addWidget(section)
            form=QFormLayout(); form.setSpacing(16 if columns == 1 else 10)
            for label,widget in entries:
                field_label = QLabel(label)
                field_label.setObjectName("SettingsFieldLabel")
                widget.setObjectName("SettingsInput")
                self._fields[label] = widget
                form.addRow(field_label,widget)
            column.addLayout(form)
        for i in range(columns_layout.count()):
            item=columns_layout.itemAt(i)
            if item.layout() is not None:
                item.layout().addStretch()
        layout.addLayout(columns_layout,1)
        row=QHBoxLayout(); row.addStretch()
        cancel=QPushButton("변경 취소"); cancel.setObjectName("SettingsButton"); cancel.clicked.connect(self.restore_saved_values)
        save=QPushButton("저장"); save.setObjectName("PrimarySettingsButton"); save.clicked.connect(self._request_save)
        # 문구 길이가 달라도 두 버튼이 같은 크기로 보이도록 폭을 맞춘다.
        for button in (cancel,save):
            button.setMinimumWidth(140); row.addWidget(button)
        layout.addLayout(row)
        self.save_status = QLabel("PostgreSQL에서 설정을 불러오는 중입니다.")
        self.save_status.setObjectName("Muted")
        layout.addWidget(self.save_status)
        self.body.addWidget(surface,1)
        self._saved_values = self.values()

    def values(self) -> dict[str, object]:
        """입력값을 JSONB와 호환되는 Python 자료형으로 수집한다."""
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
        """불러온 설정값을 자료형이 맞는 입력 위젯에 적용한다."""
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
        """저장하지 않은 변경을 버리고 최근 확정값으로 복원한다."""
        self.apply_values(self._saved_values)
        self.save_status.setText("저장된 값으로 되돌렸습니다.")

    def _request_save(self) -> None:
        """직접 DB를 호출하지 않고 서비스 계층에 저장을 요청한다."""
        self.save_status.setText("PostgreSQL에 저장하는 중입니다.")
        self.save_requested.emit(self.settings_scope, self.values())

    def mark_saved(self) -> None:
        """현재 값을 이후 변경 취소 시 사용할 기준값으로 기록한다."""
        self._saved_values = self.values()
        self.save_status.setObjectName("StatusGood")
        self.save_status.setText("PostgreSQL에 저장했습니다.")
        self.save_status.style().unpolish(self.save_status)
        self.save_status.style().polish(self.save_status)

    def show_storage_error(self, message: str) -> None:
        """저장소 오류를 해당 설정 화면에 사용자용 문구로 표시한다."""
        self.save_status.setObjectName("StatusDanger")
        self.save_status.setText(f"저장소 오류: {message}")
        self.save_status.style().unpolish(self.save_status)
        self.save_status.style().polish(self.save_status)


def line(text:str)->TouchLineEdit:
    """공통 가상 키보드를 여는 텍스트 입력 필드를 만든다."""
    return TouchLineEdit(text)
def spin(value:int,lo:int=0,hi:int=9999)->TouchSpinBox:
    """공통 터치 키패드를 여는 정수 입력 필드를 만든다."""
    w=TouchSpinBox(); w.setRange(lo,hi); w.setValue(value); return w
def dspin(value:float,suffix:str)->TouchDoubleSpinBox:
    """공학 단위가 표시되는 실수 터치 입력 필드를 만든다."""
    w=TouchDoubleSpinBox(); w.setRange(-9999,9999); w.setDecimals(2); w.setValue(value); w.setSuffix(suffix); return w


class SystemSettingsScreen(FormScreen):
    """콘솔, 갱신 주기, 보존 기간, 저장 경로를 설정하는 화면."""

    def __init__(self):
        lang=QComboBox(); lang.addItems(["한국어","English"])
        super().__init__("system","시스템 설정","운영 환경과 로그 정책을 설정합니다.",[("장비 이름",line("SMR Operator Console")),("언어",lang),("상태 갱신 주기",spin(200,50,5000)),("로그 보존 기간",spin(365,1,3650)),("데이터 저장 위치",line("D:/SMR/Data")),("안전 설정",QLabel("PLC 관리 · 읽기 전용"))])

class UTSettingsScreen(FormScreen):
    """초음파 검사 장비의 연결 및 수집 조건 설정 화면."""

    def __init__(self):
        super().__init__("ut","UT 시스템 설정","검사 중에는 품질 관련 설정이 잠깁니다.",[("UT 주소",line("192.168.0.50")),("통신 포트",spin(5000)),("검사 조건",line("SMR_SHELL_A")),("주사 속도",dspin(150," mm/s")),("게인",dspin(26," dB")),("마킹 트리거",QCheckBox("기준 초과 시 출력"))])

class CobotSettingsScreen(FormScreen):
    """협동로봇 작업 슬롯을 설정하는 화면."""

    def __init__(self):
        tasks=QComboBox(); tasks.addItems([f"TASK {i:02d}" for i in range(1,25)])
        super().__init__("cobot","Cobot 설정","검사 작업 슬롯을 관리합니다. 연결 정보는 연결 설정 화면에서 관리합니다.",[("선택 작업",tasks),("연결 상태",QLabel("● 연결됨")),("마지막 응답",QLabel("12 ms"))])


class ConnectionSettingsScreen(FormScreen):
    """협동로봇, 차량용 PLC, MQTT Broker의 유선 연결 정보를 한 화면에서 설정한다."""

    def __init__(self):
        plc_protocol=QComboBox(); plc_protocol.addItems(["KEYENCE MC Protocol","Modbus TCP"])
        super().__init__(
            "connection","연결 설정",
            "각 장비의 유선 연결 정보를 설정합니다. 변경한 값은 다음 연결 시도부터 적용됩니다.",
            [
                ("협동로봇",None),
                ("협동로봇 IP",line("192.168.227.134")),
                ("Dashboard 포트",spin(29999,1,65535)),
                ("Primary 포트",spin(30001,1,65535)),
                ("Modbus 포트",spin(502,1,65535)),
                ("차량용 PLC",None),
                ("PLC IP",line("192.168.0.10")),
                ("PLC 포트",spin(5000,1,65535)),
                ("PLC 프로토콜",plc_protocol),
                ("PLC 국번",spin(1,0,255)),
                ("MQTT Broker",None),
                ("MQTT Broker 주소",line("127.0.0.1")),
                ("MQTT 포트",spin(1883,1,65535)),
                ("MQTT Client ID",line("smr-operator-ui")),
                ("MQTT Keep Alive",spin(60,10,3600)),
                ("MQTT TLS 사용",QCheckBox()),
            ],
            columns=3,
        )


class ErrorLogScreen(BaseScreen):
    """현재 및 과거 알람을 보여주는 읽기 전용 화면."""

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
    """검사 및 시스템 로그 파일 관리 화면."""

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
    """저장된 운전 모드 슬롯을 선택하는 화면."""

    def __init__(self):
        super().__init__("운전 모드 저장","검사 조건과 장비 위치를 슬롯으로 관리합니다.")
        grid=QGridLayout(); self.body.addLayout(grid,1)
        for i in range(1,9):
            text=f"슬롯 {i}\n" + ("SMR Shell 기본\n2026-07-16" if i==1 else "비어 있음")
            b=QPushButton(text); b.setMinimumHeight(105); grid.addWidget(b,(i-1)//4,(i-1)%4)
        row=QHBoxLayout(); row.addStretch(); row.addWidget(QPushButton("불러오기")); row.addWidget(QPushButton("현재 설정 저장")); self.body.addLayout(row)
