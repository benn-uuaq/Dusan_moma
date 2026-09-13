#!/usr/bin/env python3
"""RCS 가 장애·알람을 내보내게 하는 시험용 조작판.

실제 장애 수집(PLC·로봇 알람)이 아직 붙어 있지 않아, 버튼으로 장애 상황을
만들어 **RCS 가 규격대로 `erut/{장치ID}/evt/error` 를 발행하게** 한다.
이 도구가 직접 evt/error 를 쏘는 게 아니라 RCS 를 찔러서 RCS 가 쏘게
하는 것이라, `mqtt_job_sim.py` 에서 보이는 메시지는 실제 경로 그대로다.

    [이 도구] --3s/test/inject/error--> [RCS] --erut/robot1/evt/error--> [ERUT]

장애 코드·등급은 `ERUT-3S_MQTT_인터페이스_20260818.xlsx` 탭4·탭5를 따른다.

실행:
    python3 mqtt_test/alarm_sim.py
"""

from __future__ import annotations

import json
import queue
import time
import tkinter as tk
import uuid
from tkinter import scrolledtext, ttk
from typing import Any

BROKER_DEFAULT = "127.0.0.1"
PORT_DEFAULT = 1883
INJECT_TOPIC = "3s/test/inject/error"

# level  : warning=화면 표시만 / stop=구간 중단 / estop=전체 중단
# recovery: auto=스스로 복구 / manual=작업자 조치 / reset_required=req/reset 필요
PRESETS = (
    # (버튼 문구, code, message, level, recovery, detail)
    ("주행부 과부하", "E2001", "DRIVE_ERROR", "stop", "manual",
     "주행부 모터 과부하"),
    ("주행부 해제", "E2001-CLEAR", "DRIVE_ERROR_CLEARED", "warning", "auto", ""),
    ("비상정지", "E1002", "E_STOP", "estop", "reset_required",
     "물리 E-Stop 눌림"),
    ("비상정지 해제", "E1002-CLEAR", "E_STOP_CLEARED", "warning", "auto", ""),
    ("배터리 부족", "E3001", "LOW_BATTERY", "stop", "manual",
     "배터리 잔량 15 % 미만"),
    ("스캐너 통신 끊김", "E4001", "SCANNER_COMM_LOST", "stop", "manual",
     "UT 스캐너 응답 없음"),
    ("리프트 이상", "E2101", "LIFT_ERROR", "stop", "manual",
     "리프트 목표 높이 도달 실패"),
    ("커플런트 부족", "E5001", "COUPLANT_LOW", "warning", "auto",
     "물탱크 잔량 경고"),
)


def utc_ms() -> int:
    return int(time.time() * 1000)


class AlarmSimApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("장애·알람 시험 조작판 (RCS 를 찔러 evt/error 를 내보냄)")
        root.geometry("760x620")
        root.minsize(700, 560)

        self.client: Any | None = None
        self.connected = False
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()

        self.host_var = tk.StringVar(value=BROKER_DEFAULT)
        self.port_var = tk.StringVar(value=str(PORT_DEFAULT))
        self.conn_var = tk.StringVar(value="연결 안 됨")

        self.code_var = tk.StringVar(value="E9001")
        self.msg_var = tk.StringVar(value="CUSTOM_ERROR")
        self.level_var = tk.StringVar(value="stop")
        self.recovery_var = tk.StringVar(value="manual")
        self.detail_var = tk.StringVar(value="직접 입력한 장애")

        self._build_ui()
        root.after(100, self._pump)
        root.protocol("WM_DELETE_WINDOW", self._close)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)

        conn = ttk.LabelFrame(outer, text="MQTT Broker", padding=8)
        conn.grid(row=0, column=0, sticky=tk.EW, pady=(0, 8))
        ttk.Label(conn, text="Host").grid(row=0, column=0)
        ttk.Entry(conn, textvariable=self.host_var, width=14).grid(
            row=0, column=1, padx=(4, 10))
        ttk.Label(conn, text="Port").grid(row=0, column=2)
        ttk.Entry(conn, textvariable=self.port_var, width=6).grid(
            row=0, column=3, padx=(4, 10))
        self.connect_btn = ttk.Button(conn, text="연결", command=self._connect)
        self.connect_btn.grid(row=0, column=4, padx=(0, 4))
        self.disconnect_btn = ttk.Button(conn, text="연결 해제",
                                         command=self._disconnect, state=tk.DISABLED)
        self.disconnect_btn.grid(row=0, column=5)
        ttk.Label(conn, textvariable=self.conn_var, foreground="#a33").grid(
            row=1, column=0, columnspan=6, sticky=tk.W, pady=(6, 0))

        preset = ttk.LabelFrame(outer, text="장애 발생 / 해제", padding=8)
        preset.grid(row=1, column=0, sticky=tk.EW, pady=(0, 8))
        for col in range(2):
            preset.columnconfigure(col, weight=1)
        for i, item in enumerate(PRESETS):
            label, code, message, level, recovery, detail = item
            r, c = divmod(i, 2)
            colour = {"warning": "  ⚠", "stop": "  ■", "estop": "  ✖"}[level]
            ttk.Button(
                preset, text=f"{label}{colour}  [{code}]",
                command=lambda a=(code, message, level, recovery, detail): self._send(*a),
            ).grid(row=r, column=c, padx=3, pady=3, sticky=tk.EW)

        custom = ttk.LabelFrame(outer, text="직접 입력", padding=8)
        custom.grid(row=2, column=0, sticky=tk.EW, pady=(0, 8))
        custom.columnconfigure(1, weight=1)
        custom.columnconfigure(3, weight=1)
        fields = (
            ("code", self.code_var), ("message", self.msg_var),
            ("detail", self.detail_var),
        )
        for i, (label, var) in enumerate(fields):
            ttk.Label(custom, text=label).grid(row=i, column=0, sticky=tk.W, pady=2)
            ttk.Entry(custom, textvariable=var).grid(
                row=i, column=1, columnspan=3, sticky=tk.EW, padx=(6, 0), pady=2)
        ttk.Label(custom, text="level").grid(row=3, column=0, sticky=tk.W)
        ttk.Combobox(custom, textvariable=self.level_var, width=12,
                     values=("warning", "stop", "estop"), state="readonly").grid(
            row=3, column=1, sticky=tk.W, padx=(6, 14))
        ttk.Label(custom, text="recovery").grid(row=3, column=2, sticky=tk.W)
        ttk.Combobox(custom, textvariable=self.recovery_var, width=16,
                     values=("auto", "manual", "reset_required"),
                     state="readonly").grid(row=3, column=3, sticky=tk.W, padx=(6, 0))
        ttk.Button(custom, text="발생시키기", command=self._send_custom).grid(
            row=4, column=0, columnspan=4, pady=(10, 0), sticky=tk.EW)

        log = ttk.LabelFrame(outer, text="보낸 신호", padding=8)
        log.grid(row=3, column=0, sticky=tk.NSEW)
        log.columnconfigure(0, weight=1)
        log.rowconfigure(0, weight=1)
        self.log = scrolledtext.ScrolledText(log, height=12, state=tk.DISABLED)
        self.log.grid(row=0, column=0, sticky=tk.NSEW)
        ttk.Label(
            log, foreground="#666",
            text="이 도구는 RCS 를 찌르기만 합니다. 실제 evt/error 메시지는 "
                 "mqtt_job_sim.py 로그에서 확인하세요.",
        ).grid(row=1, column=0, sticky=tk.W, pady=(6, 0))

    # ------------------------------------------------------------ 연결
    def _connect(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            self._log("[오류] paho-mqtt 가 없습니다. pip install paho-mqtt")
            return
        kwargs: dict[str, Any] = {"client_id": f"alarm-sim-{uuid.uuid4().hex[:8]}"}
        if hasattr(mqtt, "CallbackAPIVersion"):
            kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION1
        c = mqtt.Client(**kwargs)
        c.on_connect = lambda cl, u, f, rc: self.events.put(("connected", rc))
        c.on_disconnect = lambda cl, u, rc: self.events.put(("disconnected", rc))
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
            self.client.loop_stop()
            self.client.disconnect()
            self.client = None
        self.connected = False
        self.conn_var.set("연결 안 됨")
        self.connect_btn.configure(state=tk.NORMAL)
        self.disconnect_btn.configure(state=tk.DISABLED)

    # ------------------------------------------------------------ 발행
    def _send(self, code: str, message: str, level: str,
              recovery: str, detail: str = "") -> None:
        if self.client is None or not self.connected:
            self._log("[경고] 먼저 브로커에 연결하세요.")
            return
        payload = {"timestamp": utc_ms(), "code": code, "message": message,
                   "level": level, "recovery": recovery}
        if detail:
            payload["detail"] = detail
        self.client.publish(INJECT_TOPIC,
                            json.dumps(payload, ensure_ascii=False), qos=1)
        self._log(f"[{level}] {code} {message}"
                  + (f" — {detail}" if detail else ""))

    def _send_custom(self) -> None:
        self._send(self.code_var.get().strip(), self.msg_var.get().strip(),
                   self.level_var.get(), self.recovery_var.get(),
                   self.detail_var.get().strip())

    # ------------------------------------------------------------ 이벤트
    def _pump(self) -> None:
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "connected":
                    if data == 0:
                        self.connected = True
                        self.conn_var.set(
                            f"연결됨 ({self.host_var.get()}:{self.port_var.get()})")
                        self._log("[연결됨] 이제 버튼을 누르면 RCS 가 장애를 발행합니다.")
                    else:
                        self._log(f"[오류] 연결 거부 rc={data}")
                elif kind == "disconnected":
                    self.connected = False
                    self.conn_var.set("연결 끊김")
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

    def _log(self, text: str) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, f"{time.strftime('%H:%M:%S')}  {text}\n")
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _close(self) -> None:
        self._disconnect()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    AlarmSimApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
