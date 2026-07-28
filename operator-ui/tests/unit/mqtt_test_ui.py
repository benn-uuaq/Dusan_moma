"""MC 역할로 MQTT Topic을 송수신하는 tkinter 테스트 프로그램.

실행:
    python operator-ui/tests/unit/mqtt_test_ui.py

필요 패키지:
    pip install paho-mqtt
"""

from __future__ import annotations

import json
import queue
import time
import uuid
from datetime import datetime
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any


BROKER_DEFAULT = "127.0.0.1"
PORT_DEFAULT = 1883
QOS_DEFAULT = 1

MC_COMMAND = "doosan/robot/req/mc_cmd"
RESET = "doosan/robot/req/reset"
EMS = "doosan/robot/req/ems"
JOB_CLEAR = "doosan/robot/req/job_clear"
JOB_COMMAND = "doosan/robot/req/job_cmd"

ROBOT_STATE = "doosan/robot/robot_state"
ROBOT_ERROR = "doosan/robot/error"
ROBOT_TCP = "doosan/robot/tcp"
JOB_STATE = "doosan/robot/job_state"

MC_COMMAND_RESPONSE = "doosan/robot/resp/mc_cmd"
RESET_RESPONSE = "doosan/robot/resp/reset"
EMS_RESPONSE = "doosan/robot/resp/ems"
JOB_CLEAR_RESPONSE = "doosan/robot/resp/job_clear"
JOB_COMMAND_RESPONSE = "doosan/robot/resp/job_cmd"

HEARTBEAT = "Heartbeat/robot"
LAST_WILL = "Dead/robot"

SUBSCRIPTIONS = (
    "doosan/robot/#",
    HEARTBEAT,
    LAST_WILL,
)

PAYLOAD_TEMPLATES: dict[str, dict[str, Any]] = {
    ROBOT_STATE: {
        "timestamp": "AUTO",
        "amr": "run",
        "cobot": "run",
    },
    ROBOT_ERROR: {
        "timestamp": "AUTO",
        "error_code": "E000",
        "error_message": "",
    },
    ROBOT_TCP: {
        "timestamp": "AUTO",
        "x": "0",
        "y": "0",
        "z": "0",
        "rx": "0",
        "ry": "0",
        "rz": "0",
    },
    JOB_STATE: {
        "timestamp": "AUTO",
        "job_id": "jb00000001",
        "state": "executing",
    },
    MC_COMMAND_RESPONSE: {
        "timestamp": "AUTO",
        "t_id": "mc_cmd",
        "t_stamp": "1784727720000",
        "result": "accepted",
    },
    RESET_RESPONSE: {
        "timestamp": "AUTO",
        "t_id": "reset",
        "t_stamp": "1784727779111",
        "result": "accepted",
    },
    EMS_RESPONSE: {
        "timestamp": "AUTO",
        "t_id": "ems",
        "t_stamp": "1784727779111",
        "result": "accepted",
    },
    JOB_CLEAR_RESPONSE: {
        "timestamp": "AUTO",
        "t_id": "job_clear",
        "t_stamp": "1784727779111",
        "result": "accepted",
    },
    JOB_COMMAND_RESPONSE: {
        "timestamp": "AUTO",
        "t_id": "job_cmd",
        "t_stamp": "1784727779111",
        "result": "accepted",
    },
    HEARTBEAT: {
        "timestamp": "AUTO",
        "status": "ONLINE",
    },
}


def utc_epoch_ms() -> str:
    """현재 UTC Unix Epoch 시각을 밀리초 문자열로 반환한다."""
    return str(int(time.time() * 1000))


def replace_auto_timestamp(value: Any) -> Any:
    """Payload 안의 AUTO 값을 현재 timestamp로 교체한다."""
    if isinstance(value, dict):
        return {
            key: utc_epoch_ms() if key == "timestamp" and item == "AUTO"
            else replace_auto_timestamp(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [replace_auto_timestamp(item) for item in value]
    return value


class McMqttTestApp:
    """MC 명령과 로봇 상태 Topic을 시험하는 tkinter 애플리케이션."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("SMR MQTT MC Test UI")
        self.root.geometry("1180x820")
        self.root.minsize(980, 700)

        self.client: Any | None = None
        self.connected = False
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()

        self.host_var = tk.StringVar(value=BROKER_DEFAULT)
        self.port_var = tk.StringVar(value=str(PORT_DEFAULT))
        self.client_id_var = tk.StringVar(
            value=f"smr-mc-test-{uuid.uuid4().hex[:8]}"
        )
        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.connection_var = tk.StringVar(value="연결 안 됨")

        self.amr_var = tk.StringVar(value="stop")
        self.cobot_var = tk.StringVar(value="stop")

        self.job_id_var = tk.StringVar(value="jb00000001")
        self.diameter_var = tk.StringVar(value="2500")
        self.height_var = tk.StringVar(value="6000")
        self.thickness_var = tk.StringVar(value="500")
        self.target_distance_var = tk.StringVar(value="8560")

        self.custom_topic_var = tk.StringVar(value=ROBOT_STATE)

        self._build_ui()
        self._load_topic_template()
        self.root.after(100, self._process_events)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _build_ui(self) -> None:
        """연결, 명령, 사용자 Payload, 로그 영역을 구성한다."""
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        connection = ttk.LabelFrame(outer, text="Broker 연결", padding=10)
        connection.pack(fill=tk.X)

        ttk.Label(connection, text="Host").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(connection, textvariable=self.host_var, width=28).grid(
            row=0, column=1, padx=(6, 14), sticky=tk.EW
        )
        ttk.Label(connection, text="Port").grid(row=0, column=2, sticky=tk.W)
        ttk.Entry(connection, textvariable=self.port_var, width=8).grid(
            row=0, column=3, padx=(6, 14)
        )
        ttk.Label(connection, text="Client ID").grid(
            row=0, column=4, sticky=tk.W
        )
        ttk.Entry(connection, textvariable=self.client_id_var, width=30).grid(
            row=0, column=5, padx=(6, 14), sticky=tk.EW
        )

        ttk.Label(connection, text="Username").grid(
            row=1, column=0, pady=(8, 0), sticky=tk.W
        )
        ttk.Entry(connection, textvariable=self.username_var, width=28).grid(
            row=1, column=1, padx=(6, 14), pady=(8, 0), sticky=tk.EW
        )
        ttk.Label(connection, text="Password").grid(
            row=1, column=2, pady=(8, 0), sticky=tk.W
        )
        ttk.Entry(
            connection,
            textvariable=self.password_var,
            width=18,
            show="*",
        ).grid(row=1, column=3, padx=(6, 14), pady=(8, 0))

        self.connect_button = ttk.Button(
            connection, text="연결", command=self._connect
        )
        self.connect_button.grid(row=1, column=4, padx=(0, 6), pady=(8, 0))
        self.disconnect_button = ttk.Button(
            connection,
            text="연결 해제",
            command=self._disconnect,
            state=tk.DISABLED,
        )
        self.disconnect_button.grid(row=1, column=5, pady=(8, 0), sticky=tk.W)

        self.connection_label = ttk.Label(
            connection,
            textvariable=self.connection_var,
            foreground="#a61b1b",
        )
        self.connection_label.grid(
            row=0, column=6, rowspan=2, padx=(12, 0), sticky=tk.W
        )
        connection.columnconfigure(1, weight=1)
        connection.columnconfigure(5, weight=1)

        content = ttk.Panedwindow(outer, orient=tk.HORIZONTAL)
        content.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        left = ttk.Frame(content)
        right = ttk.Frame(content)
        content.add(left, weight=1)
        content.add(right, weight=1)

        self._build_command_panel(left)
        self._build_custom_panel(right)
        self._build_log_panel(outer)

    def _build_command_panel(self, parent: ttk.Frame) -> None:
        command = ttk.LabelFrame(parent, text="Command 발행", padding=10)
        command.pack(fill=tk.BOTH, expand=True, padx=(0, 6))

        ttk.Label(command, text="AMR").grid(row=0, column=0, sticky=tk.W)
        ttk.Combobox(
            command,
            textvariable=self.amr_var,
            values=("유지", "run", "stop", "ems", "lift"),
            state="readonly",
            width=15,
        ).grid(row=0, column=1, padx=6, sticky=tk.EW)
        ttk.Label(command, text="Cobot").grid(row=1, column=0, sticky=tk.W)
        ttk.Combobox(
            command,
            textvariable=self.cobot_var,
            values=("유지", "run", "stop", "ems"),
            state="readonly",
            width=15,
        ).grid(row=1, column=1, padx=6, pady=(6, 0), sticky=tk.EW)
        ttk.Button(
            command,
            text="MC Command 발행",
            command=self._publish_mc_command,
        ).grid(row=0, column=2, rowspan=2, padx=(8, 0), sticky=tk.NSEW)

        separator = ttk.Separator(command)
        separator.grid(row=2, column=0, columnspan=3, pady=12, sticky=tk.EW)

        ttk.Button(
            command, text="Reset", command=lambda: self._publish_request(RESET)
        ).grid(row=3, column=0, padx=3, sticky=tk.EW)
        ttk.Button(
            command, text="EMS", command=lambda: self._publish_request(EMS)
        ).grid(row=3, column=1, padx=3, sticky=tk.EW)
        ttk.Button(
            command,
            text="Job Clear",
            command=lambda: self._publish_request(JOB_CLEAR),
        ).grid(row=3, column=2, padx=3, sticky=tk.EW)

        job = ttk.LabelFrame(command, text="새 Job", padding=8)
        job.grid(
            row=4,
            column=0,
            columnspan=3,
            pady=(14, 0),
            sticky=tk.NSEW,
        )
        fields = (
            ("Job ID", self.job_id_var),
            ("Diameter (mm)", self.diameter_var),
            ("Height (mm)", self.height_var),
            ("Thickness (mm)", self.thickness_var),
            ("Target distance (mm)", self.target_distance_var),
        )
        for row, (label, variable) in enumerate(fields):
            ttk.Label(job, text=label).grid(row=row, column=0, sticky=tk.W)
            ttk.Entry(job, textvariable=variable).grid(
                row=row,
                column=1,
                padx=(8, 0),
                pady=3,
                sticky=tk.EW,
            )
        ttk.Button(
            job,
            text="Job Command 발행",
            command=self._publish_job_command,
        ).grid(row=len(fields), column=0, columnspan=2, pady=(10, 0), sticky=tk.EW)
        job.columnconfigure(1, weight=1)

        for column in range(3):
            command.columnconfigure(column, weight=1)
        command.rowconfigure(4, weight=1)

    def _build_custom_panel(self, parent: ttk.Frame) -> None:
        custom = ttk.LabelFrame(
            parent,
            text="상태·응답·사용자 JSON 발행",
            padding=10,
        )
        custom.pack(fill=tk.BOTH, expand=True, padx=(6, 0))

        ttk.Label(custom, text="Topic").pack(anchor=tk.W)
        topic_box = ttk.Combobox(
            custom,
            textvariable=self.custom_topic_var,
            values=tuple(PAYLOAD_TEMPLATES),
        )
        topic_box.pack(fill=tk.X, pady=(4, 8))
        topic_box.bind("<<ComboboxSelected>>", self._load_topic_template)

        ttk.Label(custom, text="JSON Payload").pack(anchor=tk.W)
        self.payload_text = tk.Text(
            custom,
            height=18,
            wrap=tk.NONE,
            font=("Consolas", 10),
            undo=True,
        )
        self.payload_text.pack(fill=tk.BOTH, expand=True, pady=(4, 8))

        button_row = ttk.Frame(custom)
        button_row.pack(fill=tk.X)
        ttk.Button(
            button_row,
            text="템플릿 불러오기",
            command=self._load_topic_template,
        ).pack(side=tk.LEFT)
        ttk.Button(
            button_row,
            text="현재 Timestamp 적용",
            command=self._apply_current_timestamp,
        ).pack(side=tk.LEFT, padx=6)
        ttk.Button(
            button_row,
            text="JSON 발행",
            command=self._publish_custom,
        ).pack(side=tk.RIGHT)

    def _build_log_panel(self, parent: ttk.Frame) -> None:
        log_frame = ttk.LabelFrame(parent, text="송수신 로그", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        toolbar = ttk.Frame(log_frame)
        toolbar.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(
            toolbar,
            text="수신 구독: doosan/robot/#, Heartbeat/robot, Dead/robot",
        ).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="로그 지우기", command=self._clear_log).pack(
            side=tk.RIGHT
        )

        self.log_text = tk.Text(
            log_frame,
            height=12,
            state=tk.DISABLED,
            wrap=tk.NONE,
            font=("Consolas", 9),
            background="#101820",
            foreground="#d9e8f5",
        )
        scrollbar = ttk.Scrollbar(
            log_frame,
            orient=tk.VERTICAL,
            command=self.log_text.yview,
        )
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _connect(self) -> None:
        """입력한 Broker 정보로 비동기 MQTT 연결을 시작한다."""
        if self.client is not None:
            return
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            messagebox.showerror(
                "패키지 없음",
                "paho-mqtt가 설치되지 않았습니다.\n"
                "pip install paho-mqtt 명령으로 설치해 주세요.",
            )
            return

        try:
            port = int(self.port_var.get().strip())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror("입력 오류", "Port는 1~65535 숫자여야 합니다.")
            return

        try:
            kwargs: dict[str, Any] = {
                "client_id": self.client_id_var.get().strip(),
                "clean_session": True,
                "protocol": mqtt.MQTTv311,
            }
            if hasattr(mqtt, "CallbackAPIVersion"):
                kwargs["callback_api_version"] = (
                    mqtt.CallbackAPIVersion.VERSION2
                )
            client = mqtt.Client(**kwargs)
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message
            client.reconnect_delay_set(min_delay=1, max_delay=30)

            username = self.username_var.get().strip()
            if username:
                client.username_pw_set(username, self.password_var.get())

            self.client = client
            self.connection_var.set("연결 중...")
            self.connect_button.configure(state=tk.DISABLED)
            client.connect_async(
                self.host_var.get().strip(),
                port,
                keepalive=60,
            )
            client.loop_start()
            self._log("INFO", "-", "Broker 연결을 시작했습니다.")
        except Exception as exc:
            self.client = None
            self.connect_button.configure(state=tk.NORMAL)
            self.connection_var.set("연결 실패")
            messagebox.showerror("MQTT 연결 오류", str(exc))

    def _disconnect(self) -> None:
        """MQTT 연결과 네트워크 스레드를 정상적으로 종료한다."""
        client, self.client = self.client, None
        if client is not None:
            try:
                client.disconnect()
                client.loop_stop()
            except Exception as exc:
                self._log("ERROR", "-", f"연결 종료 실패: {exc}")
        self._set_connected(False)

    def _on_connect(
        self,
        client: Any,
        _userdata: Any,
        _flags: Any,
        reason_code: Any,
        _properties: Any = None,
    ) -> None:
        """연결 결과와 Topic 구독 결과를 tkinter 스레드로 전달한다."""
        try:
            code = int(reason_code)
        except (TypeError, ValueError):
            code = int(getattr(reason_code, "value", -1))

        if code != 0:
            self.events.put(
                ("connection_error", f"Broker 연결 거부: {reason_code}")
            )
            return

        for topic in SUBSCRIPTIONS:
            client.subscribe(topic, qos=QOS_DEFAULT)
        self.events.put(("connected", None))

    def _on_disconnect(
        self,
        _client: Any,
        _userdata: Any,
        *args: Any,
    ) -> None:
        """연결 종료 이벤트를 tkinter 스레드로 전달한다."""
        self.events.put(("disconnected", args))

    def _on_message(
        self,
        _client: Any,
        _userdata: Any,
        message: Any,
    ) -> None:
        """수신 데이터를 UTF-8 문자열로 변환해 UI 큐에 넣는다."""
        try:
            text = bytes(message.payload).decode("utf-8")
            try:
                payload = json.loads(text)
                text = json.dumps(payload, ensure_ascii=False)
            except json.JSONDecodeError:
                pass
            self.events.put(("message", (str(message.topic), text)))
        except UnicodeDecodeError as exc:
            self.events.put(
                ("error", f"{message.topic} UTF-8 변환 실패: {exc}")
            )

    def _process_events(self) -> None:
        """MQTT 스레드의 이벤트를 tkinter 메인 스레드에서 처리한다."""
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == "connected":
                    self._set_connected(True)
                    self._log("INFO", "-", "Broker 연결 및 Topic 구독 완료")
                elif event == "disconnected":
                    self._set_connected(False)
                    self._log("INFO", "-", "Broker 연결이 종료되었습니다.")
                elif event == "connection_error":
                    self._set_connected(False)
                    self._log("ERROR", "-", str(value))
                elif event == "message":
                    topic, payload = value
                    self._log("RECV", topic, payload)
                elif event == "error":
                    self._log("ERROR", "-", str(value))
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._process_events)

    def _publish_mc_command(self) -> None:
        """선택한 AMR/Cobot 명령을 mc_cmd Topic으로 발행한다."""
        payload: dict[str, Any] = {"timestamp": utc_epoch_ms()}
        if self.amr_var.get() != "유지":
            payload["amr"] = self.amr_var.get()
        if self.cobot_var.get() != "유지":
            payload["cobot"] = self.cobot_var.get()
        if len(payload) == 1:
            messagebox.showwarning(
                "명령 없음",
                "AMR 또는 Cobot 명령을 하나 이상 선택해 주세요.",
            )
            return
        self._publish(MC_COMMAND, payload)

    def _publish_request(self, topic: str) -> None:
        """Reset, EMS 또는 Job Clear 요청을 발행한다."""
        self._publish(
            topic,
            {"timestamp": utc_epoch_ms(), "request": "true"},
        )

    def _publish_job_command(self) -> None:
        """입력한 검사 대상 정보로 신규 Job 명령을 발행한다."""
        values = {
            "job_id": self.job_id_var.get().strip(),
            "diameter": self.diameter_var.get().strip(),
            "height": self.height_var.get().strip(),
            "thickness": self.thickness_var.get().strip(),
            "target_distance": self.target_distance_var.get().strip(),
        }
        if any(not value for value in values.values()):
            messagebox.showwarning(
                "입력 누락",
                "Job 정보의 모든 값을 입력해 주세요.",
            )
            return
        payload = {
            "timestamp": utc_epoch_ms(),
            "job_id": values["job_id"],
            "job_info": {
                "diameter": values["diameter"],
                "height": values["height"],
                "thickness": values["thickness"],
                "target_distance": values["target_distance"],
            },
        }
        self._publish(JOB_COMMAND, payload)

    def _load_topic_template(self, _event: Any = None) -> None:
        """선택한 Topic의 JSON 예제를 편집기에 표시한다."""
        topic = self.custom_topic_var.get().strip()
        payload = PAYLOAD_TEMPLATES.get(
            topic,
            {"timestamp": "AUTO"},
        )
        self.payload_text.delete("1.0", tk.END)
        self.payload_text.insert(
            "1.0",
            json.dumps(payload, ensure_ascii=False, indent=2),
        )

    def _apply_current_timestamp(self) -> None:
        """편집 중인 JSON의 timestamp를 현재 값으로 교체한다."""
        payload = self._read_payload_editor()
        if payload is None:
            return
        payload["timestamp"] = utc_epoch_ms()
        self.payload_text.delete("1.0", tk.END)
        self.payload_text.insert(
            "1.0",
            json.dumps(payload, ensure_ascii=False, indent=2),
        )

    def _publish_custom(self) -> None:
        """입력한 Topic과 JSON Payload를 그대로 발행한다."""
        topic = self.custom_topic_var.get().strip()
        if not topic:
            messagebox.showwarning("입력 누락", "Topic을 입력해 주세요.")
            return
        payload = self._read_payload_editor()
        if payload is None:
            return
        self._publish(topic, replace_auto_timestamp(payload))

    def _read_payload_editor(self) -> dict[str, Any] | None:
        """JSON 편집기 내용을 사전으로 변환하고 오류를 표시한다."""
        try:
            payload = json.loads(self.payload_text.get("1.0", tk.END))
        except json.JSONDecodeError as exc:
            messagebox.showerror(
                "JSON 오류",
                f"{exc.msg}\n줄 {exc.lineno}, 열 {exc.colno}",
            )
            return None
        if not isinstance(payload, dict):
            messagebox.showerror(
                "JSON 오류",
                "Payload의 최상위 값은 JSON object여야 합니다.",
            )
            return None
        return payload

    def _publish(self, topic: str, payload: dict[str, Any]) -> None:
        """Payload를 JSON으로 직렬화하고 QoS 1, Retain N으로 발행한다."""
        if self.client is None or not self.connected:
            messagebox.showwarning(
                "연결 필요",
                "먼저 MQTT Broker에 연결해 주세요.",
            )
            return
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            info = self.client.publish(
                topic,
                encoded,
                qos=QOS_DEFAULT,
                retain=False,
            )
            if int(info.rc) != 0:
                raise RuntimeError(f"Paho 오류 코드 {info.rc}")
            self._log("SEND", topic, encoded)
        except Exception as exc:
            self._log("ERROR", topic, f"발행 실패: {exc}")
            messagebox.showerror("MQTT 발행 오류", str(exc))

    def _set_connected(self, connected: bool) -> None:
        """연결 상태에 맞춰 버튼과 상태 문구를 갱신한다."""
        self.connected = connected
        if connected:
            self.connection_var.set("연결됨")
            self.connection_label.configure(foreground="#16733a")
            self.connect_button.configure(state=tk.DISABLED)
            self.disconnect_button.configure(state=tk.NORMAL)
        else:
            self.connection_var.set("연결 안 됨")
            self.connection_label.configure(foreground="#a61b1b")
            self.connect_button.configure(state=tk.NORMAL)
            self.disconnect_button.configure(state=tk.DISABLED)

    def _log(self, direction: str, topic: str, payload: str) -> None:
        """시각, 방향, Topic, Payload를 로그 창에 추가한다."""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{timestamp}] {direction:<5} {topic}\n{payload}\n\n"
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _clear_log(self) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _close(self) -> None:
        """창을 닫기 전에 MQTT 네트워크 스레드를 종료한다."""
        self._disconnect()
        self.root.destroy()


def main() -> None:
    """tkinter MC MQTT 테스트 UI를 실행한다."""
    root = tk.Tk()
    McMqttTestApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
