#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modbus_bridge_ur.py — UR 로봇 흉내내기 (Modbus TCP 서버)
=========================================================
외부 장비(TPAC 등)가 UR 로봇 기준으로 만들어진 경우, 이 PC 를 UR 로봇인 척
하는 서버로 만들어 붙는지 시험한다. 실제 로봇은 필요 없다.

  - UR 레지스터 맵 전체(0~2053)를 채워 둔다. 장비가 어느 주소를 훑어도
    엉뚱한 오류 대신 그럴듯한 값이 나온다.
  - TCP 좌표/속도/오프셋(400~425)이 계속 움직인다.
  - FC1/FC2/FC3/FC4 를 모두 받아준다. Unit ID 는 가리지 않는다.
  - 외부 장비가 어느 주소를 읽어가는지 이름까지 붙여서 보여준다.

설치:  pip install PyQt5 pyModbusTCP
실행:  python modbus_bridge_ur.py

외부 장비 설정: IP = 이 PC, 포트 = 502
"""

import sys
import math
import random
import time

from PyQt5 import QtCore, QtWidgets

import ur_map as U
from modbus_io import ServerBridge

MONO = "font-family:Consolas,'D2Coding',monospace;"


def to_uint16(v):
    return int(round(v)) & 0xFFFF


def s16(v):
    return v - 0x10000 if v >= 0x8000 else v


# ============================================================================
class Signals(QtCore.QObject):
    log = QtCore.pyqtSignal(str)
    request = QtCore.pyqtSignal(float, str, int, int, int)


class Led(QtWidgets.QLabel):
    def __init__(self, text="정지"):
        super().__init__(text)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setMinimumWidth(220)
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
        self.setWindowTitle("UR 로봇 흉내내기 — Modbus TCP 서버 (외부 장비 접속 시험)")
        self.resize(1120, 820)

        self.sig = Signals()
        self.srv = None
        self.t0 = time.time()
        self.tick = 0

        self.req_stat = {}          # (client, fc, addr, count) -> 횟수
        self._req_prev = {}
        self._req_last = 0.0
        self._rate_total = 0.0
        self.last_req_ts = None

        self._build_ui()
        self.sig.log.connect(self.append_log)
        self.sig.request.connect(self.on_request)

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.on_tick)

        self.ui_timer = QtCore.QTimer(self)
        self.ui_timer.timeout.connect(self.refresh_view)
        self.ui_timer.start(200)

        import pyModbusTCP
        self.append_log(f"pyModbusTCP {pyModbusTCP.__version__}")
        self.append_log("UR 레지스터 맵 전체(0~2053)를 채웁니다. "
                        "외부 장비 설정은 IP=이 PC, 포트=502 로 맞추세요.")

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        c = QtWidgets.QWidget()
        self.setCentralWidget(c)
        root = QtWidgets.QVBoxLayout(c)

        # ---------------- 서버 설정 ----------------------------------
        row = QtWidgets.QHBoxLayout()

        gb = QtWidgets.QGroupBox("서버 설정")
        f = QtWidgets.QFormLayout(gb)
        self.ed_host = QtWidgets.QLineEdit("0.0.0.0")
        self.sp_port = self._spin(1, 65535, 502)
        self.sp_delay = self._spin(0, 500, 0, " ms")
        self.sp_delay.setToolTip(
            "같은 장비의 연속 요청 사이 최소 간격입니다.\n"
            "띄엄띄엄 오는 요청(접속 확인 등)은 지연 없이 즉답하고,\n"
            "쉬지 않고 몰아칠 때만 이 간격만큼 제동을 겁니다.\n"
            "0 = 제한 없음. 붙고 나서 폭주하면 20~30 으로 올리세요.")
        f.addRow("Bind 주소", self.ed_host)
        f.addRow("포트", self.sp_port)
        f.addRow("최소 응답 간격", self.sp_delay)
        self.btn = QtWidgets.QPushButton("서버 시작")
        self.btn.setMinimumHeight(38)
        self.btn.clicked.connect(self.toggle)
        self.led = Led("정지")
        f.addRow(self.btn)
        f.addRow(self.led)
        row.addWidget(gb)

        # ---------------- 값 움직임 ----------------------------------
        gb2 = QtWidgets.QGroupBox("값 움직임 (400~425)")
        f2 = QtWidgets.QFormLayout(gb2)
        self.cb_mode = QtWidgets.QComboBox()
        self.cb_mode.addItems(["사인파 (부드럽게)", "랜덤", "고정"])
        self.sp_period = self._spin(20, 2000, 100, " ms")
        f2.addRow("변화 방식", self.cb_mode)
        f2.addRow("갱신 주기", self.sp_period)
        self.chk_mode7 = QtWidgets.QCheckBox("Robot mode = 7 (Running)")
        self.chk_mode7.setChecked(True)
        self.chk_power = QtWidgets.QCheckBox("Power On / 정지 아님")
        self.chk_power.setChecked(True)
        f2.addRow(self.chk_mode7)
        f2.addRow(self.chk_power)
        self.ed_ver = QtWidgets.QLineEdit("3.15")
        self.ed_ver.setToolTip("컨트롤러 버전. 256=정수부, 257=소수부 로 들어갑니다.")
        f2.addRow("컨트롤러 버전", self.ed_ver)
        row.addWidget(gb2)

        # ---------------- 상태 요약 ----------------------------------
        gb3 = QtWidgets.QGroupBox("접속 상태")
        f3 = QtWidgets.QVBoxLayout(gb3)
        self.lb_conn = QtWidgets.QLabel("외부 장비 요청 없음")
        self.lb_conn.setStyleSheet("font-weight:bold; font-size:13px;")
        self.lb_rate = QtWidgets.QLabel("-")
        self.lb_last = QtWidgets.QLabel("-")
        for lb in (self.lb_conn, self.lb_rate, self.lb_last):
            lb.setWordWrap(True)
            f3.addWidget(lb)
        f3.addStretch(1)
        row.addWidget(gb3, 1)

        root.addLayout(row)

        # ---------------- TCP 테이블 ---------------------------------
        tables = QtWidgets.QHBoxLayout()

        self.tbl_tcp = QtWidgets.QTableWidget(6, 4)
        self.tbl_tcp.setHorizontalHeaderLabels(
            ["항목", "400~405 pose", "410~415 speed", "420~425 offset"])
        self._init_table(self.tbl_tcp, U.TCP_NAMES)
        g = QtWidgets.QGroupBox("TCP (UR 주소)")
        QtWidgets.QVBoxLayout(g).addWidget(self.tbl_tcp)
        tables.addWidget(g)

        self.tbl_joint = QtWidgets.QTableWidget(6, 4)
        self.tbl_joint.setHorizontalHeaderLabels(
            ["관절", "270~275 각도", "280~285 속도", "290~295 전류"])
        self._init_table(self.tbl_joint, U.JOINT_NAMES)
        g2 = QtWidgets.QGroupBox("관절 (UR 주소)")
        QtWidgets.QVBoxLayout(g2).addWidget(self.tbl_joint)
        tables.addWidget(g2)

        root.addLayout(tables, 1)

        # ---------------- 외부 요청 ----------------------------------
        gb4 = QtWidgets.QGroupBox("외부 장비가 읽어가는 주소")
        v4 = QtWidgets.QVBoxLayout(gb4)
        self.tbl_req = QtWidgets.QTableWidget(0, 5)
        self.tbl_req.setHorizontalHeaderLabels(
            ["클라이언트", "기능코드", "주소", "개수", "의미"])
        self.tbl_req.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeToContents)
        self.tbl_req.horizontalHeader().setStretchLastSection(True)
        self.tbl_req.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tbl_req.setMaximumHeight(170)
        v4.addWidget(self.tbl_req)
        root.addWidget(gb4)

        self.txt_log = QtWidgets.QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumBlockCount(2000)
        self.txt_log.setFixedHeight(120)
        self.txt_log.setStyleSheet(MONO + "font-size:11px;")
        root.addWidget(self.txt_log)

        self.statusBar().showMessage("대기 중")

    def _spin(self, lo, hi, val, suffix=""):
        s = QtWidgets.QSpinBox()
        s.setRange(lo, hi)
        s.setValue(val)
        if suffix:
            s.setSuffix(suffix)
        return s

    def _init_table(self, tbl, names):
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        tbl.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        tbl.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        tbl.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeToContents)
        for r, n in enumerate(names):
            tbl.setItem(r, 0, QtWidgets.QTableWidgetItem(n))
            for col in range(1, tbl.columnCount()):
                it = QtWidgets.QTableWidgetItem("-")
                it.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                tbl.setItem(r, col, it)
            tbl.setRowHeight(r, 24)
        tbl.setMinimumHeight(24 * len(names) + 32)
        tbl.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

    def append_log(self, m):
        self.txt_log.appendPlainText(time.strftime("[%H:%M:%S] ") + m)

    # ------------------------------------------------------------------
    def toggle(self):
        if self.srv is not None and self.srv.running:
            self.timer.stop()
            self.srv.stop()
            self.srv = None
            self.btn.setText("서버 시작")
            self.led.set_state(False, "정지")
            for w in (self.ed_host, self.sp_port, self.sp_delay):
                w.setEnabled(True)
            return

        host = self.ed_host.text().strip() or "0.0.0.0"
        port = self.sp_port.value()
        self.srv = ServerBridge(log=self.sig.log.emit,
                                on_request=self.sig.request.emit,
                                delay_ms=self.sp_delay.value())
        if not self.srv.start(host, port):
            QtWidgets.QMessageBox.critical(
                self, "시작 실패",
                f"{host}:{port} 을 열 수 없습니다.\n"
                f"다른 프로그램이 이미 그 포트를 쓰고 있거나,\n"
                f"502 번은 관리자 권한이 필요할 수 있습니다.")
            self.srv = None
            return

        self.t0 = time.time()
        self.fill_static()
        self.on_tick()
        self.timer.start(self.sp_period.value())
        self.btn.setText("서버 정지")
        self.led.set_state(True, f"동작중 {host}:{port}")
        for w in (self.ed_host, self.sp_port, self.sp_delay):
            w.setEnabled(False)
        self.append_log(f"UR 흉내 서버 시작 {host}:{port} — "
                        f"외부 장비 설정을 이 주소로 맞추세요")

    # ------------------------------------------------------------------
    def fill_static(self):
        """자주 안 변하는 구간을 그럴듯한 값으로 채운다."""
        sb = self.srv
        try:
            major, _, minor = self.ed_ver.text().strip().partition(".")
            ver_hi, ver_lo = int(major or 3), int(minor or 15)
        except ValueError:
            ver_hi, ver_lo = 3, 15

        # I/O 영역
        sb.set_hr(U.REG_INPUTS, [0x0000, 0x0000, 0x0000, 0x0000])   # 0~3
        sb.set_hr(U.REG_AIN0, [3200, 0, 4100, 0, 2500, 0, 1800, 1])  # 4~11
        sb.set_hr(U.REG_AOUT0, [0, 0, 0, 0])                        # 16~19
        sb.set_hr(U.REG_TOOL_VOLTAGE, [24, 0, 0])                   # 20~22
        sb.set_hr(U.REG_EUROMAP_IN0, [0, 0, 0, 0, 24000, 500])      # 24~29
        sb.set_hr(U.REG_CFG_INPUTS, [0, 0, 0, 0])                   # 30~33
        sb.set_hr(U.REG_GP_START, [0] * 128)                        # 128~255

        # 로봇 상태
        sb.set_hr(U.REG_VER_HIGH, [ver_hi, ver_lo])
        sb.set_hr(U.REG_JOINT_MODE, [253] * 6)          # RUNNING
        sb.set_hr(U.REG_JOINT_REVOLUTION, [0] * 6)
        sb.set_hr(U.REG_JOINT_TEMP, [32 + i for i in range(6)])

        # 툴
        sb.set_hr(U.REG_TOOL_STATE, [1, 31, 120])
        sb.set_hr(U.REG_GUI_STATE, [0])
        sb.set_hr(U.REG_ROBOT_CURRENT, [1850, 320])

        # 코일 — 정상 상태
        bank = sb.bank
        bank.set_coils(0, [False] * 160)
        bank.set_discrete_inputs(0, [False] * 160)
        for a, val in ((U.COIL_POWER_ON, True), (U.COIL_PROTECTIVE, False),
                       (U.COIL_EMERGENCY, False), (U.COIL_TEACH_BUTTON, False),
                       (U.COIL_POWER_BUTTON, False),
                       (U.COIL_SAFETY_SIGNAL, False)):
            bank.set_coils(a, [val])
            bank.set_discrete_inputs(a, [val])

        self.append_log(f"정적 레지스터 채움 (컨트롤러 버전 {ver_hi}.{ver_lo})")

    # ------------------------------------------------------------------
    def on_tick(self):
        """움직이는 값 갱신."""
        if self.srv is None or not self.srv.running:
            return
        sb = self.srv
        t = time.time() - self.t0
        self.tick += 1
        mode = self.cb_mode.currentIndex()      # 0 사인파 / 1 랜덤 / 2 고정

        if mode == 0:
            # 회전은 '회전벡터'다. 크기가 π(3142 mrad)를 넘으면 수학적으로
            # 불가능한 값이라, 받는 쪽이 행렬로 바꾸다 깨질 수 있다.
            # 축은 돌리되 크기는 항상 π 안에 있도록 만든다.
            ang = 2000 + 1000 * math.sin(t * 0.3)         # 회전각 1~3 rad
            ax, ay, az = (math.cos(t * 0.2), math.sin(t * 0.2) * 0.3, 0.2)
            n = math.sqrt(ax * ax + ay * ay + az * az) or 1.0
            pose = [4000 + 1000 * math.sin(t * 0.4),      # X 0.1mm
                    -1500 + 800 * math.cos(t * 0.4),      # Y
                    3000 + 500 * math.sin(t * 0.8),       # Z
                    ang * ax / n, ang * ay / n, ang * az / n]   # Rx,Ry,Rz mrad
            speed = [50 * math.cos(t + i) for i in range(6)]
            joint = [1500 * math.sin(t * 0.5 + i * 0.7) for i in range(6)]
            jvel = [750 * math.cos(t * 0.5 + i * 0.7) for i in range(6)]
        elif mode == 1:
            ang = random.uniform(0, 3.0)                  # rad, π 미만
            v = [random.uniform(-1, 1) for _ in range(3)]
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            pose = ([random.randint(-5000, 5000) for _ in range(3)]
                    + [ang * 1000 * x / n for x in v])
            speed = [random.randint(-250, 250) for _ in range(6)]
            joint = [random.randint(-3141, 3141) for _ in range(6)]
            jvel = [random.randint(-750, 750) for _ in range(6)]
        else:
            pose = [4876, -1911, 2560, 3140, 0, -1570]
            speed = [0] * 6
            joint = [0, -1571, 0, -1571, 0, 0]
            jvel = [0] * 6

        sb.set_hr(U.REG_TCP_POSE, [to_uint16(v) for v in pose])
        sb.set_hr(U.REG_TCP_SPEED, [to_uint16(v) for v in speed])
        sb.set_hr(U.REG_TCP_OFFSET, [0, 0, 150, 0, 0, 0])     # 툴 길이 150mm
        sb.set_hr(U.REG_JOINT_ANGLE, [to_uint16(v) for v in joint])
        sb.set_hr(U.REG_JOINT_VELOCITY, [to_uint16(v) for v in jvel])
        jcur = [500 + 200 * math.sin(t + i) for i in range(6)]
        sb.set_hr(U.REG_JOINT_CURRENT, [to_uint16(v) for v in jcur])

        # 상태
        sb.set_hr(U.REG_ROBOT_MODE, [7 if self.chk_mode7.isChecked() else 5])
        on = self.chk_power.isChecked()
        sb.set_hr(U.REG_POWER_ON, [1 if on else 0])
        sb.set_hr(U.REG_SECURITY_STOP, [0 if on else 1])
        sb.set_hr(U.REG_EMERGENCY_STOP, [0])
        sb.set_hr(U.REG_TEACH_BUTTON, [0])
        sb.set_hr(U.REG_POWER_BUTTON, [0])
        sb.set_hr(U.REG_SAFETY_SIGNAL, [0])
        try:
            sb.bank.set_coils(U.COIL_POWER_ON, [on])
            sb.bank.set_discrete_inputs(U.COIL_POWER_ON, [on])
        except Exception:
            pass

        # RT machine time
        el = int(t)
        sb.set_hr(U.REG_RT_MS, [int((t % 1) * 1000), el % 60,
                                (el // 60) % 60, (el // 3600) % 24,
                                el // 86400])

        self._pose, self._speed = pose, speed
        self._joint, self._jvel, self._jcur = joint, jvel, jcur

    # ------------------------------------------------------------------
    def on_request(self, ts, who, fc, addr, count):
        key = (who.split(":")[0], fc, addr, count)
        first = key not in self.req_stat
        self.req_stat[key] = self.req_stat.get(key, 0) + 1
        self.last_req_ts = ts
        if first:
            self.append_log(f"[외부요청] {who} FC{fc} addr {addr}~"
                            f"{addr + count - 1} ({count}개) — {U.addr_name(addr)}")
            self._rebuild_req_table()

    def _rebuild_req_table(self):
        items = sorted(self.req_stat.items(), key=lambda kv: (kv[0][2], kv[0][1]))
        self.tbl_req.setRowCount(len(items))
        for r, ((cli, fc, addr, count), n) in enumerate(items):
            vals = [cli, f"FC{fc}", f"{addr}~{addr + count - 1}",
                    str(count), U.addr_name(addr)]
            for c, v in enumerate(vals):
                self.tbl_req.setItem(r, c, QtWidgets.QTableWidgetItem(v))

    # ------------------------------------------------------------------
    def refresh_view(self):
        now = time.time()
        if now - self._req_last >= 1.0:
            dt = now - self._req_last if self._req_last else 1.0
            total = sum(self.req_stat.values())
            prev = sum(self._req_prev.values()) if self._req_prev else total
            self._rate_total = max(0.0, (total - prev) / dt)
            self._req_prev = dict(self.req_stat)
            self._req_last = now
            if self.req_stat:
                self._rebuild_req_table()

        if self.req_stat:
            since = now - self.last_req_ts if self.last_req_ts else 999
            if since > 3:
                self.lb_conn.setText("외부 장비 요청 끊김")
                self.lb_conn.setStyleSheet(
                    "font-weight:bold; font-size:13px; color:#c00;")
            else:
                self.lb_conn.setText("외부 장비 접속됨 — 읽어가는 중")
                self.lb_conn.setStyleSheet(
                    "font-weight:bold; font-size:13px; color:#1e7e34;")
            self.lb_rate.setText(f"{self._rate_total:.0f} 요청/초   "
                                 f"누적 {sum(self.req_stat.values())}회   "
                                 f"주소 종류 {len(self.req_stat)}개")
            self.lb_last.setText(
                f"마지막 요청 {since:.1f}초 전" if self.last_req_ts else "-")
        elif self.srv is not None and self.srv.running:
            self.lb_conn.setText("서버는 켜졌지만 아직 요청이 없습니다")
            self.lb_conn.setStyleSheet("font-weight:bold; font-size:13px;")

        if self.srv is None or not self.srv.running:
            return
        if not hasattr(self, "_pose"):
            return

        units = ["mm", "mm", "mm", "deg", "deg", "deg"]
        for r in range(6):
            p = self._pose[r]
            conv = p / 10.0 if r < 3 else p / 1000.0 * 57.29578
            self.tbl_tcp.item(r, 1).setText(f"{int(p):+d} ({conv:+.1f}{units[r]})")
            self.tbl_tcp.item(r, 2).setText(f"{int(self._speed[r]):+d}")
            self.tbl_tcp.item(r, 3).setText(str([0, 0, 150, 0, 0, 0][r]))
            j = self._joint[r]
            self.tbl_joint.item(r, 1).setText(
                f"{int(j):+d} ({j / 1000.0 * 57.29578:+.1f}°)")
            self.tbl_joint.item(r, 2).setText(f"{int(self._jvel[r]):+d}")
            self.tbl_joint.item(r, 3).setText(f"{int(self._jcur[r])}")

        self.statusBar().showMessage(
            f"갱신 {self.tick}회   Robot mode "
            f"{7 if self.chk_mode7.isChecked() else 5}   "
            f"({U.ROBOT_MODE_TXT.get(7 if self.chk_mode7.isChecked() else 5)})")

    # ------------------------------------------------------------------
    def closeEvent(self, ev):
        try:
            self.timer.stop()
            if self.srv is not None:
                self.srv.stop()
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
