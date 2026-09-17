"""MC(관제) 쪽을 흉내 내는 시뮬레이션용 MQTT 도구.

Operator UI와 별개로 독립 실행되는 도구다. MC가 실제로 보내는 것은
① 전체 작업 시작/일시정지/정지, ② 로봇이 ㄹ자를 그리기 위한 값
(job_info + scan) 뿐이므로, 이 도구도 그 범위만 다룬다. 구역/격자를
하나씩 넘어가는 자동 진행은 Operator UI/로봇 쪽 책임이라 여기서는
다루지 않는다 (`docs/mqtt_topic_form.md` T-005 참고).

실행:
    python3 mqtt_test/mqtt_job_sim.py

paho-mqtt와 tkinter만 있으면 되고, Operator UI 코드(smr_operator_ui)에는
의존하지 않는다 — Windows 등 ROS/Qt가 없는 환경에서도 그대로 쓸 수 있다.
"""

from __future__ import annotations

import json
import os
import queue
import time
import tkinter as tk
import uuid
from tkinter import messagebox, scrolledtext, ttk
from typing import Any

# docs/mqtt_topic_form.md 와 동일한 Topic 문자열이다. 두 곳이 갈라지지
# 않도록 이 파일을 고칠 때는 문서도 함께 확인한다.
MC_COMMAND = "doosan/robot/req/mc_cmd"
RESET = "doosan/robot/req/reset"
EMS = "doosan/robot/req/ems"
JOB_CLEAR = "doosan/robot/req/job_clear"
JOB_COMMAND = "doosan/robot/req/job_cmd"
SPEED = "doosan/robot/req/speed"

ROBOT_STATE = "doosan/robot/robot_state"
ROBOT_ERROR = "doosan/robot/error"
ROBOT_TCP = "doosan/robot/tcp"
JOB_STATE = "doosan/robot/job_state"

MC_COMMAND_RESPONSE = "doosan/robot/resp/mc_cmd"
RESET_RESPONSE = "doosan/robot/resp/reset"
EMS_RESPONSE = "doosan/robot/resp/ems"
JOB_CLEAR_RESPONSE = "doosan/robot/resp/job_clear"
JOB_COMMAND_RESPONSE = "doosan/robot/resp/job_cmd"

# 원점 프로브 확인 손짓.
#   RCS -> 여기 : 로봇이 원점에 서서 확인을 기다린다 / 풀렸다
#   여기 -> RCS : 프로브 눌림을 확인했다(또는 못 했다)
# ERUT 규격에서는 evt/ready(stage=at_origin) -> req/start 자리다.
PROBE_GATE = "doosan/robot/probe_gate"
PROBE_ACK = "doosan/robot/req/probe_ack"

# 오가는 것을 전부 보기 위해 두 규격을 통째로 구독한다.
#   doosan/#  사내 MC 규격 (job_cmd, tcp, job_state …)
#   erut/#    협력사 ERUT 규격 (res, evt/*)
#   3s/test/# 시험용 주입 신호 (alarm_sim.py)
SUBSCRIBE_TOPICS = ("doosan/#", "erut/#", "3s/test/#")

# 초당 여러 번 오는 토픽. 그대로 찍으면 로그가 이것만으로 가득 찬다.
# 마지막 값 한 줄로 접어서 보여준다.
# (ERUT 규격 20260818 에는 telemetry 가 없다. 상시 상태는 evt/status 가
#  30~60초마다 나가는 것이라 접을 만큼 잦지 않다.)
HIGH_RATE = ("doosan/robot/tcp",)

BROKER_DEFAULT = "127.0.0.1"
# win_sim.sh 로 Windows 에서 띄우면 1884 (Windows 쪽 1883 은 Windows mosquitto 몫).
PORT_DEFAULT = int(os.environ.get("SIM_MQTT_PORT", "1883"))


def utc_epoch_ms() -> str:
    return str(int(time.time() * 1000))


class MqttJobSimApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("MQTT 작업 시뮬레이터 (MC 대역)")
        self.root.geometry("880x680")
        self.root.minsize(820, 620)

        self.client: Any | None = None
        self.connected = False
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()

        self.host_var = tk.StringVar(value=BROKER_DEFAULT)
        self.port_var = tk.StringVar(value=str(PORT_DEFAULT))
        self.client_id_var = tk.StringVar(value=f"mc-sim-{uuid.uuid4().hex[:8]}")
        self.connection_var = tk.StringVar(value="연결 안 됨")

        # 시험용 외벽 한 장 기준 (도면: R834.6 / 현 1110 / 높이 500 / 두께 10).
        # 지름 = R x 2 = 1669.2 -> 정수 mm 로 1669.
        self.job_id_var = tk.StringVar(value="jb00000001")
        self.diameter_var = tk.StringVar(value="1669")
        # 원통 **전체** 높이다(셀 하나의 높이가 아니다). 셀 세로 500 x 4 행
        # 이라 2000. 500 이면 행이 하나뿐이라 리프트가 움직이지 않아,
        # 차량·리프트가 실제로 도는지 볼 수 없다.
        self.height_var = tk.StringVar(value="2000")
        # 차량이 검사 대상까지 가는 거리. RCS 가 0 을 거절하므로(있어야 하는
        # 값이다) 시험용 배치의 실제 거리를 기본값으로 둔다 — 설정 파일의
        # inspection_target.target_distance_m 8.56 m 와 같은 값이다.
        self.target_distance_var = tk.StringVar(value="8560")

        # 격자 분할 계획. 원통을 편 직사각형을 열(AMR 정차 구역) × 행(리프트
        # 높이)으로 나눈다. Cobot은 셀 하나(cell_width × cell_height)만
        # ㄹ자로 스캔한다 — 원통 전체 높이를 한 번에 훑는 게 아니다.
        # 지금 몇 번째 셀인지는 RCS가 알아서 순회하므로 보내지 않는다.
        # 스캐너 유효높이(scan_h)는 장비 고유값이라 RCS 로컬 설정에 있다.
        # 3 열 x 4 행 = 12 격자. 열이 바뀌면 차량이 돌고(반시계), 행이
        # 바뀌면 리프트가 오른다 — 둘 다 움직여야 순회를 눈으로 확인할 수
        # 있다. 격자 하나만 보고 싶으면 둘 다 1 로 두면 된다.
        self.column_count_var = tk.StringVar(value="3")
        self.row_count_var = tk.StringVar(value="4")
        # 셀 가로는 **호 길이**다(현이 아니다). 로봇은 movec 로 호를 따라가므로
        # 실제 이동 거리도 호 길이 쪽이다. 예전 샘플은 1110(현 1031.8)이었는데,
        # 양 끝점이 너무 벌어져 충돌 위험이 있어 줄였다 — 좌우 끝도 가운데
        # 프로브 중심 기준이라 TCP 가 이 호를 그대로 오가므로, 좌우 끝점
        # 사이 현이 700mm 를 안 넘는 최대 정수값(721, 현 699.31)으로 잡았다.
        # 로봇 쪽 app_arc 샘플과 같은 값이다.
        self.cell_width_var = tk.StringVar(value="721")
        self.cell_height_var = tk.StringVar(value="500")
        # 격자**끼리**의 겹침이다. 한 격자 안 ㄹ자 줄 겹침이 아니다 —
        # 그건 로봇이 프로브 커버로 스스로 정한다. 이 값만큼 차량과
        # 리프트가 덜 이동해서 옆·위 격자와 겹치고, 로봇은 그 겹친
        # 자리에서 다시 영점을 잡는다. ERUT 화면의 "오버랩 간격"과 같다.
        self.overlap_var = tk.StringVar(value="20")
        # 도면 반지름(안쪽 면)과 제품 두께. 로봇이 두께를 더해 바깥(볼록)
        # 면의 반지름 844.6 을 만들어 호를 계산한다.
        # 반지름 0 이면 평면으로 보고 직선으로 훑는다.
        self.radius_var = tk.StringVar(value="834.6")
        self.thickness_var = tk.StringVar(value="10")
        # 검사장비(EOAT) 종류 — 프로브 축 수로 고른다.
        # RCS 가 표에서 **유효 스캔 커버**(가로합, 세로합)를 찾아 로봇에
        # 보낸다. 세로값이 곧 ㄹ자 up 동작의 최대 상승량이다.
        #   5 = 십자형   30 x 30     (가운데 프로브 센서 지름)
        #   8 = 직사각형 75 x 167.5  (좌우합 / 상하합)
        self.eoat_var = tk.StringVar(value="5")

        # 로봇 전체 동작 속도 비율[%]. 2~100, 작업 중에도 바로 먹는다.
        self.speed_var = tk.IntVar(value=100)

        # tcp 처럼 초당 여러 번 오는 것을 로그에 넣을지.
        self.show_stream_var = tk.BooleanVar(value=True)
        # 자동 반복: 체크한 채로 "전체 작업 시작"을 누르면 켜진다.
        #   원점 프로브 확인이 뜨면 스스로 '눌림 확인'을 보내고, 모든 격자가
        #   completed 로 오면 잠깐 기다렸다가(로봇 홈 복귀) 다시 시작한다.
        self.auto_repeat_var = tk.BooleanVar(value=False)
        self.auto_delay_var = tk.StringVar(value="10")
        self.auto_status_var = tk.StringVar(value="꺼짐")
        self._auto_armed = False
        self._auto_total = 0
        self._auto_done: set[str] = set()
        self._auto_rounds = 0
        self._auto_restart_after: str | None = None
        self._auto_probe_after: str | None = None
        self._last_stream_topic: str | None = None

        self._build_ui()
        self.root.after(100, self._process_events)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(5, weight=1)

        self._build_connection_row(outer)
        self._build_job_frame(outer)
        self._build_probe_frame(outer)
        self._build_motion_frame(outer)
        self._build_speed_frame(outer)
        self._build_log_frame(outer)

    def _build_connection_row(self, parent: ttk.Frame) -> None:
        conn = ttk.LabelFrame(parent, text="MQTT Broker", padding=8)
        conn.grid(row=0, column=0, sticky=tk.EW, pady=(0, 8))
        for col in range(8):
            conn.columnconfigure(col, weight=0)

        ttk.Label(conn, text="Host").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(conn, textvariable=self.host_var, width=16).grid(
            row=0, column=1, padx=(4, 12)
        )
        ttk.Label(conn, text="Port").grid(row=0, column=2, sticky=tk.W)
        ttk.Entry(conn, textvariable=self.port_var, width=6).grid(
            row=0, column=3, padx=(4, 12)
        )
        ttk.Label(conn, text="Client ID").grid(row=0, column=4, sticky=tk.W)
        ttk.Entry(conn, textvariable=self.client_id_var, width=20).grid(
            row=0, column=5, padx=(4, 12)
        )
        self.connect_button = ttk.Button(conn, text="연결", command=self._connect)
        self.connect_button.grid(row=0, column=6, padx=(0, 4))
        self.disconnect_button = ttk.Button(
            conn, text="연결 해제", command=self._disconnect, state=tk.DISABLED
        )
        self.disconnect_button.grid(row=0, column=7)

        self.connection_label = ttk.Label(
            conn, textvariable=self.connection_var, foreground="#a33"
        )
        self.connection_label.grid(
            row=1, column=0, columnspan=8, sticky=tk.W, pady=(6, 0)
        )

    def _build_job_frame(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="전체 작업 시작 값", padding=8)
        frame.grid(row=1, column=0, sticky=tk.EW, pady=(0, 8))
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)

        left_fields = (
            ("Job ID", self.job_id_var),
            ("지름 diameter (mm)", self.diameter_var),
            ("높이 height (mm)", self.height_var),
            ("이동거리 target_distance (mm)", self.target_distance_var),
        )
        right_fields = (
            ("열 수 column_count (1~12)", self.column_count_var),
            ("행 수 row_count (A~F)", self.row_count_var),
            ("셀 가로 = 호 길이 (mm)", self.cell_width_var),
            ("셀 세로 cell_height (mm)", self.cell_height_var),
            ("겹침 overlap (mm) — 격자끼리", self.overlap_var),
            ("반지름 radius (mm)", self.radius_var),
            ("두께 thickness (mm)", self.thickness_var),
            ("검사장비 eoat (5=십자형 / 8=직사각)", self.eoat_var),
        )
        for row, (label, var) in enumerate(left_fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky=tk.W, pady=2)
            ttk.Entry(frame, textvariable=var).grid(
                row=row, column=1, sticky=tk.EW, padx=(6, 20), pady=2
            )
        for row, (label, var) in enumerate(right_fields):
            ttk.Label(frame, text=label).grid(row=row, column=2, sticky=tk.W, pady=2)
            ttk.Entry(frame, textvariable=var).grid(
                row=row, column=3, sticky=tk.EW, padx=(6, 0), pady=2
            )

        # 버튼은 **긴 쪽 열** 아래에 둔다. len(left_fields) 로 잡으면 오른쪽
        # 열의 5번째 이후(겹침·반지름)를 버튼 프레임이 덮어 입력칸이 통째로
        # 사라진다 — 실제로 겹침을 넣을 수 없었다.
        buttons = ttk.Frame(frame)
        buttons.grid(row=max(len(left_fields), len(right_fields)),
                     column=0, columnspan=4, pady=(12, 0), sticky=tk.EW)
        for col in range(4):
            buttons.columnconfigure(col, weight=1)

        ttk.Button(
            buttons, text="▶ 전체 작업 시작", command=self._publish_job_command
        ).grid(row=0, column=0, padx=3, sticky=tk.EW)
        ttk.Button(
            buttons, text="⏸ 일시정지", command=self._publish_pause
        ).grid(row=0, column=1, padx=3, sticky=tk.EW)
        ttk.Button(
            buttons, text="⏵ 재개", command=self._publish_resume
        ).grid(row=0, column=2, padx=3, sticky=tk.EW)
        ttk.Button(
            buttons, text="■ 정지", command=self._publish_stop
        ).grid(row=0, column=3, padx=3, sticky=tk.EW)

        extra = ttk.Frame(frame)
        extra.grid(row=max(len(left_fields), len(right_fields)) + 1,
                   column=0, columnspan=4, pady=(6, 0), sticky=tk.EW)
        # 로봇 홈: mc_cmd 의 cobot="home". 로봇이 동작 중이면 RCS 가 거절하고
        # 로그에만 남긴다 — 작업 중이면 먼저 '■ 정지' 후 누른다.
        ttk.Button(extra, text="⌂ 로봇 홈", command=self._publish_home).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(extra, text="Reset", command=lambda: self._publish_request(RESET)).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(extra, text="EMS(비상정지)", command=lambda: self._publish_request(EMS)).pack(
            side=tk.LEFT
        )
        # 자동 반복 — 오른쪽 끝에 둔다.
        auto = ttk.Frame(extra)
        auto.pack(side=tk.RIGHT)
        ttk.Checkbutton(auto, text="자동 반복", variable=self.auto_repeat_var,
                        command=self._on_auto_toggled).pack(side=tk.LEFT)
        ttk.Label(auto, text="  끝나고 대기").pack(side=tk.LEFT)
        ttk.Entry(auto, textvariable=self.auto_delay_var, width=4).pack(side=tk.LEFT, padx=(4, 2))
        ttk.Label(auto, text="s").pack(side=tk.LEFT)
        ttk.Label(auto, textvariable=self.auto_status_var, foreground="#b26a00").pack(
            side=tk.LEFT, padx=(10, 0))

    def _build_probe_frame(self, parent: ttk.Frame) -> None:
        """원점 프로브 확인 손짓 — 모니터 + 확인 반환 버튼.

        로봇은 3점 측정을 마치고 원점에 서면 **거기서 멈춘다**. 프로브가
        벽에 제대로 눌렸는지는 로봇이 알 수 없어서, 확인은 바깥(여기)이
        한다. 확인해 주면 로봇이 적심(비비기) 후 스캔으로 넘어간다.
        확인 전까지는 계속 서 있으므로, 이 버튼을 안 누르면 검사가 시작되지
        않는다.
        """
        frame = ttk.LabelFrame(
            parent, text="원점 프로브 확인 (로봇이 여기서 멈춰 기다린다)", padding=8)
        frame.grid(row=2, column=0, sticky=tk.EW, pady=(0, 8))
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="로봇 상태").grid(row=0, column=0, sticky=tk.W)
        self.probe_state_var = tk.StringVar(value="대기 신호 없음")
        self.probe_state_label = ttk.Label(
            frame, textvariable=self.probe_state_var, foreground="#666")
        self.probe_state_label.grid(row=0, column=1, sticky=tk.W, padx=(8, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, columnspan=2, pady=(10, 0), sticky=tk.EW)
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)

        self.probe_ok_button = ttk.Button(
            buttons, text="✔ 프로브 눌림 확인 — 스캔 시작",
            command=lambda: self._publish_probe_ack(True), state=tk.DISABLED)
        self.probe_ok_button.grid(row=0, column=0, padx=3, sticky=tk.EW)
        self.probe_ng_button = ttk.Button(
            buttons, text="✘ 눌림 불량 — 대기 유지",
            command=lambda: self._publish_probe_ack(False), state=tk.DISABLED)
        self.probe_ng_button.grid(row=0, column=1, padx=3, sticky=tk.EW)

    def _set_probe_gate(self, waiting: bool, job_id: str = "") -> None:
        """`probe_gate` 수신 상태를 화면에 반영한다."""
        state = tk.NORMAL if waiting else tk.DISABLED
        self.probe_ok_button.configure(state=state)
        self.probe_ng_button.configure(state=state)
        if waiting:
            where = f" ({job_id})" if job_id else ""
            self.probe_state_var.set(f"원점 도착 — 프로브 확인 대기 중{where}")
            self.probe_state_label.configure(foreground="#b26a00")
            if self._auto_active() and self._auto_probe_after is None:
                # 버튼이 켜진 게 화면에 보이도록 잠깐 두고 누른다.
                self._auto_probe_after = self.root.after(1000, self._auto_press_probe)
        else:
            self.probe_state_var.set("대기 아님 (스캔 중이거나 정지)")
            self.probe_state_label.configure(foreground="#666")

    def _publish_probe_ack(self, pressed: bool) -> None:
        """확인 결과를 RCS 로 되돌려 준다.

        `pressed=False` 면 로봇은 풀리지 않고 원점에서 계속 기다린다 —
        프로브가 안 붙은 채로 훑으면 검사가 성립하지 않기 때문이다.
        """
        # timestamp 는 **문자열**이어야 한다 — 사내 MC 규격이 그렇고,
        # RCS 의 검증기(_timestamp_int)가 문자열이 아니면 거부한다.
        # 다른 명령이 쓰는 것과 같은 헬퍼를 쓴다.
        payload: dict[str, Any] = {
            "timestamp": utc_epoch_ms(),
            "pressed": bool(pressed),
        }
        if not pressed:
            payload["reason"] = "PROBE_NOT_PRESSED"
        self._publish(PROBE_ACK, payload)
        if pressed:
            self._set_probe_gate(False)

    def _build_motion_frame(self, parent: ttk.Frame) -> None:
        """RCS 가 돌려주는 가상 차량·리프트 값을 보여준다.

        규격 탭5 에 이미 자리가 있는 값이다 — query 응답의 `lift_height`,
        evt/progress 의 `moved`. RCS 쪽 차량·리프트가 아직 더미라 값도
        더미지만, 이동하는 동안 중간값이 흐르므로 서 있는지 가는지 보인다.
        """
        frame = ttk.LabelFrame(
            parent, text="진행 상황 (RCS 반환값)", padding=8)
        frame.grid(row=3, column=0, sticky=tk.EW, pady=(0, 8))
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)

        # 지금 어느 격자를 하고 있는지. job_state 의 job_id 가 격자 이름
        # (1A ~ 12F) 이다 — 전체 작업 ID(jb00000001)가 아니다. 사내 MC
        # 규격 T-009 가 그렇게 정의한다.
        ttk.Label(frame, text="현재 격자").grid(row=0, column=0, sticky=tk.W)
        self.cell_var = tk.StringVar(value="—")
        self.cell_label = ttk.Label(frame, textvariable=self.cell_var,
                                    font=("Consolas", 11, "bold"))
        self.cell_label.grid(row=0, column=1, sticky=tk.W, padx=(8, 20))
        ttk.Label(frame, text="전체 작업 ID").grid(row=0, column=2, sticky=tk.W)
        self.active_job_var = tk.StringVar(value="—")
        ttk.Label(frame, textvariable=self.active_job_var,
                  font=("Consolas", 11)).grid(row=0, column=3, sticky=tk.W, padx=(8, 0))

        ttk.Label(frame, text="AMR 이동").grid(row=1, column=0, sticky=tk.W)
        self.amr_moved_var = tk.StringVar(value="—")
        ttk.Label(frame, textvariable=self.amr_moved_var,
                  font=("Consolas", 11)).grid(row=1, column=1, sticky=tk.W, padx=(8, 20))
        ttk.Label(frame, text="리프트 높이").grid(row=1, column=2, sticky=tk.W)
        self.lift_height_var = tk.StringVar(value="—")
        ttk.Label(frame, textvariable=self.lift_height_var,
                  font=("Consolas", 11)).grid(row=1, column=3, sticky=tk.W, padx=(8, 0))

    #: job_state 의 state 값을 사람이 읽는 말로.
    CELL_STATES = {"waiting": "대기", "executing": "작업 중", "completed": "완료"}

    def _handle_job_state(self, payload: Any) -> None:
        """지금 어느 격자를 하고 있는지 반영한다 (사내 MC 규격 T-009).

        `job_id` 에는 전체 작업 ID(jb00000001)가 아니라 **격자 이름**
        (1A ~ 12F)이 들어온다 — 규격이 그렇게 정의한다. 전체 작업 ID 는
        우리가 job_cmd 로 보낸 값이라 여기서 따로 붙여 보여 준다.
        """
        if not isinstance(payload, dict):
            return
        cell = str(payload.get("job_id", "")).strip()
        state = str(payload.get("state", "")).strip()
        if not cell:
            return
        self.cell_var.set(f"{cell}  ({self.CELL_STATES.get(state, state)})")
        self.cell_label.configure(
            foreground="#0a6" if state == "completed" else "#b26a00")
        if state == "completed" and self._auto_active():
            self._auto_done.add(cell)
            self._show_auto_status()
            if len(self._auto_done) >= self._auto_total and self._auto_restart_after is None:
                self._auto_rounds += 1
                delay_s = self._auto_delay_s()
                self._log(f"[자동 반복] {self._auto_rounds}회 완료 — {delay_s:g}초 뒤 다시 시작합니다.")
                self._auto_restart_after = self.root.after(int(delay_s * 1000), self._auto_restart)

    def _handle_motion_values(self, payload: Any) -> None:
        """evt/progress · query 응답에 실려 온 이동 값을 반영한다."""
        if not isinstance(payload, dict):
            return
        content = payload.get("content")
        if not isinstance(content, dict):
            return
        if "moved" in content:
            self.amr_moved_var.set(f"{float(content['moved']):,.1f} mm")
        if "lift_height" in content:
            self.lift_height_var.set(f"{float(content['lift_height']):,.1f} mm")

    def _build_speed_frame(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="로봇 동작 속도 (2~100 %)", padding=8)
        frame.grid(row=4, column=0, sticky=tk.EW, pady=(0, 8))
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="속도").grid(row=0, column=0, sticky=tk.W)
        # 끄는 동안은 숫자만 따라오고, 보내기는 버튼이나 프리셋으로 한다.
        scale = ttk.Scale(
            frame, from_=2, to=100, orient=tk.HORIZONTAL,
            command=lambda v: self.speed_var.set(int(float(v))),
        )
        scale.set(100)
        scale.grid(row=0, column=1, sticky=tk.EW, padx=8)
        self.speed_label = ttk.Label(frame, textvariable=self.speed_var, width=4)
        self.speed_label.grid(row=0, column=2)
        ttk.Label(frame, text="%").grid(row=0, column=3, padx=(0, 8))
        ttk.Button(frame, text="속도 전송", command=self._publish_speed).grid(
            row=0, column=4, padx=(4, 0)
        )

        presets = ttk.Frame(frame)
        presets.grid(row=1, column=0, columnspan=5, pady=(8, 0), sticky=tk.EW)
        for col, percent in enumerate((10, 25, 50, 75, 100)):
            presets.columnconfigure(col, weight=1)
            ttk.Button(
                presets, text=f"{percent} %",
                command=lambda p=percent, sc=scale: self._set_and_send_speed(p, sc),
            ).grid(row=0, column=col, padx=3, sticky=tk.EW)

    def _set_and_send_speed(self, percent: int, scale) -> None:
        """프리셋 버튼: 슬라이더를 맞추고 바로 보낸다."""
        scale.set(percent)
        self.speed_var.set(percent)
        self._publish_speed()

    def _publish_speed(self) -> None:
        """로봇 전체 동작 속도 비율을 발행한다. 작업 중에도 바로 반영된다."""
        percent = int(self.speed_var.get())
        if not 2 <= percent <= 100:
            messagebox.showwarning("범위 초과", "속도는 2~100 % 여야 합니다.")
            return
        self._publish(SPEED, {"timestamp": utc_epoch_ms(), "speed": str(percent)})

    def _build_log_frame(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="송수신 로그", padding=8)
        frame.grid(row=5, column=0, sticky=tk.NSEW)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.log_text = scrolledtext.ScrolledText(frame, height=18, state=tk.DISABLED)
        self.log_text.grid(row=0, column=0, sticky=tk.NSEW)
        bar = ttk.Frame(frame)
        bar.grid(row=1, column=0, sticky=tk.EW, pady=(6, 0))
        bar.columnconfigure(0, weight=1)
        ttk.Checkbutton(
            bar, variable=self.show_stream_var,
            text="상시 발행(tcp)도 표시 — 한 줄로 접어서 보여줍니다",
        ).grid(row=0, column=0, sticky=tk.W)
        ttk.Button(bar, text="로그 지우기", command=self._clear_log).grid(
            row=0, column=1, sticky=tk.E)

    # ------------------------------------------------------------ 연결/통신
    def _connect(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            messagebox.showerror(
                "의존성 없음", "paho-mqtt가 설치되어 있지 않습니다.\npip install paho-mqtt"
            )
            return

        host = self.host_var.get().strip()
        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            messagebox.showerror("입력 오류", "Port는 숫자여야 합니다.")
            return

        client_kwargs: dict[str, Any] = {"client_id": self.client_id_var.get().strip()}
        if hasattr(mqtt, "CallbackAPIVersion"):
            client_kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION1
        client = mqtt.Client(**client_kwargs)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        try:
            client.connect_async(host, port, keepalive=30)
        except Exception as exc:  # noqa: BLE001 - 연결 실패를 화면에 보여주기 위함
            messagebox.showerror("연결 실패", str(exc))
            return

        client.loop_start()
        self.client = client
        self.connect_button.configure(state=tk.DISABLED)
        self.disconnect_button.configure(state=tk.NORMAL)
        self.connection_var.set(f"연결 시도 중… ({host}:{port})")

    def _disconnect(self) -> None:
        if self.client is not None:
            self.client.loop_stop()
            self.client.disconnect()
            self.client = None
        self.connected = False
        self.connect_button.configure(state=tk.NORMAL)
        self.disconnect_button.configure(state=tk.DISABLED)
        self.connection_var.set("연결 안 됨")

    def _on_connect(self, client, _userdata, _flags, rc) -> None:  # noqa: ANN001
        if rc == 0:
            for topic in SUBSCRIBE_TOPICS:
                client.subscribe(topic, qos=1)
            self.events.put(("connected", None))
        else:
            self.events.put(("connect_failed", rc))

    def _on_disconnect(self, _client, _userdata, rc) -> None:  # noqa: ANN001
        self.events.put(("disconnected", rc))

    def _on_message(self, _client, _userdata, msg) -> None:  # noqa: ANN001
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = msg.payload
        self.events.put(("message", (msg.topic, payload)))

    def _process_events(self) -> None:
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "connected":
                    self.connected = True
                    self.connection_var.set(
                        f"연결됨 ({self.host_var.get()}:{self.port_var.get()})"
                    )
                    self._log("[연결됨]")
                elif kind == "connect_failed":
                    self._log(f"[연결 실패] rc={data}")
                    self.connect_button.configure(state=tk.NORMAL)
                    self.disconnect_button.configure(state=tk.DISABLED)
                elif kind == "disconnected":
                    self.connected = False
                    self.connection_var.set("연결 끊김")
                    self._log(f"[연결 끊김] rc={data}")
                elif kind == "message":
                    topic, payload = data
                    if topic == JOB_STATE:
                        self._handle_job_state(payload)
                    elif topic == PROBE_GATE:
                        self._handle_probe_gate(payload)
                    elif topic.startswith("erut/") and (
                            topic.endswith("/evt/progress") or topic.endswith("/res")):
                        self._handle_motion_values(payload)
                    self._show_message(topic, payload)
        except queue.Empty:
            pass
        self.root.after(100, self._process_events)

    def _publish(self, topic: str, payload: dict[str, Any]) -> None:
        if self.client is None or not self.connected:
            messagebox.showwarning("연결 필요", "먼저 MQTT Broker에 연결해 주세요.")
            return
        self.client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=1)
        self._log(f"[발행 {topic}] {json.dumps(payload, ensure_ascii=False)}")

    def _handle_probe_gate(self, payload: Any) -> None:
        """RCS 가 보내는 원점 대기 상태를 버튼에 반영한다."""
        if not isinstance(payload, dict):
            return
        state = str(payload.get("state", "")).strip().lower()
        self._set_probe_gate(state == "waiting", str(payload.get("job_id", "")))

    def _show_message(self, topic: str, payload: Any) -> None:
        """받은 메시지를 로그에 남긴다.

        tcp 는 초당 여러 번 오므로 그대로 찍으면 로그가 이것만으로
        가득 차 정작 봐야 할 res·evt/complete·evt/error 가 묻힌다. 그래서
        **한 줄로 접어서** 보여주고, 체크를 끄면 아예 감춘다.
        """
        high_rate = any(k in topic for k in HIGH_RATE)
        if high_rate and not self.show_stream_var.get():
            return
        text = json.dumps(payload, ensure_ascii=False) if isinstance(
            payload, (dict, list)) else str(payload)
        if high_rate:
            self._log(f"[상시 {topic}] {text[:110]}", replace_last_of=topic)
            return
        self._log(f"[수신 {topic}] {text}")

    def _log(self, text: str, replace_last_of: str | None = None) -> None:
        self.log_text.configure(state=tk.NORMAL)
        if replace_last_of and self._last_stream_topic == replace_last_of:
            # 직전 줄이 같은 상시 토픽이면 갈아 끼운다 (로그가 안 밀린다).
            self.log_text.delete("end-2l", "end-1l")
        self._last_stream_topic = replace_last_of
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _clear_log(self) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    # ------------------------------------------------------------- 명령들
    def _reset_progress_view(self) -> None:
        """새 작업을 보낼 때 진행 표시를 초기화한다."""
        self.cell_var.set("—")
        self.cell_label.configure(foreground="#666")
        self.amr_moved_var.set("—")
        self.lift_height_var.set("—")
        self.active_job_var.set(self.job_id_var.get().strip() or "—")

    def _publish_job_command(self) -> None:
        """job_info + plan(격자 분할)으로 "전체 작업 시작" 명령을 발행한다."""
        values = {
            "job_id": self.job_id_var.get().strip(),
            "diameter": self.diameter_var.get().strip(),
            "height": self.height_var.get().strip(),
            "target_distance": self.target_distance_var.get().strip(),
            "column_count": self.column_count_var.get().strip(),
            "row_count": self.row_count_var.get().strip(),
            "cell_width": self.cell_width_var.get().strip(),
            "cell_height": self.cell_height_var.get().strip(),
            "overlap": self.overlap_var.get().strip(),
            "radius": self.radius_var.get().strip(),
            "thickness": self.thickness_var.get().strip(),
            "eoat": self.eoat_var.get().strip(),
        }
        if any(not value for value in values.values()):
            messagebox.showwarning("입력 누락", "모든 값을 입력해 주세요.")
            return
        payload = {
            "timestamp": utc_epoch_ms(),
            "job_id": values["job_id"],
            "job_info": {
                "diameter": values["diameter"],
                "height": values["height"],
                "target_distance": values["target_distance"],
            },
            "plan": {
                "column_count": values["column_count"],
                "row_count": values["row_count"],
                "cell_width": values["cell_width"],
                "cell_height": values["cell_height"],
                "overlap": values["overlap"],
                "radius": values["radius"],
                "thickness": values["thickness"],
                "eoat": values["eoat"],
            },
        }
        self._publish(JOB_COMMAND, payload)
        self._reset_progress_view()
        # 체크돼 있으면 자동 반복을 켠다(재시작할 때도 이 경로로 온다).
        self._auto_armed = bool(self.auto_repeat_var.get())
        try:
            self._auto_total = max(1, int(values["column_count"]) * int(values["row_count"]))
        except ValueError:
            self._auto_total = 1
        self._auto_done.clear()
        self._show_auto_status()

    def _publish_pause(self) -> None:
        self._publish(
            MC_COMMAND, {"timestamp": utc_epoch_ms(), "amr": "stop", "cobot": "stop"}
        )

    def _publish_resume(self) -> None:
        self._publish(
            MC_COMMAND, {"timestamp": utc_epoch_ms(), "amr": "run", "cobot": "run"}
        )

    def _publish_home(self) -> None:
        self._publish(MC_COMMAND, {"timestamp": utc_epoch_ms(), "cobot": "home"})

    def _publish_stop(self) -> None:
        # 정지는 자동 반복도 멈춘다 — 다시 돌리려면 "전체 작업 시작"을 누른다.
        self._auto_disarm("정지")
        self._publish(JOB_CLEAR, {"timestamp": utc_epoch_ms(), "request": "true"})

    # ------------------------------------------------------------- 자동 반복
    def _auto_active(self) -> bool:
        return self._auto_armed and bool(self.auto_repeat_var.get())

    def _auto_delay_s(self) -> float:
        try:
            return max(0.0, float(self.auto_delay_var.get()))
        except ValueError:
            return 5.0

    def _show_auto_status(self) -> None:
        if not self._auto_active():
            self.auto_status_var.set("꺼짐" if not self.auto_repeat_var.get() else "시작 누르면 켜짐")
            return
        self.auto_status_var.set(
            f"{self._auto_rounds + 1}회차 · 격자 {len(self._auto_done)}/{self._auto_total}")

    def _on_auto_toggled(self) -> None:
        if not self.auto_repeat_var.get():
            self._auto_disarm("체크 해제")
        self._show_auto_status()

    def _auto_disarm(self, why: str) -> None:
        was = self._auto_armed
        self._auto_armed = False
        for attr in ("_auto_restart_after", "_auto_probe_after"):
            after_id = getattr(self, attr)
            if after_id is not None:
                self.root.after_cancel(after_id)
                setattr(self, attr, None)
        if was:
            self._log(f"[자동 반복] 멈춤 ({why}) — {self._auto_rounds}회 완료")
        self._show_auto_status()

    def _auto_press_probe(self) -> None:
        self._auto_probe_after = None
        if self._auto_active() and str(self.probe_ok_button.cget("state")) == tk.NORMAL:
            self._log("[자동 반복] 프로브 눌림 확인을 보냅니다.")
            self._publish_probe_ack(True)

    def _auto_restart(self) -> None:
        self._auto_restart_after = None
        if not self._auto_active():
            return
        if self.client is None:
            self._auto_disarm("브로커 연결 없음")
            return
        self._log(f"[자동 반복] {self._auto_rounds + 1}회차 전체 작업 시작")
        self._publish_job_command()

    def _publish_request(self, topic: str) -> None:
        self._publish(topic, {"timestamp": utc_epoch_ms(), "request": "true"})

    def _close(self) -> None:
        self._disconnect()
        self.root.destroy()


def use_visible_cursor(root: tk.Tk) -> None:
    """마우스 포인터를 직접 지정한다.

    WSLg(Xwayland)에서 Tk 창은 커서를 지정하지 않으면 X 루트의 **빈 커서**를
    물려받아, 창 위에 마우스를 올리면 포인터가 사라진다. 자식 위젯은 부모
    창의 커서를 따르므로 최상위 창에만 주면 되고, 팝업(메시지 상자)은 따로
    뜨는 최상위 창이라 옵션으로 같이 준다. 입력칸의 I 자 커서는 그대로다.
    """
    root.configure(cursor="left_ptr")
    root.option_add("*Toplevel.cursor", "left_ptr")
    root.option_add("*Dialog.cursor", "left_ptr")


def main() -> None:
    root = tk.Tk()
    use_visible_cursor(root)
    MqttJobSimApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
