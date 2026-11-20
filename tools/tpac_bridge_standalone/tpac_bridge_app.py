#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TPAC 브릿지 (단독판) — 로봇에서 기본 좌표 · CSCAN 좌표 · DO0 을 읽어 TPAC 에 내보낸다.

RCS 없이 돈다. 다른 모양을 검사하거나 로봇에서 직접 짠 동작 프로그램으로 스캔할 때, 스캔 좌표만
TPAC 에 넘기려고 RCS 의 TPAC 브리지에서 떼어 냈다(2026-11-18).

    로봇 (Modbus TCP 502, 이 프로그램이 클라이언트)
        384~389  기본 좌표 — 현재 TCP (베이스, 0.1mm · mRad)          ← 컨트롤러가 늘 채운다
        280~285  CSCAN 좌표 — 스캔 시작점(제로점) 기준 (0.1mm · mRad)  ← 로봇 프로그램이 쓴다
        286      C-scan 위치 — 이번 줄의 호 위 거리 [0.1mm]            ← 로봇 프로그램이 쓴다(선택)
        287      누적 거리 [mm]                                        ← 로봇 프로그램이 쓴다(선택)
        2        표준 디지털 출력 — 비트 0 = DO0 (스캔하는 동안 켠다)
        400~405  실제 TCP 속도, 73~78 관절, 63~72 · 500 상태          ← 컨트롤러가 채운다
    TPAC (Modbus TCP 502, 이 프로그램이 서버 — TPAC 은 UR 로봇처럼 읽는다)
        400 포즈 · 410 속도 · 420 작업 평면 · 430 Odometer · 431 C-scan · 270 관절
        1 / 코일 16~  DO 신호(기본: 로봇 DO0 그대로)

실행
    TPAC_Bridge.exe                    화면
    python tpac_bridge_app.py          화면(소스로)
    python tpac_bridge_app.py --headless   화면 없이 설정 파일대로 바로 시작(Ctrl+C 로 끝)
설정은 실행 파일 옆 tpac_bridge_settings.json 에 저장한다.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import time

from bridge_core import (CSCAN_ADDR, ODOMETER_ADDR, PLANE_ADDR, POSE_SOURCES, SIGNAL_MODES,
                         RobotPoller, TpacServer)
from robot_map import DEFAULT_READ_MAP, RAD2DEG, ROBOT_MODE_TXT, RUNNING_TXT

APP_TITLE = "TPAC 브릿지 (단독판)"
VERSION = "1.1 (2026-11-18)"
SETTINGS_NAME = "tpac_bridge_settings.json"
TPAC_LINK_TIMEOUT_S = 5.0
MAX_LOG_LINES = 300

DEFAULTS = {
    "robot_ip": "192.168.227.134", "robot_port": 502, "robot_unit": 1, "interval_ms": 20,
    "read_pose": DEFAULT_READ_MAP["pose"], "read_scan": DEFAULT_READ_MAP["scan"],
    "read_do": DEFAULT_READ_MAP["do"], "read_tcp_speed": DEFAULT_READ_MAP["tcp_speed"],
    "tpac_bind": "0.0.0.0", "tpac_port": 502, "start_addr": 0,
    "pose_source": "scan", "signal_mode": "do0", "latch_ms": 50, "delay_ms": 0,
    "plane_addr": PLANE_ADDR, "odometer_addr": ODOMETER_ADDR, "cscan_addr": CSCAN_ADDR,
    "auto_start": False,
    "settings_version": 2,
}


def app_dir() -> str:
    """설정 파일을 둘 곳 — exe 로 굳혔으면 exe 옆, 아니면 이 스크립트 옆."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def load_settings(path: str) -> dict:
    values = dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as f:
            stored = json.load(f)
        values.update({k: v for k, v in stored.items() if k in DEFAULTS})
        # 1.0 판은 읽기 주기 기본이 100 ms 였다 — TPAC 좌표가 평균 60 ms · 최대 110 ms 넘게 묵었다.
        # 그 기본값 그대로 저장돼 있으면 새 기본(20 ms)으로 올린다(사람이 바꾼 값은 그대로).
        if int(stored.get("settings_version", 1)) < 2 and int(stored.get("interval_ms", 100)) == 100:
            values["interval_ms"] = DEFAULTS["interval_ms"]
        values["settings_version"] = DEFAULTS["settings_version"]
    except (OSError, ValueError, TypeError):
        pass
    return values


def save_settings(path: str, values: dict) -> bool:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(values, f, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False


def read_map(values: dict) -> dict:
    return {"pose": int(values["read_pose"]), "scan": int(values["read_scan"]),
            "do": int(values["read_do"]), "tcp_speed": int(values["read_tcp_speed"])}


def pose_text(raw) -> list[str]:
    """0.1mm · mRad → mm · deg 글자 6개."""
    return [f"{raw[i] / 10.0:.1f}" for i in range(3)] + [f"{raw[i] / 1000.0 * RAD2DEG:.2f}" for i in range(3, 6)]


# ============================================================================
class BridgeRunner:
    """로봇 읽기 + TPAC 서버를 같이 켜고 끈다. 콜백은 스레드에서 오므로 큐로만 넘긴다."""

    def __init__(self, events: "queue.Queue"):
        self.events = events
        self.server = TpacServer(on_log=self._log, on_state=lambda ok, m: self._put("srv", ok, m),
                                 on_request=self._request, on_signal=lambda w, t: self._put("signal", w, t))
        self.poller: RobotPoller | None = None
        self.last_data = None
        self.last_request = None          # (시각, client, fc, addr, count)
        self._warned = set()

    def _put(self, *event):
        self.events.put(event)

    def _log(self, text: str):
        self._put("log", time.strftime("%H:%M:%S ") + text)

    def _request(self, ts, who, fc, addr, count):
        self.last_request = (ts, who, fc, addr, count)
        if self.server.running and self.server.covers(addr, count, fc) is None:
            key = (fc, addr, count)
            if key not in self._warned:
                self._warned.add(key)
                self._log(f"[TPAC] 채우지 않는 자리를 읽습니다 — FC{fc} {addr}~{addr + count - 1} "
                          f"({who}). 0 이 갑니다 — TPAC 쪽 주소를 확인하세요.")

    def _on_data(self, data):
        self.last_data = data
        self.server.update(data)
        self._put("data", data)

    @property
    def running(self) -> bool:
        return self.poller is not None

    def start(self, values: dict) -> bool:
        self.stop()
        self._warned.clear()
        ok = self.server.start(
            host=str(values["tpac_bind"]).strip() or "0.0.0.0", port=int(values["tpac_port"]),
            start_addr=int(values["start_addr"]), pose_source=values["pose_source"],
            signal_mode=values["signal_mode"], signal_latch_ms=int(values["latch_ms"]),
            delay_ms=int(values["delay_ms"]), plane_addr=int(values["plane_addr"]),
            odometer_addr=int(values["odometer_addr"]), cscan_addr=int(values["cscan_addr"]))
        if not ok:
            err = getattr(self.server.bridge, "last_error", None)
            self._log(f"[TPAC] 서버를 열지 못했습니다(포트 {values['tpac_port']}): {err} — 다른 프로그램"
                      "(RCS · 다른 브릿지 · 포트프록시)이 그 포트를 쓰는지 확인하세요: "
                      f"netstat -ano | findstr :{values['tpac_port']}")
            return False
        self.poller = RobotPoller(
            str(values["robot_ip"]).strip(), int(values["robot_port"]), int(values["robot_unit"]),
            int(values["interval_ms"]), on_data=self._on_data, on_log=self._log,
            on_conn=lambda ok, m: self._put("robot", ok, m), read_map=read_map(values),
            use_signal=values["signal_mode"] == "task")
        self.poller.start()
        return True

    def stop(self):
        poller, self.poller = self.poller, None
        if poller is not None:
            poller.stop(wait=2.0)
        if self.server.running:
            self.server.stop()


# ============================================================================
def run_headless(values: dict, path: str) -> int:
    events: queue.Queue = queue.Queue()
    runner = BridgeRunner(events)
    print(f"{APP_TITLE} {VERSION} — 설정 {path}")
    if not runner.start(values):
        while not events.empty():
            event = events.get()
            if event[0] == "log":
                print(event[1])
        return 1
    last_print = 0.0
    try:
        while True:
            try:
                event = events.get(timeout=0.5)
            except queue.Empty:
                event = None
            if event and event[0] == "log":
                print(event[1], flush=True)
            elif event and event[0] in ("robot", "srv"):
                print(f"[{'로봇' if event[0] == 'robot' else 'TPAC'}] {event[2]}", flush=True)
            data = runner.last_data
            if data is not None and time.time() - last_print > 5.0:
                last_print = time.time()
                print(f"  CSCAN {pose_text(data.tcp_scan)[:3]} mm · C-scan {data.arc_pos:.1f} mm · "
                      f"DO0 {data.do_word & 1} → TPAC DO {runner.server.signal_word:03b}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        runner.stop()
    return 0


# ============================================================================
def run_gui(values: dict, path: str, selftest: bool = False) -> int:
    import tkinter as tk
    from tkinter import messagebox, ttk

    events: queue.Queue = queue.Queue()
    runner = BridgeRunner(events)
    root = tk.Tk()
    root.title(f"{APP_TITLE} {VERSION}")
    root.geometry("1040x760")
    root.minsize(900, 640)
    font = ("Malgun Gothic", 10)
    bold = ("Malgun Gothic", 10, "bold")
    root.option_add("*Font", font)
    style = ttk.Style(root)
    style.configure("Good.TLabel", foreground="#1b7f3b", font=bold)
    style.configure("Bad.TLabel", foreground="#b3261e", font=bold)
    style.configure("Muted.TLabel", foreground="#666666")
    style.configure("Head.TLabel", font=("Malgun Gothic", 11, "bold"))

    vars_: dict[str, tk.Variable] = {}
    for key, default in DEFAULTS.items():
        if isinstance(default, bool):
            vars_[key] = tk.BooleanVar(value=bool(values[key]))
        elif isinstance(default, int):
            vars_[key] = tk.StringVar(value=str(values[key]))
        else:
            vars_[key] = tk.StringVar(value=str(values[key]))
    pose_names = dict(POSE_SOURCES)
    signal_names = {k: n for k, n, _m in SIGNAL_MODES}
    pose_choice = tk.StringVar(value=pose_names.get(values["pose_source"], POSE_SOURCES[0][1]))
    signal_choice = tk.StringVar(value=signal_names.get(values["signal_mode"], SIGNAL_MODES[0][1]))

    def collect() -> dict | None:
        out = {}
        for key, default in DEFAULTS.items():
            raw = vars_[key].get()
            if isinstance(default, bool):
                out[key] = bool(raw)
            elif isinstance(default, int):
                try:
                    out[key] = int(str(raw).strip())
                except ValueError:
                    messagebox.showerror(APP_TITLE, f"숫자가 아닙니다: {key} = {raw!r}")
                    return None
            else:
                out[key] = str(raw).strip()
        out["pose_source"] = next(k for k, n in POSE_SOURCES if n == pose_choice.get())
        out["signal_mode"] = next(k for k, n, _m in SIGNAL_MODES if n == signal_choice.get())
        return out

    # ---- 화면 ---------------------------------------------------------------
    top = ttk.Frame(root, padding=8)
    top.pack(fill="both", expand=True)
    left = ttk.Frame(top)
    left.pack(side="left", fill="y")
    right = ttk.Frame(top)
    right.pack(side="left", fill="both", expand=True, padx=(10, 0))

    def form(parent, title, rows):
        box = ttk.LabelFrame(parent, text=title, padding=8)
        box.pack(fill="x", pady=(0, 8))
        inputs = []
        for r, (label, key) in enumerate(rows):
            ttk.Label(box, text=label).grid(row=r, column=0, sticky="w", pady=2)
            entry = ttk.Entry(box, textvariable=vars_[key], width=18)
            entry.grid(row=r, column=1, sticky="w", padx=(8, 0), pady=2)
            inputs.append(entry)
        return box, inputs

    robot_box, robot_inputs = form(left, "로봇에서 읽기 (Modbus 클라이언트)", [
        ("로봇 IP", "robot_ip"), ("포트", "robot_port"), ("Unit ID", "robot_unit"),
        ("읽기 주기 [ms] (20 권장)", "interval_ms"),
        ("기본 좌표 (현재 TCP) 주소", "read_pose"), ("CSCAN 좌표 블록 주소", "read_scan"),
        ("DO 레지스터 주소 (비트0 = DO0)", "read_do"), ("실제 TCP 속도 주소", "read_tcp_speed")])
    ttk.Label(robot_box, text="CSCAN 블록 = +0~5 좌표, +6 C-scan 위치[0.1mm], +7 누적[mm]",
              style="Muted.TLabel").grid(row=8, column=0, columnspan=2, sticky="w", pady=(4, 0))

    tpac_box, tpac_inputs = form(left, "TPAC 에 내보내기 (Modbus 서버 — UR 배치)", [
        ("Bind IP", "tpac_bind"), ("포트", "tpac_port"), ("데이터 블록 시작 주소", "start_addr"),
        ("작업 평면(CSCAN) 주소", "plane_addr"), ("Odometer 주소", "odometer_addr"),
        ("C-scan 위치 주소", "cscan_addr"), ("DO 최소 유지 [ms]", "latch_ms"),
        ("최소 응답 간격 [ms]", "delay_ms")])
    row = len(tpac_inputs)
    ttk.Label(tpac_box, text="포즈(400) 값").grid(row=row, column=0, sticky="w", pady=2)
    pose_combo = ttk.Combobox(tpac_box, textvariable=pose_choice, state="readonly", width=34,
                              values=[n for _k, n in POSE_SOURCES])
    pose_combo.grid(row=row + 1, column=0, columnspan=2, sticky="w")
    ttk.Label(tpac_box, text="DO 신호 (레지스터 1 · 코일 16~)").grid(row=row + 2, column=0, sticky="w",
                                                                     pady=(6, 2))
    signal_combo = ttk.Combobox(tpac_box, textvariable=signal_choice, state="readonly", width=34,
                                values=[n for _k, n, _m in SIGNAL_MODES])
    signal_combo.grid(row=row + 3, column=0, columnspan=2, sticky="w")
    editable = robot_inputs + tpac_inputs + [pose_combo, signal_combo]

    buttons = ttk.Frame(left)
    buttons.pack(fill="x")
    start_btn = ttk.Button(buttons, text="시작")
    stop_btn = ttk.Button(buttons, text="정지", state="disabled")
    start_btn.pack(side="left")
    stop_btn.pack(side="left", padx=6)
    ttk.Checkbutton(buttons, text="켤 때 바로 시작", variable=vars_["auto_start"]).pack(side="left", padx=6)

    status = ttk.LabelFrame(right, text="상태", padding=8)
    status.pack(fill="x")
    robot_state = ttk.Label(status, text="● 로봇: 미연결", style="Bad.TLabel")
    tpac_state = ttk.Label(status, text="● TPAC 서버: 정지", style="Bad.TLabel")
    tpac_link = ttk.Label(status, text="● TPAC 요청: 없음", style="Bad.TLabel")
    for w in (robot_state, tpac_state, tpac_link):
        w.pack(anchor="w")

    table_box = ttk.LabelFrame(right, text="값", padding=8)
    table_box.pack(fill="both", expand=True, pady=(8, 0))
    cols = ("item", "x", "y", "z", "rx", "ry", "rz")
    table = ttk.Treeview(table_box, columns=cols, show="headings", height=9)
    for col, text, width in zip(cols, ("항목", "X", "Y", "Z", "Rx", "Ry", "Rz"),
                                (230, 80, 80, 80, 70, 70, 70)):
        table.heading(col, text=text)
        table.column(col, width=width, anchor="w" if col == "item" else "e")
    table.pack(fill="both", expand=True)
    rows = {}
    for key, label in (("pose", "기본 좌표 (현재 TCP) mm · deg"), ("scan", "CSCAN 좌표 (280~) mm · deg"),
                       ("tpac_pose", "→ TPAC 포즈(400) mm · deg"), ("speed", "→ TPAC 속도(410) mm/s"),
                       ("arc", "C-scan 위치 · 누적 [mm]"), ("do", "로봇 DO (DO0 ~ DO7)"),
                       ("tpac_do", "→ TPAC DO 신호 (레지스터 1)"), ("robot", "로봇 모드 · 태스크 · 주기")):
        rows[key] = table.insert("", "end", values=(label, "-", "-", "-", "-", "-", "-"))

    log_box = ttk.LabelFrame(right, text="기록", padding=8)
    log_box.pack(fill="both", expand=True, pady=(8, 0))
    log = tk.Text(log_box, height=12, wrap="word", state="disabled")
    log.pack(fill="both", expand=True)

    def append_log(text: str):
        log.configure(state="normal")
        log.insert("end", text + "\n")
        lines = int(log.index("end-1c").split(".")[0])
        if lines > MAX_LOG_LINES:
            log.delete("1.0", f"{lines - MAX_LOG_LINES}.0")
        log.see("end")
        log.configure(state="disabled")

    def set_row(key, cells):
        cells = list(cells) + ["-"] * (6 - len(cells))
        table.item(rows[key], values=(table.item(rows[key], "values")[0], *cells[:6]))

    def show_data(data):
        server = runner.server
        set_row("pose", pose_text(data.tcp_raw))
        set_row("scan", pose_text(data.tcp_scan))
        set_row("tpac_pose", pose_text(server.pose_for_tpac(data)))
        speed = data.tcp_spd_real if server.speed_live(data) else [0] * 6
        set_row("speed", [str(v) for v in speed])
        set_row("arc", [f"{data.arc_pos:.1f}", f"{data.arc_total:.0f}"])
        set_row("do", [" ".join(str((data.do_word >> i) & 1) for i in range(8))])
        set_row("tpac_do", [f"{server.signal_word:03b}  ({server.signal_word})"])
        poller = runner.poller
        cycle = f"{poller.cycle_ms:.0f} ms" if poller is not None else "-"
        set_row("robot", [ROBOT_MODE_TXT.get(data.robot_mode, str(data.robot_mode)),
                          RUNNING_TXT.get(data.running_state, str(data.running_state)), cycle])

    def set_running(on: bool):
        start_btn.configure(state="disabled" if on else "normal")
        stop_btn.configure(state="normal" if on else "disabled")
        for w in editable:
            if isinstance(w, ttk.Combobox):
                w.configure(state="disabled" if on else "readonly")
            else:
                w.configure(state="disabled" if on else "normal")

    def start():
        values_now = collect()
        if values_now is None:
            return
        save_settings(path, values_now)
        if runner.start(values_now):
            set_running(True)
            append_log(time.strftime("%H:%M:%S ") + "시작 — 로봇에 붙고 TPAC 서버를 엽니다.")

    def stop():
        runner.stop()
        set_running(False)
        robot_state.configure(text="● 로봇: 미연결", style="Bad.TLabel")

    start_btn.configure(command=start)
    stop_btn.configure(command=stop)

    def pump():
        latest = None
        try:
            while True:
                event = events.get_nowait()
                kind = event[0]
                if kind == "log":
                    append_log(event[1])
                elif kind == "robot":
                    robot_state.configure(text=f"● 로봇: {event[2]}",
                                          style="Good.TLabel" if event[1] else "Bad.TLabel")
                elif kind == "srv":
                    tpac_state.configure(text=f"● TPAC 서버: {event[2]}",
                                         style="Good.TLabel" if event[1] else "Bad.TLabel")
                elif kind == "data":
                    latest = event[1]
        except queue.Empty:
            pass
        if latest is not None:
            show_data(latest)
        req = runner.last_request
        if req and time.time() - req[0] < TPAC_LINK_TIMEOUT_S:
            tpac_link.configure(text=f"● TPAC 요청: {req[1]} · FC{req[2]} {req[3]}~{req[3] + req[4] - 1}",
                                style="Good.TLabel")
        else:
            tpac_link.configure(text="● TPAC 요청: 없음" + (" (5초 넘게)" if req else ""),
                                style="Bad.TLabel")
        root.after(100, pump)

    def on_close():
        values_now = collect()
        if values_now is not None:
            save_settings(path, values_now)
        runner.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    append_log(f"{APP_TITLE} {VERSION} — 설정 파일 {path}")
    root.after(100, pump)
    if values.get("auto_start") and not selftest:
        root.after(300, start)
    if selftest:
        # 화면을 그리고 값 표시를 한 번 돌려 본 뒤 닫는다(빌드 확인용).
        from robot_map import RobotData
        sample = RobotData()
        sample.tcp_raw = [1234, -567, 890, 1571, 0, -1571]
        sample.tcp_scan = [100, 0, 50, 0, 0, 0]
        sample.do_word = 0b101
        root.withdraw()
        root.after(200, lambda: (show_data(sample), print("selftest ok", flush=True), on_close()))
    root.mainloop()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--headless", action="store_true", help="화면 없이 설정 파일대로 시작")
    parser.add_argument("--settings", help="설정 파일 경로(기본: 실행 파일 옆)")
    parser.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    path = args.settings or os.path.join(app_dir(), SETTINGS_NAME)
    values = load_settings(path)
    if args.headless:
        return run_headless(values, path)
    return run_gui(values, path, selftest=args.selftest)


if __name__ == "__main__":
    sys.exit(main())
