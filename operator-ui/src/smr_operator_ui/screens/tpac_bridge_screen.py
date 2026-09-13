"""TPAC 등 외부 Modbus 장비로 로봇 값을 중계하는 화면.

로봇 Modbus(502)를 직접 폴링해 관절/TCP 값을 읽고, 그 값을 이 화면이 여는
Modbus TCP 서버로 다시 내보낸다. 로직은 `services/tpac_bridge`(원본:
`\\wsl.localhost\\...\\src\\tpac_bridge\\modbus_bridge.py`)를 그대로 쓰고,
여기서는 표시만 간추린다: 모니터링은 "주소 + 값" 수준으로, 연결 여부는
배지 색으로 보여준다.

ROS 쪽 `robot_control_node`가 이미 로봇을 폴링하고 있지만 그건 별개의
Modbus 접속이다 — 이 화면은 TPAC이 보는 서버를 독립적으로 열고 켜 두어야
하므로, 운영 UI 프로세스가 로봇에 두 번째 Modbus 클라이언트로 붙는다.

다만 로봇 노드가 이미 붙어 있는데 이 화면에서 또 손으로 "연결"을 누르게
하는 건 불합리하다. 그래서 `robot/status/connected`(ROS, robot_control_node
연결 여부)를 따라 "로봇에서 읽기"를 자동으로 켜고 끄고, 그게 실제로 붙으면
"외부에 제공" 서버도 자동으로 켠다 — `app.py`의 `on_robot_link_changed()`
호출이 그 경로다.
버튼은 다른 IP로 시험하거나 수동으로 다시 걸 때 쓰는 보조 수단으로 남긴다.

작은 화면(펜던트, 저해상도 모니터)에서도 카드가 잘리지 않도록 본문 전체를
QScrollArea에 담는다 — 이 화면은 다른 720px 고정 화면과 달리 입력 항목이
많아 세로 공간을 다 채우지 못할 수 있다.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from smr_operator_ui.keypad import TouchComboBox, TouchLineEdit, TouchSpinBox
from smr_operator_ui.screens.all_screens import BaseScreen
from smr_operator_ui.services.tpac_bridge import JOINT_NAMES, OUT_FORMATS, TCP_NAMES, WORD_ORDERS
from smr_operator_ui.services.tpac_bridge.bridge_core import ExternalServer, MIRROR_UR, RobotPoller
from smr_operator_ui.services.tpac_bridge.robot_map import (
    CONTROL_TXT, DEFAULT_READ_MAP, OPERATION_TXT, ROBOT_MODE_TXT, RUNNING_TXT,
    clamp_int16, to_uint16,
)

MAX_LOG_LINES = 200
# 외부 요청이 이 시간(초) 안에 한 번도 없으면 TPAC이 끊겼다고 본다.
TPAC_LINK_TIMEOUT_S = 5.0
# UR 주소 매핑의 기본 자리. bridge_core.MIRROR_UR 과 같다 — 라벨 표시용으로만 쓴다.
_UR_HINT = (f"관절 {MIRROR_UR['joint']} / pose {MIRROR_UR['pose']} / "
           f"속도 {MIRROR_UR['speed']} / offset {MIRROR_UR['offset']}")


class _BridgeSignals(QObject):
    """폴링/서버 스레드에서 온 콜백을 GUI 스레드로 넘기는 신호 모음.

    `RobotPoller`/`ExternalServer`는 자기 스레드에서 콜백을 그냥 부르므로,
    직접 위젯을 만지면 안 된다. 신호로만 emit 하면 Qt가 GUI 스레드로
    큐잉해 안전하게 슬롯을 실행한다.
    """

    data = pyqtSignal(object)
    log = pyqtSignal(str)
    robot_conn = pyqtSignal(bool, str)
    srv_state = pyqtSignal(bool, str)
    srv_request = pyqtSignal(float, str, int, int, int)


class TpacBridgeScreen(BaseScreen):
    """로봇 Modbus 값을 읽어 TPAC 등 외부 Modbus 장비에 서버로 제공한다."""

    # 실제 TPAC(외부 마스터)이 서버에서 값을 읽어가고 있는지. 상단 바
    # 배지가 이 신호를 구독한다.
    tpac_link_changed = pyqtSignal(bool)

    def __init__(self) -> None:
        super().__init__("TPAC 설정 / TCP 인코딩",
                         "로봇에서 읽은 관절·TCP 값을 외부 Modbus 서버로 중계합니다.")
        self.setObjectName("SettingsScreen")

        self.sig = _BridgeSignals()
        self.poller: RobotPoller | None = None
        self.server = ExternalServer(
            on_log=self.sig.log.emit,
            on_state=self.sig.srv_state.emit,
            on_request=self.sig.srv_request.emit,
        )
        self._last_ext_request_ts = 0.0
        self._tpac_connected = False
        # 작업 평면(제로점 기준 스캔 좌표) 위치 — 표 갱신용으로 마지막 값만
        # 들고 있는다. 속도는 베이스와 공용이라(둘 다 같은 로봇 동작을
        # 다른 원점 기준으로 볼 뿐이라 변화율은 같다) 따로 계산하지 않는다.
        self._last_plane = [0] * 6

        # 본문이 길어질 수 있어 스크롤 영역에 담는다. 작은 화면에서도
        # 아래쪽 표·로그가 잘리지 않고 스크롤로 보인다.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        inner = QWidget()
        content = QVBoxLayout(inner)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(14)
        scroll.setWidget(inner)
        self.body.addWidget(scroll, 1)

        top = QHBoxLayout(); top.setSpacing(14); content.addLayout(top)
        top.addWidget(self._build_robot_card(), 1)
        top.addWidget(self._build_server_card(), 1)

        tables = QHBoxLayout(); tables.setSpacing(14); content.addLayout(tables)
        tables.addWidget(self._build_table_card("읽은 값 (로봇 → 여기)", "read_table"), 1)
        tables.addWidget(self._build_table_card("제공 값 (여기 → 외부)", "serve_table"), 1)

        log_card, log_layout = self.surface("통신 로그")
        self.log_list = QListWidget()
        self.log_list.setMinimumHeight(140)
        log_layout.addWidget(self.log_list)
        content.addWidget(log_card)
        # 같은 내용의 메시지(외부요청 반복 등)를 무한히 쌓지 않고, 이미
        # 있으면 그 줄을 맨 아래로 옮기기만 한다. 메시지 문자열 그대로를
        # 키로 쓴다 — 요청 로그는 who/fc/addr/count가 그대로 문자열에
        # 들어가 있어 내용이 같으면 문자열도 완전히 같다.
        self._log_items: dict[str, QListWidgetItem] = {}

        self.sig.data.connect(self._on_data)
        self.sig.log.connect(self._append_log)
        self.sig.robot_conn.connect(self._on_robot_conn)
        self.sig.srv_state.connect(self._on_srv_state)
        self.sig.srv_request.connect(self._on_srv_request)

        # 외부(TPAC)가 최근에 실제로 읽어갔는지 주기적으로 확인한다.
        # on_request 콜백만으로는 "더 이상 안 읽어간다"를 알 수 없어서다.
        self._link_timer = QTimer(self)
        self._link_timer.timeout.connect(self._check_tpac_link)
        self._link_timer.start(1000)

    # ------------------------------------------------------------ 자동 연동
    def on_robot_link_changed(self, connected: bool) -> None:
        """robot_control_node(ROS)의 로봇 연결 여부를 그대로 따라간다.

        노드가 이미 로봇에 붙어 있는데 이 화면에서 또 수동으로 연결을
        누르게 하는 건 불합리하다는 지적에 따라, 여기서 자동으로 켜고 끈다.
        서버("외부에 제공")는 손대지 않는다 — `_on_robot_conn`에서 로봇 폴링이 실제로
        성공했을 때 자동으로 켠다. 로봇 연결이 잠깐 끊겨도 서버는 계속
        열어 둬, TPAC이 alive 카운터로 정지 여부를 스스로 판단하게 한다.
        """
        if connected:
            if self.poller is None:
                self._start_robot()
        elif self.poller is not None:
            self._stop_robot()

    def _check_tpac_link(self) -> None:
        """외부 요청이 최근에 있었는지로 TPAC 실제 연결 여부를 추정한다."""
        connected = (self.server.running and
                    time.monotonic() - self._last_ext_request_ts < TPAC_LINK_TIMEOUT_S)
        if connected != self._tpac_connected:
            self._tpac_connected = connected
            self.tpac_link_changed.emit(connected)

    def set_robot_endpoint(self, ip: str, port: int | None = None) -> None:
        """"연결 설정"의 협동로봇 주소를 그대로 받아 쓴다.

        이 화면은 로봇 주소의 주인이 아니라 사용자다 — 표시만 하고,
        수정은 "연결 설정" 화면에서만 한다.
        """
        if ip:
            self.robot_ip.setText(ip)
        if port:
            self.robot_port.setValue(int(port))

    # ------------------------------------------------------------ 카드 구성
    def _build_robot_card(self) -> QWidget:
        card, layout = self.surface("로봇에서 읽기")
        form = QFormLayout(); form.setSpacing(8)
        self.robot_ip = TouchLineEdit("192.168.227.134")
        self.robot_port = TouchSpinBox(); self.robot_port.setRange(1, 65535); self.robot_port.setValue(502)
        self.robot_unit = TouchSpinBox(); self.robot_unit.setRange(0, 247); self.robot_unit.setValue(1)
        self.robot_interval = TouchSpinBox(); self.robot_interval.setRange(20, 2000); self.robot_interval.setValue(100)
        self.robot_interval.setSuffix(" ms")
        # 로봇 IP/포트는 "연결 설정" 화면 하나에서만 관리한다 — 같은 주소를
        # 화면마다 따로 입력하다 서로 어긋나는 것을 막는다. 여기서는 그 값을
        # 받아 보여 주기만 하므로 직접 고칠 수 없게 잠근다
        # (app.py `_sync_robot_endpoint` 가 값을 넣어 준다).
        self.robot_ip.setReadOnly(True)
        self.robot_ip.setEnabled(False)
        self.robot_port.setReadOnly(True)
        self.robot_port.setEnabled(False)
        form.addRow("로봇 IP", self.robot_ip)
        form.addRow("포트", self.robot_port)
        form.addRow("Unit ID", self.robot_unit)
        form.addRow("폴링 주기", self.robot_interval)
        layout.addLayout(form)

        # 읽어올 주소만 사용자가 바꿀 수 있게 둔다. 기본값은 로봇 팀
        # config/modbus_registers.json 이 현재 쓰는 자리(robot_map.
        # DEFAULT_READ_MAP)다 — 주소가 바뀌면 여기만 고치면 된다.
        addr_label = QLabel("읽어올 주소"); addr_label.setObjectName("SectionTitle")
        layout.addWidget(addr_label)
        addr_form = QFormLayout(); addr_form.setSpacing(8)
        self.read_scan_addr = TouchSpinBox(); self.read_scan_addr.setRange(0, 9999)
        self.read_scan_addr.setValue(DEFAULT_READ_MAP["scan"])
        self.read_pose_addr = TouchSpinBox(); self.read_pose_addr.setRange(0, 9999)
        self.read_pose_addr.setValue(DEFAULT_READ_MAP["pose"])
        self.read_speed_addr = TouchSpinBox(); self.read_speed_addr.setRange(0, 9999)
        self.read_speed_addr.setValue(DEFAULT_READ_MAP["speed"])
        # 로봇이 직접 주는 실제 TCP 속도(베이스 프레임). 좌표 변화량으로
        # 추정하지 않고 이 값을 그대로 쓴다 — robot_map.REG_TCP_SPEED.
        self.read_tcp_speed_addr = TouchSpinBox(); self.read_tcp_speed_addr.setRange(0, 9999)
        self.read_tcp_speed_addr.setValue(DEFAULT_READ_MAP["tcp_speed"])
        addr_form.addRow("스캔 좌표 (제로점 기준)", self.read_scan_addr)
        addr_form.addRow("TCP pose (베이스 기준)", self.read_pose_addr)
        addr_form.addRow("작업 속도/속도 비율", self.read_speed_addr)
        addr_form.addRow("TCP 속도 (베이스 기준)", self.read_tcp_speed_addr)
        layout.addLayout(addr_form)
        self._read_addr_fields = (self.read_scan_addr, self.read_pose_addr,
                                  self.read_speed_addr, self.read_tcp_speed_addr)

        self.robot_status = QLabel("● 연결 안 됨")
        self.robot_status.setObjectName("StatusDanger")
        layout.addWidget(self.robot_status)

        auto_note = QLabel("로봇 제어 노드가 붙으면 자동으로 연결됩니다. "
                           "아래 버튼은 다른 주소로 시험하거나 수동으로 다시 걸 때만 쓰세요.")
        auto_note.setObjectName("Muted")
        auto_note.setWordWrap(True)
        layout.addWidget(auto_note)

        # 연결/연결 해제는 "연결 설정" 화면 한 곳에서만 한다. 여기서 또 걸 수
        # 있게 두면 같은 로봇에 화면마다 따로 붙는 꼴이 되고, 지금 어느
        # 주소로 붙어 있는지 알기 어려워진다.
        goto = QPushButton("연결 설정")
        goto.setMinimumHeight(52)
        goto.clicked.connect(lambda: self.navigate.emit("connection"))
        layout.addWidget(goto)
        return card

    def _build_server_card(self) -> QWidget:
        card, layout = self.surface("외부에 제공 (Modbus 서버)")
        form = QFormLayout(); form.setSpacing(8)
        self.srv_bind_ip = TouchLineEdit("0.0.0.0")
        self.srv_port = TouchSpinBox(); self.srv_port.setRange(1, 65535); self.srv_port.setValue(502)
        self.srv_start_addr = TouchSpinBox(); self.srv_start_addr.setRange(0, 65000); self.srv_start_addr.setValue(0)
        self.srv_fmt = TouchComboBox()
        for key, (label, _n, desc) in OUT_FORMATS.items():
            self.srv_fmt.addItem(f"{label} — {desc}", key)
        self.srv_fmt.setCurrentIndex(list(OUT_FORMATS).index("float32"))
        self.srv_word_order = TouchComboBox()
        for key, label in WORD_ORDERS.items():
            self.srv_word_order.addItem(label, key)
        # 폭주하는 장치가 스스로 죽는 것을 막는 응답 지연. 0이면 지연 없음.
        self.srv_delay_ms = TouchSpinBox(); self.srv_delay_ms.setRange(0, 5000); self.srv_delay_ms.setValue(0)
        self.srv_delay_ms.setSuffix(" ms")
        form.addRow("Bind IP", self.srv_bind_ip)
        form.addRow("포트", self.srv_port)
        form.addRow("시작 주소", self.srv_start_addr)
        form.addRow("데이터 타입", self.srv_fmt)
        form.addRow("워드 순서", self.srv_word_order)
        form.addRow("최소 응답 간격", self.srv_delay_ms)
        layout.addLayout(form)

        # 270/400/410/420 은 로봇 매뉴얼(S_series_UserManual_Modbus_Server_
        # Data.xlsx)이 정의한 UR 표준 자리 그대로다 — 400~405 TCP 위치,
        # 410~415 TCP 속도, 420~425 offset(전부 베이스 프레임, offset만
        # 툴 프레임). TPAC 등 UR 기준 장비가 별도 설정 없이 그대로 읽는
        # 주소라 "UR 주소 매핑"이라 부르지만, 로봇 쪽에서도 실제로 쓰는
        # 진짜 주소다 — bridge_core.MIRROR_UR 참고.
        self.srv_ur_addr = QCheckBox(f"UR 주소 매핑 ({_UR_HINT})")
        self.srv_ur_addr.setChecked(True)
        layout.addWidget(self.srv_ur_addr)

        # TPAC은 두 버전이 있다: ① 관절·pose·속도·offset 네 자리를 다 읽는
        # 것, ② pose 3개·속도 3개만 읽는 것. ②는 offset(420) 자체를 안
        # 물어보므로, 아래 "작업 평면" 자리를 아무리 채워도 못 읽어간다 —
        # 그런 장비를 위해 pose(400) 자리에 뭘 넣을지 여기서 고른다.
        self.srv_pose_source = TouchComboBox()
        self.srv_pose_source.addItem("실제 TCP (베이스 기준)", "pose")
        self.srv_pose_source.addItem("제로점 기준 스캔 값", "scan")
        self.srv_pose_source.addItem("자동 (스캔 중엔 스캔값, 아니면 실제 TCP)", "auto")
        # TPAC 은 로봇이 호를 그리며 훑는 동안의 이동 거리를 받아 평면으로
        # 펴는 장치다. 그러니 기본값은 베이스 좌표가 아니라 제로점 기준
        # 스캔 값이어야 한다.
        self.srv_pose_source.setCurrentIndex(1)
        pose_form = QFormLayout(); pose_form.setSpacing(8)
        pose_form.addRow("포즈(400) 값", self.srv_pose_source)
        layout.addLayout(pose_form)

        # 400~405는 위에서 고른 값, 420~425는 우리 작업의 스캔 위치
        # (제로점에서 시작하는 값)로 쓴다 — 매뉴얼상 420~425는 "TCP
        # offset(툴 프레임)"이지만 그 값은 로봇이 채워 주지 않아 항상
        # 0이었으므로(bridge_core.RobotData.tcp_off), 이 자리를 우리
        # 스캔 좌표로 대신 쓴다. 속도(410~415)는 베이스/평면 공용이다 —
        # 평면 좌표가 베이스 좌표를 제로점만큼 평행이동한 것뿐이라
        # 속도(위치의 변화율)는 두 좌표계에서 같다.
        # bridge_core 는 이 스캔 좌표 블록을 모르므로(로봇 쪽 원본에 없는
        # 우리 전용 확장) 화면(_write_plane_block)에서 직접 쓴다.
        plane_form = QFormLayout(); plane_form.setSpacing(8)
        self.plane_pos_addr = TouchSpinBox(); self.plane_pos_addr.setRange(0, 9999)
        self.plane_pos_addr.setValue(420)
        plane_form.addRow("작업 평면(스캔) 위치 주소", self.plane_pos_addr)
        # TPAC "Robot readings" 화면의 Odometer/Cscan 자리. 로봇이 계산해
        # 주는 **호 길이** 기준 값이다(로봇 매뉴얼에는 없는 우리 전용 확장이라
        # 마찬가지로 화면(_write_scan_axis_block)에서 직접 쓴다).
        #   Odometer : 누적 호 길이 [mm]        (로봇 레지스터 287 그대로)
        #   Cscan    : 이번 줄의 호 위 위치 [0.1mm] (로봇 레지스터 286 그대로)
        self.odometer_addr = TouchSpinBox(); self.odometer_addr.setRange(0, 9999)
        self.odometer_addr.setValue(430)
        self.cscan_addr = TouchSpinBox(); self.cscan_addr.setRange(0, 9999)
        self.cscan_addr.setValue(431)
        plane_form.addRow("누적 거리(Odometer) 주소", self.odometer_addr)
        plane_form.addRow("C-scan 위치 주소", self.cscan_addr)
        layout.addLayout(plane_form)

        # UR 배치 — bridge_core.ExternalServer.ur_mode 그대로. UR 로봇
        # 자신의 Modbus 맵(Outputs@1, 버전@256~257, 모드@258, 전원·정지
        # 상태@260~265, 전류@450~451)을 따라 외부에 그대로 채운다.
        # TPAC처럼 UR 기준으로 만들어진 장비가 접속 확인 때 이 자리를
        # 먼저 확인하므로, 비어 있으면 접속을 거부할 수 있다.
        self.srv_ur_mode = QCheckBox("UR 배치 (TPAC 등 UR 기준 장비)")
        self.srv_ur_mode.setChecked(True)
        layout.addWidget(self.srv_ur_mode)

        self.srv_status = QLabel("● 정지됨")
        self.srv_status.setObjectName("StatusDanger")
        layout.addWidget(self.srv_status)

        auto_note = QLabel("'로봇에서 읽기'가 연결되면 자동으로 시작됩니다. "
                           "아래 버튼은 설정을 바꿔 수동으로 다시 시작할 때만 쓰세요.")
        auto_note.setObjectName("Muted")
        auto_note.setWordWrap(True)
        layout.addWidget(auto_note)

        buttons = QHBoxLayout()
        self.srv_start_btn = QPushButton("서버 시작"); self.srv_start_btn.clicked.connect(self._start_server)
        self.srv_stop_btn = QPushButton("정지"); self.srv_stop_btn.setEnabled(False)
        self.srv_stop_btn.clicked.connect(self._stop_server)
        buttons.addWidget(self.srv_start_btn); buttons.addWidget(self.srv_stop_btn)
        layout.addLayout(buttons)
        return card

    def _build_table_card(self, title: str, attr: str) -> QWidget:
        card, layout = self.surface(title)
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["주소", "항목", "값", "단위"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.setMinimumHeight(320)
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(1, header.ResizeMode.Stretch)
        layout.addWidget(table)
        setattr(self, attr, table)
        return card

    # ------------------------------------------------------------ 로봇 폴링
    def _set_read_fields_enabled(self, enabled: bool) -> None:
        # robot_ip / robot_port 는 "연결 설정"이 주인이라 항상 잠긴 채로 둔다
        # (여기서 다시 켜면 잠금이 풀려 버린다).
        for field in (self.robot_unit, self.robot_interval, *self._read_addr_fields):
            field.setEnabled(enabled)

    def _start_robot(self) -> None:
        if self.poller is not None:
            return
        read_map = {
            "scan": self.read_scan_addr.value(),
            "pose": self.read_pose_addr.value(),
            "speed": self.read_speed_addr.value(),
            "tcp_speed": self.read_tcp_speed_addr.value(),
        }
        self.poller = RobotPoller(
            self.robot_ip.text().strip(), self.robot_port.value(),
            self.robot_unit.value(), self.robot_interval.value(),
            on_data=self.sig.data.emit, on_log=self.sig.log.emit,
            on_conn=self.sig.robot_conn.emit, read_map=read_map,
        )
        self._set_read_fields_enabled(False)
        self.robot_status.setText("● 연결 시도 중")
        self.robot_status.setObjectName("Muted")
        self._repolish(self.robot_status)
        self.poller.start()

    def _stop_robot(self) -> None:
        if self.poller is None:
            return
        self.poller.stop()
        self.poller = None
        self._set_read_fields_enabled(True)
        self.robot_status.setText("● 연결 안 됨")
        self.robot_status.setObjectName("StatusDanger")
        self._repolish(self.robot_status)

    # ------------------------------------------------------------ 외부 서버
    def _set_server_fields_enabled(self, enabled: bool) -> None:
        for field in (self.srv_bind_ip, self.srv_port, self.srv_start_addr,
                      self.srv_fmt, self.srv_word_order, self.srv_delay_ms,
                      self.srv_ur_addr, self.srv_ur_mode, self.srv_pose_source,
                      self.plane_pos_addr, self.odometer_addr, self.cscan_addr):
            field.setEnabled(enabled)

    def _start_server(self) -> None:
        fmt = self.srv_fmt.currentData()
        word_order = self.srv_word_order.currentData()
        ok = self.server.start(
            host=self.srv_bind_ip.text().strip(), port=self.srv_port.value(),
            start_addr=self.srv_start_addr.value(), mirror=self.srv_ur_addr.isChecked(),
            fmt=fmt, word_order=word_order, ur_mode=self.srv_ur_mode.isChecked(),
            delay_ms=self.srv_delay_ms.value(),
            # pose(400) 자리에 뭘 낼지는 위 "포즈(400) 값" 콤보로 고른다.
            # offset(420)만 따로 읽는 장비는 "작업 평면" 블록(_write_plane_block)
            # 으로 스캔 좌표를 받지만, pose·속도 두 블록만 읽는 TPAC은 그
            # 자리를 아예 안 물어보므로 이 pose 자리로 골라 보내야 한다.
            pose_source=self.srv_pose_source.currentData(),
        )
        if not ok:
            return
        self._set_server_fields_enabled(False)
        self.srv_start_btn.setEnabled(False)
        self.srv_stop_btn.setEnabled(True)
        self._last_plane = [0] * 6
        self._render_serve_table()

    def _stop_server(self) -> None:
        self.server.stop()
        self._set_server_fields_enabled(True)
        self.srv_start_btn.setEnabled(True)
        self.srv_stop_btn.setEnabled(False)
        self.serve_table.setRowCount(0)

    # ------------------------------------------------------------ 콜백 슬롯
    def _on_data(self, data) -> None:
        """폴링 스레드가 값을 읽을 때마다(기본 100ms) 불린다."""
        if self.server.running:
            self.server.update(data)
            if self.server.mirror:
                self._write_real_speed(data)
                self._write_plane_block(data)
                self._write_scan_axis_block(data)
        self._render_read_table(data)
        if self.server.running:
            self._render_serve_table(data)

    def _write_real_speed(self, data) -> None:
        """로봇이 직접 주는 실제 TCP 속도(400~405)로 410~415를 덮어쓴다.

        bridge_core.ExternalServer.update()는 좌표 변화량으로 속도를
        추정해(_calc_speed) 이미 410~415에 넣어 뒀는데, 로봇이 레지스터
        400~405로 실제 속도를 직접 주므로 그걸로 덮어쓴다 — 추정값은
        폴링 주기·통신 지연에 따라 흔들리지만 실제값은 그렇지 않다.
        `server.update(data)` 다음에 불려야 한다.
        """
        addr = self.server.mirror_map["speed"]
        self.server.bridge.set_hr(addr, [to_uint16(v) for v in data.tcp_spd_real])

    def _write_plane_block(self, data) -> None:
        """작업 평면(제로점 기준 스캔 좌표) 위치를 420~425에 낸다.

        420~425는 매뉴얼상 "TCP offset(툴 프레임)"이지만, 로봇이 그 값을
        채워 주지 않아(RobotData.tcp_off) 항상 0이었다. 그 자리를 우리
        작업에 실제로 필요한 스캔 위치(원점에서 시작하는 값)로 대신 쓴다.
        속도는 베이스/평면 공용이라 여기서 따로 안 낸다 — 평면 좌표는
        베이스 좌표를 제로점만큼 평행이동한 것뿐이라(회전 없음) 속도(위치
        변화율)가 두 좌표계에서 같다. 410~415는 `_write_real_speed`가
        이미 실제 속도로 채웠으니 그걸 그대로 쓰면 된다.
        `server.update(data)` 다음에 불려야 한다 — mirror의 offset 쓰기(0)
        를 우리 값으로 덮어써야 하기 때문이다.
        """
        pose = list(data.tcp_scan)
        self._last_plane = pose
        self.server.bridge.set_hr(self.plane_pos_addr.value(), [to_uint16(v) for v in pose])

    def _write_scan_axis_block(self, data) -> None:
        """호 길이 기준 스캔 축 값(Odometer/Cscan)을 낸다.

        좌표(pose/plane)를 그대로 주면 현(chord)으로 눌리고 호 위치에
        비례하지도 않아 TPAC의 C-scan이 실제 위치와 어긋난다(최대 14.6mm).
        그래서 로봇이 atan2로 직접 계산해 286/287에 낸 **호 길이** 기준
        값(RobotData.arc_pos/arc_total)을 그대로 옮겨 쓴다 — 로봇 레지스터와
        같은 단위를 그대로 따른다: Cscan은 0.1mm, Odometer는 mm.
        `server.update(data)` 다음에 불려야 한다(다른 미러 쓰기와 순서만
        다를 뿐 겹쳐 쓰는 자리는 없다).
        """
        self.server.bridge.set_hr(self.cscan_addr.value(),
                                  [to_uint16(clamp_int16(data.arc_pos * 10))])
        self.server.bridge.set_hr(self.odometer_addr.value(),
                                  [to_uint16(clamp_int16(data.arc_total))])

    def _on_robot_conn(self, ok: bool, message: str) -> None:
        self.robot_status.setText(f"● {message}")
        self.robot_status.setObjectName("StatusGood" if ok else "StatusDanger")
        self._repolish(self.robot_status)
        # 로봇 읽기가 실제로 붙으면 외부 제공 서버를 자동으로 켠다.
        # 사용자가 이미 서버 설정을 손봐 뒀을 수 있으니 값은 화면에 있는
        # 그대로 쓴다. 로봇이 잠깐 끊겨도(ok=False) 서버는 정지하지 않는다.
        if ok and not self.server.running:
            self._start_server()

    def _on_srv_state(self, ok: bool, message: str) -> None:
        self.srv_status.setText(f"● {message}")
        self.srv_status.setObjectName("StatusGood" if ok else "StatusDanger")
        self._repolish(self.srv_status)
        # 1024 미만 포트(502 포함)는 리눅스에서 일반 사용자가 bind할 수
        # 없다("Errno 13 허가 거부"). TPAC이 502 고정이라 포트를 바꿀 수
        # 없으므로, 해결 방법을 로그에 바로 남긴다.
        #
        # python3에 setcap으로 권한을 직접 주면 안 된다 — ROS 2도 같은
        # python3을 쓰는데, capability가 걸린 실행 파일에서는 리눅스 동적
        # 로더가 LD_LIBRARY_PATH를 무시해 버려 ROS 2가 라이브러리를 못
        # 찾고 깨진다(ImportError: librcl_action.so). authbind로 이
        # 프로세스 하나에만 권한을 줘야 한다 — operator-ui/README.md 참고.
        permission_denied = "Errno 13" in message or "허가 거부" in message or "Permission denied" in message
        if not ok and permission_denied and self.srv_port.value() < 1024:
            self._append_log(
                "[안내] 1024 미만 포트는 일반 사용자 권한으로 열 수 없습니다. "
                "python3에 setcap을 걸면 ROS 2가 깨지니 authbind를 쓰세요 "
                "(최초 1회): sudo apt-get install -y authbind && "
                "sudo touch /etc/authbind/byport/502 && "
                "sudo chmod 500 /etc/authbind/byport/502 && "
                "sudo chown \"$(whoami)\" /etc/authbind/byport/502 — 이후 "
                "'authbind --deep python -m smr_operator_ui'로 실행하세요."
            )

    def _on_srv_request(self, ts: float, who: str, fc: int, addr: int, count: int) -> None:
        self._last_ext_request_ts = time.monotonic()
        covers = self.server.covers(addr, count)
        note = "" if covers else "  ⚠ 채워지지 않은 주소"
        self._append_log(f"[외부요청] {who} FC{fc} addr {addr}~{addr + count - 1} ({count}개){note}")

    def _append_log(self, message: str) -> None:
        """같은 내용이면 새로 쌓지 않고 기존 줄을 맨 아래로 옮긴다.

        TPAC처럼 몇 가지 주소를 반복해서 읽는 외부 장치는 매 폴링마다
        같은 문구를 그대로 다시 내보낸다 — 그걸 매번 새 줄로 쌓으면
        로그가 무한히 길어지고 정작 새로운 내용(연결 끊김, 오류)이
        파묻힌다.
        """
        existing = self._log_items.get(message)
        if existing is not None:
            row = self.log_list.row(existing)
            self.log_list.takeItem(row)
            self.log_list.addItem(existing)
        else:
            item = QListWidgetItem(message)
            self.log_list.addItem(item)
            self._log_items[message] = item
            if self.log_list.count() > MAX_LOG_LINES:
                stale = self.log_list.takeItem(0)
                stale_key = next(
                    (key for key, value in self._log_items.items() if value is stale), None)
                if stale_key is not None:
                    del self._log_items[stale_key]
        self.log_list.scrollToBottom()

    # ------------------------------------------------------------ 표 갱신
    def _render_read_table(self, data) -> None:
        """실제 필요한 것만 보여준다: TCP 위치·속도(베이스), 스캔 위치.

        관절값·로봇 상태 코드는 여전히 읽고(외부 제공에 필요) 있지만,
        이 표에는 안 보여준다 — 확인할 게 너무 많으면 정작 봐야 할
        TCP/속도/스캔 값이 묻힌다.
        """
        rows = []
        for i, name in enumerate(TCP_NAMES):
            unit = "0.1mm" if i < 3 else "mRad"
            rows.append((384 + i, name, f"{data.tcp_raw[i]}", unit))
        for i, name in enumerate(TCP_NAMES):
            unit = "mm/s" if i < 3 else "mRad/s"
            rows.append((400 + i, f"속도 {name}", f"{data.tcp_spd_real[i]}", unit))
        for i, name in enumerate(TCP_NAMES):
            unit = "0.1mm" if i < 3 else "mRad"
            rows.append((280 + i, f"스캔 {name}", f"{data.tcp_scan[i]}", unit))
        # TPAC 이 실제로 쓸 스캔 축. 좌표가 아니라 **호 길이** 기준이라
        # C-scan 이 눌리거나(7%) 비선형으로 밀리지(최대 14.6mm) 않는다.
        rows.append((286, "호 위치 (이번 줄)", f"{data.arc_pos:.1f}", "mm"))
        rows.append((287, "호 위치 (누적)", f"{data.arc_total:.0f}", "mm"))
        self._fill_table(self.read_table, rows)

    def _render_serve_table(self, data=None) -> None:
        eng_names = [f"관절 {n.split(' ')[-1].strip('()')}" for n in JOINT_NAMES] + TCP_NAMES
        eng_units = ["deg"] * 6 + ["mm"] * 3 + ["deg"] * 3
        fmt = self.server.fmt
        n_words = OUT_FORMATS[fmt][1]
        step = n_words // 12
        base = self.server.start_addr
        rows = []
        eng = list(data.eng_values()) if data is not None else [None] * 12
        for i, (name, unit) in enumerate(zip(eng_names, eng_units)):
            addr = base + i * step
            value = f"{eng[i]:.2f}" if eng[i] is not None else "-"
            rows.append((addr, name, value, unit))

        status_names = ["Robot Mode", "운전 상태", "Power", "P-Stop", "E-Stop",
                        "제어 방식", "운전 모드", "alive"]
        if data is not None:
            status_vals = [
                ROBOT_MODE_TXT.get(data.robot_mode, str(data.robot_mode)),
                RUNNING_TXT.get(data.running_state, str(data.running_state)),
                str(data.power_on), str(data.protective_stop), str(data.emergency_stop),
                CONTROL_TXT.get(data.control_method, str(data.control_method)),
                OPERATION_TXT.get(data.operation_mode, str(data.operation_mode)),
                str(self.server.alive),
            ]
        else:
            status_vals = ["-"] * 8
        status_base = base + n_words
        for i, (name, value) in enumerate(zip(status_names, status_vals)):
            rows.append((status_base + i, name, value, "-"))

        # UR 주소 매핑 — 관절/pose 뿐 아니라 속도·offset 자리도 채운다.
        # (전에는 속도·offset 표시가 빠져 있었다.)
        if self.server.mirror:
            m = self.server.mirror_map
            joint_vals = [str(v) for v in data.joint_raw] if data is not None else ["-"] * 6
            for i, (name, value) in enumerate(zip(JOINT_NAMES, joint_vals)):
                rows.append((m["joint"] + i, f"[UR] {name}", value, "mRad"))
            # "포즈(400) 값" 콤보로 고른 소스를 그대로 보여준다 — 실제로
            # 서버가 그 자리에 내보내는 값과 항상 같아야 하기 때문이다.
            pose_vals = ([str(v) for v in data.pose_by_source(self.server.pose_source)]
                        if data is not None else ["-"] * 6)
            for i, (name, value) in enumerate(zip(TCP_NAMES, pose_vals)):
                unit = "0.1mm" if i < 3 else "mRad"
                rows.append((m["pose"] + i, f"[UR] {name}", value, unit))
            # 속도는 베이스/평면 공용이다 — 좌표계가 제로점만큼 평행이동한
            # 차이뿐이라 변화율(속도)은 같다. tcp_spd_real은 로봇이 직접
            # 주는 실제 속도(400~405) — _write_real_speed가 이미 이 값을
            # 410~415에 냈다.
            speed_vals = [str(v) for v in data.tcp_spd_real] if data is not None else ["-"] * 6
            for i, (name, value) in enumerate(zip(TCP_NAMES, speed_vals)):
                unit = "mm/s" if i < 3 else "mRad/s"
                rows.append((m["speed"] + i, f"[UR] 속도(Base/평면 공용) {name.split(' ')[-1]}", value, unit))

            # 작업 평면(제로점 기준 스캔 좌표) 위치 — 매뉴얼상 offset 자리
            # (420~425)를 대신 쓴다. 로봇이 offset을 채워 주지 않아 항상
            # 0이었으므로 [UR] offset 행은 따로 보여주지 않는다.
            for i, (name, value) in enumerate(
                    zip(TCP_NAMES, (str(v) for v in self._last_plane))):
                unit = "0.1mm" if i < 3 else "mRad"
                rows.append((self.plane_pos_addr.value() + i, f"[평면] {name}", value, unit))

            # 호 길이 기준 스캔 축 (Odometer/Cscan) — _write_scan_axis_block 참고.
            cscan_val = f"{data.arc_pos:.1f}" if data is not None else "-"
            odo_val = f"{data.arc_total:.0f}" if data is not None else "-"
            rows.append((self.cscan_addr.value(), "[평면] Cscan (호 위치)", cscan_val, "mm"))
            rows.append((self.odometer_addr.value(), "[평면] Odometer (누적)", odo_val, "mm"))

        if self.server.ur_mode:
            if data is not None:
                cur_sum = str(sum(abs(c) for c in data.joint_cur))
                power = str(1 if data.power_on else 0)
                pstop = str(1 if data.protective_stop else 0)
                estop = str(1 if data.emergency_stop else 0)
            else:
                cur_sum = power = pstop = estop = "-"
            rows += [
                (256, "[UR] 컨트롤러 버전", "-", "-"),
                (258, "[UR] Robot Mode", "-", "-"),
                (260, "[UR] Power On", power, "-"),
                (261, "[UR] P-Stop", pstop, "-"),
                (262, "[UR] E-Stop", estop, "-"),
                (450, "[UR] 관절 전류합", cur_sum, "mA"),
            ]
        self._fill_table(self.serve_table, rows)

    @staticmethod
    def _fill_table(table: QTableWidget, rows: list[tuple]) -> None:
        table.setRowCount(len(rows))
        for r, (addr, name, value, unit) in enumerate(rows):
            for c, text in enumerate((str(addr), name, value, unit)):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                table.setItem(r, c, item)

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    # ------------------------------------------------------------ 종료 정리
    def shutdown(self) -> None:
        """앱 종료 시 폴링 스레드와 서버 소켓을 정리한다."""
        self._link_timer.stop()
        self._stop_robot()
        self.server.stop()
