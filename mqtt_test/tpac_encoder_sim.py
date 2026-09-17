#!/usr/bin/env python3
"""TPAC 엔코더 보드 모의기 — RCS 브리지가 보내는 신호를 받아 보는 모니터.

RCS 의 TPAC 브리지(Modbus TCP 서버)에 **클라이언트로 붙어** TPAC 엔코더 보드가
읽는 것과 똑같은 자리를 읽는다. 브리지는 여러 클라이언트를 동시에 받으므로
실제 TPAC 과 이 모의기가 **함께** 붙어 있어도 된다.

  읽는 자리
    Coil 16~18 (FC1)          DO[0] 스캔 방향 / DO[1] 스캔 플래그 / DO[2] 호밍·초기화
    Register 1 bit 0~2 (FC3)  위와 같은 값 — 두 채널이 늘 같아야 한다
    Register 400~405 (FC3)    TCP X, Y, Z [0.1 mm] / Rx, Ry, Rz [mRad]

  보여 주는 것
    - DO 세 개의 현재 상태와 FC1 / FC3 일치 여부
    - 엔코더 보드처럼 센 값: 라인 수, C-scan 카운터(전진 +, 후진 -), 누적 거리
      DO[2]=1 이면 전부 0, DO[0] 0->1 이면 C-scan 카운터 0(라인 리셋),
      DO[1]=1 인 동안만 거리를 센다(0 이면 동결)
    - 라인별 기록(방향·시작·끝·길이·시간)
    - 신호가 바뀔 때마다 직전 상태를 얼마나 유지했는지 — 50 ms 미만이면 빨갛게
    - 최근 20 초 로직 분석기 그림

표준 라이브러리(tkinter, socket, threading)만 쓴다 — Windows 파이썬으로 그대로
띄울 수 있다(WSL 에서는 mqtt_test/win_sim.sh tpac_encoder_sim).

실행:
    python3 mqtt_test/tpac_encoder_sim.py --host 127.0.0.1 --port 502
"""

from __future__ import annotations

import argparse
import math
import queue
import socket
import struct
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import ttk

COIL_BASE = 16
STATUS_REGISTER = 1
POSE_REGISTER = 400
LATCH_MS = 50
DO_NAMES = ("DO[0] 스캔 방향", "DO[1] 스캔 플래그", "DO[2] 호밍/초기화")


# ============================================================================
#  Modbus TCP 클라이언트 (표준 라이브러리만)
# ============================================================================
class ModbusError(Exception):
    pass


class ModbusTcpClient:
    """FC1(코일 읽기)·FC3(홀딩 레지스터 읽기)만 하는 작은 클라이언트."""

    def __init__(self, host: str, port: int = 502, unit: int = 1, timeout: float = 1.0) -> None:
        self.host, self.port, self.unit, self.timeout = host, int(port), int(unit), float(timeout)
        self._sock: socket.socket | None = None
        self._tid = 0

    def connect(self) -> None:
        self.close()
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None

    def _recv(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ModbusError("연결이 끊겼습니다")
            buf += chunk
        return buf

    def _request(self, fc: int, addr: int, count: int) -> bytes:
        if self._sock is None:
            raise ModbusError("연결되어 있지 않습니다")
        self._tid = (self._tid + 1) & 0xFFFF
        pdu = struct.pack(">BHH", fc, addr, count)
        self._sock.sendall(struct.pack(">HHHB", self._tid, 0, len(pdu) + 1, self.unit) + pdu)
        tid, _proto, length, _unit = struct.unpack(">HHHB", self._recv(7))
        body = self._recv(length - 1)
        if tid != self._tid:
            raise ModbusError(f"응답 번호가 다릅니다({tid} != {self._tid})")
        if body[0] & 0x80:
            raise ModbusError(f"예외 응답 FC{fc} 코드 {body[1]}")
        return body[2:2 + body[1]]

    def read_coils(self, addr: int, count: int) -> list[int]:
        data = self._request(1, addr, count)
        return [(data[i // 8] >> (i % 8)) & 1 for i in range(count)]

    def read_holding(self, addr: int, count: int) -> list[int]:
        data = self._request(3, addr, count)
        return list(struct.unpack(f">{count}H", data[:2 * count]))


def signed(v: int) -> int:
    return v - 65536 if v > 32767 else v


def mm(value: float) -> str:
    """소수 한 자리 mm. 0 근처의 -0.0 은 0.0 으로 보인다."""
    return f"{0.0 if abs(value) < 0.05 else value:.1f}"


# ============================================================================
#  엔코더 보드 흉내 (화면과 분리 — 시험에서 그대로 쓴다)
# ============================================================================
@dataclass
class LineRecord:
    number: int
    direction: str          # "전진" / "후진"
    start_mm: float
    end_mm: float | None = None
    length_mm: float = 0.0
    started: float = 0.0
    duration_s: float = 0.0


@dataclass
class BoardEvent:
    t: float
    text: str
    warn: bool = False


@dataclass
class EncoderBoard:
    """TPAC 엔코더 보드가 DO 신호와 좌표로 하는 일을 흉내 낸다."""

    latch_ms: float = LATCH_MS
    poll_ms: float = 10.0
    do: list[int] | None = None
    since: float = 0.0
    lines: int = 0
    cscan_mm: float = 0.0
    odometer_mm: float = 0.0
    frozen_move_mm: float = 0.0
    line_records: list[LineRecord] = field(default_factory=list)
    history: list[tuple[float, list[int]]] = field(default_factory=list)
    violations: int = 0
    mismatch: bool = False
    _mismatch_run: int = 0
    _pose: list[float] | None = None

    @property
    def short_limit_ms(self) -> float:
        """이보다 짧으면 확실히 latch 위반이다(폴링 간격만큼 측정 오차를 뺀다)."""
        return max(0.0, self.latch_ms - self.poll_ms)

    def reset_local(self) -> None:
        self.lines = 0
        self.cscan_mm = self.odometer_mm = self.frozen_move_mm = 0.0
        self.line_records.clear()

    def update(self, t: float, coils: list[int], word: int, pose_raw: list[int]) -> list[BoardEvent]:
        events: list[BoardEvent] = []
        do = [int(b) for b in coils[:3]]
        fc3 = [word & 1, (word >> 1) & 1, (word >> 2) & 1]

        # FC1 과 FC3 는 따로 읽으므로 바뀌는 순간 한 번은 어긋날 수 있다 — 두 번 연속일 때만 본다.
        if do != fc3:
            self._mismatch_run += 1
            if self._mismatch_run == 2:
                self.mismatch = True
                events.append(BoardEvent(t, f"⚠ FC1 코일 {do} 과 FC3 레지스터 1 {fc3} 가 다릅니다", True))
        else:
            if self.mismatch:
                events.append(BoardEvent(t, "FC1 / FC3 다시 일치"))
            self._mismatch_run = 0
            self.mismatch = False

        pose_mm = [signed(v) * 0.1 for v in pose_raw[:3]]
        if self.do is None:
            self.do, self.since, self._pose = do, t, pose_mm
            self.history.append((t, list(do)))
            if do[2]:
                self.reset_local()
            events.append(BoardEvent(t, f"첫 값 {self._describe(do)}"))
            return events

        # 지난 샘플부터 지금까지 움직인 거리 — 그 동안의 신호(이전 값)로 센다.
        ds = math.dist(pose_mm, self._pose) if self._pose is not None else 0.0
        self._pose = pose_mm
        prev = self.do
        if prev[1] and not prev[2]:
            self.odometer_mm += ds
            self.cscan_mm += ds if prev[0] else -ds
            if self.line_records and self.line_records[-1].end_mm is None:
                self.line_records[-1].length_mm += ds
        else:
            self.frozen_move_mm += ds

        if do == prev:
            return events

        held = (t - self.since) * 1000.0
        changed = [i for i in range(3) if do[i] != prev[i]]
        short = held < self.short_limit_ms or len(changed) > 1
        if short:
            self.violations += 1
        names = ", ".join(f"{DO_NAMES[i]} {prev[i]}→{do[i]}" for i in changed)
        note = f"  (직전 상태 {held:.0f} ms 유지)"
        if len(changed) > 1:
            note += "  ⚠ 한 샘플에 두 비트가 같이 바뀜"
        elif held < self.short_limit_ms:
            note += f"  ⚠ {self.latch_ms:.0f} ms 미만"
        events.append(BoardEvent(t, names + note, short))

        # 보드 동작 — 리셋 -> 방향 -> 스캔 순으로 적용한다.
        if 2 in changed and do[2]:
            self.reset_local()
            events.append(BoardEvent(t, "리셋: 라인·C-scan·누적 카운터 0"))
        if 0 in changed and do[0] and not prev[0]:
            self.cscan_mm = 0.0
            events.append(BoardEvent(t, "방향 0→1: 라인 리셋(C-scan 카운터 0)"))
        if 1 in changed:
            if do[1]:
                self.lines += 1
                self.line_records.append(LineRecord(
                    self.lines, "전진" if do[0] else "후진", self.cscan_mm, started=t))
                events.append(BoardEvent(t, f"라인 {self.lines} 시작 ({'전진' if do[0] else '후진'})"))
            elif self.line_records and self.line_records[-1].end_mm is None:
                rec = self.line_records[-1]
                rec.end_mm = self.cscan_mm
                rec.duration_s = t - rec.started
                events.append(BoardEvent(
                    t, f"라인 {rec.number} 끝 — {rec.length_mm:.1f} mm, {rec.duration_s:.2f} s"))

        self.do, self.since = do, t
        self.history.append((t, list(do)))
        if len(self.history) > 5000:
            del self.history[:1000]
        return events

    @staticmethod
    def _describe(do: list[int]) -> str:
        return (f"방향 {'Forward' if do[0] else 'Backward'} · 스캔 {'유효' if do[1] else '동결'} · "
                f"리셋 {do[2]}")


# ============================================================================
#  폴링 스레드
# ============================================================================
class Poller(threading.Thread):
    def __init__(self, host, port, unit, interval_ms, out: queue.Queue) -> None:
        super().__init__(daemon=True, name="tpac-encoder-poll")
        self.client = ModbusTcpClient(host, port, unit, timeout=1.0)
        self.interval = max(5, int(interval_ms)) / 1000.0
        self.out = out
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        try:
            self.client.connect()
        except OSError as exc:
            self.out.put(("conn", False, f"연결 실패: {exc}"))
            return
        self.out.put(("conn", True, f"연결됨 {self.client.host}:{self.client.port}"))
        fails = 0
        while not self._halt.is_set():
            t0 = time.monotonic()
            try:
                coils = self.client.read_coils(COIL_BASE, 3)
                word = self.client.read_holding(STATUS_REGISTER, 1)[0]
                pose = self.client.read_holding(POSE_REGISTER, 6)
                self.out.put(("data", time.monotonic(), coils, word, pose))
                fails = 0
            except (OSError, ModbusError, struct.error) as exc:
                fails += 1
                if fails == 1:
                    self.out.put(("log", f"읽기 오류: {exc}"))
                if fails >= 3:
                    self.out.put(("conn", False, "통신 끊김 — 다시 연결합니다"))
                    time.sleep(0.5)
                    try:
                        self.client.connect()
                        self.out.put(("conn", True, "다시 연결됨"))
                        fails = 0
                    except OSError:
                        pass
            wait = self.interval - (time.monotonic() - t0)
            if wait > 0:
                self._halt.wait(wait)
        self.client.close()
        self.out.put(("conn", False, "연결 해제"))


# ============================================================================
#  화면
# ============================================================================
def use_visible_cursor(root: tk.Tk) -> None:
    """WSLg 에서 X11 커서가 숨는 문제 — 다른 시뮬레이터와 같은 처리."""
    try:
        root.configure(cursor="left_ptr")
        root.option_add("*Toplevel.cursor", "left_ptr")
    except tk.TclError:
        pass


class EncoderSimApp:
    WINDOW_S = 20.0
    ON, OFF = "#2e7d32", "#c8c8c8"

    def __init__(self, root: tk.Tk, host: str, port: int) -> None:
        self.root = root
        root.title("TPAC 엔코더 보드 모의기 — RCS 브리지 신호 모니터")
        root.geometry("1180x780")
        self.queue: queue.Queue = queue.Queue()
        self.poller: Poller | None = None
        self.board = EncoderBoard()
        self.t0 = time.monotonic()

        self.host_var = tk.StringVar(value=host)
        self.port_var = tk.StringVar(value=str(port))
        self.unit_var = tk.StringVar(value="1")
        self.poll_var = tk.StringVar(value="10")
        self.latch_var = tk.StringVar(value=str(LATCH_MS))
        self.status_var = tk.StringVar(value="연결 안 됨")
        self._build()
        self.root.after(30, self._drain)
        self.root.after(100, self._redraw_timeline)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        pad = {"padx": 6, "pady": 4}
        conn = ttk.LabelFrame(self.root, text="RCS 브리지 (Modbus TCP 서버)")
        conn.pack(fill="x", **pad)
        for col, (label, var, width) in enumerate((
                ("Host", self.host_var, 16), ("Port", self.port_var, 6), ("Unit", self.unit_var, 4),
                ("폴링 ms", self.poll_var, 5), ("Latch 기준 ms", self.latch_var, 5))):
            ttk.Label(conn, text=label).grid(row=0, column=col * 2, sticky="e", padx=(8, 2))
            ttk.Entry(conn, textvariable=var, width=width).grid(row=0, column=col * 2 + 1, sticky="w")
        self.connect_btn = ttk.Button(conn, text="연결", command=self._connect)
        self.connect_btn.grid(row=0, column=10, padx=6)
        ttk.Button(conn, text="연결 해제", command=self._disconnect).grid(row=0, column=11)
        self.status_label = ttk.Label(conn, textvariable=self.status_var, foreground="#c62828")
        self.status_label.grid(row=0, column=12, padx=10, sticky="w")

        top = ttk.Frame(self.root)
        top.pack(fill="x", **pad)

        do_frame = ttk.LabelFrame(top, text="DO 신호 (Coil 16~18 / Reg 1)")
        do_frame.pack(side="left", fill="y", padx=4)
        self.lamps = []
        self.lamp_text = []
        for i, name in enumerate(DO_NAMES):
            canvas = tk.Canvas(do_frame, width=26, height=26, highlightthickness=0)
            canvas.grid(row=i, column=0, padx=6, pady=4)
            lamp = canvas.create_oval(3, 3, 23, 23, fill=self.OFF, outline="#666")
            self.lamps.append((canvas, lamp))
            ttk.Label(do_frame, text=f"{name} (Coil {COIL_BASE + i})").grid(row=i, column=1, sticky="w")
            var = tk.StringVar(value="-")
            ttk.Label(do_frame, textvariable=var, width=18, font=("", 10, "bold")).grid(
                row=i, column=2, sticky="w", padx=6)
            self.lamp_text.append(var)
        self.fc3_var = tk.StringVar(value="Reg 1 = -")
        ttk.Label(do_frame, textvariable=self.fc3_var).grid(row=3, column=1, sticky="w")
        self.match_var = tk.StringVar(value="FC1 / FC3 -")
        self.match_label = ttk.Label(do_frame, textvariable=self.match_var)
        self.match_label.grid(row=3, column=2, sticky="w", padx=6)

        count = ttk.LabelFrame(top, text="엔코더 보드 카운터")
        count.pack(side="left", fill="y", padx=4)
        self.count_vars = {}
        for r, (key, label) in enumerate((
                ("lines", "라인 수"), ("cscan", "C-scan 카운터 [mm]"),
                ("line_len", "이번 라인 길이 [mm]"), ("odometer", "누적 거리 [mm]"),
                ("frozen", "동결 중 이동 [mm]"), ("violations", "Latch 위반"))):
            ttk.Label(count, text=label).grid(row=r, column=0, sticky="w", padx=6)
            var = tk.StringVar(value="0")
            ttk.Label(count, textvariable=var, width=12, font=("", 10, "bold")).grid(
                row=r, column=1, sticky="e", padx=6)
            self.count_vars[key] = var
        ttk.Button(count, text="카운터 0 (모의기만)", command=self._reset_local).grid(
            row=6, column=0, columnspan=2, pady=4)

        pose = ttk.LabelFrame(top, text="TCP (Reg 400~405)")
        pose.pack(side="left", fill="y", padx=4)
        self.pose_vars = []
        for i, (name, unit) in enumerate((("X", "mm"), ("Y", "mm"), ("Z", "mm"),
                                          ("Rx", "mRad"), ("Ry", "mRad"), ("Rz", "mRad"))):
            ttk.Label(pose, text=f"{name} [{unit}]").grid(row=i, column=0, sticky="w", padx=6)
            var = tk.StringVar(value="-")
            ttk.Label(pose, textvariable=var, width=10).grid(row=i, column=1, sticky="e", padx=6)
            self.pose_vars.append(var)

        timeline = ttk.LabelFrame(self.root, text=f"최근 {self.WINDOW_S:.0f} 초 (로직 분석기)")
        timeline.pack(fill="x", **pad)
        self.timeline = tk.Canvas(timeline, height=150, background="white")
        self.timeline.pack(fill="x", padx=4, pady=4)

        bottom = ttk.Frame(self.root)
        bottom.pack(fill="both", expand=True, **pad)
        lines_frame = ttk.LabelFrame(bottom, text="라인 기록")
        lines_frame.pack(side="left", fill="both", expand=False, padx=4)
        columns = ("no", "dir", "start", "end", "len", "time")
        self.line_tree = ttk.Treeview(lines_frame, columns=columns, show="headings", height=12)
        for col, (text, width) in zip(columns, (("#", 40), ("방향", 60), ("시작 mm", 80),
                                                 ("끝 mm", 80), ("길이 mm", 80), ("시간 s", 70))):
            self.line_tree.heading(col, text=text)
            self.line_tree.column(col, width=width, anchor="e")
        self.line_tree.pack(fill="both", expand=True)

        log_frame = ttk.LabelFrame(bottom, text="신호 변화 로그")
        log_frame.pack(side="left", fill="both", expand=True, padx=4)
        self.log = tk.Text(log_frame, height=12, wrap="none", font=("Consolas", 9))
        self.log.tag_configure("warn", foreground="#c62828")
        self.log.pack(fill="both", expand=True)
        ttk.Button(log_frame, text="로그 지우기", command=lambda: self.log.delete("1.0", "end")).pack(
            anchor="e", pady=2)

    # ------------------------------------------------------------------
    def _connect(self) -> None:
        self._disconnect()
        try:
            port, unit, poll = int(self.port_var.get()), int(self.unit_var.get()), int(self.poll_var.get())
            latch = float(self.latch_var.get())
        except ValueError:
            self.status_var.set("숫자를 확인하세요")
            return
        self.board = EncoderBoard(latch_ms=latch, poll_ms=poll)
        self.t0 = time.monotonic()
        self.poller = Poller(self.host_var.get().strip(), port, unit, poll, self.queue)
        self.poller.start()
        self.status_var.set("연결 중…")

    def _disconnect(self) -> None:
        if self.poller is not None:
            self.poller.stop()
            self.poller = None

    def _reset_local(self) -> None:
        self.board.reset_local()
        self.line_tree.delete(*self.line_tree.get_children())
        self._render_counters()

    def _drain(self) -> None:
        latest = None
        try:
            while True:
                item = self.queue.get_nowait()
                kind = item[0]
                if kind == "data":
                    _, t, coils, word, pose = item
                    for ev in self.board.update(t, coils, word, pose):
                        self._log(ev)
                    latest = (coils, word, pose)
                elif kind == "conn":
                    self.status_var.set(item[2])
                    self.status_label.configure(foreground="#2e7d32" if item[1] else "#c62828")
                elif kind == "log":
                    self._log(BoardEvent(time.monotonic(), item[1], True))
        except queue.Empty:
            pass
        if latest is not None:
            self._render(*latest)
        self.root.after(30, self._drain)

    def _log(self, ev: BoardEvent) -> None:
        # ev.t 는 monotonic 이다 — 벽시계로 바꿔 찍어야 순서가 뒤집히지 않는다.
        wall = time.time() - (time.monotonic() - ev.t)
        stamp = time.strftime("%H:%M:%S", time.localtime(wall)) + f".{int((wall % 1) * 1000):03d}"
        self.log.insert("end", f"{stamp}  {ev.text}\n", ("warn",) if ev.warn else ())
        self.log.see("end")

    def _render(self, coils, word, pose) -> None:
        texts = (("Forward", "Backward"), ("스캔 유효", "동결"), ("리셋", "준비 완료"))
        for i, (canvas, lamp) in enumerate(self.lamps):
            on = bool(coils[i])
            canvas.itemconfigure(lamp, fill=self.ON if on else self.OFF)
            self.lamp_text[i].set(f"{int(on)}  {texts[i][0] if on else texts[i][1]}")
        self.fc3_var.set(f"Reg 1 = {word} ({word & 7:03b})")
        if self.board.mismatch:
            self.match_var.set("FC1 / FC3 불일치")
            self.match_label.configure(foreground="#c62828")
        else:
            self.match_var.set("FC1 / FC3 일치")
            self.match_label.configure(foreground="#2e7d32")
        for i, var in enumerate(self.pose_vars):
            v = signed(pose[i])
            var.set(f"{v * 0.1:.1f}" if i < 3 else str(v))
        self._render_counters()

    def _render_counters(self) -> None:
        b = self.board
        current = b.line_records[-1].length_mm if b.line_records and b.line_records[-1].end_mm is None else 0.0
        self.count_vars["lines"].set(str(b.lines))
        self.count_vars["cscan"].set(mm(b.cscan_mm))
        self.count_vars["line_len"].set(f"{current:.1f}")
        self.count_vars["odometer"].set(f"{b.odometer_mm:.1f}")
        self.count_vars["frozen"].set(f"{b.frozen_move_mm:.1f}")
        self.count_vars["violations"].set(str(b.violations))
        rows = self.line_tree.get_children()
        if len(rows) > len(b.line_records):
            self.line_tree.delete(*rows)
            rows = ()
        for i, rec in enumerate(b.line_records):
            values = (rec.number, rec.direction, mm(rec.start_mm),
                      "-" if rec.end_mm is None else mm(rec.end_mm),
                      mm(rec.length_mm), f"{rec.duration_s:.2f}" if rec.end_mm is not None else "…")
            if i < len(rows):
                self.line_tree.item(rows[i], values=values)
            else:
                self.line_tree.insert("", "end", values=values)

    def _redraw_timeline(self) -> None:
        c = self.timeline
        c.delete("all")
        w = max(c.winfo_width(), 400)
        left, row_h = 150, 42
        now = time.monotonic()
        start = now - self.WINDOW_S
        for i, name in enumerate(DO_NAMES):
            y_hi, y_lo = 12 + i * row_h, 12 + i * row_h + 22
            c.create_text(8, (y_hi + y_lo) / 2, text=name, anchor="w", font=("", 9))
            c.create_line(left, y_lo + 6, w - 8, y_lo + 6, fill="#eeeeee")
            points = []
            state = None
            for t, bits in self.board.history:
                if t < start:
                    state = bits[i]
                    continue
                x = left + (t - start) / self.WINDOW_S * (w - left - 8)
                if state is not None:
                    points += [x, y_hi if state else y_lo]
                state = bits[i]
                points += [x, y_hi if state else y_lo]
            if state is not None:
                if not points:
                    points = [left, y_hi if state else y_lo]
                points += [w - 8, y_hi if state else y_lo]
                if len(points) >= 4:
                    c.create_line(*points, fill="#1565c0", width=2)
        for s in range(0, int(self.WINDOW_S) + 1, 5):
            x = left + s / self.WINDOW_S * (w - left - 8)
            c.create_text(x, 140, text=f"-{int(self.WINDOW_S) - s}s", font=("", 8), fill="#888")
        self.root.after(100, self._redraw_timeline)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--connect", action="store_true", help="켜자마자 연결")
    args = parser.parse_args()
    root = tk.Tk()
    use_visible_cursor(root)
    app = EncoderSimApp(root, args.host, args.port)
    if args.connect:
        root.after(200, app._connect)
    root.protocol("WM_DELETE_WINDOW", lambda: (app._disconnect(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
