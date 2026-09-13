#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modbus_bridge.py — 로봇 관절/좌표 실시간 Modbus 중계기 (PyQt5 UI)
=================================================================
로봇(Modbus TCP 서버)에서 관절값·TCP 좌표를 실시간으로 읽어서
  ② 외부 장비의 Modbus 서버로 write (Client 모드)
  ③ 이 프로그램이 Modbus 서버가 되어 외부가 read (Server 모드)
두 방식을 동시에 지원하고, 값을 UI 에서 눈으로 확인할 수 있다.

설치:  pip install PyQt5 pyModbusTCP
실행:  python modbus_bridge.py

같은 폴더에 modbus_io.py, robot_map.py, bridge_core.py 가 있어야 한다.
robot.py 가 같이 있으면 그 안의 Robot_modbus 클래스를 상속해서 사용한다.
실제 로봇 없이 테스트하려면 먼저 robot_simulator.py 를 실행한다.
"""

import sys
import time

from PyQt5 import QtCore, QtWidgets

import modbus_io
from bridge_core import RobotPoller, ExternalWriter, ExternalServer
from robot_map import (JOINT_NAMES, TCP_NAMES, ROBOT_MODE_TXT, RUNNING_TXT,
                       CONTROL_TXT, OPERATION_TXT, RobotData,
                       OUT_FORMATS, WORD_ORDERS, encode_payload, decode_payload)

FMT_KEYS = ["int16_raw", "int16_scaled", "int32_scaled", "float32"]
WO_KEYS = ["hi_lo", "lo_hi"]

MONO = "font-family:Consolas,'D2Coding',monospace;"


# ============================================================================
class Signals(QtCore.QObject):
    """워커 스레드 → UI 스레드 전달용 시그널 버스."""
    data = QtCore.pyqtSignal(object)
    log = QtCore.pyqtSignal(str)
    robot_conn = QtCore.pyqtSignal(bool, str)
    ext_conn = QtCore.pyqtSignal(bool, str)
    srv_state = QtCore.pyqtSignal(bool, str)
    srv_request = QtCore.pyqtSignal(float, str, int, int, int)


class Led(QtWidgets.QLabel):
    def __init__(self, text="미연결"):
        super().__init__(text)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setMinimumWidth(200)
        self.set_state(False)

    def set_state(self, ok, text=None):
        if text is not None:
            self.setText(text)
        self.setStyleSheet(
            f"background:{'#1e9e50' if ok else '#a33a3a'}; color:white;"
            "padding:5px 8px; border-radius:4px; font-weight:bold;")


# ============================================================================
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Robot Modbus Bridge — 관절/TCP 실시간 중계")
        self.resize(1180, 840)

        self.sig = Signals()
        self.poller = None
        self.writer = ExternalWriter(on_log=self.sig.log.emit,
                                     on_conn=self.sig.ext_conn.emit)
        self.server = ExternalServer(on_log=self.sig.log.emit,
                                     on_state=self.sig.srv_state.emit,
                                     on_request=self.sig.srv_request.emit)
        self.req_stat = {}          # (client, fc, addr, count) -> 횟수
        self._req_last = 0.0
        self._req_prev = {}         # 1초 전 스냅샷 (초당 요청수 계산용)
        self._req_rate = {}         # key -> 초당 요청수
        self._req_total_rate = 0.0

        self.last_data = None
        self.rx_count = 0
        self._rx_mark = 0
        self._t_mark = time.time()
        self.hz = 0.0

        self._build_ui()

        self.sig.data.connect(self.on_data)
        self.sig.log.connect(self.append_log)
        self.sig.robot_conn.connect(lambda ok, m: self.led_robot.set_state(ok, m))
        self.sig.ext_conn.connect(lambda ok, m: self.led_writer.set_state(ok, m))
        self.sig.srv_state.connect(lambda ok, m: self.led_server.set_state(ok, m))
        self.sig.srv_request.connect(self.on_srv_request)

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh_view)
        self.timer.start(100)

        import pyModbusTCP
        self.append_log(f"pyModbusTCP {pyModbusTCP.__version__} / robot.py 연동 "
                        f"{'O (Robot_modbus 상속)' if modbus_io.HAVE_ROBOT_PY else 'X (단독 동작)'}")

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)

        root.addLayout(self._build_conn_row())
        root.addWidget(self._build_state_box())

        tables = QtWidgets.QHBoxLayout()
        tables.addWidget(self._build_joint_box(), 1)
        tables.addWidget(self._build_tcp_box(), 1)
        root.addLayout(tables, 1)

        root.addWidget(self._build_payload_box())

        self.txt_log = QtWidgets.QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumBlockCount(3000)
        self.txt_log.setFixedHeight(130)
        self.txt_log.setStyleSheet(MONO + "font-size:11px;")
        root.addWidget(self.txt_log)

        self.statusBar().showMessage("대기 중")

    # ---------------- 연결 설정 3분할 ---------------------------------
    def _build_conn_row(self):
        row = QtWidgets.QHBoxLayout()

        # ① 로봇
        gb = QtWidgets.QGroupBox("① 로봇에서 읽기 (Modbus TCP Client)")
        f = QtWidgets.QFormLayout(gb)
        self.ed_rip = QtWidgets.QLineEdit("192.168.1.101")
        self.sp_rport = self._spin(1, 65535, 502)
        self.sp_runit = self._spin(0, 255, 1)
        self.sp_interval = self._spin(10, 5000, 10, " ms")
        self.sp_interval.setToolTip(
            "로봇에서 좌표를 읽어오는 간격입니다.\n"
            "외부 장비가 이보다 자주 읽으면 그만큼 묵은 값을 보게 됩니다.\n"
            "아래 '사이클' 표시가 이 값보다 크면 로봇이 못 따라오는 것이니\n"
            "더 낮춰도 소용이 없습니다.")
        self.cb_readfc = QtWidgets.QComboBox()
        self.cb_readfc.addItems(["자동 (FC3 → FC4)",
                                 "FC3 홀딩 레지스터",
                                 "FC4 입력 레지스터"])
        self.sp_maxwords = self._spin(1, 125, 48, " words")
        f.addRow("로봇 IP", self.ed_rip)
        f.addRow("포트", self.sp_rport)
        f.addRow("Unit ID", self.sp_runit)
        f.addRow("폴링 주기", self.sp_interval)
        f.addRow("읽기 방식", self.cb_readfc)
        f.addRow("블록 크기", self.sp_maxwords)
        self.sp_r_scan = self._spin(0, 60000, 280)
        self.sp_r_pose = self._spin(0, 60000, 384)
        self.sp_r_speed = self._spin(0, 60000, 306)
        self.sp_r_scan.setToolTip(
            "제로점 기준 상대좌표 6개 (280~285).\n"
            "로봇 태스크가 스캔 중일 때만 값을 쓰고 그 외에는 0 입니다.")
        self.sp_r_pose.setToolTip("현재 TCP 6개, 베이스 프레임 (384~389)")
        self.sp_r_speed.setToolTip("작업 속도 / 속도 비율 2개 (306, 307)")
        rrow = QtWidgets.QGridLayout()
        for i, (cap, w) in enumerate([("스캔", self.sp_r_scan),
                                      ("pose", self.sp_r_pose),
                                      ("속도2", self.sp_r_speed)]):
            lb = QtWidgets.QLabel(cap)
            lb.setStyleSheet("color:#666; font-size:11px;")
            rrow.addWidget(lb, i // 2, (i % 2) * 2)
            rrow.addWidget(w, i // 2, (i % 2) * 2 + 1)
        f.addRow("읽기 주소", rrow)
        self.btn_robot = QtWidgets.QPushButton("연결")
        self.btn_robot.clicked.connect(self.toggle_robot)
        self.led_robot = Led("미연결")
        f.addRow(self.btn_robot)
        f.addRow(self.led_robot)
        row.addWidget(gb)

        # ② 외부로 쓰기
        gb2 = QtWidgets.QGroupBox("② 외부로 쓰기 (Modbus TCP Client)")
        f2 = QtWidgets.QFormLayout(gb2)
        self.chk_writer = QtWidgets.QCheckBox("사용")
        self.chk_writer.setChecked(True)
        self.ed_eip = QtWidgets.QLineEdit("192.168.1.50")
        self.sp_eport = self._spin(1, 65535, 502)
        self.sp_eunit = self._spin(0, 255, 1)
        self.sp_eaddr = self._spin(0, 65000, 0)
        self.chk_estatus = QtWidgets.QCheckBox("상태 8워드도 함께 전송")
        self.chk_estatus.setChecked(True)
        f2.addRow(self.chk_writer)
        f2.addRow("외부 IP", self.ed_eip)
        f2.addRow("포트", self.sp_eport)
        f2.addRow("Unit ID", self.sp_eunit)
        f2.addRow("시작 주소", self.sp_eaddr)
        f2.addRow(self.chk_estatus)
        self.btn_writer = QtWidgets.QPushButton("연결")
        self.btn_writer.clicked.connect(self.toggle_writer)
        self.led_writer = Led("미연결")
        f2.addRow(self.btn_writer)
        f2.addRow(self.led_writer)
        row.addWidget(gb2)

        # ③ 외부에 제공
        gb3 = QtWidgets.QGroupBox("③ 외부에 제공 (Modbus TCP Server)")
        f3 = QtWidgets.QFormLayout(gb3)
        self.chk_server = QtWidgets.QCheckBox("사용")
        self.chk_server.setChecked(True)
        self.ed_shost = QtWidgets.QLineEdit("0.0.0.0")
        self.sp_sport = self._spin(1, 65535, 502)
        self.sp_saddr = self._spin(0, 60000, 0)
        self.chk_mirror = QtWidgets.QCheckBox(
            "로봇 주소 그대로 재현 (관절/pose/속도/offset)")
        self.chk_mirror.setChecked(True)
        self.cb_posunit = QtWidgets.QComboBox()
        self.cb_posunit.addItems(["0.1mm (로봇 원본과 동일)", "mm (10으로 나눠서)"])
        lb_note = QtWidgets.QLabel(
            "FC3(홀딩)·FC4(입력) 모두 응답, Unit ID 무관\n"
            "주소가 고정된 장치는 로봇 대신 이 서버를 읽게 하면 됩니다.")
        lb_note.setStyleSheet("color:#888; font-size:11px;")
        f3.addRow(self.chk_server)
        f3.addRow("Bind 주소", self.ed_shost)
        f3.addRow("포트", self.sp_sport)
        f3.addRow("시작 주소", self.sp_saddr)
        self.sp_m_joint = self._spin(0, 60000, 270)
        self.sp_m_pose = self._spin(0, 60000, 400)
        self.sp_m_speed = self._spin(0, 60000, 410)
        self.sp_m_off = self._spin(0, 60000, 420)
        mrow = QtWidgets.QGridLayout()
        for i, (cap, w) in enumerate([("관절", self.sp_m_joint), ("pose", self.sp_m_pose),
                                      ("속도", self.sp_m_speed), ("offset", self.sp_m_off)]):
            lb = QtWidgets.QLabel(cap)
            lb.setStyleSheet("color:#666; font-size:11px;")
            mrow.addWidget(lb, i // 2, (i % 2) * 2)
            mrow.addWidget(w, i // 2, (i % 2) * 2 + 1)
        f3.addRow("재현 주소", mrow)
        f3.addRow("재현 위치 단위", self.cb_posunit)
        self.sp_sdelay = self._spin(0, 500, 0, " ms")
        self.sp_sdelay.setToolTip(
            "같은 장치의 연속 요청 사이 최소 간격입니다.\n"
            "띄엄띄엄 오는 요청(접속 확인 등)은 지연 없이 즉답하고,\n"
            "쉬지 않고 몰아칠 때만 이 간격만큼 제동을 겁니다.\n"
            "0 = 제한 없음. 붙고 나서 폭주하면 10~30 으로 올리세요.")
        f3.addRow("최소 응답 간격", self.sp_sdelay)
        self.cb_posesrc = QtWidgets.QComboBox()
        self.cb_posesrc.addItems(["스캔 좌표 (제로점 기준, 280~285)",
                                  "현재 TCP (베이스 프레임, 384~389)",
                                  "자동 (스캔 중이면 스캔좌표)"])
        self.cb_posesrc.setToolTip(
            "외부로 내보낼 좌표를 어디서 가져올지 정합니다.\n"
            "스캔 좌표는 로봇 태스크가 스캔 중일 때만 값을 쓰고 그 외에는 0 입니다.\n"
            "게이팅이 로봇 쪽에 있으므로 보통 '스캔 좌표' 를 그대로 쓰면 됩니다.")
        f3.addRow("내보낼 좌표", self.cb_posesrc)
        self.chk_ur = QtWidgets.QCheckBox("UR 배치 (TPAC 등 UR 기준 장비용)")
        self.chk_ur.setToolTip(
            "재현 주소를 UR 로봇과 같게 맞추고, UR 상태 레지스터도 채웁니다.\n"
            "  관절 280~275 / pose 400~405 / 속도 410~415 / offset 420~425\n"
            "  1(Outputs), 256~258(버전·모드), 260~265(전원·정지), 450/451(전류)\n"
            "TPAC 는 pose 400, 속도 410, offset 420 을 읽습니다.")
        self.chk_ur.setChecked(True)
        self.chk_ur.toggled.connect(self._apply_ur_preset)
        f3.addRow(self.chk_ur)
        self.cb_shift = QtWidgets.QComboBox()
        self.cb_shift.addItems(["0 (보정 없음)", "1칸 밀기", "2칸 밀기"])
        self.cb_shift.setToolTip(
            "장치 화면에서 좌표·속도·툴 값이 서로 뒤바뀌어 보일 때 씁니다.\n"
            "0 → 1 → 2 를 차례로 시도해 제대로 나오는 값을 고르세요.\n"
            "서버를 다시 시작해야 반영됩니다.")
        f3.addRow("재현 순서 보정", self.cb_shift)
        self.chk_poseonly = QtWidgets.QCheckBox("좌표만 (세 블록 모두 좌표)")
        self.chk_poseonly.setToolTip(
            "pose / 속도 / offset 세 블록 전부에 좌표를 넣습니다.\n"
            "응답이 어느 칸에 들어가든 전부 좌표라 섞여도 티가 나지 않고,\n"
            "어떤 칸이 갱신을 못 받아도 나머지가 같은 값을 보여줍니다.\n"
            "속도와 툴 값은 포기하게 됩니다.")
        f3.addRow(self.chk_poseonly)
        f3.addRow(lb_note)
        f3.addRow(self.chk_mirror)
        self.btn_server = QtWidgets.QPushButton("서버 시작")
        self.btn_server.clicked.connect(self.toggle_server)
        self.led_server = Led("정지")
        f3.addRow(self.btn_server)
        f3.addRow(self.led_server)
        row.addWidget(gb3)

        return row

    def _spin(self, lo, hi, val, suffix=""):
        s = QtWidgets.QSpinBox()
        s.setRange(lo, hi)
        s.setValue(val)
        if suffix:
            s.setSuffix(suffix)
        return s

    # ---------------- 상태 요약 ---------------------------------------
    def _build_state_box(self):
        gb = QtWidgets.QGroupBox("로봇 상태")
        h = QtWidgets.QHBoxLayout(gb)
        self.lb = {}
        items = [("scan", "스캔"), ("wspd", "작업속도/비율"),
                 ("mode", "Robot Mode"), ("run", "Running"), ("power", "Power"),
                 ("pstop", "P-Stop"), ("estop", "E-Stop"), ("ctrl", "Control"),
                 ("oper", "Operation"), ("ver", "Ctrl Ver"),
                 ("hz", "폴링 속도"), ("cyc", "사이클"), ("age", "값 나이"),
                 ("rx", "수신"), ("tx", "전송(쓰기/서버)")]
        for key, cap in items:
            v = QtWidgets.QVBoxLayout()
            c = QtWidgets.QLabel(cap)
            c.setStyleSheet("color:#888; font-size:11px;")
            val = QtWidgets.QLabel("-")
            val.setStyleSheet("font-weight:bold; font-size:13px;")
            v.addWidget(c)
            v.addWidget(val)
            h.addLayout(v)
            self.lb[key] = val
        return gb

    # ---------------- 테이블 ------------------------------------------
    def _build_joint_box(self):
        self.tbl_j = QtWidgets.QTableWidget(6, 5)
        self.tbl_j.setHorizontalHeaderLabels(
            ["관절", "Raw [mRad]", "각도 [deg]", "속도 [deg/s]", "전류[mA]/온도[℃]"])
        self._init_table(self.tbl_j, JOINT_NAMES)
        gb = QtWidgets.QGroupBox("관절값  (Reg 73~78)")
        QtWidgets.QVBoxLayout(gb).addWidget(self.tbl_j)
        return gb

    def _build_tcp_box(self):
        self.tbl_t = QtWidgets.QTableWidget(6, 4)
        self.tbl_t.setHorizontalHeaderLabels(
            ["항목", "Raw", "변환값", "속도"])
        self._init_table(self.tbl_t, TCP_NAMES)
        gb = QtWidgets.QGroupBox("TCP 좌표값 (Reg 280, 원점)")
        QtWidgets.QVBoxLayout(gb).addWidget(self.tbl_t)
        return gb

    def _init_table(self, tbl, names):
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        tbl.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        hdr = tbl.horizontalHeader()
        hdr.setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        hdr.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        for r, n in enumerate(names):
            tbl.setItem(r, 0, QtWidgets.QTableWidgetItem(n))
            for c in range(1, tbl.columnCount()):
                it = QtWidgets.QTableWidgetItem("-")
                it.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                tbl.setItem(r, c, it)
            tbl.setRowHeight(r, 26)
        tbl.setMinimumHeight(26 * len(names) + 34)
        tbl.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        tbl.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

    # ---------------- 페이로드 ----------------------------------------
    def _build_payload_box(self):
        gb = QtWidgets.QGroupBox("외부 전송 데이터 — ②③ 공통 (관절 6 + TCP 6)")
        v = QtWidgets.QVBoxLayout(gb)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("데이터 타입"))
        self.cb_fmt = QtWidgets.QComboBox()
        for k in FMT_KEYS:
            name, n, desc = OUT_FORMATS[k]
            self.cb_fmt.addItem(f"{name} — {desc} [{n}워드]")
        self.cb_fmt.setCurrentIndex(FMT_KEYS.index("float32"))
        row.addWidget(self.cb_fmt, 1)
        row.addWidget(QtWidgets.QLabel("워드 순서"))
        self.cb_wo = QtWidgets.QComboBox()
        for k in WO_KEYS:
            self.cb_wo.addItem(WORD_ORDERS[k])
        row.addWidget(self.cb_wo, 1)
        v.addLayout(row)

        self.lb_layout = QtWidgets.QLabel("-")
        self.lb_pay_words = QtWidgets.QLabel("-")
        self.lb_pay_dec = QtWidgets.QLabel("-")
        self.lb_req = QtWidgets.QLabel("외부 요청: 아직 없음")
        self.lb_mirror = QtWidgets.QLabel("재현 구간: 서버 정지")
        self.lb_scan = QtWidgets.QLabel("스캔 좌표: -")
        for lb in (self.lb_layout, self.lb_scan, self.lb_pay_words,
                   self.lb_pay_dec, self.lb_mirror, self.lb_req):
            lb.setStyleSheet(MONO + "font-size:11px;")
            lb.setWordWrap(True)
            v.addWidget(lb)
        return gb

    def _apply_ur_preset(self, on):
        """UR 배치 체크 시 재현 주소를 UR 기준으로 맞춘다."""
        if not on:
            return
        for w, v in ((self.sp_m_joint, 270), (self.sp_m_pose, 400),
                     (self.sp_m_speed, 410), (self.sp_m_off, 420)):
            w.setValue(v)
        self.append_log("[UR 배치] 재현 주소를 관절 270 / pose 400 / 속도 410 / "
                        "offset 420 으로 맞췄습니다. 서버를 다시 시작하세요.")

    def cur_fmt(self):
        return FMT_KEYS[self.cb_fmt.currentIndex()]

    def cur_wo(self):
        return WO_KEYS[self.cb_wo.currentIndex()]

    # ------------------------------------------------------------------
    def append_log(self, msg):
        self.txt_log.appendPlainText(time.strftime("[%H:%M:%S] ") + msg)

    # ---------------- ① 로봇 ------------------------------------------
    def toggle_robot(self):
        if self.poller is not None and self.poller.is_alive():
            self.poller.stop()
            self.poller.join(timeout=2)
            self.poller = None
            self.btn_robot.setText("연결")
            self._lock_robot_inputs(False)
            return

        ip = self.ed_rip.text().strip()
        if not ip:
            self._warn("로봇 IP를 입력하세요.")
            return
        read_fc = {0: "auto", 1: 3, 2: 4}[self.cb_readfc.currentIndex()]
        self.poller = RobotPoller(ip, self.sp_rport.value(), self.sp_runit.value(),
                                  self.sp_interval.value(),
                                  on_data=self.sig.data.emit,
                                  on_log=self.sig.log.emit,
                                  on_conn=self.sig.robot_conn.emit,
                                  read_fc=read_fc,
                                  max_words=self.sp_maxwords.value(),
                                  read_map=self.cur_read_map())
        self.append_log("[읽기] 블록 " + ", ".join(
            f"{a}~{a + n - 1}" for a, n in self.poller.blocks))
        self.poller.start()
        self.btn_robot.setText("연결 해제")
        self._lock_robot_inputs(True)

    def _lock_robot_inputs(self, lock):
        for w in (self.ed_rip, self.sp_rport, self.sp_runit, self.sp_interval,
                  self.cb_readfc, self.sp_maxwords, self.sp_r_scan,
                  self.sp_r_pose, self.sp_r_speed):
            w.setEnabled(not lock)

    def cur_read_map(self):
        return {"scan": self.sp_r_scan.value(), "pose": self.sp_r_pose.value(),
                "speed": self.sp_r_speed.value()}

    # ---------------- ② 외부 쓰기 -------------------------------------
    def toggle_writer(self):
        if self.writer.is_open():
            self.writer.close()
            self.btn_writer.setText("연결")
            self._lock_writer_inputs(False)
            return
        ip = self.ed_eip.text().strip()
        if not ip:
            self._warn("외부 IP를 입력하세요.")
            return
        ok = self.writer.open(ip, self.sp_eport.value(), self.sp_eunit.value(),
                              self.sp_eaddr.value(),
                              fmt=self.cur_fmt(), word_order=self.cur_wo(),
                              send_status=self.chk_estatus.isChecked())
        self.btn_writer.setText("연결 해제" if ok else "연결")
        self._lock_writer_inputs(ok)

    def _lock_writer_inputs(self, lock):
        for w in (self.ed_eip, self.sp_eport, self.sp_eunit, self.sp_eaddr,
                  self.chk_estatus):
            w.setEnabled(not lock)
        self._sync_fmt_lock()

    def _sync_fmt_lock(self):
        """②③ 중 하나라도 동작 중이면 데이터 타입 변경 잠금."""
        busy = self.writer.is_open() or self.server.running
        self.cb_fmt.setEnabled(not busy)
        self.cb_wo.setEnabled(not busy)

    # ---------------- ③ 외부 서버 -------------------------------------
    def toggle_server(self):
        if self.server.running:
            self.server.stop()
            self.btn_server.setText("서버 시작")
            self._lock_server_inputs(False)
            return
        ok = self.server.start(self.ed_shost.text().strip() or "0.0.0.0",
                               self.sp_sport.value(), self.sp_saddr.value(),
                               mirror=self.chk_mirror.isChecked(),
                               fmt=self.cur_fmt(), word_order=self.cur_wo(),
                               pos_unit=("mm" if self.cb_posunit.currentIndex() == 1
                                         else "raw"),
                               mirror_map={"joint": self.sp_m_joint.value(),
                                           "pose": self.sp_m_pose.value(),
                                           "speed": self.sp_m_speed.value(),
                                           "offset": self.sp_m_off.value()},
                               delay_ms=self.sp_sdelay.value(),
                               mirror_shift=self.cb_shift.currentIndex(),
                               pose_only=self.chk_poseonly.isChecked(),
                               ur_mode=self.chk_ur.isChecked(),
                               pose_source={0: "scan", 1: "pose",
                                            2: "auto"}[self.cb_posesrc.currentIndex()])
        self.btn_server.setText("서버 정지" if ok else "서버 시작")
        self._lock_server_inputs(ok)

    def _lock_server_inputs(self, lock):
        for w in (self.ed_shost, self.sp_sport, self.sp_saddr,
                  self.chk_mirror, self.cb_posunit, self.sp_sdelay,
                  self.cb_shift, self.chk_poseonly, self.chk_ur,
                  self.cb_posesrc):
            w.setEnabled(not lock)
        self._sync_fmt_lock()

    def _warn(self, msg):
        QtWidgets.QMessageBox.warning(self, "확인", msg)

    # ---------------- 외부 요청 로그 -----------------------------------
    def on_srv_request(self, ts, who, fc, addr, count):
        """엔코더 블록 등 외부 마스터가 무엇을 읽어가는지 기록."""
        key = (who.split(":")[0], fc, addr, count)
        first = key not in self.req_stat
        self.req_stat[key] = self.req_stat.get(key, 0) + 1

        if first:
            # 서버가 실제로 채우고 있는 구간인지 직접 확인한다
            end = addr + count - 1
            note = ""
            if fc in (3, 4):
                label = self.server.covers(addr, count)
                if label:
                    note = f"  → {label} 구간, 정상"
                else:
                    note = "  <== 우리가 채우지 않는 주소! (항상 0)  채우는 구간: " \
                           + ", ".join(f"{lo}~{hi}({lb})"
                                       for lo, hi, lb in self.server.filled_ranges())
            self.append_log(f"[외부요청] {who} FC{fc} addr {addr}~{end} "
                            f"({count}개){note}")
        # 요약은 1초에 한 번만
        if ts - self._req_last > 1.0:
            dt = ts - self._req_last if self._req_last else 1.0
            self._req_rate = {k: (v - self._req_prev.get(k, v)) / dt
                              for k, v in self.req_stat.items()}
            self._req_total_rate = sum(self._req_rate.values())
            self._req_prev = dict(self.req_stat)
            self._req_last = ts

            # 외부가 우리 갱신 속도보다 훨씬 빨리 읽으면 같은 값을 반복해서 본다
            blocks = max(1, len(self.req_stat))
            per_block = self._req_total_rate / blocks
            if per_block > self.hz * 2 and self.hz > 0 and not getattr(
                    self, "_warned_rate", False):
                self._warned_rate = True
                cyc = self.poller.cycle_ms if self.poller else 0
                self.append_log(
                    f"[안내] 외부가 블록당 약 {per_block:.0f}회/초로 읽는데 "
                    f"로봇 폴링은 {self.hz:.1f}Hz 입니다. 외부가 보는 값이 "
                    f"최대 {1000 / max(self.hz, 0.1):.0f}ms 묵은 값입니다. "
                    f"한 주기에 실제로 {cyc:.0f}ms 걸리므로 폴링 주기를 "
                    f"{max(10, int(cyc * 1.2)):d}ms 근처까지 줄일 수 있습니다.")

            top = sorted(self.req_stat.items(), key=lambda kv: -kv[1])[:4]
            self.lb_req.setText(
                f"외부 요청 (합계 {self._req_total_rate:.0f}회/초): " + "   ".join(
                    f"{k[0]} FC{k[1]} {k[2]}~{k[2] + k[3] - 1}({k[3]}) "
                    f"×{v} [{self._req_rate.get(k, 0):.0f}/s]"
                    for k, v in top))

    # ---------------- 데이터 수신 → 중계 ------------------------------
    def on_data(self, data: RobotData):
        self.last_data = data
        self.rx_count += 1
        if self.chk_writer.isChecked() and self.writer.is_open():
            self.writer.write(data)
        if self.chk_server.isChecked() and self.server.running:
            self.server.update(data)

    # ---------------- 화면 갱신 ---------------------------------------
    def refresh_view(self):
        now = time.time()
        if now - self._t_mark >= 1.0:
            self.hz = (self.rx_count - self._rx_mark) / (now - self._t_mark)
            self._rx_mark, self._t_mark = self.rx_count, now
        self.lb["hz"].setText(f"{self.hz:.1f} Hz")
        if self.poller is not None:
            cyc = self.poller.cycle_ms
            self.lb["cyc"].setText(f"{cyc:.0f} ms")
            over = cyc > self.sp_interval.value() * 1.2
            self.lb["cyc"].setStyleSheet(
                "font-weight:bold; font-size:13px;" + (" color:#c00;" if over else ""))
        if self.last_data is not None:
            age = (now - self.last_data.ts) * 1000
            self.lb["age"].setText(f"{age:.0f} ms")
            self.lb["age"].setStyleSheet(
                "font-weight:bold; font-size:13px;"
                + (" color:#c00;" if age > 150 else ""))
        self.lb["rx"].setText(str(self.rx_count))
        self.lb["tx"].setText(f"{self.writer.tx_count} / {self.server.update_count}")

        d = self.last_data
        if d is None:
            return

        self.lb["scan"].setText("스캔중" if d.scanning else "정지")
        self.lb["wspd"].setText(f"{d.work_speed} / {d.speed_ratio}")
        self.lb["scan"].setStyleSheet(
            "font-weight:bold; font-size:13px;"
            + (" color:#1e7e34;" if d.scanning else " color:#888;"))
        self.lb["mode"].setText(ROBOT_MODE_TXT.get(d.robot_mode, str(d.robot_mode)))
        self.lb["run"].setText(RUNNING_TXT.get(d.running_state, str(d.running_state)))
        self.lb["power"].setText("ON" if d.power_on else "OFF")
        self.lb["pstop"].setText("STOP" if d.protective_stop else "OK")
        self.lb["estop"].setText("STOP" if d.emergency_stop else "OK")
        self.lb["ctrl"].setText(CONTROL_TXT.get(d.control_method, str(d.control_method)))
        self.lb["oper"].setText(OPERATION_TXT.get(d.operation_mode, str(d.operation_mode)))
        self.lb["ver"].setText("%d.%d.%d" % d.version)
        for k in ("pstop", "estop"):
            bad = self.lb[k].text() == "STOP"
            self.lb[k].setStyleSheet("font-weight:bold; font-size:13px;"
                                     + (" color:#e03131;" if bad else ""))

        deg, spd = d.joint_deg, d.joint_spd_deg
        for r in range(6):
            self.tbl_j.item(r, 1).setText(f"{d.joint_raw[r]:d}")
            self.tbl_j.item(r, 2).setText(f"{deg[r]:+9.3f}")
            self.tbl_j.item(r, 3).setText(f"{spd[r]:+8.2f}")
            self.tbl_j.item(r, 4).setText(f"{d.joint_cur[r]} / {d.joint_tmp[r]}")

        src = {0: "scan", 1: "pose", 2: "auto"}[self.cb_posesrc.currentIndex()]
        sent = d.pose_by_source(src)
        self.lb_scan.setText(
            f"스캔 좌표 {self.sp_r_scan.value()}~{self.sp_r_scan.value() + 5} : "
            + " ".join(f"{v:6d}" for v in d.tcp_scan)
            + ("   (스캔중)" if d.scanning else "   (전부 0 — 스캔 아님)")
            + f"\n내보내는 좌표 : " + " ".join(f"{v:6d}" for v in sent))

        conv, cspd = d.tcp_conv, d.tcp_spd_conv
        units = ["mm", "mm", "mm", "deg", "deg", "deg"]
        sunits = ["mm/s", "mm/s", "mm/s", "deg/s", "deg/s", "deg/s"]
        for r in range(6):
            self.tbl_t.item(r, 1).setText(f"{d.tcp_raw[r]:d}")
            self.tbl_t.item(r, 2).setText(f"{conv[r]:+.2f} {units[r]}")
            self.tbl_t.item(r, 3).setText(f"{cspd[r]:+.1f} {sunits[r]}")

        fmt, wo = self.cur_fmt(), self.cur_wo()
        n = OUT_FORMATS[fmt][1]
        words = encode_payload(d, fmt, wo)
        eb, sb = self.sp_eaddr.value(), self.sp_saddr.value()
        self.lb_layout.setText(
            f"② 쓰기 : 데이터 {eb}~{eb + n - 1}, 상태 {eb + n}~{eb + n + 7}"
            f"     ③ 서버 : 데이터 {sb}~{sb + n - 1}, 상태 {sb + n}~{sb + n + 7}"
            f"     (alive 카운터 = 마지막 워드, 매 갱신 +1)")
        self.lb_pay_words.setText(
            "words  : " + " ".join(f"{v:5d}" for v in words))
        dec = decode_payload(words, fmt, wo)
        names = ["J1", "J2", "J3", "J4", "J5", "J6",
                 "X", "Y", "Z", "Rx", "Ry", "Rz"]
        self.lb_pay_dec.setText(
            "외부해석: " + "  ".join(f"{k}={v:+.2f}" for k, v in zip(names, dec)))

        # 서버 데이터뱅크에서 되읽어, 외부 장치가 실제로 가져가는 값을 보여준다
        if self.server.running:
            parts = []
            for lo, hi, lb in self.server.filled_ranges():
                if "재현" not in lb:
                    continue
                v = self.server.bridge.get_hr(lo, hi - lo + 1)
                parts.append(f"{lb} {lo}~{hi} ["
                             + " ".join(str(x - 0x10000 if x >= 0x8000 else x)
                                        for x in v) + "]")
            self.lb_mirror.setText("재현 구간: " + "   ".join(parts)
                                   if parts else "재현 구간: 미러링 꺼짐")
        else:
            self.lb_mirror.setText("재현 구간: 서버 정지")

        self.statusBar().showMessage(
            f"마지막 수신 {time.strftime('%H:%M:%S', time.localtime(d.ts))}"
            f"  |  폴링 {self.hz:.1f} Hz"
            f"  |  로봇 오류 {self.poller.err_count if self.poller else 0}")

    # ------------------------------------------------------------------
    def closeEvent(self, ev):
        try:
            if self.poller is not None and self.poller.is_alive():
                self.poller.stop()
                self.poller.join(timeout=2)
            self.writer.close(silent=True)
            self.server.stop()
        except Exception:
            pass
        ev.accept()


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
