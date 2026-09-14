#!/usr/bin/env python3
"""ERUT(스테이션) 역할을 대신하는 시뮬레이터.

실제 ERUT 가 아직 없으므로 이 도구가 그 자리에서 요청을 보내고 응답을 받는다.
규격은 `ERUT-3S_MQTT_인터페이스_*.xlsx` 탭3(정상 시나리오)을 그대로 따른다.

    ① 접속 확인   erut/status 발행 → req/query → res 확인
    ② 캘리브레이션 req/calibrate → res 202 → evt/complete
    ③ 검사 준비   req/prepare  → res 202 → evt/ready
    ④ 구간 검사   req/start    → res 202 → evt/progress … → evt/complete
    ⑤ 마킹        req/mark     → res 202 → evt/complete
    중간 개입     req/pause · req/resume · req/abort · req/reset
    홈 이동       req/home — 동작 중이면 409 BUSY + evt/message 로 사유 (20260914 추가)

「시나리오 자동 진행」을 누르면 ①~④를 순서대로 밟으며, 각 단계의 응답을
기다렸다가 다음으로 넘어간다. 기다리는 대상이 오지 않으면 그 자리에서 멈추고
무엇을 못 받았는지 로그에 남긴다.

실행:
    python3 mqtt_test/erut_sim.py
"""

from __future__ import annotations

import json
import re
import queue
import time
import tkinter as tk
import uuid
from tkinter import messagebox, scrolledtext, ttk
from typing import Any

DEVICE_DEFAULT = "robot1"
BROKER_DEFAULT = "127.0.0.1"
PORT_DEFAULT = 1883

REQ = "doosan/robot/req/"
ERUT_STATUS = "erut/status"


def utc_ms() -> int:
    """ERUT 규격의 timestamp — UTC 밀리초 숫자."""
    return int(time.time() * 1000)


class ErutSimApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("ERUT 시뮬레이터 (스테이션 대역)")
        root.geometry("1000x760")
        root.minsize(900, 680)

        self.client: Any | None = None
        self.connected = False
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.seq = 0
        # 시나리오 자동 진행이 기다리는 것: (토픽 꼬리, action) → 채워지면 진행
        self._waiting: tuple[str, str] | None = None
        self._received: set[tuple[str, str]] = set()

        self.host_var = tk.StringVar(value=BROKER_DEFAULT)
        self.port_var = tk.StringVar(value=str(PORT_DEFAULT))
        self.device_var = tk.StringVar(value=DEVICE_DEFAULT)
        self.conn_var = tk.StringVar(value="연결 안 됨")

        self.job_id_var = tk.StringVar(value="jb00000001")
        self.diameter_var = tk.StringVar(value="2500")
        self.height_var = tk.StringVar(value="6000")
        self.surface_var = tk.StringVar(value="outer")
        self.columns_var = tk.StringVar(value="2")
        self.rows_var = tk.StringVar(value="2")
        self.cell_w_var = tk.StringVar(value="600")
        self.cell_h_var = tk.StringVar(value="800")
        self.overlap_var = tk.StringVar(value="20")
        self.speed_var = tk.StringVar(value="100")

        self._build_ui()
        root.after(100, self._pump)
        root.protocol("WM_DELETE_WINDOW", self._close)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)

        conn = ttk.LabelFrame(outer, text="MQTT Broker", padding=8)
        conn.grid(row=0, column=0, sticky=tk.EW, pady=(0, 8))
        ttk.Label(conn, text="Host").grid(row=0, column=0)
        ttk.Entry(conn, textvariable=self.host_var, width=14).grid(row=0, column=1, padx=(4, 10))
        ttk.Label(conn, text="Port").grid(row=0, column=2)
        ttk.Entry(conn, textvariable=self.port_var, width=6).grid(row=0, column=3, padx=(4, 10))
        ttk.Label(conn, text="장치ID").grid(row=0, column=4)
        ttk.Entry(conn, textvariable=self.device_var, width=10).grid(row=0, column=5, padx=(4, 10))
        self.connect_btn = ttk.Button(conn, text="연결", command=self._connect)
        self.connect_btn.grid(row=0, column=6, padx=(0, 4))
        self.disconnect_btn = ttk.Button(conn, text="연결 해제",
                                         command=self._disconnect, state=tk.DISABLED)
        self.disconnect_btn.grid(row=0, column=7)
        ttk.Label(conn, textvariable=self.conn_var, foreground="#a33").grid(
            row=1, column=0, columnspan=8, sticky=tk.W, pady=(6, 0))

        job = ttk.LabelFrame(outer, text="검사 계획", padding=8)
        job.grid(row=1, column=0, sticky=tk.EW, pady=(0, 8))
        for col in (1, 3, 5):
            job.columnconfigure(col, weight=1)
        fields = (
            ("Job ID", self.job_id_var), ("검사면 surface", self.surface_var),
            ("지름 diameter", self.diameter_var), ("높이 height", self.height_var),
            ("열 columns", self.columns_var), ("행 rows", self.rows_var),
            ("셀 가로 cell_width", self.cell_w_var), ("셀 세로 cell_height", self.cell_h_var),
            ("겹침 overlap", self.overlap_var), ("속도 speed_ratio", self.speed_var),
        )
        for i, (label, var) in enumerate(fields):
            r, c = divmod(i, 3)
            ttk.Label(job, text=label).grid(row=r, column=c * 2, sticky=tk.W, pady=2)
            ttk.Entry(job, textvariable=var, width=12).grid(
                row=r, column=c * 2 + 1, sticky=tk.EW, padx=(4, 14), pady=2)

        cmd = ttk.LabelFrame(outer, text="시퀀스", padding=8)
        cmd.grid(row=2, column=0, sticky=tk.EW, pady=(0, 8))
        for col in range(5):
            cmd.columnconfigure(col, weight=1)
        buttons = (
            ("▶ 시나리오 자동 진행", self._run_scenario),
            ("① query", lambda: self._send("query")),
            ("② calibrate", lambda: self._send("calibrate")),
            ("③ prepare", lambda: self._send("prepare")),
            ("④ start", lambda: self._send("start")),
            ("⑤ mark", lambda: self._send("mark")),
            ("⏸ pause", lambda: self._send("pause")),
            ("⏵ resume", lambda: self._send("resume")),
            ("■ abort", lambda: self._send("abort")),
            ("↺ reset", lambda: self._send("reset")),
            # 규격 20260914 추가 (탭4 E-1). 로봇이 동작 중이면
            # res 409 BUSY 뒤에 evt/message(M1001)로 사유 문장이 온다.
            ("⌂ home", lambda: self._send("home")),
        )
        for i, (label, fn) in enumerate(buttons):
            r, c = divmod(i, 5)
            ttk.Button(cmd, text=label, command=fn).grid(
                row=r, column=c, padx=3, pady=3, sticky=tk.EW)

        log = ttk.LabelFrame(outer, text="송수신 로그", padding=8)
        log.grid(row=3, column=0, sticky=tk.NSEW)
        log.columnconfigure(0, weight=1)
        log.rowconfigure(0, weight=1)
        self.log = scrolledtext.ScrolledText(log, height=20, state=tk.DISABLED)
        self.log.grid(row=0, column=0, sticky=tk.NSEW)
        ttk.Button(log, text="로그 지우기", command=self._clear).grid(
            row=1, column=0, sticky=tk.E, pady=(6, 0))

    # ------------------------------------------------------------ 연결
    def _connect(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            self._log("[오류] paho-mqtt 가 없습니다. pip install paho-mqtt")
            return
        kwargs: dict[str, Any] = {"client_id": f"erut-sim-{uuid.uuid4().hex[:8]}"}
        if hasattr(mqtt, "CallbackAPIVersion"):
            kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION1
        c = mqtt.Client(**kwargs)
        c.on_connect, c.on_disconnect, c.on_message = (
            self._on_connect, self._on_disconnect, self._on_message)
        # ERUT 도 자신의 생존을 알린다. 장치는 이걸 보고 offline 이면 멈춘다.
        c.will_set(ERUT_STATUS, json.dumps({"timestamp": utc_ms(), "state": "offline"}),
                   qos=1, retain=True)
        try:
            c.connect_async(self.host_var.get().strip(), int(self.port_var.get()), 30)
        except Exception as exc:  # noqa: BLE001
            self._log(f"[오류] 연결 실패: {exc}")
            return
        c.loop_start()
        self.client = c
        self.connect_btn.configure(state=tk.DISABLED)
        self.disconnect_btn.configure(state=tk.NORMAL)

    def _disconnect(self) -> None:
        if self.client is not None:
            self._publish(ERUT_STATUS, {"timestamp": utc_ms(), "state": "offline"},
                          retain=True)
            self.client.loop_stop()
            self.client.disconnect()
            self.client = None
        self.connected = False
        self.conn_var.set("연결 안 됨")
        self.connect_btn.configure(state=tk.NORMAL)
        self.disconnect_btn.configure(state=tk.DISABLED)

    def _on_connect(self, client, _u, _f, rc) -> None:  # noqa: ANN001
        if rc != 0:
            self.events.put(("log", f"[오류] 연결 거부 rc={rc}"))
            return
        client.subscribe(f"erut/{self.device_var.get().strip()}/#", qos=1)
        self.events.put(("connected", None))

    def _on_disconnect(self, _c, _u, rc) -> None:  # noqa: ANN001
        self.events.put(("disconnected", rc))

    def _on_message(self, _c, _u, msg) -> None:  # noqa: ANN001
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:  # noqa: BLE001
            payload = msg.payload.decode("utf-8", "ignore")
        self.events.put(("message", (msg.topic, payload)))

    # ------------------------------------------------------------ 발행
    def _publish(self, topic: str, payload: dict, retain: bool = False) -> bool:
        if self.client is None or not self.connected:
            self._log("[경고] 브로커에 연결되어 있지 않습니다.")
            return False
        self.client.publish(topic, json.dumps(payload, ensure_ascii=False),
                            qos=1, retain=retain)
        self._log(f"[발행 {topic}] {json.dumps(payload, ensure_ascii=False)}")
        return True

    def _next_req_id(self) -> str:
        self.seq += 1
        return f"req-{time.strftime('%Y%m%d')}-{self.seq:06d}"

    def _content(self, action: str) -> dict:
        """동작별 content 를 만든다. 규격의 payload 예시를 그대로 따른다."""
        c: dict[str, Any] = {"req_id": self._next_req_id()}
        if action == "calibrate":
            c.update(diameter=int(self.diameter_var.get()),
                     height=int(self.height_var.get()))
        elif action in ("prepare", "start"):
            # 규격 **20260812** 탭3 9·12번 그대로 보낸다: area + scan 만.
            # plan 블록은 0812 판에 없다. 반지름·두께·EOAT 같은 장비 값은
            # RCS 가 자기 설정으로 채운다.
            #
            # 구간 하나 = job 하나. 지금 구간의 좌표를 area 로 준다.
            # 이웃 구간과 겹치게 좌표를 당겨 잡고(ERUT 화면의 타일 배치),
            # 그 겹침 값을 scan.pitch 로 함께 보낸다 — scan.pitch 가 곧
            # 격자끼리의 오버랩이다. 순서는 열 우선, 아래에서 위로
            # (A-01 -> A-02 -> B-01 …) — ERUT 화면과 같다.
            w, h = float(self.cell_w_var.get()), float(self.cell_h_var.get())
            x0, y0 = self._cell_origin()
            c.update(
                action=action, job_id=self._cell_job_id(),
                surface=self.surface_var.get().strip(),
                area={"start": {"x": x0, "y": y0},
                      "end": {"x": x0 + w, "y": y0 + h}},
                scan={"pitch": float(self.overlap_var.get()), "speed": 40},
            )
        elif action == "mark":
            c.update(method="paint",
                     points=[{"id": "p1", "x": 350, "y": 1200},
                             {"id": "p2", "x": 780, "y": 1350}])
        elif action in ("pause", "resume", "abort"):
            c.update(job_id=self.job_id_var.get().strip())
        return c

    # ------------------------------------------------------------ 구간
    def _cell_total(self) -> int:
        return max(1, int(self.columns_var.get())) * max(1, int(self.rows_var.get()))

    def _cell_origin(self) -> tuple[float, float]:
        """지금 구간의 area.start. 겹침만큼 당겨서 이웃 구간과 겹치게 한다."""
        rows = max(1, int(self.rows_var.get()))
        col, row = divmod(self._cell, rows)
        w, h = float(self.cell_w_var.get()), float(self.cell_h_var.get())
        ov = float(self.overlap_var.get())
        return col * (w - ov), row * (h - ov)

    def _cell_job_id(self) -> str:
        """구간마다 새 job_id (규격 탭3 ④). 끝 숫자를 구간 순번만큼 올린다."""
        base = self.job_id_var.get().strip() or "jb00000001"
        m = re.search(r"(\d+)$", base)
        if not m:
            return f"{base}-{self._cell + 1}"
        num = int(m.group(1)) + self._cell
        return f"{base[:m.start()]}{num:0{len(m.group(1))}d}"

    def _send(self, action: str) -> str:
        content = self._content(action)
        self._publish(f"{REQ}{action}", {"timestamp": utc_ms(), "content": content})
        return content["req_id"]

    # ------------------------------------------------------------ 시나리오
    def _run_scenario(self) -> None:
        """①~④를 순서대로 밟는다. 각 단계의 응답을 기다렸다가 넘어간다."""
        if self.client is None or not self.connected:
            self._log("[경고] 먼저 브로커에 연결하세요.")
            return
        self._received.clear()
        self._cell = 0
        self._log("=" * 62)
        self._log(f"시나리오 시작 — 구간 {self._cell_total()}개를 차례로 검사합니다.")
        self._publish(ERUT_STATUS, {"timestamp": utc_ms(), "state": "online"},
                      retain=True)
        self._step_query()

    def _expect(self, tail: str, action: str, then, timeout_ms: int = 20000,
                label: str = "") -> None:
        """지정한 수신을 기다렸다가 `then` 을 부른다. 못 받으면 멈추고 알린다."""
        deadline = time.time() + timeout_ms / 1000
        want = (tail, action)

        def poll():
            if want in self._received:
                self._received.discard(want)
                then()
                return
            if time.time() > deadline:
                self._log(f"[중단] {label or want} 를 {timeout_ms // 1000}초 안에 받지 못했습니다.")
                return
            self.root.after(100, poll)

        self.root.after(100, poll)

    def _step_query(self) -> None:
        self._log("① 접속 확인 — query")
        self._send("query")
        self._expect("res", "query", self._step_calibrate, label="query 응답")

    def _step_calibrate(self) -> None:
        self._log("② 캘리브레이션 — calibrate")
        self._send("calibrate")
        self._expect("evt/complete", "calibrate", self._step_prepare,
                     label="calibrate 완료")

    def _step_prepare(self) -> None:
        x0, y0 = self._cell_origin()
        self._log(f"③ 검사 준비 — prepare  [{self._cell + 1}/{self._cell_total()}] "
                  f"{self._cell_job_id()}  area.start=({x0:g}, {y0:g})")
        self._send("prepare")
        # 준비는 차량 정렬 -> 리프트 정렬 -> 프로브 3점 -> 원점까지라 오래
        # 걸린다. 20초로 두면 로봇이 벽을 찾는 중에 시나리오가 끊긴다.
        self._expect("evt/ready", "prepare", self._step_start,
                     timeout_ms=600000, label="prepare 준비완료")

    def _step_start(self) -> None:
        self._log("④ 구간 검사 — start (프로브 확인 후 적심 -> ㄹ자 스캔)")
        self._send("start")
        self._expect("evt/complete", "start", self._step_next,
                     timeout_ms=600000, label="start 완료")

    def _step_next(self) -> None:
        """구간 하나가 끝났다. 남았으면 다음 구간 prepare 로 (탭3 ④ 구간 루프)."""
        self._cell += 1
        if self._cell < self._cell_total():
            self._step_prepare()
            return
        self._step_done()

    def _step_done(self) -> None:
        self._log(f"시나리오 완료 — 구간 {self._cell_total()}개 검사가 끝났습니다.")
        self._log("=" * 62)

    # ------------------------------------------------------------ 이벤트
    def _pump(self) -> None:
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "connected":
                    self.connected = True
                    self.conn_var.set(f"연결됨 ({self.host_var.get()}:{self.port_var.get()})")
                    self._log("[연결됨] erut/{장치ID}/# 구독 시작")
                    self._publish(ERUT_STATUS,
                                  {"timestamp": utc_ms(), "state": "online"}, retain=True)
                elif kind == "disconnected":
                    self.connected = False
                    self.conn_var.set("연결 끊김")
                    self._log(f"[연결 끊김] rc={data}")
                elif kind == "log":
                    self._log(str(data))
                elif kind == "message":
                    self._on_payload(*data)
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

    def _on_payload(self, topic: str, payload: Any) -> None:
        dev = self.device_var.get().strip()
        tail = topic[len(f"erut/{dev}/"):] if topic.startswith(f"erut/{dev}/") else topic
        # 상시 상태는 evt/status 다(규격 20260818 — telemetry 는 없다).
        # 30~60초 주기라 잦지 않지만 한 줄로 줄여 로그를 덮지 않게 한다.
        if tail == "evt/status":
            c = payload if isinstance(payload, dict) else {}
            self._log(f"[상태] {c.get('state')} "
                      f"battery={c.get('battery')} charging={c.get('charging')}")
            return
        self._log(f"[수신 {tail}] {json.dumps(payload, ensure_ascii=False)}")
        if isinstance(payload, dict):
            content = payload.get("content") or {}
            action = content.get("action", "")
            self._received.add((tail, action))
            # 거절 사유 문장이 오면(예: 동작 중 홈 요청) ERUT 화면처럼 띄운다.
            if content.get("detail"):
                messagebox.showwarning(f"RCS 응답 — {action}", str(content["detail"]),
                                       parent=self.root)

    def _log(self, text: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, f"{time.strftime('%H:%M:%S')}  {text}\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _clear(self) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.configure(state=tk.DISABLED)

    def _close(self) -> None:
        self._disconnect()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    ErutSimApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
