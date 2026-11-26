#!/usr/bin/env python3
"""ERUT 시뮬레이터 — ERUT Robot Service(MQTT 브릿지) 대역 (if-0.13, 2026-11-24 다시 만듦).

실제 ERUT 대신 MQTT 브로커로 RCS 에 명령을 보내고 답을 본다. 화면에서 모재 · 격자 · 스캔 값을 넣고
버튼으로 보낸다.

  연결        브로커 주소 · 포트 · 계정(선택) · 장치 ID. 붙으면 erut/status(online)를 5초마다 낸다
              — RCS 는 20초 넘게 조용하면 브릿지가 없는 것으로 보고 하던 일을 멈춘다(끄고 시험할 수 있다).
  모재        원통(cylinder) · 곡판(curved_plate) — 지름 · 높이 · (곡판) 가로 → geometry
  격자        시작 x · y, 구역 가로 · 세로, 열 · 행, 겹침 → 구역마다 area{start, end} · scan.overlap
              순서는 열 우선, 아래에서 위로(A-01 → A-02 → B-01 …). 곡판은 차량이 판 가운데 QR 앞에서
              움직이지 않으므로 열 1 개 · 판 가운데 기준으로만 만든다(RCS 규칙).
  스캔        좌 · 우 끝 이동높이(pitch_left · pitch_right, if-0.12), 속도 %(speed_pct · speed_mm_s),
              홀더 채널 수(holder.channel_count, 비우면 안 실음), Job ID(구역마다 끝 숫자 + 1)

  버튼        query · calibrate · prepare · start · pause · resume(리스타트) · abort(스탑) · reset ·
              home · mark, 구역 앞 · 뒤, 자동 진행(query → calibrate → 구역마다 prepare → start)
  보임        미리보기(펼친 모재 위 구역 — 눌러서 고른다), RCS 상태(evt/status · progress · contact ·
              home), 명령별 마지막 결과, 송수신 로그

규격: 요청 doosan/robot/req/<명령> {timestamp, content}, 답 erut/<장치>/res · erut/<장치>/evt/…
(evt/status 만 칸이 맨 바깥). 결과는 맨 바깥 status_code, 까닭은 content.error{code, name, detail}.

실행:
    python3 mqtt_test/erut_sim.py                 # WSL · 리눅스 (tkinter + paho-mqtt)
    mqtt_test/win_sim.sh erut_sim                 # WSL 에서 마우스 포인터가 안 보일 때 Windows 로
브로커 계정 · 비밀번호는 화면에서만 넣는다 — 파일에 적지 않는다.
"""

from __future__ import annotations

import json
import math
import os
import queue
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

REQ_PREFIX = "doosan/robot/req/"
ERUT_STATUS = "erut/status"
HEARTBEAT_MS = 5000
DEVICE_DEFAULT = "robot1"
BROKER_DEFAULT = "127.0.0.1"
# win_sim.sh 로 Windows 에서 띄우면 1884 (Windows 쪽 1883 은 Windows mosquitto 몫).
PORT_DEFAULT = int(os.environ.get("SIM_MQTT_PORT", "1883"))
SHAPE_CYLINDER, SHAPE_PLATE = "cylinder", "curved_plate"
SHAPE_NAMES = {SHAPE_CYLINDER: "원통", SHAPE_PLATE: "곡판"}
#: 속도 100 % 의 mm/s — RCS 가 evt/info timings.scan_speed_max_mm_s 로 알려 주면 그 값으로 바꾼다.
SCAN_SPEED_MAX_DEFAULT = 100.0


def utc_ms() -> int:
    """ERUT 규격의 timestamp — UTC 밀리초 숫자."""
    return int(time.time() * 1000)


# ================================================================== 검사 계획 (화면 없이 쓰는 부분)
@dataclass
class Section:
    """구역 하나 = job 하나. 좌표는 펼친 검사면 mm(x 원주 · 곡판 가로, y 높이)."""

    index: int
    column: int
    row: int
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def label(self) -> str:
        return f"{chr(65 + self.column % 26)}-{self.row + 1:02d}"

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass
class Plan:
    """화면에 넣은 값 한 벌."""

    shape: str = SHAPE_CYLINDER
    diameter: float = 2500.0
    height: float = 6000.0
    width: float = 1500.0            # 곡판 가로(펼친 길이) — 원통은 쓰지 않는다
    start_x: float = 0.0
    start_y: float = 0.0
    cell_w: float = 600.0
    cell_h: float = 800.0
    columns: int = 2
    rows: int = 2
    overlap: int = 20
    pitch_left: float = 30.0
    pitch_right: float = 30.0
    speed_pct: float = 100.0
    channels: str = "5"              # 빈 글자면 holder 를 싣지 않는다
    surface: str = "outer"
    job_base: str = "jb00000001"

    @property
    def plate(self) -> bool:
        return self.shape == SHAPE_PLATE

    def surface_width(self) -> float:
        """펼친 검사면 가로 — 원통은 둘레 π × 지름, 곡판은 판 가로."""
        return self.width if self.plate else math.pi * self.diameter

    def geometry(self) -> dict:
        """geometry 묶음(if-0.13). 곡판만 width 를 싣는다."""
        geometry: dict[str, Any] = {"shape": self.shape, "diameter": _int_or_float(self.diameter),
                                    "height": _int_or_float(self.height)}
        if self.plate:
            geometry["width"] = _int_or_float(self.width)
        return geometry

    def sections(self) -> list[Section]:
        """열 우선, 아래에서 위로. 이웃 구역과 겹침만큼 당겨 잡는다(ERUT 화면의 타일 배치).

        곡판은 차량이 판 가운데 QR 앞에서 움직이지 않는다 — 구역 가운데가 판 가운데여야 해서 열은 1 개,
        x 는 (판 가로 − 구역 가로) / 2 부터다(시작 x · 열 수는 쓰지 않는다).
        """
        columns = 1 if self.plate else max(1, int(self.columns))
        rows = max(1, int(self.rows))
        step_x, step_y = self.cell_w - self.overlap, self.cell_h - self.overlap
        out = []
        for column in range(columns):
            for row in range(rows):
                if self.plate:
                    x0 = (self.width - self.cell_w) / 2.0
                else:
                    x0 = self.start_x + column * step_x
                y0 = self.start_y + row * step_y
                out.append(Section(len(out), column, row, x0, y0, x0 + self.cell_w, y0 + self.cell_h))
        return out

    def job_id(self, index: int) -> str:
        """구역마다 새 job_id(규격 탭3 ④) — 끝 숫자를 구역 순번만큼 올린다."""
        base = self.job_base.strip() or "jb00000001"
        match = re.search(r"(\d+)$", base)
        if not match:
            return f"{base}-{index + 1}"
        number = int(match.group(1)) + index
        return f"{base[:match.start()]}{number:0{len(match.group(1))}d}"

    def problems(self) -> list[str]:
        """RCS 가 거절할 만한 값 — 보내기 전에 화면에 알린다(보내는 것은 막지 않는다)."""
        notes = []
        if self.diameter <= 0 or self.height <= 0:
            notes.append("지름 · 높이는 0 보다 커야 합니다")
        if self.plate and not 0 < self.width < math.pi * self.diameter:
            notes.append(f"곡판 가로는 0 초과 π × 지름({math.pi * self.diameter:,.0f}) 미만이어야 합니다")
        if self.cell_w <= 0 or self.cell_h <= 0:
            notes.append("구역 가로 · 세로는 0 보다 커야 합니다")
        if self.overlap < 0:
            notes.append("겹침은 0 이상이어야 합니다")
        if self.pitch_left < 0 or self.pitch_right < 0 or self.pitch_left + self.pitch_right <= 0:
            notes.append("좌 · 우 끝 이동높이는 0 이상이고 합이 0 보다 커야 합니다")
        if not 2 <= self.speed_pct <= 100:
            notes.append("속도는 2 ~ 100 % 여야 합니다")
        channels = self.channels.strip()
        if channels and (not channels.isdigit() or int(channels) < 1):
            notes.append("홀더 채널 수는 1 이상의 정수(또는 비움)여야 합니다")
        sections = self.sections()
        if sections and max(s.y1 for s in sections) > self.height + 1e-6:
            notes.append(f"위 끝 {max(s.y1 for s in sections):,.0f} mm 가 모재 높이 {self.height:,.0f} 를 넘습니다")
        if self.plate and self.cell_w > self.width:
            notes.append("곡판 구역 가로가 판 가로보다 큽니다")
        if not self.plate and sections and max(s.x1 for s in sections) > self.surface_width() + 1e-6:
            notes.append(f"오른쪽 끝 {max(s.x1 for s in sections):,.0f} mm 가 둘레 "
                         f"{self.surface_width():,.0f} 를 넘습니다(이음매를 지나 감깁니다)")
        return notes


def _int_or_float(value: float) -> int | float:
    return int(value) if float(value).is_integer() else float(value)


#: 「현재 값 저장」 파일(2026-11-24) — 저장소 밖(브로커 계정 · 비밀번호가 들어갈 수 있다). ERUT_SIM_SETTINGS 로 바꾼다.
SETTINGS_PATH = Path(os.environ.get("ERUT_SIM_SETTINGS")
                     or Path.home() / ".config" / "smr-erut-sim" / "settings.json")
#: 저장하는 체크박스(값 칸 self.v 는 전부 저장한다).
SAVED_FLAGS = ("heartbeat_on", "auto_marker", "skip_calibrated", "zoom")


def save_settings(values: dict, path: Path = SETTINGS_PATH) -> Path:
    """값을 JSON 으로 남긴다(나만 읽게 0600)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def load_settings(path: Path = SETTINGS_PATH) -> dict:
    """저장한 값(없거나 깨졌으면 빈 dict)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class Requests:
    """요청 content 를 만든다 — req_id 는 보낼 때마다 새 번호."""

    def __init__(self) -> None:
        self.seq = 0
        self.scan_speed_max = SCAN_SPEED_MAX_DEFAULT

    def next_req_id(self) -> str:
        self.seq += 1
        return f"req-{time.strftime('%Y%m%d')}-{self.seq:06d}"

    def content(self, action: str, plan: Plan, section: Section | None) -> dict:
        c: dict[str, Any] = {"req_id": self.next_req_id()}
        job_id = plan.job_id(section.index) if section is not None else ""
        if action == "calibrate":
            c["geometry"] = plan.geometry()
        elif action in ("prepare", "start") and section is not None:
            scan: dict[str, Any] = {
                "pitch_left": _int_or_float(plan.pitch_left),
                "pitch_right": _int_or_float(plan.pitch_right),
                "overlap": int(plan.overlap),
                "speed_pct": _int_or_float(plan.speed_pct),
                "speed_mm_s": round(plan.speed_pct / 100.0 * self.scan_speed_max, 3),
            }
            c.update(action=action, job_id=job_id, surface=plan.surface,
                     area={"start": {"x": _int_or_float(round(section.x0, 3)),
                                     "y": _int_or_float(round(section.y0, 3))},
                           "end": {"x": _int_or_float(round(section.x1, 3)),
                                   "y": _int_or_float(round(section.y1, 3))}},
                     scan=scan, geometry=plan.geometry())
            if plan.channels.strip():
                c["holder"] = {"channel_count": int(plan.channels.strip())}
        elif action == "mark" and section is not None:
            # 마커는 ERUT 것이다 — 이 구역 안 두 점(가로 30 % · 70 %, 세로 가운데)을 보낸다.
            y = round(section.y0 + section.height / 2.0)
            c.update(method="paint", marker="erut", points=[
                {"id": "p1", "x": round(section.x0 + section.width * 0.3), "y": y},
                {"id": "p2", "x": round(section.x0 + section.width * 0.7), "y": y}])
        elif action in ("pause", "resume", "abort") and job_id:
            c["job_id"] = job_id
            if action == "pause":
                c["reason"] = "operator"
        return c

    @staticmethod
    def message(content: dict) -> dict:
        return {"timestamp": utc_ms(), "content": content}


# ================================================================== MQTT
class MqttLink:
    """paho 클라이언트 — 받은 것은 큐로 넘긴다(Tk 는 주 스레드에서 꺼내 그린다)."""

    def __init__(self, events: "queue.Queue[tuple[str, Any]]") -> None:
        self.events = events
        self.client: Any | None = None
        self.connected = False
        self.device = DEVICE_DEFAULT

    def connect(self, host: str, port: int, device: str, user: str = "", password: str = "") -> str:
        """연결을 시작한다. 못 하면 까닭(글), 되면 빈 글자."""
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            return "paho-mqtt 가 없습니다 — pip install paho-mqtt"
        self.device = device
        kwargs: dict[str, Any] = {"client_id": f"erut-sim-{uuid.uuid4().hex[:8]}"}
        if hasattr(mqtt, "CallbackAPIVersion"):
            kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2
        client = mqtt.Client(**kwargs)
        if user:
            client.username_pw_set(user, password or None)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        # ERUT 도 자기 생존을 알린다 — 끊기면 브로커가 offline 을 대신 낸다.
        client.will_set(ERUT_STATUS, json.dumps({"timestamp": utc_ms(), "state": "offline"}),
                        qos=1, retain=True)
        try:
            client.connect_async(host, int(port), 30)
        except Exception as exc:  # noqa: BLE001
            return f"연결 실패: {exc}"
        client.loop_start()
        self.client = client
        return ""

    def disconnect(self) -> None:
        if self.client is not None:
            if self.connected:
                self.client.publish(ERUT_STATUS, json.dumps({"timestamp": utc_ms(), "state": "offline"}),
                                    qos=1, retain=True)
            self.client.loop_stop()
            self.client.disconnect()
        self.client = None
        self.connected = False

    def publish(self, topic: str, payload: dict, retain: bool = False) -> bool:
        if self.client is None or not self.connected:
            return False
        self.client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=1, retain=retain)
        return True

    def heartbeat(self) -> bool:
        return self.publish(ERUT_STATUS, {"timestamp": utc_ms(), "state": "online"}, retain=True)

    # paho 2.x(VERSION2) · 1.x 둘 다 받게 인자를 넉넉히 둔다.
    def _on_connect(self, client, _userdata, _flags, reason_code, *_rest) -> None:  # noqa: ANN001
        code = getattr(reason_code, "value", reason_code)
        if code not in (0, None) and str(reason_code) != "Success":
            self.events.put(("log", ("error", f"연결 거부 — {reason_code}")))
            return
        self.connected = True
        client.subscribe(f"erut/{self.device}/#", qos=1)
        self.events.put(("connected", None))

    def _on_disconnect(self, _client, _userdata, *rest) -> None:  # noqa: ANN001
        self.connected = False
        self.events.put(("disconnected", rest[-2] if len(rest) >= 2 else rest[0] if rest else ""))

    def _on_message(self, _client, _userdata, msg) -> None:  # noqa: ANN001
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:  # noqa: BLE001
            payload = msg.payload.decode("utf-8", "ignore")
        self.events.put(("message", (msg.topic, payload)))


# ================================================================== 화면
def main() -> None:
    import tkinter as tk

    root = tk.Tk()
    root.configure(cursor="left_ptr")              # WSLg 에서 포인터가 사라지지 않게
    root.option_add("*Toplevel.cursor", "left_ptr")
    root.option_add("*Dialog.cursor", "left_ptr")
    ErutSimApp(root)
    root.mainloop()


class ErutSimApp:
    CANVAS_W, CANVAS_H = 600, 270

    def __init__(self, root) -> None:  # noqa: ANN001
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk = tk, ttk
        self.root = root
        root.title("ERUT 시뮬레이터 — MQTT 브릿지 대역 (if-0.13)")
        root.geometry("1260x960")
        root.minsize(1100, 860)
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.link = MqttLink(self.events)
        self.requests = Requests()
        self.section = 0
        self.done_jobs: set[str] = set()
        self._start_jobs: dict[str, str] = {}        # start req_id → job_id
        self.last_status: dict = {}
        self._wait: dict | None = None            # 자동 진행이 기다리는 답
        self._auto_steps: list[Callable[[], None]] = []
        self._redraw_pending = False
        self._log_lines: dict[str, str] = {}

        s = tk.StringVar
        self.v = {
            "host": s(value=BROKER_DEFAULT), "port": s(value=str(PORT_DEFAULT)), "user": s(value=""),
            "password": s(value=""), "device": s(value=DEVICE_DEFAULT),
            "shape": s(value=SHAPE_CYLINDER), "diameter": s(value="2500"), "height": s(value="6000"),
            "width": s(value="1500"),
            "start_x": s(value="0"), "start_y": s(value="0"), "cell_w": s(value="600"),
            "cell_h": s(value="800"), "columns": s(value="2"), "rows": s(value="2"), "overlap": s(value="20"),
            "pitch_left": s(value="30"), "pitch_right": s(value="30"), "speed_pct": s(value="100"),
            "channels": s(value="5"), "job_base": s(value="jb00000001"),
        }
        self.heartbeat_on = tk.BooleanVar(value=True)
        self.auto_marker = tk.BooleanVar(value=True)
        self.skip_calibrated = tk.BooleanVar(value=True)
        self.zoom = tk.BooleanVar(value=True)
        self.conn_text = s(value="연결 안 됨")
        self.section_text = s(value="")
        self.problem_text = s(value="")
        self.status_vars = {key: s(value="-") for key in (
            "link", "activity", "calibrated", "job", "progress", "contact", "home", "auto")}
        self.saved_text = s(value="")
        loaded = self._apply_saved(load_settings())

        self._build()
        if loaded:
            self._log("info", f"저장값을 불러왔습니다 — {SETTINGS_PATH}")
        for var in self.v.values():
            var.trace_add("write", lambda *_a: self._schedule_redraw())
        self._redraw()
        root.after(100, self._pump)
        root.protocol("WM_DELETE_WINDOW", self._close)

    # ------------------------------------------------------------------ 화면 만들기
    def _build(self) -> None:
        tk, ttk = self.tk, self.ttk
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=0)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(3, weight=1)

        conn = ttk.LabelFrame(outer, text="MQTT 브로커", padding=6)
        conn.grid(row=0, column=0, columnspan=2, sticky=tk.EW, pady=(0, 6))
        items = (("주소", "host", 14), ("포트", "port", 6), ("계정", "user", 10),
                 ("비밀번호", "password", 10), ("장치 ID", "device", 9))
        col = 0
        for label, key, width in items:
            ttk.Label(conn, text=label).grid(row=0, column=col, padx=(0, 3))
            entry = ttk.Entry(conn, textvariable=self.v[key], width=width,
                              show="*" if key == "password" else "")
            entry.grid(row=0, column=col + 1, padx=(0, 10))
            col += 2
        self.connect_btn = ttk.Button(conn, text="연결", command=self._connect)
        self.connect_btn.grid(row=0, column=col)
        self.disconnect_btn = ttk.Button(conn, text="끊기", command=self._disconnect, state=tk.DISABLED)
        self.disconnect_btn.grid(row=0, column=col + 1, padx=(4, 10))
        ttk.Checkbutton(conn, text="생존 신호(erut/status 5초)", variable=self.heartbeat_on).grid(
            row=0, column=col + 2)
        ttk.Label(conn, textvariable=self.conn_text, foreground="#a33").grid(
            row=1, column=0, columnspan=col + 3, sticky=tk.W, pady=(4, 0))

        # ---- 값
        params = ttk.Frame(outer)
        params.grid(row=1, column=0, sticky=tk.NSEW, padx=(0, 8))
        material = ttk.LabelFrame(params, text="모재 (geometry)", padding=6)
        material.pack(fill=tk.X)
        ttk.Radiobutton(material, text="원통", value=SHAPE_CYLINDER, variable=self.v["shape"]).grid(
            row=0, column=0, sticky=tk.W)
        ttk.Radiobutton(material, text="곡판", value=SHAPE_PLATE, variable=self.v["shape"]).grid(
            row=0, column=1, sticky=tk.W)
        self._fields(material, 1, (("지름 diameter", "diameter"), ("높이 height", "height"),
                                   ("곡판 가로 width", "width")))
        grid = ttk.LabelFrame(params, text="격자 (구역 = job)", padding=6)
        grid.pack(fill=tk.X, pady=(6, 0))
        self._fields(grid, 0, (("시작 x", "start_x"), ("시작 y", "start_y"), ("구역 가로", "cell_w"),
                               ("구역 세로", "cell_h"), ("열", "columns"), ("행", "rows"),
                               ("겹침 overlap", "overlap")))
        scan = ttk.LabelFrame(params, text="스캔 · 작업", padding=6)
        scan.pack(fill=tk.X, pady=(6, 0))
        self._fields(scan, 0, (("왼쪽 끝 이동높이", "pitch_left"), ("오른쪽 끝 이동높이", "pitch_right"),
                               ("속도 %", "speed_pct"), ("홀더 채널 수", "channels"),
                               ("Job ID 시작", "job_base")))
        ttk.Label(params, textvariable=self.problem_text, foreground="#b40", wraplength=330,
                  justify=tk.LEFT).pack(fill=tk.X, pady=(6, 0))
        # 현재 값 저장 · 불러오기(2026-11-24) — 브로커 · 모재 · 격자 · 스캔 · 체크박스 전부. 켤 때 저장값을 불러온다.
        keep = ttk.Frame(params)
        keep.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(keep, text="현재 값 저장", command=self._save_values).pack(side=tk.LEFT)
        ttk.Button(keep, text="저장값 불러오기", command=self._load_values).pack(side=tk.LEFT, padx=6)
        ttk.Label(params, textvariable=self.saved_text, foreground="#666", wraplength=330,
                  justify=tk.LEFT).pack(fill=tk.X, pady=(2, 0))

        # ---- 미리보기 · 상태
        right = ttk.Frame(outer)
        right.grid(row=1, column=1, sticky=tk.NSEW)
        right.columnconfigure(0, weight=1)
        preview = ttk.LabelFrame(right, text="미리보기 — 펼친 검사면 (구역을 눌러 고른다)", padding=6)
        preview.grid(row=0, column=0, sticky=tk.EW)
        self.canvas = tk.Canvas(preview, width=self.CANVAS_W, height=self.CANVAS_H, bg="#fbfbf8",
                                highlightthickness=0)
        self.canvas.pack(fill=tk.X)
        self.canvas.bind("<Button-1>", self._click_canvas)
        nav = ttk.Frame(preview)
        nav.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(nav, text="◀ 앞 구역", command=lambda: self._step_section(-1)).pack(side=tk.LEFT)
        ttk.Button(nav, text="다음 구역 ▶", command=lambda: self._step_section(1)).pack(side=tk.LEFT, padx=4)
        ttk.Checkbutton(nav, text="구역에 맞춰 확대", variable=self.zoom,
                        command=self._redraw).pack(side=tk.RIGHT)
        ttk.Label(preview, textvariable=self.section_text).pack(fill=tk.X, pady=(4, 0))

        below = ttk.Frame(right)
        below.grid(row=1, column=0, sticky=tk.NSEW, pady=(6, 0))
        below.columnconfigure(1, weight=1)
        status = ttk.LabelFrame(below, text="RCS 상태", padding=6)
        status.grid(row=0, column=0, sticky=tk.NS, padx=(0, 6))
        names = (("link", "장치"), ("activity", "활동"), ("calibrated", "원점"), ("job", "작업"),
                 ("progress", "진행"), ("contact", "접촉"), ("home", "홈"), ("auto", "자동 진행"))
        for i, (key, label) in enumerate(names):
            r, c = divmod(i, 2)
            ttk.Label(status, text=label, foreground="#555").grid(row=r, column=c * 2, sticky=tk.W)
            ttk.Label(status, textvariable=self.status_vars[key], width=15).grid(
                row=r, column=c * 2 + 1, sticky=tk.W, padx=(4, 8))
        results = ttk.LabelFrame(below, text="명령별 마지막 결과", padding=6)
        results.grid(row=0, column=1, sticky=tk.NSEW)
        self.results = ttk.Treeview(results, columns=("result", "time"), height=4)
        self.results.heading("#0", text="명령")
        self.results.heading("result", text="결과")
        self.results.heading("time", text="시각")
        self.results.column("#0", width=80)
        self.results.column("result", width=230)
        self.results.column("time", width=64)
        self.results.pack(fill=tk.BOTH, expand=True)

        # ---- 버튼
        cmd = ttk.LabelFrame(outer, text="명령", padding=6)
        cmd.grid(row=2, column=0, columnspan=2, sticky=tk.EW, pady=(6, 0))
        rows = (
            (("접속 확인 query", "query"), ("캘리브레이션 calibrate", "calibrate"),
             ("준비 prepare", "prepare"), ("시작 start", "start"), ("마킹 mark", "mark"),
             ("홈 home", "home")),
            (("일시정지 pause", "pause"), ("재개 · 리스타트 resume", "resume"),
             ("정지 · 스탑 abort", "abort"), ("리셋 reset", "reset")),
        )
        for r, row in enumerate(rows):
            for c, (label, action) in enumerate(row):
                ttk.Button(cmd, text=label, command=lambda a=action: self.send(a)).grid(
                    row=r, column=c, padx=3, pady=3, sticky=tk.EW)
        for c in range(6):
            cmd.columnconfigure(c, weight=1)
        auto = ttk.Frame(cmd)
        auto.grid(row=2, column=0, columnspan=6, sticky=tk.W, pady=(4, 0))
        ttk.Button(auto, text="▶ 자동 진행 (지금 구역부터 끝까지)", command=self._auto_start).pack(side=tk.LEFT)
        ttk.Button(auto, text="■ 자동 멈춤", command=lambda: self._auto_stop("작업자가 멈췄습니다")).pack(
            side=tk.LEFT, padx=6)
        ttk.Checkbutton(auto, text="원점이 잡혀 있으면 캘리브레이션 건너뛰기",
                        variable=self.skip_calibrated).pack(side=tk.LEFT, padx=10)
        ttk.Checkbutton(auto, text="마커 자동 발사 (mark_ready → mark_next)",
                        variable=self.auto_marker).pack(side=tk.LEFT, padx=10)

        # ---- 로그
        log = ttk.LabelFrame(outer, text="송수신 로그", padding=6)
        log.grid(row=3, column=0, columnspan=2, sticky=tk.NSEW, pady=(6, 0))
        log.columnconfigure(0, weight=1)
        log.rowconfigure(0, weight=1)
        from tkinter import scrolledtext
        self.log = scrolledtext.ScrolledText(log, height=9, state=tk.DISABLED, font=("Monospace", 9))
        self.log.grid(row=0, column=0, sticky=tk.NSEW)
        self.log.tag_configure("send", foreground="#1f5fa8")
        self.log.tag_configure("error", foreground="#c0262d")
        self.log.tag_configure("info", foreground="#666")
        ttk.Button(log, text="로그 지우기", command=self._clear_log).grid(row=1, column=0, sticky=tk.E, pady=(4, 0))

    def _fields(self, parent, first_row: int, fields) -> None:  # noqa: ANN001
        for i, (label, key) in enumerate(fields):
            r, c = divmod(i, 2)
            self.ttk.Label(parent, text=label).grid(row=first_row + r, column=c * 2, sticky=self.tk.W, pady=2)
            self.ttk.Entry(parent, textvariable=self.v[key], width=11).grid(
                row=first_row + r, column=c * 2 + 1, padx=(4, 10), pady=2)

    # ------------------------------------------------------------------ 값
    def plan(self) -> Plan:
        def num(key: str, default: float) -> float:
            try:
                return float(self.v[key].get())
            except ValueError:
                return default
        p = Plan()
        return Plan(
            shape=self.v["shape"].get(), diameter=num("diameter", p.diameter), height=num("height", p.height),
            width=num("width", p.width), start_x=num("start_x", 0), start_y=num("start_y", 0),
            cell_w=num("cell_w", p.cell_w), cell_h=num("cell_h", p.cell_h),
            columns=int(num("columns", 1)), rows=int(num("rows", 1)), overlap=int(num("overlap", 0)),
            pitch_left=num("pitch_left", p.pitch_left), pitch_right=num("pitch_right", p.pitch_right),
            speed_pct=num("speed_pct", p.speed_pct), channels=self.v["channels"].get(),
            job_base=self.v["job_base"].get())

    def current(self) -> Section | None:
        sections = self.plan().sections()
        if not sections:
            return None
        self.section = min(max(self.section, 0), len(sections) - 1)
        return sections[self.section]

    # ------------------------------------------------------------------ 미리보기
    def _schedule_redraw(self) -> None:
        if not self._redraw_pending:
            self._redraw_pending = True
            self.root.after(120, self._redraw)

    def _redraw(self) -> None:
        self._redraw_pending = False
        plan = self.plan()
        sections = plan.sections()
        current = self.current()
        c = self.canvas
        c.delete("all")
        width, height = max(plan.surface_width(), 1.0), max(plan.height, 1.0)
        if self.zoom.get() and sections:
            # 구역들만 크게 — 모재는 그 둘레만 보인다.
            vx0, vx1 = min(s.x0 for s in sections), max(s.x1 for s in sections)
            vy0, vy1 = min(s.y0 for s in sections), max(s.y1 for s in sections)
            pad = 0.08 * max(vx1 - vx0, vy1 - vy0)
            vx0, vy0, vx1, vy1 = vx0 - pad, vy0 - pad, vx1 + pad, vy1 + pad
        else:
            vx0, vy0 = 0.0, 0.0
            vx1 = max([width] + [s.x1 for s in sections])
            vy1 = max([height] + [s.y1 for s in sections])
        margin = 22
        scale = min((self.CANVAS_W - 2 * margin) / max(vx1 - vx0, 1.0),
                    (self.CANVAS_H - 2 * margin) / max(vy1 - vy0, 1.0))
        self._view = (margin, self.CANVAS_H - margin, scale, vx0, vy0)

        def xy(x: float, y: float) -> tuple[float, float]:
            return margin + (x - vx0) * scale, self.CANVAS_H - margin - (y - vy0) * scale

        x0, y0 = xy(0, 0)
        x1, y1 = xy(width, height)
        c.create_rectangle(x0, y1, x1, y0, fill="#e9edf1", outline="#7d8b99")
        name = SHAPE_NAMES.get(plan.shape, plan.shape)
        c.create_text(6, 9, anchor="w", fill="#444",
                      text=f"{name} — 펼친 가로 {width:,.0f} mm"
                           + (" (π × 지름)" if not plan.plate else "") + f" · 높이 {height:,.0f} mm"
                           + ("  (구역 확대)" if self.zoom.get() else ""))
        for s in sections:
            job = plan.job_id(s.index)
            fill = "#cfe8c8" if job in self.done_jobs else ""
            if current is not None and s.index == current.index:
                fill = "#ffe08a"
            outline = "#c0262d" if s.y1 > plan.height or (plan.plate and s.x1 > width) else "#2d6db0"
            a, b = xy(s.x0, s.y0)
            d, e = xy(s.x1, s.y1)
            c.create_rectangle(a, e, d, b, fill=fill, outline=outline, width=2 if fill else 1)
            if d - a >= 34:
                c.create_text((a + d) / 2, (b + e) / 2, text=s.label, fill="#223",
                              font=("TkDefaultFont", 9 if d - a >= 60 else 7))
        if plan.plate:
            mid, _ = xy(width / 2.0, 0)
            c.create_line(mid, y0 + 4, mid, y1 - 4, fill="#999", dash=(3, 3))
            c.create_text(mid, y0 + 12, text="판 가운데 · QR", fill="#777")
        if current is not None:
            done = "  (끝남)" if plan.job_id(current.index) in self.done_jobs else ""
            self.section_text.set(
                f"구역 {current.index + 1}/{len(sections)} · {current.label} · job {plan.job_id(current.index)} · "
                f"x {current.x0:,.0f}~{current.x1:,.0f} · y {current.y0:,.0f}~{current.y1:,.0f} mm{done}")
        problems = plan.problems()
        notes = [("곡판: 열 1 개 · 판 가운데 기준으로 만듭니다(시작 x · 열은 쓰지 않음)" if plan.plate else "")]
        self.problem_text.set("\n".join([n for n in notes if n] + [f"⚠ {p}" for p in problems]))

    def _click_canvas(self, event) -> None:  # noqa: ANN001
        margin, base, scale, vx0, vy0 = getattr(self, "_view", (24, self.CANVAS_H - 24, 1.0, 0.0, 0.0))
        x, y = (event.x - margin) / scale + vx0, (base - event.y) / scale + vy0
        for s in self.plan().sections():
            if s.x0 <= x <= s.x1 and s.y0 <= y <= s.y1:
                self.section = s.index
                self._redraw()
                return

    def _step_section(self, delta: int) -> None:
        self.section += delta
        self._redraw()

    # ------------------------------------------------------------------ 연결
    def _connect(self) -> None:
        try:
            port = int(self.v["port"].get())
        except ValueError:
            self._log("error", "포트는 숫자여야 합니다")
            return
        reason = self.link.connect(self.v["host"].get().strip(), port, self.v["device"].get().strip(),
                                   self.v["user"].get().strip(), self.v["password"].get())
        if reason:
            self._log("error", reason)
            return
        self.conn_text.set(f"연결 중 — {self.v['host'].get()}:{port}")
        self.connect_btn.configure(state=self.tk.DISABLED)
        self.disconnect_btn.configure(state=self.tk.NORMAL)

    def _disconnect(self) -> None:
        self._auto_stop("연결을 끊었습니다")
        self.link.disconnect()
        self.conn_text.set("연결 안 됨")
        self.connect_btn.configure(state=self.tk.NORMAL)
        self.disconnect_btn.configure(state=self.tk.DISABLED)

    def _heartbeat(self) -> None:
        if not self.link.connected:
            return
        if self.heartbeat_on.get():
            self.link.heartbeat()
        self.root.after(HEARTBEAT_MS, self._heartbeat)

    # ------------------------------------------------------------------ 보내기
    def send(self, action: str) -> str:
        """명령 하나를 보낸다. 돌려준 값은 req_id(못 보냈으면 빈 글자)."""
        if not self.link.connected:
            self._log("error", "브로커에 연결되어 있지 않습니다")
            return ""
        plan = self.plan()
        content = self.requests.content(action, plan, self.current())
        payload = Requests.message(content)
        topic = f"{REQ_PREFIX}{action}"
        self.link.publish(topic, payload)
        self._log("send", f"→ {topic}  {json.dumps(payload['content'], ensure_ascii=False)}")
        self._result(action, f"보냄 ({content['req_id']})")
        if action == "start" and content.get("job_id"):
            self._start_jobs[content["req_id"]] = content["job_id"]   # 완료가 job_id 없이 와도 칠한다
        return content["req_id"]

    # ------------------------------------------------------------------ 자동 진행
    def _auto_start(self) -> None:
        if not self.link.connected:
            self._log("error", "먼저 브로커에 연결하세요")
            return
        plan = self.plan()
        start = self.current()
        if start is None:
            return
        steps: list[Callable[[], None]] = [self._auto_query]
        if not (self.skip_calibrated.get() and self.last_status.get("calibrated") is True):
            steps.append(self._auto_calibrate)
        for s in plan.sections()[start.index:]:
            steps.append(lambda i=s.index: self._auto_prepare(i))
            steps.append(lambda i=s.index: self._auto_start_section(i))
        self._auto_steps = steps
        self._log("info", f"자동 진행 시작 — 구역 {start.label}부터 {len(plan.sections()) - start.index}개")
        self.status_vars["auto"].set("진행 중")
        self._auto_next()

    def _auto_next(self) -> None:
        if not self._auto_steps:
            self._wait = None
            self.status_vars["auto"].set("끝")
            self._log("info", "자동 진행 끝 — 모든 구역을 보냈습니다")
            return
        self._auto_steps.pop(0)()

    def _auto_stop(self, why: str) -> None:
        if self._auto_steps or self._wait:
            self._log("info", f"자동 진행 멈춤 — {why}")
            self.status_vars["auto"].set(f"멈춤 — {why}")
        self._auto_steps = []
        self._wait = None

    def _expect(self, tail: str, action: str, req_id: str, timeout_s: float, label: str) -> None:
        if not req_id:
            self._auto_stop("보내지 못했습니다")
            return
        self._wait = {"tail": tail, "action": action, "req_id": req_id,
                      "deadline": time.time() + timeout_s, "label": label}
        self.status_vars["auto"].set(f"{label} 기다리는 중")
        self.root.after(500, self._check_deadline)

    def _check_deadline(self) -> None:
        wait = self._wait
        if wait is None:
            return
        if time.time() > wait["deadline"]:
            self._auto_stop(f"{wait['label']} 을(를) 시간 안에 받지 못했습니다")
            return
        self.root.after(500, self._check_deadline)

    def _auto_query(self) -> None:
        self._expect("res", "query", self.send("query"), 20, "query 응답")

    def _auto_calibrate(self) -> None:
        # 차량 QR 정렬 + 아웃트리거 고정 — RCS 차량 설정 「정렬 최대 시간」보다 넉넉히.
        self._expect("evt/complete", "calibrate", self.send("calibrate"), 3600, "calibrate 완료")

    def _auto_prepare(self, index: int) -> None:
        self.section = index
        self._redraw()
        self._expect("evt/ready", "prepare", self.send("prepare"), 600, f"{self.current().label} 준비(ready)")

    def _auto_start_section(self, index: int) -> None:
        self.section = index
        self._expect("evt/complete", "start", self.send("start"), 7200, f"{self.current().label} 완료")

    # ------------------------------------------------------------------ 받기
    def _pump(self) -> None:
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "connected":
                    self.conn_text.set(f"연결됨 — {self.v['host'].get()}:{self.v['port'].get()} · "
                                       f"erut/{self.link.device}/# 구독")
                    self._log("info", "연결됨 — erut/status online 을 5초마다 냅니다")
                    self._heartbeat()
                elif kind == "disconnected":
                    self.conn_text.set(f"연결 끊김 {data}")
                    self._log("error", f"연결 끊김 {data}")
                elif kind == "log":
                    self._log(*data)
                elif kind == "message":
                    self._on_message(*data)
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

    def _on_message(self, topic: str, payload: Any) -> None:
        prefix = f"erut/{self.link.device}/"
        tail = topic[len(prefix):] if topic.startswith(prefix) else topic
        if not isinstance(payload, dict):
            self._log("info", f"← {tail}  {payload}")
            return
        content = payload.get("content") if isinstance(payload.get("content"), dict) else {}
        if tail == "evt/status":
            self.last_status = payload
            self.status_vars["link"].set(str(payload.get("state", "-")))
            self.status_vars["activity"].set(str(payload.get("activity", "-")))
            calibrated = payload.get("calibrated")
            self.status_vars["calibrated"].set("잡힘" if calibrated else "안 잡힘" if calibrated is False else "-")
            self.status_vars["job"].set(str(payload.get("job_id", "-")))
            self._log_once("status", f"← 상태 {payload.get('activity')} · 원점 {calibrated} · "
                                     f"job {payload.get('job_id', '-')}")
            return
        if tail == "evt/progress":
            loc = content.get("location") or {}
            self.status_vars["progress"].set(f"{content.get('progress', '-')} % · {content.get('job_id', '')}"
                                             f" {loc.get('cell', '')}")
            return
        if tail == "evt/contact":
            self.status_vars["contact"].set(f"{content.get('state', '-')} {content.get('job_id', '')}")
            self._log_once("contact", f"← 접촉 {content.get('state')} · job {content.get('job_id', '-')}")
            return
        if tail == "evt/home":
            self.status_vars["home"].set(str(content.get("state", "-")))
            self._log("info", f"← 이동 안전 자세 {content.get('state')}")
            return
        if tail == "evt/info":
            timings = content.get("timings") or {}
            if timings.get("scan_speed_max_mm_s"):
                self.requests.scan_speed_max = float(timings["scan_speed_max_mm_s"])
            self._log("info", f"← 자기소개 {content.get('model', '')} if-{content.get('interface_version', '')} "
                              f"shapes {content.get('shapes', '')}")
            return
        if tail == "evt/motion":
            self._log("info", f"← 이동 방향 {content.get('dir')} · seq {content.get('seq')}"
                              + (f" · 남은 {content['remain_mm']} mm" if content.get("remain_mm") is not None else ""))
            return
        action = str(content.get("action", ""))
        status = payload.get("status_code")
        error = content.get("error") or {}
        text = f"{tail} {status if status is not None else ''}"
        if error:
            text += f" {error.get('code', '')} {error.get('name', '')} — {error.get('detail', '')}"
        failed = isinstance(status, int) and status >= 300
        self._log("error" if failed or tail == "evt/error" else "",
                  f"← {tail}  {json.dumps(payload, ensure_ascii=False)}")
        if action:
            self._result(action, text.strip())
        if tail == "evt/complete" and action == "start" and not failed:
            job = content.get("job_id") or self._start_jobs.get(str(content.get("req_id", "")))
            if job:
                self.done_jobs.add(str(job))
                self._redraw()
        if tail == "evt/mark_ready" and self.auto_marker.get():
            req_id, point = content.get("req_id", ""), content.get("point_id", "")
            self._log("info", f"   마커 발사 — 점 {point} (1초 뒤 mark_next)")
            self.root.after(1000, lambda: self._fire_marker(req_id, point))
        self._match_wait(tail, action, content, failed, text)

    def _match_wait(self, tail: str, action: str, content: dict, failed: bool, text: str) -> None:
        wait = self._wait
        if wait is None or action != wait["action"]:
            return
        if content.get("req_id") and content.get("req_id") != wait["req_id"]:
            return
        if failed and tail in ("res", wait["tail"]):
            self._auto_stop(f"{action} 실패 — {text}")
            return
        if tail == wait["tail"]:
            self._wait = None
            self._auto_next()

    def _fire_marker(self, req_id: str, point_id: str) -> None:
        """ERUT 마커로 찍은 셈 치고 다음 점으로 보낸다."""
        payload = Requests.message({"req_id": req_id, "point_id": point_id, "marked": True})
        if self.link.publish(f"{REQ_PREFIX}mark_next", payload):
            self._log("send", f"→ {REQ_PREFIX}mark_next  {json.dumps(payload['content'], ensure_ascii=False)}")

    # ------------------------------------------------------------------ 기록
    def _result(self, action: str, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        if self.results.exists(action):
            self.results.item(action, values=(text, stamp))
        else:
            self.results.insert("", "end", iid=action, text=action, values=(text, stamp))

    def _log_once(self, key: str, text: str) -> None:
        if self._log_lines.get(key) != text:
            self._log_lines[key] = text
            self._log("info", text)

    def _log(self, tag: str, text: str) -> None:
        tk = self.tk
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, f"{time.strftime('%H:%M:%S')}  {text}\n", (tag,) if tag else ())
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _clear_log(self) -> None:
        self.log.configure(state=self.tk.NORMAL)
        self.log.delete("1.0", self.tk.END)
        self.log.configure(state=self.tk.DISABLED)

    def _close(self) -> None:
        self._disconnect()
        self.root.destroy()

    # ------------------------------------------------------------------ 현재 값 저장 (2026-11-24)
    def current_values(self) -> dict:
        values = {key: var.get() for key, var in self.v.items()}
        values.update({name: bool(getattr(self, name).get()) for name in SAVED_FLAGS})
        values["section"] = self.section
        values["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        return values

    def _apply_saved(self, data: dict) -> bool:
        """저장값을 칸에 넣는다(모르는 칸은 버린다). 넣었으면 True."""
        if not data:
            return False
        for key, var in self.v.items():
            if key in data and data[key] is not None:
                var.set(str(data[key]))
        for name in SAVED_FLAGS:
            if name in data:
                getattr(self, name).set(bool(data[name]))
        if isinstance(data.get("section"), int) and data["section"] >= 0:
            self.section = data["section"]
        if data.get("saved_at"):
            self.saved_text.set(f"저장값 {data['saved_at']} — {SETTINGS_PATH}")
        return True

    def _save_values(self) -> None:
        values = self.current_values()
        try:
            path = save_settings(values)
        except OSError as exc:
            self._log("error", f"현재 값을 저장하지 못했습니다 — {exc}")
            return
        self.saved_text.set(f"저장값 {values['saved_at']} — {path}")
        self._log("info", f"현재 값을 저장했습니다 — {path}")

    def _load_values(self) -> None:
        if not self._apply_saved(load_settings()):
            self._log("error", f"저장값이 없습니다 — {SETTINGS_PATH}")
            return
        self.section = min(self.section, max(len(self.plan().sections()) - 1, 0))
        self._redraw()
        self._log("info", f"저장값을 불러왔습니다 — {SETTINGS_PATH}")


if __name__ == "__main__":
    main()
