#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
capture_compare.py — 시뮬레이터 vs 실제 로봇 데이터 비교용 기록기
==================================================================
버튼 두 개로 일정 시간 동안 Modbus 레지스터를 그대로 기록한다.
각 버튼은 서로 다른 파일에 저장하므로 나중에 두 파일을 비교하면
"시뮬은 되는데 로봇은 값이 튄다" 의 원인을 찾을 수 있다.

  [시뮬레이터 캡처]  -> capture_SIM_20260730_153012.csv   + _summary.txt
  [실제 로봇 캡처]   -> capture_ROBOT_20260730_153140.csv + _summary.txt

기록 내용
  - 매 샘플의 원시 레지스터 값 (63~110, 384~389, 400~405, 410~415, 500)
  - 디코딩한 관절 deg / TCP mm·deg
  - 샘플 간격(ms), 읽기 오류, 사용된 기능코드
  - summary.txt : 레지스터별 min/max/변화횟수/최대 점프량 + 이상 징후 자동 진단

실행:  python capture_compare.py
"""

import os
import csv
import sys
import time
import statistics
import threading
import traceback
from datetime import datetime

from PyQt5 import QtCore, QtWidgets

import modbus_io
from modbus_io import RobotModbus
from robot_map import (to_int16, RAD2DEG, RobotData,
                       JOINT_NAMES, TCP_NAMES, ROBOT_MODE_TXT)

# 기록할 레지스터 블록 (시작, 개수)
# 매뉴얼상 "Reserved" 인 구간(390~399, 406~409, 416~448)도 일부러 포함한다.
# 외부 장치가 연속으로 읽을 때 이 구간까지 넘어가 쓰레기값을 받는 경우가 있어서,
# 실제 로봇이 여기에 무엇을 돌려주는지 확인해야 한다.
CAPTURE_BLOCKS = [
    (63, 48),      # 63~110  버전/상태/관절 위치·속도·전류·온도
    (111, 20),     # 111~130 매뉴얼 미기재 구간
    (384, 65),     # 384~448 TCP pose/speed/offset + Reserved 구간 전체
    (500, 12),     # 500~511 운전 상태 + 그 뒤
]
JOINT_ADDRS = list(range(73, 79))
TCP_ADDRS = list(range(384, 390))

# 물리적으로 가능한 최대 변화량 (10Hz 기준, 이보다 크면 '점프'로 본다)
JUMP_LIMIT_JOINT_MRAD = 400      # 0.4 rad = 약 23도/샘플
JUMP_LIMIT_TCP_01MM = 2000       # 200 mm/샘플
JUMP_LIMIT_TCP_MRAD = 800


def addr_label(a):
    if 63 <= a <= 65:
        return f"{a} version"
    if a == 66:
        return "66 robot_mode"
    if a == 67:
        return "67 power_on"
    if a == 68:
        return "68 protective_stop"
    if a == 69:
        return "69 emergency_stop"
    if a == 70:
        return "70 reduced_mode"
    if a == 71:
        return "71 control_method"
    if a == 72:
        return "72 operation_mode"
    if 73 <= a <= 78:
        return f"{a} joint_pos_{a - 73} ({JOINT_NAMES[a - 73].split()[0]})"
    if 84 <= a <= 89:
        return f"{a} joint_spd_{a - 84}"
    if 95 <= a <= 100:
        return f"{a} joint_cur_{a - 95}"
    if 105 <= a <= 110:
        return f"{a} joint_tmp_{a - 105}"
    if 384 <= a <= 389:
        return f"{a} {TCP_NAMES[a - 384].replace(' ', '_')}"
    if 400 <= a <= 405:
        return f"{a} tcp_spd_{a - 400}"
    if 410 <= a <= 415:
        return f"{a} tcp_offset_{a - 410}"
    if a == 500:
        return "500 running_state"
    return str(a)


# ============================================================================
class CaptureWorker(threading.Thread):
    """지정 시간 동안 레지스터를 읽어 리스트에 쌓는다."""

    def __init__(self, host, port, unit, read_fc, interval_ms, duration_s,
                 on_log, on_progress, on_done):
        super().__init__(daemon=True, name="capture")
        self.host, self.port, self.unit = host, int(port), int(unit)
        self.read_fc = read_fc
        self.interval = max(20, int(interval_ms)) / 1000.0
        self.duration = float(duration_s)
        self.on_log, self.on_progress, self.on_done = on_log, on_progress, on_done
        self._stop = False
        self.samples = []      # [{'t':초, 'regs':{addr:val}}]
        self.errors = []       # [(초, 메시지)]
        self.fc_used = None
        self.block_err = {}    # 블록 시작주소 -> 실패 횟수
        self.dead_blocks = set()

    def stop(self):
        self._stop = True

    def run(self):
        mb = RobotModbus(self.host, self.port, self.unit, timeout=1.0,
                         read_fc=self.read_fc, on_log=self.on_log)
        if not mb.connect():
            self.on_log(f"연결 실패: {self.host}:{self.port}")
            self.on_done(self, False)
            return
        self.on_log(f"연결 성공 {self.host}:{self.port} (unit {self.unit}) — "
                    f"{self.duration:.0f}초 기록 시작")

        t0 = time.time()
        next_t = t0
        while not self._stop:
            now = time.time()
            el = now - t0
            if el >= self.duration:
                break
            regs = {}
            for start, count in CAPTURE_BLOCKS:
                if start in self.dead_blocks:
                    continue
                try:
                    for i, v in enumerate(mb.read_hr(start, count)):
                        regs[start + i] = v
                except Exception as e:                  # noqa: BLE001
                    self.errors.append((el, f"블록 {start}+{count}: {e}"))
                    self.block_err[start] = self.block_err.get(start, 0) + 1
                    if self.block_err[start] == 1:
                        self.on_log(f"블록 {start}~{start + count - 1} 읽기 실패: {e}")
                    if self.block_err[start] == 3:
                        self.dead_blocks.add(start)
                        self.on_log(f"블록 {start}~{start + count - 1} 는 "
                                    f"이 컨트롤러에서 읽을 수 없음 — 제외하고 계속")
            if regs:
                self.samples.append({"t": el, "regs": regs})
            self.on_progress(min(1.0, el / self.duration), len(self.samples))

            next_t += self.interval
            sleep = next_t - time.time()
            if sleep > 0:
                time.sleep(sleep)
            else:
                next_t = time.time()      # 밀렸으면 리셋

        self.fc_used = mb.read_fc_used
        mb.disconnect()
        self.on_log(f"기록 종료 — 샘플 {len(self.samples)}개, 오류 {len(self.errors)}회")
        self.on_done(self, True)


# ============================================================================
def write_csv(path, worker):
    addrs = sorted({a for s in worker.samples for a in s["regs"]})
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        head = ["idx", "elapsed_s", "dt_ms"]
        head += [f"r{a}" for a in addrs]                    # 원시 unsigned
        head += [f"s{a}" for a in addrs]                    # signed 해석
        head += [f"J{i + 1}_deg" for i in range(6)]
        head += ["X_mm", "Y_mm", "Z_mm", "Rx_deg", "Ry_deg", "Rz_deg"]
        w.writerow(head)

        prev_t = None
        for i, s in enumerate(worker.samples):
            regs = s["regs"]
            dt = "" if prev_t is None else round((s["t"] - prev_t) * 1000, 1)
            prev_t = s["t"]
            row = [i, round(s["t"], 4), dt]
            row += [regs.get(a, "") for a in addrs]
            row += [to_int16(regs[a]) if a in regs else "" for a in addrs]
            d = RobotData.from_registers(regs)
            row += [round(v, 4) for v in d.joint_deg]
            row += [round(v, 4) for v in d.tcp_conv]
            w.writerow(row)
    return addrs


def analyze(worker, addrs):
    """레지스터별 통계 + 이상 징후 진단 문자열 리스트 반환."""
    lines = []
    n = len(worker.samples)
    if n < 2:
        return ["샘플이 너무 적어 분석할 수 없습니다."], []

    # 샘플 간격
    dts = [(worker.samples[i]["t"] - worker.samples[i - 1]["t"]) * 1000
           for i in range(1, n)]
    lines.append(f"샘플 수        : {n}")
    lines.append(f"기록 시간      : {worker.samples[-1]['t']:.2f} s")
    lines.append(f"읽기 오류      : {len(worker.errors)} 회")
    lines.append(f"사용 기능코드  : FC{worker.fc_used}")
    lines.append(f"샘플 간격(ms)  : 평균 {statistics.mean(dts):.1f} / "
                 f"최소 {min(dts):.1f} / 최대 {max(dts):.1f} / "
                 f"표준편차 {statistics.pstdev(dts):.1f}")

    # 레지스터별 통계
    stats = {}
    for a in addrs:
        vals = [to_int16(s["regs"][a]) for s in worker.samples if a in s["regs"]]
        if len(vals) < 2:
            continue
        diffs = [abs(vals[i] - vals[i - 1]) for i in range(1, len(vals))]
        stats[a] = {
            "min": min(vals), "max": max(vals),
            "changes": sum(1 for d in diffs if d != 0),
            "maxjump": max(diffs),
            "mean_step": statistics.mean(diffs),
            "vals": vals,
        }

    problems = []

    # 1) 관절/TCP 점프 검사
    lines.append("")
    lines.append("[관절 위치 73~78]")
    for a in JOINT_ADDRS:
        st = stats.get(a)
        if not st:
            continue
        flag = ""
        if st["maxjump"] > JUMP_LIMIT_JOINT_MRAD:
            flag = "  <== 점프 의심"
            problems.append(f"관절 주소 {a} 에서 한 샘플 사이 {st['maxjump']} mRad "
                            f"({st['maxjump'] / 1000 * RAD2DEG:.1f}도) 변화")
        if st["changes"] == 0:
            flag = "  <== 값이 전혀 변하지 않음"
            problems.append(f"관절 주소 {a} 값이 고정 ({st['min']})")
        lines.append(f"  {addr_label(a):<34} min {st['min']:>7} max {st['max']:>7} "
                     f"변화 {st['changes']:>5} 최대점프 {st['maxjump']:>6} "
                     f"평균스텝 {st['mean_step']:>7.1f}{flag}")

    lines.append("")
    lines.append("[TCP 384~389]")
    for a in TCP_ADDRS:
        st = stats.get(a)
        if not st:
            continue
        lim = JUMP_LIMIT_TCP_01MM if a <= 386 else JUMP_LIMIT_TCP_MRAD
        flag = ""
        if st["maxjump"] > lim:
            flag = "  <== 점프 의심"
            problems.append(f"TCP 주소 {a} 에서 한 샘플 사이 {st['maxjump']} 변화")
        if st["changes"] == 0:
            flag = "  <== 값이 전혀 변하지 않음"
        lines.append(f"  {addr_label(a):<34} min {st['min']:>7} max {st['max']:>7} "
                     f"변화 {st['changes']:>5} 최대점프 {st['maxjump']:>6} "
                     f"평균스텝 {st['mean_step']:>7.1f}{flag}")

    # 2) 상태 레지스터
    lines.append("")
    lines.append("[상태 레지스터]")
    for a in [63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 500]:
        st = stats.get(a)
        if not st:
            continue
        extra = ""
        if a == 66:
            extra = f"  ({ROBOT_MODE_TXT.get(st['max'], '?')})"
        lines.append(f"  {addr_label(a):<34} min {st['min']:>7} max {st['max']:>7} "
                     f"변화 {st['changes']:>5}{extra}")

    # 3) 전 구간에서 값이 안 변하는 / 이상하게 큰 레지스터
    lines.append("")
    lines.append("[전체 레지스터 요약]  (변화가 있는 것만)")
    for a in addrs:
        st = stats.get(a)
        if not st or st["changes"] == 0:
            continue
        lines.append(f"  {addr_label(a):<34} min {st['min']:>7} max {st['max']:>7} "
                     f"변화 {st['changes']:>5} 최대점프 {st['maxjump']:>6}")

    # 3-b) 매뉴얼상 Reserved 구간에 값이 들어있는지
    reserved = [a for a in addrs
                if (390 <= a <= 399) or (406 <= a <= 409) or (416 <= a <= 448)
                or (111 <= a <= 130) or (501 <= a <= 511)]
    lines.append("")
    lines.append("[매뉴얼상 Reserved / 미기재 구간]  외부가 연속으로 읽다 넘어가는 곳")
    nz = []
    for a in reserved:
        st = stats.get(a)
        if not st:
            continue
        if st["min"] != 0 or st["max"] != 0:
            nz.append(a)
            lines.append(f"  {a:>5} min {st['min']:>7} max {st['max']:>7} "
                         f"변화 {st['changes']:>5} 최대점프 {st['maxjump']:>6}"
                         f"   <== 0 이 아닌 값이 있음")
    if not nz:
        lines.append("  전부 0 — 이 구간을 넘어 읽어도 0 만 나옴")
    else:
        problems.append(f"Reserved 구간 {nz} 에 0 이 아닌 값이 있음 — "
                        f"외부가 여기까지 연속으로 읽으면 쓰레기값을 받는다")

    dead = [a for a in addrs if a in stats and stats[a]["changes"] == 0]
    lines.append("")
    lines.append(f"[변화 없는 레지스터] {len(dead)}개: "
                 + ", ".join(str(a) for a in dead))

    # 4) 자동 진단
    lines.append("")
    lines.append("=" * 60)
    lines.append("자동 진단")
    if len(worker.errors) > 0:
        problems.append(f"읽기 오류가 {len(worker.errors)}회 발생 "
                        f"(첫 오류: {worker.errors[0][1][:80]})")
    if max(dts) > statistics.mean(dts) * 3:
        problems.append(f"샘플 간격이 불규칙 (최대 {max(dts):.0f}ms, "
                        f"평균 {statistics.mean(dts):.0f}ms) — 통신 지연 의심")
    if not problems:
        lines.append("  뚜렷한 이상 없음.")
    else:
        for p in problems:
            lines.append(f"  - {p}")

    return lines, problems


def write_summary(path, worker, addrs, meta):
    lines, problems = analyze(worker, addrs)
    with open(path, "w", encoding="utf-8") as f:
        f.write("=" * 60 + "\n")
        f.write("Modbus 캡처 요약\n")
        f.write("=" * 60 + "\n")
        for k, v in meta.items():
            f.write(f"{k:<14}: {v}\n")
        f.write("\n")
        f.write("\n".join(lines))
        f.write("\n")
        if worker.errors:
            f.write("\n[읽기 오류 목록 (앞 30개)]\n")
            for t, m in worker.errors[:30]:
                f.write(f"  {t:8.3f}s  {m}\n")
    return problems


# ============================================================================
class MainWindow(QtWidgets.QMainWindow):
    log_sig = QtCore.pyqtSignal(str)
    prog_sig = QtCore.pyqtSignal(float, int)
    done_sig = QtCore.pyqtSignal(object, bool)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Modbus 캡처 비교 — 시뮬레이터 vs 실제 로봇")
        self.resize(820, 620)
        self.worker = None
        self.cur_kind = ""
        self.outdir = os.path.dirname(os.path.abspath(__file__))

        self._build_ui()
        self.log_sig.connect(self.append_log)
        self.prog_sig.connect(self.on_progress)
        self.done_sig.connect(self.on_done)

        self.append_log(f"pyModbusTCP / robot.py 연동 "
                        f"{'O' if modbus_io.HAVE_ROBOT_PY else 'X'}")
        self.append_log(f"저장 폴더: {self.outdir}")

    # ------------------------------------------------------------------
    def _build_ui(self):
        c = QtWidgets.QWidget()
        self.setCentralWidget(c)
        root = QtWidgets.QVBoxLayout(c)

        row = QtWidgets.QHBoxLayout()

        # 시뮬레이터
        gb1 = QtWidgets.QGroupBox("A. 시뮬레이터")
        f1 = QtWidgets.QFormLayout(gb1)
        self.ed_sip = QtWidgets.QLineEdit("127.0.0.1")
        self.sp_sport = self._spin(1, 65535, 5502)
        self.sp_sunit = self._spin(0, 255, 1)
        f1.addRow("IP", self.ed_sip)
        f1.addRow("포트", self.sp_sport)
        f1.addRow("Unit ID", self.sp_sunit)
        self.btn_sim = QtWidgets.QPushButton("▶  시뮬레이터 캡처")
        self.btn_sim.setMinimumHeight(44)
        self.btn_sim.setStyleSheet("font-weight:bold; background:#2b6cb0; color:white;")
        self.btn_sim.clicked.connect(lambda: self.start("SIM"))
        f1.addRow(self.btn_sim)
        row.addWidget(gb1)

        # 실제 로봇
        gb2 = QtWidgets.QGroupBox("B. 실제 로봇")
        f2 = QtWidgets.QFormLayout(gb2)
        self.ed_rip = QtWidgets.QLineEdit("192.168.227.134")
        self.sp_rport = self._spin(1, 65535, 502)
        self.sp_runit = self._spin(0, 255, 1)
        f2.addRow("IP", self.ed_rip)
        f2.addRow("포트", self.sp_rport)
        f2.addRow("Unit ID", self.sp_runit)
        self.btn_robot = QtWidgets.QPushButton("▶  실제 로봇 캡처")
        self.btn_robot.setMinimumHeight(44)
        self.btn_robot.setStyleSheet("font-weight:bold; background:#b7791f; color:white;")
        self.btn_robot.clicked.connect(lambda: self.start("ROBOT"))
        f2.addRow(self.btn_robot)
        row.addWidget(gb2)

        root.addLayout(row)

        # 공통 설정
        gb3 = QtWidgets.QGroupBox("공통 설정")
        f3 = QtWidgets.QHBoxLayout(gb3)
        self.sp_dur = self._spin(1, 600, 20, " 초")
        self.sp_int = self._spin(20, 2000, 100, " ms")
        self.cb_fc = QtWidgets.QComboBox()
        self.cb_fc.addItems(["자동 (FC3 → FC4)", "FC3 홀딩", "FC4 입력"])
        f3.addWidget(QtWidgets.QLabel("기록 시간"))
        f3.addWidget(self.sp_dur)
        f3.addWidget(QtWidgets.QLabel("샘플 주기"))
        f3.addWidget(self.sp_int)
        f3.addWidget(QtWidgets.QLabel("읽기 방식"))
        f3.addWidget(self.cb_fc)
        f3.addStretch(1)
        self.btn_stop = QtWidgets.QPushButton("중지")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop)
        f3.addWidget(self.btn_stop)
        root.addWidget(gb3)

        # 진행 표시
        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 100)
        root.addWidget(self.bar)
        self.lb_state = QtWidgets.QLabel("대기 중")
        self.lb_state.setStyleSheet("font-weight:bold;")
        root.addWidget(self.lb_state)

        # 로그
        self.txt = QtWidgets.QPlainTextEdit()
        self.txt.setReadOnly(True)
        self.txt.setStyleSheet("font-family:Consolas,monospace; font-size:11px;")
        root.addWidget(self.txt, 1)

        note = QtWidgets.QLabel(
            "① 시뮬레이터를 먼저 켜고(python robot_simulator.py --port 5502) A 캡처 → "
            "② 로봇을 실제로 움직이면서 B 캡처 → 생성된 csv / _summary.txt 파일을 공유")
        note.setWordWrap(True)
        note.setStyleSheet("color:#666;")
        root.addWidget(note)

    def _spin(self, lo, hi, val, suffix=""):
        s = QtWidgets.QSpinBox()
        s.setRange(lo, hi)
        s.setValue(val)
        if suffix:
            s.setSuffix(suffix)
        return s

    # ------------------------------------------------------------------
    def append_log(self, m):
        self.txt.appendPlainText(time.strftime("[%H:%M:%S] ") + m)

    def on_progress(self, frac, n):
        self.bar.setValue(int(frac * 100))
        self.lb_state.setText(f"{self.cur_kind} 캡처 중… 샘플 {n}개")

    # ------------------------------------------------------------------
    def start(self, kind):
        if self.worker is not None and self.worker.is_alive():
            return
        self.cur_kind = kind
        if kind == "SIM":
            host, port, unit = (self.ed_sip.text().strip(),
                                self.sp_sport.value(), self.sp_sunit.value())
        else:
            host, port, unit = (self.ed_rip.text().strip(),
                                self.sp_rport.value(), self.sp_runit.value())
        if not host:
            QtWidgets.QMessageBox.warning(self, "확인", "IP를 입력하세요.")
            return

        read_fc = {0: "auto", 1: 3, 2: 4}[self.cb_fc.currentIndex()]
        self.btn_sim.setEnabled(False)
        self.btn_robot.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.bar.setValue(0)
        self.append_log("=" * 50)
        self.append_log(f"[{kind}] {host}:{port} unit {unit} — "
                        f"{self.sp_dur.value()}초 / {self.sp_int.value()}ms")

        self.worker = CaptureWorker(
            host, port, unit, read_fc,
            self.sp_int.value(), self.sp_dur.value(),
            on_log=self.log_sig.emit,
            on_progress=lambda f, n: self.prog_sig.emit(f, n),
            on_done=lambda w, ok: self.done_sig.emit(w, ok))
        self.worker.start()

    def stop(self):
        if self.worker is not None:
            self.worker.stop()
            self.append_log("중지 요청")

    # ------------------------------------------------------------------
    def on_done(self, worker, ok):
        self.btn_sim.setEnabled(True)
        self.btn_robot.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.bar.setValue(100 if ok else 0)

        if not ok or not worker.samples:
            self.lb_state.setText("실패 — 저장할 데이터 없음")
            self.append_log("저장할 샘플이 없습니다.")
            return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = os.path.join(self.outdir, f"capture_{self.cur_kind}_{ts}")
        csv_path, sum_path = base + ".csv", base + "_summary.txt"

        try:
            addrs = write_csv(csv_path, worker)
            meta = {
                "종류": self.cur_kind,
                "대상": f"{worker.host}:{worker.port} unit {worker.unit}",
                "일시": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "설정 주기": f"{self.sp_int.value()} ms",
                "설정 시간": f"{self.sp_dur.value()} s",
                "읽기 방식": self.cb_fc.currentText(),
                "robot.py": "연동" if modbus_io.HAVE_ROBOT_PY else "없음",
            }
            problems = write_summary(sum_path, worker, addrs, meta)
        except Exception:                               # noqa: BLE001
            self.append_log("저장 중 오류:\n" + traceback.format_exc())
            return

        self.append_log(f"저장 완료: {os.path.basename(csv_path)}")
        self.append_log(f"저장 완료: {os.path.basename(sum_path)}")
        if problems:
            self.append_log(f"이상 징후 {len(problems)}건:")
            for p in problems:
                self.append_log(f"   - {p}")
        else:
            self.append_log("뚜렷한 이상 징후 없음")
        self.lb_state.setText(
            f"{self.cur_kind} 완료 — 샘플 {len(worker.samples)}개, "
            f"오류 {len(worker.errors)}회 → {os.path.basename(csv_path)}")

    def closeEvent(self, ev):
        if self.worker is not None:
            self.worker.stop()
        ev.accept()


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
