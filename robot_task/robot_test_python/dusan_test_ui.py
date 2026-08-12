#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dusan_test_ui.py — dusan_v1 태스크 실시간 테스트 프로그램

  * 29999 대시보드 : play / pause / stop, 변수 읽기·쓰기
  * 30001 스트림   : 실제 TCP 좌표 (로봇 원본)
  * 502  Modbus    : 범용 레지스터 256~383 (파라미터 쓰기 / 로봇 발행값 읽기)
                     상세 맵은 register_map.txt 참고

상대좌표는 두 가지를 나란히 보여준다.
  PC 계산  : 30001 의 실제 TCP 와 zero_pose 로 이 프로그램이 직접 계산
  로봇 발행: 태스크가 레지스터 280~285 에 쓴 값
둘이 일치하면 태스크의 실시간 발행이 정상이라는 뜻이다.

실행:  python dusan_test_ui.py
필요:  pip install PyQt5 pyModbusTCP     (robot.py 가 같은 폴더에 있어야 한다)
"""

import csv
import os
import sys
import time
import traceback

from PyQt5 import QtCore, QtGui, QtWidgets

import dusan_map as M

# --------------------------------------------------------------------- robot.py
try:
    from robot import Robot_29999, Robot_30001, Robot_modbus
    HAVE_ROBOT = True
except Exception as e:                                          # pragma: no cover
    HAVE_ROBOT = False
    ROBOT_IMPORT_ERROR = str(e)

DEFAULT_IP = "192.168.1.123"


# ===================================================================== 폴링 스레드
class Poller(QtCore.QThread):
    sample = QtCore.pyqtSignal(dict)
    log = QtCore.pyqtSignal(str)
    linkchanged = QtCore.pyqtSignal(bool, bool)     # (stream_ok, modbus_ok)

    def __init__(self, ip, mb_port=502, period_ms=50, stream_port=30001, parent=None):
        super().__init__(parent)
        self.ip = ip
        self.mb_port = mb_port
        self.stream_port = stream_port
        self.period = period_ms / 1000.0
        self._stop = False
        self._writes = []                            # [(addr, value), ...]
        self._lock = QtCore.QMutex()
        self.r30001 = None
        self.rmb = None
        self.zero_pose = None
        self._want_zero = False

    # ---- 외부에서 호출
    def request_write(self, addr, value):
        with QtCore.QMutexLocker(self._lock):
            self._writes.append((int(addr), int(value)))

    def request_zero_refresh(self):
        self._want_zero = True

    def set_zero_pose(self, pose):
        self.zero_pose = pose

    def stop(self):
        self._stop = True

    # ---- 스레드 본체
    def run(self):
        stream_ok = mb_ok = False
        try:
            self.r30001 = Robot_30001(self.ip, self.stream_port)
            stream_ok = self.r30001.connect_30001() is not None
        except Exception as e:
            self.log.emit(f"[30001] 연결 실패: {e}")
        try:
            self.rmb = Robot_modbus(self.ip, self.mb_port)
            mb_ok = bool(self.rmb.connect())
        except Exception as e:
            self.log.emit(f"[Modbus] 연결 실패: {e}")
        self.linkchanged.emit(stream_ok, mb_ok)

        last_zero_ok = False
        while not self._stop:
            t0 = time.time()
            out = {"ts": t0, "stream": False, "modbus": False}

            # --- 30001 실제 TCP
            if stream_ok:
                try:
                    d = self.r30001.get_data()
                    if d is not None:
                        out["tcp"] = [d.tcp_x, d.tcp_y, d.tcp_z, d.rot_x, d.rot_y, d.rot_z]
                        out["running"] = bool(getattr(d, "is_task_running", False))
                        out["paused"] = bool(getattr(d, "is_task_paused", False))
                        out["robot_mode"] = getattr(d, "robot_mode", -1)
                        out["di"] = getattr(d, "digital_input_bits", 0)
                        out["stream"] = True
                except Exception as e:
                    self.log.emit(f"[30001] 수신 오류: {e}")
                    stream_ok = False
                    self.linkchanged.emit(stream_ok, mb_ok)

            # --- Modbus 쓰기 (대기중인 것 먼저)
            if mb_ok:
                with QtCore.QMutexLocker(self._lock):
                    pending, self._writes = self._writes, []
                for addr, val in pending:
                    try:
                        self.rmb.client.write_single_register(addr, M.to_unsigned(val))
                        self.log.emit(f"[Modbus] {addr} <- {val}")
                    except Exception as e:
                        self.log.emit(f"[Modbus] {addr} 쓰기 실패: {e}")

                # --- Modbus 읽기
                try:
                    regs = self.rmb.client.read_holding_registers(M.BLOCK_START, M.BLOCK_COUNT)
                    if regs:
                        raw = {M.BLOCK_START + i: v for i, v in enumerate(regs)}
                        out["raw"] = raw
                        out["dec"] = M.decode(raw)
                        out["modbus"] = True
                except Exception as e:
                    self.log.emit(f"[Modbus] 읽기 오류: {e}")

            # --- 원점 확정 시점에 zero_pose 갱신 요청
            dec = out.get("dec")
            if dec is not None:
                if dec["zero_ok"] and not last_zero_ok:
                    self._want_zero = True
                last_zero_ok = dec["zero_ok"]
            if self._want_zero:
                self._want_zero = False
                out["need_zero"] = True

            # --- PC 계산 상대좌표
            if "tcp" in out and self.zero_pose:
                try:
                    out["rel_pc"] = M.relative_to_zero(self.zero_pose, out["tcp"])
                except Exception:
                    pass

            self.sample.emit(out)
            dt = self.period - (time.time() - t0)
            if dt > 0:
                self.msleep(int(dt * 1000))

        for closer in ((self.r30001, "disconnect_30001"), (self.rmb, "disconnect")):
            try:
                getattr(closer[0], closer[1])()
            except Exception:
                pass


# ===================================================================== 경로 플롯
class PathPlot(QtWidgets.QWidget):
    """원점 기준 Y-Z 평면. 오른쪽(로봇 Y-)이 화면 오른쪽으로 가도록 y 를 뒤집는다."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(260)
        self.ideal = []
        self.trace = []
        self.cur = None

    def set_ideal(self, pts):
        self.ideal = pts
        self.update()

    def add_point(self, y_mm, z_mm, record=True):
        """record=False 면 현재 위치만 갱신하고 궤적에는 남기지 않는다."""
        self.cur = (y_mm, z_mm)
        if record:
            last = self.trace[-1] if self.trace else None
            if last is not None and (abs(last[0] - y_mm) > 50 or abs(last[1] - z_mm) > 50):
                self.trace.append(None)      # 순간이동은 선으로 잇지 않는다
                last = None
            if (last is None or abs(last[0] - y_mm) > 0.5 or abs(last[1] - z_mm) > 0.5):
                self.trace.append((y_mm, z_mm))
            if len(self.trace) > 20000:
                del self.trace[:5000]
        self.update()

    def clear_trace(self):
        self.trace = []
        self.cur = None
        self.update()

    def _bounds(self):
        pts = list(self.ideal) + [p for p in self.trace if p]
        if not pts:
            return -50, 600, -50, 400
        ys = [p[0] for p in pts]
        zs = [p[1] for p in pts]
        my = max(20.0, (max(ys) - min(ys)) * 0.08)
        mz = max(20.0, (max(zs) - min(zs)) * 0.08)
        return min(ys) - my, max(ys) + my, min(zs) - mz, max(zs) + mz

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor("#fbfbfd"))
        w, h = self.width(), self.height()
        pad = 30
        y0, y1, z0, z1 = self._bounds()
        sx = (w - 2 * pad) / max(1e-6, (y1 - y0))
        sy = (h - 2 * pad) / max(1e-6, (z1 - z0))
        s = min(sx, sy)

        def to_px(y, z):
            return (pad + (y - y0) * s, h - pad - (z - z0) * s)

        # 축
        p.setPen(QtGui.QPen(QtGui.QColor("#d0d0d8"), 1))
        ox, oy = to_px(0, 0)
        p.drawLine(int(pad), int(oy), int(w - pad), int(oy))
        p.drawLine(int(ox), int(pad), int(ox), int(h - pad))

        # 이상 경로
        if len(self.ideal) > 1:
            p.setPen(QtGui.QPen(QtGui.QColor("#c9c9d4"), 2, QtCore.Qt.DashLine))
            path = QtGui.QPainterPath()
            path.moveTo(*to_px(*self.ideal[0]))
            for pt in self.ideal[1:]:
                path.lineTo(*to_px(*pt))
            p.drawPath(path)

        # 실제 궤적 (None 은 끊김 표시)
        if len(self.trace) > 1:
            p.setPen(QtGui.QPen(QtGui.QColor("#2563eb"), 2))
            path = QtGui.QPainterPath()
            pen_down = False
            for pt in self.trace:
                if pt is None:
                    pen_down = False
                    continue
                if pen_down:
                    path.lineTo(*to_px(*pt))
                else:
                    path.moveTo(*to_px(*pt))
                    pen_down = True
            p.drawPath(path)

        # 원점 / 현재 위치
        p.setBrush(QtGui.QColor("#16a34a"))
        p.setPen(QtCore.Qt.NoPen)
        p.drawEllipse(QtCore.QPointF(*to_px(0, 0)), 4, 4)
        if self.cur:
            p.setBrush(QtGui.QColor("#dc2626"))
            p.drawEllipse(QtCore.QPointF(*to_px(*self.cur)), 5, 5)

        p.setPen(QtGui.QColor("#6b7280"))
        f = p.font(); f.setPointSize(8); p.setFont(f)
        p.drawText(int(ox) + 6, int(oy) - 6, "원점(0,0)")
        p.drawText(w - pad - 70, h - 8, "→ 오른쪽(베이스 Y-)")
        p.drawText(6, pad - 10, "↑ 상승(Z+)")


# ===================================================================== 메인 창
def vbox(*ws):
    l = QtWidgets.QVBoxLayout(); l.setSpacing(6)
    for w in ws:
        (l.addLayout if isinstance(w, QtWidgets.QLayout) else l.addWidget)(w)
    return l


class Main(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("dusan_v1 실시간 테스트")
        self.resize(1180, 780)
        self.poller = None
        self.r29999 = None
        self.zero_pose = None
        self.csv_file = None
        self.csv_writer = None
        self._last_alive = None
        self.amap = M.DEFAULT_AMAP
        self._alive_stamp = 0.0
        self._build()

    # ------------------------------------------------------------- UI 구성
    def _build(self):
        # --- 연결
        self.ed_ip = QtWidgets.QLineEdit(DEFAULT_IP)
        self.ed_ip.setMinimumWidth(120)
        self.sp_mbport = QtWidgets.QSpinBox()
        self.sp_mbport.setRange(1, 65535); self.sp_mbport.setValue(502)
        self.sp_mbport.setFixedWidth(80)
        self.btn_conn = QtWidgets.QPushButton("연결")
        self.btn_conn.clicked.connect(self.toggle_conn)
        self.lb_link = QtWidgets.QLabel("● 미연결")
        self.lb_link.setStyleSheet("color:#9ca3af;font-weight:bold")
        g_conn = QtWidgets.QGroupBox("연결")
        gc = QtWidgets.QGridLayout(g_conn)
        gc.addWidget(QtWidgets.QLabel("로봇 IP"), 0, 0)
        gc.addWidget(self.ed_ip, 0, 1)
        gc.addWidget(QtWidgets.QLabel("Modbus 포트"), 0, 2)
        gc.addWidget(self.sp_mbport, 0, 3)
        gc.addWidget(self.btn_conn, 0, 4)
        gc.addWidget(self.lb_link, 1, 0, 1, 3)
        self.btn_remote = QtWidgets.QPushButton("원격모드 ON")
        self.btn_power = QtWidgets.QPushButton("전원 ON + 브레이크 해제")
        self.btn_remote.clicked.connect(lambda: self.dash("remoteControl -on"))
        self.btn_power.clicked.connect(self.power_on)
        gc.addWidget(self.btn_remote, 1, 3)
        gc.addWidget(self.btn_power, 1, 4)
        gc.setColumnStretch(1, 1)

        # --- 작업 영역
        g_par = QtWidgets.QGroupBox("작업 영역")
        form = QtWidgets.QGridLayout(g_par)
        self.spins = {}
        for i, (key, addr, label, unit, dflt) in enumerate(M.INPUT_FIELDS):
            sp = QtWidgets.QSpinBox()
            sp.setRange(0, 30000); sp.setValue(dflt); sp.setSuffix(f" {unit}")
            sp.setFixedWidth(110)
            sp.valueChanged.connect(self.update_preview)
            self.spins[key] = sp
            form.addWidget(QtWidgets.QLabel(f"{label} ({addr})"), i, 0)
            form.addWidget(sp, i, 1)
        self.lb_preview = QtWidgets.QLabel("-")
        self.lb_preview.setWordWrap(True)
        self.lb_preview.setStyleSheet("color:#374151")
        form.addWidget(self.lb_preview, 0, 2, 4, 1)

        self.btn_send_mb = QtWidgets.QPushButton("Modbus 256~259 + 266 으로 전송")
        self.btn_send_var = QtWidgets.QPushButton("29999 변수로 전송")
        self.btn_send_mb.clicked.connect(self.send_params_modbus)
        self.btn_send_var.clicked.connect(self.send_params_variable)
        form.addWidget(self.btn_send_mb, 4, 0, 1, 2)
        form.addWidget(self.btn_send_var, 4, 2)

        # --- 제어
        g_ctl = QtWidgets.QGroupBox("제어")
        hc = QtWidgets.QHBoxLayout(g_ctl)
        self.btn_start = QtWidgets.QPushButton("▶ START")
        self.btn_pause = QtWidgets.QPushButton("❚❚ PAUSE")
        self.btn_stop = QtWidgets.QPushButton("■ STOP")
        for b, c in ((self.btn_start, "#16a34a"), (self.btn_pause, "#d97706"), (self.btn_stop, "#dc2626")):
            b.setMinimumHeight(44)
            b.setStyleSheet(f"background:{c};color:white;font-weight:bold;font-size:14px;border-radius:6px")
            hc.addWidget(b)
        self.btn_start.clicked.connect(self.on_start)
        self.btn_pause.clicked.connect(lambda: self.dash("pause"))
        self.btn_stop.clicked.connect(lambda: self.dash("stop"))
        self.lb_task = QtWidgets.QLabel("작업: -")
        hc.addWidget(self.lb_task)

        # --- 모니터 테이블
        self.tbl = QtWidgets.QTableWidget(9, 4)
        self.tbl.setHorizontalHeaderLabels(["축", "실제 TCP (30001)", "상대 · PC계산", "상대 · 로봇발행"])
        self.tbl.setColumnWidth(0, 116)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        rownames = ["가로 [mm]", "세로 [mm]", "깊이 [mm]",
                    "X [mm]", "Y [mm]", "Z [mm]", "Rx [deg]", "Ry [deg]", "Rz [deg]"]
        for r, name in enumerate(rownames):
            it0 = QtWidgets.QTableWidgetItem(name)
            if r < 3:                      # 벽면 기준 3성분은 눈에 띄게
                f = it0.font(); f.setBold(True); it0.setFont(f)
            self.tbl.setItem(r, 0, it0)
            for c in range(1, 4):
                it = QtWidgets.QTableWidgetItem("-")
                it.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                self.tbl.setItem(r, c, it)
        hh = self.tbl.horizontalHeader()
        hh.setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        hh.setSectionResizeMode(0, QtWidgets.QHeaderView.Fixed)
        self.tbl.setFixedHeight(9 * 30 + 30)

        self.lb_diff = QtWidgets.QLabel("PC계산 ↔ 로봇발행 차이: -")
        self.lb_diff.setStyleSheet("font-weight:bold")

        # --- 상태 표시
        g_st = QtWidgets.QGroupBox("태스크 상태")
        gs = QtWidgets.QGridLayout(g_st)
        gs.setHorizontalSpacing(14)
        self.st = {}
        items = [("state", "상태"), ("zero", "원점"), ("row", "패스"), ("prog", "진행률"),
                 ("path", "누적거리"), ("pitch", "실피치"), ("alive", "alive"), ("zpose", "zero_pose")]
        for i, (k, label) in enumerate(items):
            lb = QtWidgets.QLabel("-")
            lb.setStyleSheet("font-family:Consolas,monospace")
            self.st[k] = lb
            gs.addWidget(QtWidgets.QLabel(label), i // 2, (i % 2) * 2)
            gs.addWidget(lb, i // 2, (i % 2) * 2 + 1)
        gs.setColumnStretch(1, 1)
        gs.setColumnStretch(3, 1)
        self.lb_axis = QtWidgets.QLabel("벽면 좌표: zero_pose 수신 전")
        self.lb_axis.setStyleSheet("color:#6b7280")
        gs.addWidget(self.lb_axis, 4, 0, 1, 4)
        self.btn_zero = QtWidgets.QPushButton("zero_pose 다시 읽기")
        self.btn_zero.clicked.connect(lambda: self.poller and self.poller.request_zero_refresh())
        gs.addWidget(self.btn_zero, 5, 0, 1, 4)

        # --- 플롯 / 로그 / CSV
        self.plot = PathPlot()
        self.chk_csv = QtWidgets.QCheckBox("CSV 기록")
        self.chk_csv.toggled.connect(self.toggle_csv)
        self.btn_clear = QtWidgets.QPushButton("궤적 지우기")
        self.btn_clear.clicked.connect(self.plot.clear_trace)
        self.txt_log = QtWidgets.QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumBlockCount(500)
        self.txt_log.setFixedHeight(110)

        hplot = QtWidgets.QHBoxLayout()
        hplot.addWidget(QtWidgets.QLabel("경로 (원점 기준 벽면 가로 / 세로)"))
        hplot.addStretch(1); hplot.addWidget(self.chk_csv); hplot.addWidget(self.btn_clear)

        left = vbox(g_conn, g_par, g_ctl, self.tbl, self.lb_diff)
        right = vbox(g_st, hplot, self.plot, self.txt_log)
        root = QtWidgets.QHBoxLayout(self)
        lw = QtWidgets.QWidget(); lw.setLayout(left); lw.setFixedWidth(560)
        rw = QtWidgets.QWidget(); rw.setLayout(right)
        root.addWidget(lw); root.addWidget(rw)

        self.update_preview()
        if not HAVE_ROBOT:
            self.log(f"robot.py 를 불러오지 못했습니다: {ROBOT_IMPORT_ERROR}")

    # ------------------------------------------------------------- 유틸
    def log(self, msg):
        self.txt_log.appendPlainText(f"{time.strftime('%H:%M:%S')}  {msg}")

    def dash(self, cmd):
        if self.r29999 is None:
            self.log("29999 미연결")
            return None
        try:
            r = self.r29999.send_command_29999(cmd)
            self.log(f"[29999] {cmd} -> {r}")
            return r
        except Exception as e:
            self.log(f"[29999] {cmd} 실패: {e}")
            return None

    def power_on(self):
        for c in ("remoteControl -on", "robotControl -on", "brakeRelease"):
            self.dash(c)
            time.sleep(0.3)

    # ------------------------------------------------------------- 연결
    def toggle_conn(self):
        if self.poller is None:
            if not HAVE_ROBOT:
                QtWidgets.QMessageBox.warning(self, "오류", "robot.py 를 같은 폴더에 두세요.")
                return
            ip = self.ed_ip.text().strip()
            try:
                self.r29999 = Robot_29999(ip, 29999)
                self.r29999.connect_29999()
            except Exception as e:
                self.log(f"[29999] 연결 실패: {e}")
            self.poller = Poller(ip, self.sp_mbport.value())
            self.poller.sample.connect(self.on_sample)
            self.poller.log.connect(self.log)
            self.poller.linkchanged.connect(self.on_link)
            self.poller.start()
            self.btn_conn.setText("해제")
        else:
            self.poller.stop(); self.poller.wait(2000); self.poller = None
            try:
                self.r29999 and self.r29999.disconnect_29999()
            except Exception:
                pass
            self.r29999 = None
            self.btn_conn.setText("연결")
            self.on_link(False, False)

    def on_link(self, stream, mb):
        if stream and mb:
            self.lb_link.setText("● 연결됨 (30001+Modbus)"); c = "#16a34a"
        elif stream or mb:
            self.lb_link.setText(f"● 부분 연결 ({'30001' if stream else 'Modbus'})"); c = "#d97706"
        else:
            self.lb_link.setText("● 미연결"); c = "#9ca3af"
        self.lb_link.setStyleSheet(f"color:{c};font-weight:bold")

    # ------------------------------------------------------------- 파라미터
    def params(self):
        return {k: sp.value() for k, sp in self.spins.items()}

    def update_preview(self):
        p = self.params()
        pl = M.plan(**p)
        self.lb_preview.setText(
            f"상승 피치   {pl['pitch']} mm  (= {p['scan_h']} - {p['overlap']})\n"
            f"가로 패스   {pl['rows']} 회\n"
            f"마지막 높이 {pl['top']} mm\n"
            f"커버 상단   {pl['covered']} mm  "
            + ("OK" if pl['covered'] >= p['app_height'] else "부족!") + "\n"
            f"총 이동     {pl['path_len']:.0f} mm")
        self.plot.set_ideal(M.ideal_path(**p))

    def send_params_modbus(self):
        if self.poller is None:
            self.log("미연결"); return
        p = self.params()
        for key, addr, *_ in M.INPUT_FIELDS:
            self.poller.request_write(addr, p[key])
        self.poller.request_write(M.R_IN_SRC, 1)
        self.log("파라미터를 Modbus 256~259 로 전송, 266(param_src)=1")

    def send_params_variable(self):
        if self.r29999 is None:
            self.log("29999 미연결"); return
        for key, *_ in M.INPUT_FIELDS:
            self.dash(f"variable -set {key} {self.params()[key]}")
        self.dash("variable -set param_src 0")
        if self.poller:
            self.poller.request_write(M.R_IN_SRC, 0)
        self.log("파라미터를 29999 변수로 전송, param_src=0")

    def on_start(self):
        self.plot.clear_trace()
        self.zero_pose = None
        if self.poller:
            self.poller.set_zero_pose(None)
        self.st["zpose"].setText("-")
        self.dash("play")

    # ------------------------------------------------------------- CSV
    def toggle_csv(self, on):
        if on:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                time.strftime("dusan_log_%Y%m%d_%H%M%S.csv"))
            self.csv_file = open(path, "w", newline="", encoding="utf-8-sig")
            self.csv_writer = csv.writer(self.csv_file)
            self.csv_writer.writerow(
                ["t", "tcp_x", "tcp_y", "tcp_z", "tcp_rx", "tcp_ry", "tcp_rz",
                 "pc_x", "pc_y", "pc_z", "rb_x", "rb_y", "rb_z",
                 "state", "row", "rows", "prog", "path_mm", "alive"])
            self.log(f"CSV 기록 시작: {path}")
        else:
            if self.csv_file:
                self.csv_file.close()
            self.csv_file = self.csv_writer = None
            self.log("CSV 기록 중지")

    # ------------------------------------------------------------- 샘플 수신
    def on_sample(self, s):
        if s.get("need_zero"):
            self.fetch_zero_pose()

        tcp = s.get("tcp")
        if tcp:
            vals = [tcp[0] * 1000, tcp[1] * 1000, tcp[2] * 1000,
                    tcp[3] * 57.2957795, tcp[4] * 57.2957795, tcp[5] * 57.2957795]
            for r, v in enumerate(vals):
                self.tbl.item(r + 3, 1).setText(f"{v:10.2f}")
            for r in range(3):
                self.tbl.item(r, 1).setText("-")      # 절대 TCP 는 벽면 좌표가 없다
            st = "실행중" if s.get("running") else ("일시정지" if s.get("paused") else "정지")
            self.lb_task.setText(f"작업: {st}")

        dec = s.get("dec")
        scanning = bool(dec and dec["state"] == 4)

        pc = s.get("rel_pc")
        if pc:
            for r, v in enumerate(pc):
                self.tbl.item(r + 3, 2).setText(f"{v:10.2f}")
            wall = M.to_wall3(pc[:3], self.amap)
            for r, v in enumerate(wall):
                self.tbl.item(r, 2).setText(f"{v:10.2f}")
            # 궤적은 스캔중(state=4)일 때만 남긴다.
            # 3점 측정이나 홈 복귀 구간까지 그리면 ㄹ자가 안 보인다.
            self.plot.add_point(wall[0], wall[1], record=scanning)

        rb = None
        if dec:
            rb = list(dec["rel_mm"]) + list(dec["rel_deg"])
            for r, v in enumerate(rb):
                self.tbl.item(r + 3, 3).setText(f"{v:10.2f}")
            for r, v in enumerate(M.to_wall3(dec["rel_mm"], self.amap)):
                self.tbl.item(r, 3).setText(f"{v:10.2f}")
            self.st["state"].setText(f"{dec['state']}  {M.STATE_TXT.get(dec['state'], '?')}")
            self.st["zero"].setText("확정" if dec["zero_ok"] else "미확정")
            self.st["row"].setText(f"{dec['row']} / {dec['rows']}")
            self.st["prog"].setText(f"{dec['progress']} %")
            self.st["path"].setText(f"{dec['path_mm']:.0f} mm")
            self.st["pitch"].setText(f"{dec['pitch_mm']:.2f} mm")
            a = dec["alive"]
            now = time.time()
            if a != self._last_alive:
                self._last_alive, self._alive_stamp = a, now
            stale = now - self._alive_stamp
            self.st["alive"].setText(f"{a}" + ("" if stale < 1.5 else f"  (정지 {stale:.0f}s)"))
            self.st["alive"].setStyleSheet(
                "font-family:Consolas,monospace;color:" + ("#16a34a" if stale < 1.5 else "#dc2626"))
            if not dec["zero_ok"]:
                self.plot.cur = None

        if pc and rb:
            d = max(abs(pc[i] - rb[i]) for i in range(3))
            self.lb_diff.setText(f"PC계산 ↔ 로봇발행 최대 차이: {d:.2f} mm"
                                 + ("   (0.1mm 양자화 범위 내)" if d <= 0.35 else "   ← 확인 필요"))
            self.lb_diff.setStyleSheet("font-weight:bold;color:" + ("#16a34a" if d <= 0.35 else "#dc2626"))

        if self.csv_writer and tcp:
            self.csv_writer.writerow(
                [f"{s['ts']:.3f}"] + [f"{v:.5f}" for v in tcp]
                + [f"{v:.3f}" for v in (pc[:3] if pc else (0, 0, 0))]
                + [f"{v:.3f}" for v in (rb[:3] if rb else (0, 0, 0))]
                + ([dec["state"], dec["row"], dec["rows"], dec["progress"],
                    f"{dec['path_mm']:.1f}", dec["alive"]] if dec else [""] * 6))

    def fetch_zero_pose(self):
        if self.r29999 is None:
            return
        try:
            v = self.r29999.get_variable("zero_pose")
            if isinstance(v, (list, tuple)) and len(v) == 6:
                self.zero_pose = [float(x) for x in v]
                self.poller and self.poller.set_zero_pose(self.zero_pose)
                self.st["zpose"].setText("[" + ", ".join(f"{x:.4f}" for x in self.zero_pose) + "]")
                self.amap = M.axis_map(self.zero_pose)
                self.lb_axis.setText("벽면 좌표 변환 적용   " + M.tool_axis_hint(self.amap))
                self.log(f"zero_pose 갱신: {self.zero_pose}")
            else:
                self.log(f"zero_pose 읽기 실패: {v!r}")
        except Exception as e:
            self.log(f"zero_pose 읽기 오류: {e}")

    def closeEvent(self, e):
        if self.poller:
            self.poller.stop(); self.poller.wait(2000)
        if self.csv_file:
            self.csv_file.close()
        e.accept()


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    w = Main()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        input("엔터를 누르면 종료합니다...")
