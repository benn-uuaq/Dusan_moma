"""ERUT 표준 인터페이스(if-0.9)의 검사로봇 쪽을 구현하는 프로토콜 계층.

규격은 `mqtt_test/MQTT_인터페이스/ERUT_검사로봇_MQTT_표준인터페이스_if-0.9.xlsx` 다.
3S 가 구현할 것은 탭1~5·8~10 이고, 탭6·7 은 ERUT 내부(클라이언트↔브릿지) 규격이라
무관하다. 상대는 ERUT 의 Robot Service(로봇 브릿지)다.

답(res · evt/ready · evt/complete)의 결과는 맨 바깥 status_code(HTTP 원래 뜻) 하나,
문제는 content.error{code, name, detail} 다(if-0.9 — 번호는 erut_errors.py).

    query     → res 200 (activity·resumable·calibrated·errors[]·last_job …)
    calibrate → res 202 → evt/complete (origin, calibration_error_mm, total_length_mm)
                **차량(AGV) 캘리브레이션**: 모재 외벽을 따라 한 바퀴 돌며 좌표계(맵)를 잡는다
    prepare   → res 202 → evt/ready  (작업 값만 확인해 둔다 — 아무것도 움직이지 않는다)
    start     → res 202 → 차량 이동 → 아웃트리거 고정 → 리프트 → 로봇 3점 측정 →
                시작점에 붙음 → evt/contact attached → 적심 → ㄹ자 스캔 →
                evt/progress … → evt/complete
    pause · abort · reset → res 2xx (「받았다」 — 섰는지는 evt/status 의 activity 로)
    resume    → res 202 (완료는 처음 요청의 req_id 로)
    mark      → res 202 → [evt/mark_ready → req/mark_next] × 점 → evt/complete
    home      → res 202 → evt/complete (action=home)        ※ 3S 확장 (명령은 표준에 없다)
    그 밖     → res 501 + error E6501 NOT_IMPLEMENTED

상시 발행: evt/status(5초 + 바뀌면 바로) · evt/contact(2초 + 바뀌면 바로) ·
evt/home(접속 시 + 바뀔 때만) · evt/info(접속 시) · evt/progress(작업 중 2초).

**아직 시험용·미구현**: 배터리·충전(차량 개발자에게 받기로 했다 — 그때까지는
칸을 싣지 않는다. 규격이 0 으로 채우지 말라고 한다), 캘리브레이션의 총 둘레
(`total_length_mm` — 차량을 한 바퀴 돌려 잰 거리라 역시 차량 쪽 값이다).
"""

from __future__ import annotations

import json
import math
import time
from typing import Any, Callable

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from smr_operator_ui.services import erut_errors as E
from smr_operator_ui.services.erut_client import ErutClient
from smr_operator_ui.services.erut_errors import make_error
from smr_operator_ui.services.job_sequencer import GridPlan, SequencerState, cell_label

# ---- 자기소개 (탭8 evt/info) ------------------------------------------------
INTERFACE_VERSION = "0.9"
VENDOR = "3S"
MODEL = "SMR-UT-CS612"
DEVICE_TYPE = "articulated_arm"
#: 지원하는 기능. battery·charging 은 차량 자료가 오면 넣는다(TODO).
#: 마킹은 마커가 ERUT 것이라 marking 이 아니라 mark_positioning 이다(탭2 초안).
#: home(if-0.5): 이동 안전 자세를 evt/home 으로 알린다 — 3S 가 요청한 기능.
CAPABILITIES = (
    "core_control", "core_events", "calibration", "prepare",
    "probe_contact", "mark_positioning", "position_feedback", "home",
)

#: 합의한 검사면. 모르는 surface 는 400 으로 거절한다(탭4 E-1 11번) — 짐작으로
#: 다른 면을 검사하면 보고서에는 요청한 면으로 적힌다.
SURFACES = ("outer",)
#: 낼 수 있는 최고 검사 속도 [mm/s]. 넘으면 거절한다(탭8 45행).
SCAN_SPEED_MAX = 100

STATUS_PERIOD_MS = 5_000       # evt/status 재발행 (탭2 11행 「5초 안팎」)
CONTACT_PERIOD_MS = 2_000      # evt/contact 재발행 (탭2 12행 「2초마다」)
PROGRESS_PERIOD_MS = 2_000     # evt/progress 주기 (자기소개 timings 로 알린다)
HOME_TIMEOUT_MS = 120_000      # 홈 도착을 기다리는 한도
#: 시작점에 붙어 evt/contact(attached)를 낸 뒤 적심을 시작하기까지 [ms].
#: ERUT 는 attached 를 받으면 물을 켠다 — 물이 나오기 전에 비비면 마른 채로 문지른다.
CONTACT_LEAD_MS = 2_000
#: pause · abort 를 받고 장비(팔·차량·리프트)가 다 서기까지 가장 오래 걸릴 때 [ms]
#: (evt/info timings.stop_latency_ms — 탭8 51행 · Q-30). 팔은 29999 pause/stop 로
#: 1초 안에 서고, 차량(0.3 m/s)·리프트도 감속해 서는 데 1~2초다. 상태 확인 주기와
#: 통신 지연을 더해 넉넉히 잡았다.
STOP_LATENCY_MS = 5_000
#: 일을 시키는 요청이 보낸 시각보다 이만큼 늦게 오면 실행하지 않는다 [s]
#: (탭2 34행 · 400 E6005 REQUEST_EXPIRED). 세우는 명령은 늦어도 실행한다.
REQUEST_EXPIRY_S = 10.0
_WORK_ACTIONS = frozenset({"calibrate", "prepare", "start", "resume", "mark"})

# 시퀀서 상태 → activity (탭5 8값). 준비·원점 대기·장애는 세션이 따로 가린다.
_ACTIVITY_MAP = {
    SequencerState.IDLE: "idle",
    SequencerState.SECURING: "running",
    SequencerState.LEVELING: "running",
    SequencerState.MOVING_LIFT: "running",
    SequencerState.READY: "ready",
    SequencerState.SCANNING: "running",
    SequencerState.RETRACTING: "running",
    SequencerState.MOVING_AMR: "running",
    SequencerState.PAUSED: "paused",
    SequencerState.DONE: "idle",
    SequencerState.STOPPED: "idle",
}
_SEQ_IDLE = (SequencerState.IDLE, SequencerState.DONE, SequencerState.STOPPED)

#: 로봇이 동작 중이라 홈 요청을 받을 수 없을 때 보여 주는 문장.
HOME_BUSY_TEXT = "현재 로봇이 동작 중이므로 홈 이동이 불가합니다."

#: evt/message 알림 (3S 확장 — 표준에 없는 토픽이라 브릿지는 무시할 수 있다).
MSG_HOME_NOT_ALLOWED = ("M1001", "HOME_NOT_ALLOWED")
MSG_ARC_LIMIT_CLAMPED = ("M2001", "ARC_LIMIT_CLAMPED")
MSG_AREA_APPLY_PENDING = ("M2002", "AREA_APPLY_PENDING")

#: 좌표계가 무효가 되는 순간 내는 장애 (탭2 10행).
E_CALIBRATION_LOST = ("E1003", "CALIBRATION_LOST")

#: 3S 제조사 코드(E9xxx) 가운데 세션이 쓰는 것 — 완료(evt/complete)를 실패로
#: 끝낼 때 까닭으로 싣는다. 장애(evt/error)로 끝났으면 그 장애의 번호를 쓴다.
E_OPERATOR_STOPPED = ("E9401", "STOPPED_BY_OPERATOR")
E_CALIBRATION_FAILED = ("E9402", "CALIBRATION_FAILED")
E_MARK_FAILED = ("E9403", "MARK_FAILED")


def _mm(value: Any) -> float:
    """요청에 실려 온 치수를 mm 실수로 바꾼다. 못 읽으면 0."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class ErutSession(QObject):
    """ERUT 요청을 받아 로봇·차량을 움직이고 표준대로 답한다."""

    activity = pyqtSignal(str)
    # 구역 검사를 시작해 달라는 요청 (app.py 가 시퀀서에 넘긴다) — start 를 받았을 때.
    # (GridPlan, 준비만 하는가) — 지금은 늘 False 다: prepare 는 아무것도 움직이지
    # 않고(2026-10-14), start 가 차량 → 아웃트리거 → 리프트 → 로봇을 한 번에 한다.
    job_requested = pyqtSignal(object, bool)
    # 장애가 풀렸다(ERUT req/reset 또는 화면 알람 리셋). 푼 코드 목록.
    # 앱은 이걸 받아 쉬는 팔을 홈으로 거둔다(쉬는 동안 home — if-0.5/0.6).
    errors_cleared = pyqtSignal(list)
    # 걸린 장애 없이 reset 이 왔는데 팔이 펴져 있다 — home 으로 거둬 달라(if-0.8).
    fold_requested = pyqtSignal()
    pause_requested = pyqtSignal()
    resume_requested = pyqtSignal()
    abort_requested = pyqtSignal()
    speed_requested = pyqtSignal(int)
    # 로봇을 세워 달라는 요청. 순회(시퀀서)와 별개로 나간다.
    robot_stop_requested = pyqtSignal()
    # 준비해 둔(또는 돌던) 작업을 버리라는 요청 — 홈으로 보내지 않고 순회만 접는다.
    # 준비 상태(ready)에서 새 요청이 오거나, 장애로 구간이 실패했을 때 나간다.
    job_dropped = pyqtSignal()
    # 로봇이 원점에서 멈춰 기다리는 것을 풀어 달라는 요청 (레지스터 267).
    scan_go_requested = pyqtSignal()
    # 마킹 점들을 돌아 달라는 요청. [{id, x, y}, ...] (검사면 좌표 mm)
    mark_requested = pyqtSignal(list)
    # 마킹 자리에서 마킹이 끝났다 (점 id, 찍었는가). 로봇 대기를 푼다.
    mark_next_requested = pyqtSignal(str, bool)
    # 마킹을 도중에 접어 달라는 요청(일시정지·장애). 완료는 세션이 낸다.
    mark_stop_requested = pyqtSignal()
    # 마킹 일시정지·재개 — 그 자리에 섰다가 잇는다(if-0.7 마킹 ② ⑥).
    mark_pause_requested = pyqtSignal()
    mark_resume_requested = pyqtSignal()
    # 로봇을 홈으로 보내 달라는 요청. 로봇이 쉬고 있을 때만 나간다.
    home_requested = pyqtSignal()
    # 모재 기준 좌표계를 잡아 달라는 요청 (지름 mm, 높이 mm) — 차량이 한 바퀴 돈다.
    calibration_requested = pyqtSignal(float, float)
    # 캘리브레이션 주행을 멈추라 / 이어 가라(일시정지·재개).
    calibration_stop_requested = pyqtSignal()
    calibration_resume_requested = pyqtSignal()
    # evt/error 를 냈다 (코드, 이름, level).
    error_published = pyqtSignal(str, str, str)
    # 장애가 풀렸다고 evt/error(cleared=true)를 냈다 (코드, 이름).
    error_cleared = pyqtSignal(str, str)
    # evt/message 를 냈다 (코드, 문장).
    message_published = pyqtSignal(str, str)

    def __init__(self, client: ErutClient, sequencer,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.client = client
        self.sequencer = sequencer
        self._calibrated = False
        self._job_id = ""
        self._plan: GridPlan | None = None
        # 요청별로 살아 있는 req_id. 완료·준비 통보는 **처음 받은 요청**의
        # 번호로 낸다(resume 의 번호가 아니라 — 탭2 7·8행).
        self._prepare_req_id = ""
        self._ready_sent = False
        # prepare 로 받은 작업 값을 확인해 두고 start 를 기다리는 중인가(activity ready).
        self._prepared = False
        self._start_req_id = ""
        self._started_at = 0.0
        self._calibrate_req_id = ""
        self._calibrate_target = (0.0, 0.0)
        self._mark_req_id = ""
        self._mark_ids: list[str] = []
        self._home_req_id = ""
        # 로봇이 원점에 도착해 시작 신호를 기다리는 중인가 (activity = ready).
        self._at_origin = False
        # 시퀀서가 아닌 일(캘리브레이션)을 일시정지해 둔 것.
        self._paused_work = ""
        # 걸려 있는 장애: code → error{code, name, level, recovery, detail…}
        self._errors: dict[str, dict] = {}
        self._last_job: dict | None = None
        self._progress = 0
        self._moved_mm = 0
        self._contact: bool | None = None
        self._status_sig: tuple | None = None

        # app.py 가 물려 주는 값들. 기본값은 장비 없이도 돌게 비워 둔다.
        # 차량·리프트 현재 값 {"lift_height", "moved"} (mm)
        self.motion_state: Callable[[], dict] = dict
        # 로봇 위치 {"arc_mm": 호 위 거리, "row_mm": 줄 높이, "progress": %}
        self.position_state: Callable[[], dict] = dict
        # 로봇이 동작 중인가 (RCS 작업·로봇 태스크 실행)
        self.robot_busy: Callable[[], bool] = lambda: False
        # 로봇이 한 번에 훑을 수 있는 최대 구간 가로(호 길이)·세로 [mm]. 0 이면
        # 모름(한계를 따지지 않는다).
        self.max_area_width: Callable[[], float] = lambda: 0.0
        self.max_area_height: Callable[[], float] = lambda: 0.0
        # 그 한계를 정한 모재 지름 [mm] (evt/info area_limit.diameter). 0 이면 모름.
        self.area_diameter: Callable[[], float] = lambda: 0.0
        # 펴진 자세에서 home 까지 가장 오래 걸릴 때 [ms] (evt/info timings.home_within_ms).
        # 0 이면 싣지 않는다(ERUT 는 30초를 쓴다).
        self.home_within_ms: Callable[[], int] = lambda: 0
        # reset 으로 팔을 거둘 수 있는 때인가(돌거나 멈춰 둔 작업이 없고 로봇이 붙어 있다).
        self.can_fold: Callable[[], bool] = lambda: False
        # 마킹 ②: 지금 자리에서 마킹을 기다리는 점 id (없으면 빈 문자열).
        self.mark_waiting_point: Callable[[], str] = lambda: ""
        # 일시정지 뒤 장비(팔·차량·리프트)가 실제로 다 섰는가 (if-0.6 — paused 는
        # 선 뒤에만 알린다. 감속 중에는 아직 running).
        self.pause_settled: Callable[[], bool] = lambda: True
        # 선 자리를 그대로 지키고 있어 그 자리에서 이어 갈 수 있는가 (if-0.6 —
        # 밀렸거나 태스크가 중지됐으면 False → resumable=false, resume 은 412).
        self.pause_holds_position: Callable[[], bool] = lambda: True
        # 로봇이 홈에 있는가 (레지스터 276). 모르면 None.
        self.at_home: Callable[[], bool | None] = lambda: None
        # 캘리브레이션 때 차량으로 잰 총 둘레 [mm]. 차량 자료가 오기 전엔 None.
        self.total_length_mm: Callable[[], float | None] = lambda: None

        # 같은 req_id 를 다시 받으면 재실행하지 않고 이전 응답을 되돌려준다(탭2 22행).
        #: 받아들인 요청 — 키 → (status_code, error, extra, 내용, 받은 시각). 재전송 판정용.
        self._handled: dict[str, tuple[int, dict | None, dict, str, float]] = {}
        self._request_body = ""
        self._pending: dict[int, QTimer] = {}

        client.request_received.connect(self.handle_request)
        client.erut_online_changed.connect(self._on_erut_online)
        # 접속은 비동기라 start() 시점엔 아직 붙기 전이다. 붙은 뒤에 올린다.
        client.connected_changed.connect(self._on_broker_connected)
        client.test_error_injected.connect(self.raise_error)
        if hasattr(sequencer, "state_changed"):
            sequencer.state_changed.connect(lambda *_: self.refresh_status())

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(STATUS_PERIOD_MS)
        self._status_timer.timeout.connect(self.publish_status)
        self._contact_timer = QTimer(self)
        self._contact_timer.setInterval(CONTACT_PERIOD_MS)
        self._contact_timer.timeout.connect(self._publish_contact)
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(PROGRESS_PERIOD_MS)
        self._progress_timer.timeout.connect(self._publish_progress)
        self._home_timer = QTimer(self)
        self._home_timer.setSingleShot(True)
        self._home_timer.setInterval(HOME_TIMEOUT_MS)
        self._home_timer.timeout.connect(lambda: self.finish_home(False, "HOME_TIMEOUT"))

    @property
    def job_id(self) -> str:
        """지금 구간의 job_id (prepare/start 가 준 값). 작업기록에 쓴다."""
        return self._job_id

    @property
    def calibrating(self) -> bool:
        return bool(self._calibrate_req_id) and self._paused_work != "calibrate"

    # ------------------------------------------------------------ 상시 발행
    def start(self) -> None:
        """상시 발행을 시작한다. online 통보는 접속이 붙은 뒤에 나간다."""
        if self.client.is_connected:
            self._on_broker_connected(True)
        self._status_timer.start()
        self._contact_timer.start()
        self._progress_timer.start()

    def stop(self) -> None:
        for timer in (self._status_timer, self._contact_timer, self._progress_timer):
            timer.stop()

    def _on_broker_connected(self, connected: bool) -> None:
        if not connected:
            return
        self.publish_info()
        self._status_sig = None
        self.publish_status()
        self.publish_home()
        self._publish_contact()

    def _info_content(self) -> dict:
        content: dict[str, Any] = {
            "interface_version": INTERFACE_VERSION,
            "vendor": VENDOR, "model": MODEL, "device_type": DEVICE_TYPE,
            "capabilities": list(CAPABILITIES),
            "timings": {
                "progress_interval_ms": PROGRESS_PERIOD_MS,
                "stop_latency_ms": STOP_LATENCY_MS,
                "scan_speed_max_mm_s": SCAN_SPEED_MAX,
            },
        }
        home_ms = int(self.home_within_ms() or 0)
        if home_ms > 0:
            # 이동 명령 전에 ERUT 가 home 을 기다리는 시간(if-0.7 탭8 69행).
            content["timings"]["home_within_ms"] = home_ms
        limit = self._area_limit()
        if limit:
            content["area_limit"] = limit
        return content

    def _area_limit(self) -> dict:
        """한 구간으로 검사할 수 있는 최대 가로·세로 (if-0.7 탭2 13행, 선택).

        모재 지름에 따라 달라지므로 좌표계(캘리브레이션)가 서서 지름을 알 때만
        싣는다. 한계가 있는 쪽만 — 세로 한계는 정해지면 싣는다.
        """
        diameter = float(self.area_diameter() or 0.0)
        if not self._calibrated or diameter <= 0:
            return {}
        limit: dict[str, int] = {}
        width = float(self.max_area_width() or 0.0)
        height = float(self.max_area_height() or 0.0)
        if width > 0:
            limit["max_width"] = int(width)
        if height > 0:
            limit["max_height"] = int(height)
        if limit:
            limit = {"diameter": int(round(diameter)), **limit}
        return limit

    def publish_info(self) -> None:
        """장비 자기소개 (탭8). 안 보내면 브릿지는 core 기능만 있는 장비로 본다."""
        content = self._info_content()
        self._info_published = content
        self.client.publish_info(content)

    def refresh_info(self) -> None:
        """자기소개가 바뀌었으면 다시 보낸다(evt/info 는 값이 바뀔 때 다시 보낸다)."""
        if self._info_content() != getattr(self, "_info_published", None):
            self.publish_info()

    def publish_status(self) -> None:
        """장치 상태 (탭2 11행). 모든 칸이 맨 바깥이다.

        battery·charging 은 싣지 않는다 — 차량 자료가 아직 없다(TODO). 규격은
        배터리가 없는 장비는 칸을 빼라고 한다(0 으로 채우면 시작을 영영 못 한다).
        홈 자세는 if-0.5 부터 따로 `evt/home` 으로 낸다(예전 at_home 칸은 뺐다).
        """
        sig = self._signature()
        self._status_sig = sig
        activity, calibrated, job_id = sig
        self.client.publish_status(
            "online", activity=activity, calibrated=calibrated,
            job_id=job_id or None)

    def refresh_status(self) -> None:
        """값이 바뀌었으면 주기를 기다리지 않고 바로 낸다(탭2 11·14행)."""
        if self._signature() != self._status_sig:
            self.publish_status()
        self.refresh_home()

    def _signature(self) -> tuple:
        return (self.activity_state(), self._calibrated,
                self._job_id if self._job_active() else "")

    # ---- 이동 안전 자세 (if-0.5 evt/home) ----------------------------------------
    def home_state(self) -> str:
        """home / deployed. 모르면 deployed 다 — 모르면 위험한 쪽(탭5 130행).

        홈 플래그(레지스터 276)를 쓴다. 태스크가 제어주기마다 **실제 관절값**을
        홈 관절과 비교해 쓰는 값이라 「접으라고 명령했다」가 아니라 「접힌 것을
        확인했다」다. 홈으로 가는 동안(접는 중)은 0 이라 deployed 로 나간다.
        """
        return "home" if self.at_home() is True else "deployed"

    def publish_home(self) -> None:
        """evt/home — 붙을 때 + 바뀔 때만(주기 재전송 없음)."""
        state = self.home_state()
        self._home_published = state
        self.client.publish_home(state)

    def refresh_home(self) -> None:
        if self.home_state() != getattr(self, "_home_published", None):
            self.publish_home()

    def set_contact(self, attached: bool) -> None:
        """탐촉자가 검사면에 붙었는가. 바뀌면 바로 알린다(탭2 12행).

        ERUT 가 시킨 구간(prepare·start) 안에서만 attached 로 알린다 — ERUT 는 이
        신호로 물을 켠다. 캘리브레이션의 3점 측정이나 RCS 단독 작업 중에 벽에
        닿는 것은 ERUT 의 검사가 아니다.
        """
        attached = bool(attached) and bool(self._prepare_req_id or self._start_req_id)
        if attached == self._contact:
            return
        self._contact = attached
        self._publish_contact()
        self.refresh_home()          # 붙어 있는 동안은 home 이 아니다

    def _publish_contact(self) -> None:
        if self._contact is None:
            return
        self.client.publish_contact(
            "attached" if self._contact else "detached", self._job_id)

    # ------------------------------------------------------------ 활동 상태
    def activity_state(self) -> str:
        """지금 장치가 하는 일 (탭5 activity 8값)."""
        levels = {e["level"] for e in self._errors.values()}
        if "estop" in levels:
            return "estop"
        if "stop" in levels:
            return "error"
        if self._paused_work or self.sequencer.state is SequencerState.PAUSED:
            if self.pause_settled():
                return "paused"
            # 아직 감속 중이다 — 선 뒤에야 paused (if-0.6, 탭2 25행). 작업자는
            # 「일시정지」를 보고 장비에 다가간다.
            if self._paused_work == "calibrate":
                return "calibrating"
            return _ACTIVITY_MAP.get(self.sequencer.resume_state, "running")
        if self._calibrate_req_id:
            return "calibrating"
        if self._is_ready():
            return "ready"
        if self._prepare_req_id and not self._ready_sent:
            return "preparing"
        if self._mark_req_id:
            # mark 를 받고 complete 를 낼 때까지 — 점으로 가는 중·점에서 기다리는
            # 중 모두(if-0.8). running 은 구역 검사라 ERUT 가 물을 켤 수 있다.
            return "marking"
        if self._home_req_id:
            return "running"
        return _ACTIVITY_MAP.get(self.sequencer.state, "idle")

    #: 옛 이름. 화면·기록이 부른다.
    robot_state = activity_state

    def _is_ready(self) -> bool:
        """준비가 끝나 start 를 기다리는가 (activity = ready)."""
        return self._prepared or self._at_origin

    def has_work(self) -> bool:
        """ERUT 가 시킨 일(캘리브레이션·준비·구간·마킹)이 걸려 있는가."""
        return bool(self._calibrate_req_id or self._prepare_req_id
                    or self._start_req_id or self._mark_req_id)

    def _job_active(self) -> bool:
        return bool(self._prepare_req_id or self._start_req_id
                    or self.sequencer.state is SequencerState.PAUSED)

    def _resumable(self) -> bool:
        """멈춘 자리를 지키고 있어 이어 갈 수 있는가 (if-0.6 탭5 59행).

        일시정지는 제자리에서 서므로 보통 True. 아직 서는 중이거나, 밀렸거나,
        태스크가 중지돼(pause 거절·펜던트 stop 등) 그 자리로 정확히 못 돌아가면
        False — ERUT 는 abort → query → home → start 로 구간을 처음부터 다시 한다.
        """
        if not (self._paused_work == "calibrate"
                or self.sequencer.state is SequencerState.PAUSED):
            return False
        return self.pause_settled() and self.pause_holds_position()

    # ------------------------------------------------------------ 요청 처리
    def handle_request(self, action: str, content: dict,
                       timestamp: float | None = None) -> None:
        """요청 하나를 처리한다. timestamp 는 브릿지가 보낸 시각(UTC ms, 없으면 None)."""
        req_id = str(content.get("req_id", "")).strip()
        if not req_id:
            self.activity.emit(f"ERUT {action}: req_id 가 없어 거절했습니다.")
            self.client.publish_res("", action, 400,
                                    make_error(E.BAD_REQUEST, detail="req_id 가 없습니다"))
            return

        # 재전송이면 다시 실행하지 않고 이전 응답만 되풀이한다(브릿지는 응답이
        # 없으면 같은 요청을 몇 초 간격으로 다시 보낸다). mark_next 는 마킹
        # 요청의 req_id 를 그대로 싣고 점마다 오므로 점까지 본다.
        key = f"{action}:{req_id}"
        if action == "mark_next":
            key = f"{key}#{content.get('point_id', '')}"
        body = json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)
        if self._is_retransmission(key, req_id, body):
            status, error, extra, _body, _at = self._handled[key]
            self.client.publish_res(req_id, action, status, error, **extra)
            self.activity.emit(f"ERUT {action}: 재전송({req_id}) — 이전 응답 재발행")
            return
        if self._handled.pop(key, None) is not None:
            self.activity.emit(
                f"ERUT {action}: 같은 req_id({req_id})지만 새 요청으로 처리합니다"
                " — 내용이 다르거나 앞 작업이 이미 끝났습니다.")
        self._request_body = body

        handler = getattr(self, f"_do_{action}", None)
        if handler is None:
            # 모르는 동작은 못 본 척하지 말고 거절한다 — 무시하면 브릿지가 세 번
            # 다시 보낸 뒤 통신 오류로 본다. 501 은 다시 보내지 않는다(탭5).
            self._refuse(req_id, action, E.NOT_IMPLEMENTED, f"모르는 동작: {action}",
                         key=key)
            return
        if self._expired(req_id, action, timestamp):
            return
        handler(req_id, content)
        self.refresh_status()

    def _expired(self, req_id: str, action: str, timestamp: float | None) -> bool:
        """일 요청이 보낸 시각보다 10초 넘게 늦게 왔으면 400 E6005 로 거절한다.

        브릿지는 1초 간격으로 세 번 보내 보고 포기하므로 10초 넘게 늦은 요청은
        아무도 기다리지 않는다 — 실행하면 기록에 없는 검사가 된다(탭2 34행).
        세우는 명령(pause · abort · reset · query)은 늦어도 실행한다. 보낸 시각이
        없는 요청은 받는다 — 현장에서 MQTT Explorer 로 손으로 보내 시험한다.
        """
        if action not in _WORK_ACTIONS or timestamp is None:
            return False
        late_s = time.time() - float(timestamp) / 1000.0
        if late_s <= REQUEST_EXPIRY_S:
            return False
        self.activity.emit(
            f"ERUT {action}: 보낸 지 {late_s:.0f}초 지난 요청이라 실행하지 않습니다(E6005).")
        self._refuse(req_id, action, E.REQUEST_EXPIRED,
                     f"보낸 지 {late_s:.1f}초 지나 도착했습니다(한도 {REQUEST_EXPIRY_S:g}초)")
        return True

    #: 같은 req_id·같은 내용이 이 시간 안에 다시 오면 재전송으로 본다 [s].
    RETRANSMIT_WINDOW_S = 30.0

    def _is_retransmission(self, key: str, req_id: str, body: str) -> bool:
        """재전송인가 — 다시 실행하면 안 되는 같은 요청인가.

        받아들인 요청만 기억한다(거절은 안 남긴다 — 조건이 풀린 뒤 같은
        req_id 로 다시 보내면 다시 따져야 한다. 예: calibrate 전에 428 을 받은
        prepare 를 캘리브레이션 뒤에 그대로 다시 보낸 경우). 그리고 내용이
        같고, 받은 지 RETRANSMIT_WINDOW_S 안이거나 그 요청이 시작한 일이 아직
        돌고 있을 때만 재전송이다. 작업을 정지한 뒤 같은 req_id 로 다시 보낸
        calibrate·prepare 는 새 요청이다.
        """
        seen = self._handled.get(key)
        if seen is None or seen[3] != body:
            return False
        recent = time.monotonic() - seen[4] <= self.RETRANSMIT_WINDOW_S
        return recent or self._work_in_progress(req_id)

    def _work_in_progress(self, req_id: str) -> bool:
        """이 req_id 가 시작한 일이 아직 끝나지 않았는가."""
        return req_id in {r for r in (
            getattr(self, "_calibrate_req_id", ""), getattr(self, "_prepare_req_id", ""),
            getattr(self, "_start_req_id", ""), getattr(self, "_mark_req_id", ""),
            getattr(self, "_home_req_id", "")) if r}

    def _reply(self, key: str, req_id: str, action: str, status: int,
               error: dict | None = None, **extra) -> None:
        if status < 300:
            self._handled[key] = (status, error, extra, self._request_body,
                                  time.monotonic())
        self.client.publish_res(req_id, action, status, error, **extra)

    def _answer(self, req_id: str, action: str, status: int,
                error: dict | None = None, **extra) -> None:
        self._reply(f"{action}:{req_id}", req_id, action, status, error, **extra)

    def _refuse(self, req_id: str, action: str, code: str, detail: str = "",
                key: str = "", **fields) -> None:
        """표준 번호로 거절한다 — status_code 는 번호가 정한다(탭5 145행~)."""
        self._reply(key or f"{action}:{req_id}", req_id, action, E.STATUS[code],
                    make_error(code, detail=detail or None, **fields))

    def _refuse_by_state(self, req_id: str, action: str) -> bool:
        """지금 상태로 받을 수 없는 일이면 거절하고 True (탭9).

        받는 때는 idle · ready 뿐이다. 장애가 걸려 있으면 409 E6103, 다른 일을
        하는 중이면 409 E6101 — 하던 일을 스스로 그만두지 않는다.
        """
        activity = self.activity_state()
        if activity in ("error", "estop"):
            self._refuse(req_id, action, E.FAULT_ACTIVE,
                         f"걸린 장애: {', '.join(self._errors) or activity}")
            return True
        if activity not in ("idle", "ready"):
            self._refuse(req_id, action, E.BUSY, f"지금 {activity}")
            return True
        if activity == "idle" and self.robot_busy():
            # 펜던트에서 직접 튼 태스크 등 — RCS 가 모르는 동작 중이다.
            self._refuse(req_id, action, E.BUSY, "로봇이 다른 동작 중입니다")
            return True
        return False

    def _needs_calibration(self, req_id: str, action: str) -> bool:
        """좌표계가 무효면 409 E6104 로 거절하고 True (탭4 D-4)."""
        if self._calibrated:
            return False
        self._refuse(req_id, action, E.CALIBRATION_REQUIRED, "원점을 먼저 잡아야 합니다")
        self.activity.emit(
            f"ERUT {action} 거절 — 캘리브레이션이 무효합니다(E6104). 재캘리브레이션이 필요합니다.")
        return True

    def _drop_prepared(self) -> None:
        """준비해 둔 구간을 버린다(ready 에서 새 요청이 왔을 때 — 탭9)."""
        if not (self._at_origin or self._prepare_req_id or self._prepared):
            return
        self._at_origin = False
        self._prepared = False
        self._prepare_req_id = ""
        self._ready_sent = False
        self.activity.emit("준비해 둔 구간을 버리고 새 요청을 받습니다.")
        self.job_dropped.emit()

    def invalidate_calibration(self, reason: str = "") -> None:
        """좌표계를 무효로 돌린다 (탭2 중요사항 23행).

        자리를 벗어나거나 옮겨져 좌표계를 더는 믿을 수 없으면 calibrated=false
        로 바꾸고, **그 순간** E1003 CALIBRATION_LOST 를 낸다 — query 로 물어봐야만
        알면 그 사이 다음 구역까지 엉뚱한 좌표로 검사하고 정상 완료로 끝난다.
        """
        if not self._calibrated:
            return
        self._calibrated = False
        note = f" — {reason}" if reason else ""
        self.activity.emit(f"캘리브레이션을 무효로 표시했습니다{note}.")
        code, name = E_CALIBRATION_LOST
        self.raise_error({"code": code, "name": name, "level": "stop",
                          "recovery": "reset_required", "detail": reason or None})
        self.refresh_info()          # 지름이 무효 — area_limit 을 뺀다

    # ---- query --------------------------------------------------------------
    def _do_query(self, req_id: str, content: dict) -> None:
        extra: dict[str, Any] = {
            "activity": self.activity_state(),
            "resumable": self._resumable(),
            "calibrated": self._calibrated,
            # ERUT 는 이동 명령 직전에 이 칸으로 판단한다(evt/home 보다 기준).
            "home": self.home_state(),
        }
        if self._job_active() and self._job_id:
            extra["job_id"] = self._job_id
            extra["progress"] = self._progress
        if self._errors:
            # 항목은 evt/error 의 error 와 같은 칸이다 — cleared 만 없다(탭5 72행).
            extra["errors"] = [dict(e) for e in self._errors.values()]
        if self._last_job:
            extra["last_job"] = dict(self._last_job)
        # battery·charging: 차량 자료가 오면 넣는다(TODO — docs/todo.md).
        # query 는 재실행 금지 대상이 아니라 기록하지 않는다(늘 지금 상태로 답한다).
        self.client.publish_res(req_id, "query", 200, **extra)

    # ---- calibrate ----------------------------------------------------------
    def _do_calibrate(self, req_id: str, content: dict) -> None:
        """모재 기준 좌표계 수립 (탭1 · 탭3 ②) — **차량(AGV) 캘리브레이션**이다.

        원형 모재 정보(지름·높이)를 받아, 차량이 외벽을 따라 붙어 한 바퀴 돌며
        좌표계(맵)를 잡는다. 검사 시작 전 1회, 자리를 옮겼다 돌아왔을 때 다시
        한다. 로봇의 3점 측정은 여기가 아니라 구간 검사(start) 안에서 한다.
        차량이 움직이므로 로봇은 홈(이동 안전 자세)에 있어야 한다.
        """
        diameter = _mm(content.get("diameter"))
        height = _mm(content.get("height"))
        if diameter <= 0:
            self._refuse(req_id, "calibrate", E.BAD_REQUEST, "diameter 가 없거나 0 입니다")
            return
        if self._refuse_by_state(req_id, "calibrate"):
            return
        self._drop_prepared()
        self._calibrate_req_id = req_id
        self._calibrate_target = (diameter, height)
        self._calibrated = False
        self._answer(req_id, "calibrate", 202)
        self.activity.emit(
            f"ERUT 캘리브레이션 — 모재 지름 {diameter:g} mm, 높이 {height:g} mm "
            "— 차량이 외벽을 따라 한 바퀴 돌며 좌표계를 잡습니다.")
        self.calibration_requested.emit(diameter, height)

    def finish_calibration(self, ok: bool, error_mm: float = 0.0,
                           detail: str = "", total_length_mm: float | None = None) -> None:
        """3점 측정이 끝났다. 결과를 evt/complete 로 낸다 (탭3 8번).

        실패해도 반드시 발행한다 — 안 내면 브릿지가 영영 기다린다(탭2 8행).
        """
        req_id, self._calibrate_req_id = self._calibrate_req_id, ""
        self._paused_work = ""
        if not req_id:
            return
        if not ok:
            code, name = E_CALIBRATION_FAILED
            self.client.publish_event(
                "complete", req_id, "calibrate", 500,
                self._fault_error() or make_error(code, name, detail))
            self._record_last(req_id, "calibrate", 500)
            self.activity.emit(f"ERUT 캘리브레이션 실패를 알렸습니다 — {detail}")
            self.refresh_status()
            return
        self._calibrated = True
        extra: dict[str, Any] = {
            # 좌표계의 원점이므로 그 자신은 (0, 0) 이다(탭5 x·y 정의).
            "origin": {"x": 0, "y": 0},
            "calibration_error_mm": round(float(error_mm), 2),
        }
        total = total_length_mm if total_length_mm is not None else self.total_length_mm()
        if total:
            extra["total_length_mm"] = int(round(total))
        self.client.publish_event("complete", req_id, "calibrate", **extra)
        self._record_last(req_id, "calibrate", 200)
        note = f" ({detail})" if detail else ""
        self.activity.emit(
            f"ERUT 캘리브레이션 완료 — 오차 {error_mm:.2f} mm{note}")
        # 지름을 알게 됐다 — 한 구간 최대 가로를 자기소개에 실어 다시 보낸다.
        self.refresh_info()
        self.refresh_status()

    # ---- prepare ------------------------------------------------------------
    def _do_prepare(self, req_id: str, content: dict) -> None:
        """구간 검사 준비 (탭1 7행) — **작업 값을 확인해 두기만 한다**(2026-10-14).

        캘리브레이션을 마친 장비는 QR 영점에 서 있다. prepare 에서는 아무것도
        움직이지 않는다 — area · scan 값과 좌표계 · 크기 한계를 확인해 보관하고
        바로 evt/ready 를 낸다(activity ready). start 를 받으면 그때 차량 이동 →
        아웃트리거 고정 → 리프트 → 로봇 순으로 간다.
        """
        plan = self._validated_plan(req_id, "prepare", content)
        if plan is None:
            return
        if self._refuse_by_state(req_id, "prepare"):
            return
        if self._needs_calibration(req_id, "prepare"):
            return
        self._drop_prepared()
        self._begin_job(content, plan)
        self._prepare_req_id = req_id
        self._prepared = True
        self._answer(req_id, "prepare", 202)
        self.client.publish_event("ready", req_id, "prepare", job_id=self._job_id)
        self._ready_sent = True
        self.activity.emit(
            f"ERUT 검사 준비 ({self._job_id}) — 구역 x {plan.origin_x:.0f}~"
            f"{plan.origin_x + plan.cell_width:.0f} · y {plan.origin_y:.0f}~"
            f"{plan.origin_y + plan.cell_height:.0f} mm 를 확인했습니다. start 를 받으면 차량부터 움직입니다.")

    # ---- start --------------------------------------------------------------
    def _do_start(self, req_id: str, content: dict) -> None:
        """구간 검사 실행 (탭1 8행). 매번 그 구간의 좌표·스캔 값 전체가 온다.

        차량 이동 → 아웃트리거 고정 → 리프트 → 로봇(3점 측정 → 적심 → 스캔)을
        한 번에 한다. 준비해 둔 **같은 구간**이면 start 의 값으로 이어 가고(값이
        없으면 준비해 둔 값), 다른 구간이면(앞 구역 완료를 못 받은 채 다음 구역이
        온 경우 등) 준비를 버리고 새로 돈다 — 다른 구역 이름으로 검사하지 않게.
        prepare 없이 온 start 도 같다(「prepare 가 없으면 query 다음에 바로 start」).
        """
        job_id = str(content.get("job_id", "")).strip()
        same = self._prepared and (not job_id or job_id == self._job_id)
        if same and not content.get("area"):
            plan = self._plan
        else:
            plan = self._validated_plan(req_id, "start", content)
            if plan is None:
                return
        if self._refuse_by_state(req_id, "start"):
            return
        if self._needs_calibration(req_id, "start"):
            return
        if same:
            self._plan = plan
            self._apply_speed(content)
            self._prepared = False
        else:
            self._drop_prepared()
            self._begin_job(content, plan)
        self._start_req_id = req_id
        self._started_at = time.time()
        self._progress = 0
        self._moved_mm = 0
        self._answer(req_id, "start", 202)
        self.activity.emit(f"ERUT 구간 검사 시작 ({self._job_id}) — 차량 이동부터")
        self.job_requested.emit(plan, False)

    def _begin_job(self, content: dict, plan: GridPlan) -> None:
        self._job_id = str(content.get("job_id", "")).strip()
        self._plan = plan
        self._progress = 0
        self._moved_mm = 0
        self._contact = None           # 새 구역 — 접촉 상태는 모름에서 시작
        self._apply_speed(content)

    def _validated_plan(self, req_id: str, action: str, content: dict) -> GridPlan | None:
        """area·surface·speed 를 확인한다. 안 맞으면 400 + error 로 답하고 None."""
        plan = self._read_plan(content)
        if plan is None:
            self._refuse(req_id, action, E.BAD_REQUEST,
                         "area.start/end 의 x·y 가 없거나 구역 크기가 0 입니다")
            return None
        # 원통 이음매 (if-0.8 탭5 77행 · Q-25): start.x 는 0 이상 둘레 미만, 구역
        # 가로는 둘레 이하. end.x 는 둘레를 넘어도 된다 — 이음매를 지나 0 부터 감긴
        # 자리로 이어 훑는다(차량은 start.x 에 서고 팔이 이음매를 넘어 훑는다).
        circumference = self._circumference()
        if circumference > 0 and not (0 <= plan.origin_x < circumference
                                      and plan.cell_width <= circumference):
            self._refuse(req_id, action, E.BAD_REQUEST,
                         f"구역 x {plan.origin_x:.0f}~{plan.origin_x + plan.cell_width:.0f} mm"
                         f" — start.x 는 0 이상 둘레({circumference:.0f} mm) 미만,"
                         " 가로는 둘레 이하여야 합니다")
            return None
        # 로봇은 좌우 현 700 mm 넘게 못 움직인다(호 1000 mm 를 받아 왼쪽 끝 자세의
        # 역기구학이 안 풀려 멈춘 일이 있다 — 2026-10-02). 줄여서 하면 구간 일부를
        # 안 훑은 채 끝나므로 거절하고 최대값을 알려 준다.
        height_limit = float(self.max_area_height() or 0.0)
        if height_limit > 0 and plan.cell_height > height_limit:
            self.activity.emit(
                f"ERUT {action}: 구간 세로 {plan.cell_height:.0f} mm 는 한 구간 최대"
                f" {height_limit:.0f} mm 를 넘어 거절합니다.")
            self._refuse(req_id, action, E.AREA_HEIGHT,
                         f"구역 세로 {plan.cell_height:.0f} mm > 최대 {height_limit:.0f} mm",
                         max_height=int(height_limit))
            return None
        limit = float(self.max_area_width() or 0.0)
        if limit > 0 and plan.cell_width > limit:
            self.activity.emit(
                f"ERUT {action}: 구간 가로 {plan.cell_width:.0f} mm 는 로봇이 한 번에 훑을 수"
                f" 있는 최대 {limit:.0f} mm 를 넘어 거절합니다.")
            self._refuse(req_id, action, E.AREA_WIDTH,
                         f"구역 가로 {plan.cell_width:.0f} mm > 최대 {limit:.0f} mm",
                         max_width=int(limit))
            return None
        surface = str(content.get("surface", "")).strip()
        if surface and surface not in SURFACES:
            self._refuse(req_id, action, E.BAD_REQUEST,
                         f"합의하지 않은 surface: {surface} (지원: {', '.join(SURFACES)})")
            return None
        speed = (content.get("scan") or {}).get("speed")
        if speed is not None and _mm(speed) > SCAN_SPEED_MAX:
            self._refuse(req_id, action, E.BAD_REQUEST,
                         f"scan.speed {_mm(speed):g} mm/s > 최고 {SCAN_SPEED_MAX} mm/s",
                         max_speed=SCAN_SPEED_MAX)
            return None
        return plan

    # ---- 원점 도착 ------------------------------------------------------------
    def notify_at_origin(self) -> None:
        """로봇이 3점 측정을 마치고 시작점에 붙어 섰다(레지스터 290 = 7).

        ERUT 구간 검사(start) 중이면, 붙은 사실은 evt/contact(attached)로 이미
        나간다(app.py 가 로봇 상태로 알린다). ERUT 는 그걸 보고 물을 켜므로
        CONTACT_LEAD_MS 뒤에 대기를 풀어 적심 → ㄹ자 스캔으로 넘어간다.
        ERUT 작업이 아니면 아무것도 안 한다(RCS 단독 작업은 app.py 가 스스로 푼다).
        """
        req_id = self._start_req_id
        if not req_id:
            return
        self.activity.emit(
            f"시작점에 붙었습니다 — 물 공급을 기다렸다가 {CONTACT_LEAD_MS / 1000:g}초 뒤 적심을 시작합니다.")

        def release() -> None:
            # 그 사이 abort·장애로 작업이 바뀌었거나 일시정지했으면 풀지 않는다.
            # 일시정지 중이면 재개 뒤 로봇이 다시 원점 대기로 잡혀 새로 센다.
            if (self._start_req_id == req_id
                    and self.sequencer.state is not SequencerState.PAUSED):
                self.scan_go_requested.emit()

        self._after(CONTACT_LEAD_MS, release)

    def clear_at_origin(self) -> None:
        """로봇이 대기에서 풀렸다. 다음 도착까지 초기화한다."""
        self._at_origin = False

    # ---- 진행률 · 완료 -------------------------------------------------------
    def _position(self, robot: dict | None = None) -> tuple[dict, dict]:
        """(pos, location). pos 는 표준 칸(검사면 좌표 정수 mm), location 은 3S 확장.

        좌표 하나로는 모자라다 — 브릿지가 앞 구역 완료를 못 받은 채 다음 구역을
        보냈을 때, 어느 구역·어느 자리의 값인지 가를 수 있게 격자 이름·차량
        위치·리프트 높이·격자 안 로봇 위치를 같이 싣는다(모르는 칸은 무시된다).
        """
        motion = self.motion_state() or {}
        if robot is None:
            robot = self.position_state() or {}
        arc = float(robot.get("arc_mm") or 0.0)
        row = float(robot.get("row_mm") or 0.0)
        plan = self._plan
        ox, oy = (plan.origin_x, plan.origin_y) if plan else (0.0, 0.0)
        x = ox + arc
        circumference = self._circumference()
        if circumference > 0:
            # 이음매를 넘은 자리는 감긴 자리(0 이상 둘레 미만)로 낸다(탭10 32번).
            x %= circumference
        pos = {"x": int(round(x)), "y": int(round(oy + row))}
        location = {
            "cell": self._cell_label(),
            "vehicle_mm": int(round(float(motion.get("moved") or 0.0))),
            "lift_mm": int(round(float(motion.get("lift_height") or 0.0))),
            "robot": {"x": int(round(arc)), "y": int(round(row))},
        }
        return pos, location

    def _cell_label(self) -> str:
        """이 구역이 전체 격자에서 몇 열·몇 행인가 (1A, 2B …).

        ERUT 는 구역 하나씩 보내므로 시퀀서 안에서는 늘 1A 다. area 원점을
        구간 길이로 나눠 전체 격자에서의 자리를 센다(GridPlan.section_column).
        """
        plan = self._plan
        if plan is None:
            return self.sequencer.current_cell() if self.sequencer.plan else ""
        return cell_label(plan.section_column, plan.section_row)

    def _publish_progress(self) -> None:
        """구간 검사 중 진행률 (탭2 9행, QoS 0). 0~100 정수, 뒤로 가지 않는다."""
        if not self._start_req_id or self.activity_state() != "running":
            return
        robot = self.position_state() or {}
        value = robot.get("progress")
        if value is not None:
            self._progress = max(self._progress, min(100, int(value)))
        # moved_mm(선택, 표시용) = 이 구간에서 지금까지 훑은 거리 — 끝나면
        # evt/complete 의 scanned_distance_mm 가 된다(탭3 예: 45 % · 2100 → 4600).
        # 예전엔 차량 위치를 실어 첫 구간(차량 0 mm)에서 늘 0 이었다. 왼쪽으로
        # 가는 줄에서는 로봇 값이 잠깐 줄 수 있어 뒤로 가지 않게 잡는다.
        scanned = robot.get("scanned_mm")
        if scanned is not None:
            self._moved_mm = max(getattr(self, "_moved_mm", 0), int(round(float(scanned))))
        pos, location = self._position(robot)
        self.client.publish_progress(
            self._start_req_id, "start", job_id=self._job_id,
            progress=self._progress, activity="running",
            moved_mm=getattr(self, "_moved_mm", 0), pos=pos, location=location)

    def on_cell_changed(self, *_args) -> None:
        """셀이 넘어갈 때 진행률을 한 번 더 알린다(주기 발행과 같은 내용)."""
        self._publish_progress()

    def on_job_complete(self) -> None:
        """구간을 다 돌았다 (탭3 16번). 완료는 처음 start 의 req_id 로 낸다."""
        if not self._start_req_id:
            return
        req_id, self._start_req_id = self._start_req_id, ""
        self._progress = 100
        robot = self.position_state() or {}
        pos, location = self._position(robot)
        scanned = robot.get("scanned_mm")
        if scanned is None:
            plan = self._plan
            scanned = plan.cell_width if plan else 0
        self.client.publish_event(
            "complete", req_id, "start",
            job_id=self._job_id,
            scanned_distance_mm=int(round(float(scanned))),
            duration_ms=int((time.time() - self._started_at) * 1000),
            pos=pos, location=location,
        )
        self._record_last(req_id, "start", 200, job_id=self._job_id)
        self._prepare_req_id = ""
        self._ready_sent = False
        self._prepared = False
        self.activity.emit("ERUT 구간 검사 완료를 발행했습니다.")
        self.refresh_status()

    def _record_last(self, req_id: str, action: str, code: int,
                     job_id: str = "") -> None:
        """query 의 last_job {job_id, status_code, progress} (탭5 73행 · if-0.9).

        탭5 초안 A안대로 action 을 함께 싣는다 — 무엇의 결과인지 가를 수 있게.
        """
        self._last_job = {"job_id": job_id or req_id, "action": action,
                          "status_code": code, "progress": self._progress
                          if action == "start" else (100 if code < 300 else 0)}

    # ---- pause / resume / abort ---------------------------------------------
    def _do_pause(self, req_id: str, content: dict) -> None:
        """일시정지. 준비·캘리브레이션 중에도 받는다 — 그때도 로봇은 움직인다.

        200 은 「받았다」다. 섰다는 것은 evt/status 의 activity=paused 로 알린다.
        """
        self._answer(req_id, "pause", 200)
        if self._calibrate_req_id and not self._paused_work:
            self._paused_work = "calibrate"
            self.calibration_stop_requested.emit()
            self.activity.emit("ERUT 일시정지 — 캘리브레이션 주행을 멈췄습니다.")
        elif self._mark_req_id and not self._paused_work:
            # 그 자리에 선다 — 점에서 기다리던 중이면 그대로, 가던 중이면 거기서
            # (if-0.7 마킹 ② ⑥). 재개하면 기다리던 점의 mark_ready 를 다시 낸다.
            self._paused_work = "mark"
            self.mark_pause_requested.emit()
        elif self._home_req_id:
            self.robot_stop_requested.emit()
            self.finish_home(False, "INTERRUPTED")
        elif self.sequencer.state not in _SEQ_IDLE:
            self.pause_requested.emit()
        self._at_origin = False

    def _do_resume(self, req_id: str, content: dict) -> None:
        """멈춘 자리에서 이어 간다. 완료는 처음 요청의 req_id 로 낸다."""
        if self._paused_work == "mark":
            if not self.pause_settled():
                self._refuse(req_id, "resume", E.BUSY, "아직 서는 중입니다")
                return
            if not self.pause_holds_position():
                self._refuse(req_id, "resume", E.NOT_RESUMABLE, "멈춘 자리를 지키지 못했습니다")
                return
            self._paused_work = ""
            self._answer(req_id, "resume", 202)
            self.mark_resume_requested.emit()
            return
        if self._paused_work == "calibrate":
            if not self.pause_settled():
                self._refuse(req_id, "resume", E.BUSY, "차량이 아직 서는 중입니다")
                return
            self._paused_work = ""
            self._answer(req_id, "resume", 202)
            self.calibration_resume_requested.emit()
            return
        if self.sequencer.state is SequencerState.PAUSED:
            if not self.pause_settled():
                # 아직 서는 중이다(activity 는 running) — 탭9: running 에 resume 은 409 E6101.
                self._refuse(req_id, "resume", E.BUSY, "아직 서는 중입니다")
                return
            if not self.pause_holds_position():
                # 자리를 못 지켰다 — 이어 가지 않는다. abort 뒤 처음부터(탭9·탭7 S-3 4b).
                self._refuse(req_id, "resume", E.NOT_RESUMABLE, "멈춘 자리를 지키지 못했습니다")
                self.activity.emit("ERUT 재개 거절(E6102) — 멈춘 자리를 지키지 못했습니다."
                                   " 구간을 처음부터 다시 해야 합니다.")
                return
            self._answer(req_id, "resume", 202)
            self.resume_requested.emit()
            return
        activity = self.activity_state()
        if activity in ("error", "estop"):
            self._refuse(req_id, "resume", E.FAULT_ACTIVE, "장애를 먼저 풀어야 합니다")
        elif activity in ("idle", "ready"):
            # 멈춰 둔 작업이 없다 (탭9 — idle · ready 의 resume 은 409 E6102).
            self._refuse(req_id, "resume", E.NOT_RESUMABLE, "멈춘 작업이 없습니다")
        else:
            self._refuse(req_id, "resume", E.BUSY, f"지금 {activity}")

    def _do_abort(self, req_id: str, content: dict) -> None:
        """작업자 종료 (탭4 D-3). job_id 없이 와도 지금 하는 일을 멈춘다.

        abort 된 작업은 완료를 내지 않는다. 캘리브레이션·마킹·홈 중이어도 같다.
        """
        self._answer(req_id, "abort", 200)
        self._forget_work()
        self.robot_stop_requested.emit()
        self.abort_requested.emit()

    def _forget_work(self) -> None:
        self._prepare_req_id = self._start_req_id = ""
        self._calibrate_req_id = self._mark_req_id = self._home_req_id = ""
        self._ready_sent = False
        self._prepared = False
        self._at_origin = False
        self._paused_work = ""
        self._home_timer.stop()

    # ---- home 명령 (3S 확장 — if-0.5 는 확인 신호 evt/home 만 넣었다) ------------
    def _do_home(self, req_id: str, content: dict) -> None:
        """로봇을 홈으로 보낸다. 202 → 홈에 닿으면 evt/complete(action=home).

        if-0.5 는 홈 **확인 신호**(evt/home)만 표준에 넣었고, 홈으로 보내는
        명령은 없다(장비가 이동 전·쉬는 동안 스스로 간다). 이 명령은 3S 확장으로
        남겨 둔다 — 표준의 작업 요청 모양(202 → complete)을 따른다.
        """
        if self.activity_state() not in ("idle",) or self.robot_busy():
            self._refuse(req_id, "home", E.BUSY, HOME_BUSY_TEXT)
            self.notify(*MSG_HOME_NOT_ALLOWED, HOME_BUSY_TEXT,
                        req_id=req_id, action="home")
            return
        self._answer(req_id, "home", 202)
        self._home_req_id = req_id
        self._home_timer.start()
        self.activity.emit("ERUT 요청으로 로봇을 홈으로 보냅니다.")
        self.home_requested.emit()

    def home_arrived(self) -> None:
        """로봇이 홈에 닿았다(레지스터 276 = 1)."""
        if self._home_req_id:
            self.finish_home(True)
        else:
            self.refresh_status()

    def finish_home(self, ok: bool, reason: str = "") -> None:
        req_id, self._home_req_id = self._home_req_id, ""
        self._home_timer.stop()
        if not req_id:
            return
        if ok:
            self.client.publish_event("complete", req_id, "home", home="home")
            self._record_last(req_id, "home", 200)
            self.activity.emit("ERUT 에 홈 도착을 알렸습니다.")
        else:
            self.client.publish_event("complete", req_id, "home", 500,
                                      make_error("E2002", detail=reason))
            self._record_last(req_id, "home", 500)
            self.activity.emit(f"ERUT 홈 이동 실패를 알렸습니다 — {reason}")
        self.refresh_status()

    # ---- reset --------------------------------------------------------------
    def _do_reset(self, req_id: str, content: dict) -> None:
        """장애 해제 (탭4 D-2). 여러 번 와도 결과가 같다(멱등).

        걸린 장애가 없으면 200 으로 답하고 아무것도 안 한다 — 409 로 거절하면
        받는 쪽이 「현장 조치 필요」로 오판한다. 풀리면 장애마다 cleared=true 를
        한 번씩 내고 activity 가 idle 이 된다(비상정지 때 하던 구간은 이미
        끝난 것으로 처리했다 — if-0.4).
        """
        # home 기능이 있는 장비는 reset 으로 home 까지 거둔다 — 걸린 장애가 없어도
        # (if-0.8 탭5 47행). 돌거나 멈춰 둔 작업이 있으면 거두지 않는다(탭9).
        fold = self.home_state() != "home" and bool(self.can_fold())
        if not self._errors and not fold:
            self._answer(req_id, "reset", 200)
            self.activity.emit("ERUT 리셋 요청 (해제할 장애 없음 · 이미 home)")
            return
        self._answer(req_id, "reset", 202)
        cleared = self.clear_errors()          # 풀리면 앱이 팔을 거둔다(errors_cleared)
        if cleared:
            self.activity.emit(f"ERUT 리셋 — 장애 해제: {', '.join(cleared)}")
        elif fold:
            self.activity.emit("ERUT 리셋 — 펴진 팔을 home 으로 거둡니다.")
            self.fold_requested.emit()

    # ---- mark (mark_positioning — 마커는 ERUT 것) -----------------------------
    def _do_mark(self, req_id: str, content: dict) -> None:
        """결함 자리 마킹 (탭3 ⑤ + 탭2 초안 「마킹 주체」 ②).

        마커는 ERUT 미니 PC 의 것이다. 장비는 점까지 옮겨 가 멈추기만 한다:
          점에 붙음 → evt/mark_ready{req_id, point_id}
          ERUT 가 찍음 → req/mark_next{req_id, point_id, marked} → 다음 점
          다 끝 → evt/complete{marked[], failed[]}
        """
        points = content.get("points")
        if not isinstance(points, list) or not points:
            self._refuse(req_id, "mark", E.BAD_REQUEST, "points 가 없습니다")
            return
        clean = []
        for pt in points:
            try:
                clean.append({"id": str(pt["id"]), "x": float(pt["x"]),
                              "y": float(pt["y"])})
            except (KeyError, TypeError, ValueError):
                self._refuse(req_id, "mark", E.BAD_REQUEST, "points 의 id·x·y 를 읽지 못했습니다")
                return
        # marker 가 없거나 device 면 장비가 찍는 마킹(①)이다 — 3S 는 마커가
        # ERUT 것이라(mark_positioning ②) ① 은 없다 → 501 (if-0.7 탭2 44행 ①).
        marker = str(content.get("marker", "") or "device").strip().lower()
        if marker != "erut":
            # 장비가 가진 마커로 찍는 방식(marking)은 지원하지 않는다.
            self._refuse(req_id, "mark", E.NOT_IMPLEMENTED,
                         "장비 마커로 찍는 마킹(marking)은 없습니다 — marker=erut 만")
            return
        if self._refuse_by_state(req_id, "mark"):
            return
        if self._needs_calibration(req_id, "mark"):
            return
        self._drop_prepared()
        self._answer(req_id, "mark", 202)
        self._mark_req_id = req_id
        self._mark_ids = [p["id"] for p in clean]
        self.activity.emit(f"ERUT 마킹 요청 {len(clean)}점 — 차례로 이동합니다.")
        self.mark_requested.emit(clean)

    def mark_point_ready(self, point_id: str) -> None:
        """로봇이 마킹 자리에 붙어 섰다. ERUT 에 쏘라고 알린다."""
        if not self._mark_req_id:
            return
        pos, location = self._position()
        self.client.publish_event("mark_ready", self._mark_req_id, "mark",
                                  point_id=str(point_id), pos=pos, location=location)

    def _do_mark_next(self, req_id: str, content: dict) -> None:
        """ERUT 가 점 하나를 찍었다(또는 못 찍었다) — 다음 점으로."""
        point_id = str(content.get("point_id", "")).strip()
        key = f"mark_next:{req_id}#{point_id}"
        waiting = str(self.mark_waiting_point() or "") if self._mark_req_id else ""
        if not self._mark_req_id or req_id != self._mark_req_id or not point_id:
            # 이 마킹에서 기다리는 점이 아니다(없는 마킹 · 다른 req_id 포함).
            self._refuse(req_id, "mark_next", E.WRONG_POINT, "기다리는 점이 아닙니다",
                         key=key, waiting_point_id=waiting or None)
            return
        if self._paused_work == "mark":
            # 멈춰 있다 — 재개해 mark_ready 를 다시 낸 뒤에 받는다(⑥).
            self._refuse(req_id, "mark_next", E.BUSY, "일시정지 중입니다", key=key)
            return
        if point_id != waiting:
            # 기다리는 점이 아니다 — 거절하고 그 자리에 그대로 선다(if-0.7 ④).
            self._refuse(req_id, "mark_next", E.WRONG_POINT,
                         f"기다리는 점은 {waiting or '(없음)'} 입니다",
                         key=key, waiting_point_id=waiting or None)
            return
        marked = content.get("marked", True)
        if isinstance(marked, str):
            marked = marked.strip().lower() in ("true", "1", "yes", "ok")
        self._reply(key, req_id, "mark_next", 200)
        self.mark_next_requested.emit(point_id, bool(marked))

    def finish_mark(self, marked: list, failed: list, code: int | None = None) -> None:
        """마킹을 다 돌았다 (탭3 19번). 일부 실패도 200 — 전부 실패면 5xx.

        장애로 접었으면(code=500) 그때까지의 marked[] · failed[] 를 싣고 까닭은
        걸린 장애의 번호다(마킹 ② ⑩).
        """
        req_id, self._mark_req_id = self._mark_req_id, ""
        if not req_id:
            return
        if code is None:
            code = 500 if failed and not marked else 200
        error = None
        if code >= 300:
            name_code, name = E_MARK_FAILED
            error = self._fault_error() or make_error(
                name_code, name, f"찍은 점 {len(marked)} · 못 찍은 점 {len(failed)}")
        self.client.publish_event("complete", req_id, "mark", code, error,
                                  marked=list(marked), failed=list(failed))
        self._record_last(req_id, "mark", code)
        self.activity.emit(
            f"ERUT 마킹 완료를 발행했습니다 (성공 {len(marked)}, 실패 {len(failed)}).")
        self.refresh_status()

    # ---- 우리 쪽에서 끊었을 때 ------------------------------------------------
    def interrupt_active_job(self, code: int = 500, error: dict | None = None) -> None:
        """ERUT 가 시킨 일을 **우리 쪽에서** 끊었다(작업자 정지·장애·홈 이동).

        ERUT 가 모르는 사이에 끊겼으므로 반드시 알린다 — 안 오면 브릿지가 계속
        기다린다(탭2 8행). 202 로 받은 일은 res 가 아니라 evt/complete 에 싣는다:
          start      → evt/complete(action=start)   5xx
          prepare    → evt/complete(action=prepare) 5xx  (evt/ready 는 성공 전용)
          calibrate  → evt/complete(action=calibrate) 5xx
          mark       → evt/complete(action=mark)    5xx {marked, failed}
          home       → evt/complete(action=home)    5xx
        까닭(error)은 걸린 장애의 번호다 — 장애가 아니라 RCS 화면에서 작업자가
        멈췄으면 E9401 STOPPED_BY_OPERATOR.
        """
        if error is None:
            op_code, op_name = E_OPERATOR_STOPPED
            error = self._fault_error() or make_error(
                op_code, op_name, "RCS 에서 작업을 정지했습니다")
        if self._start_req_id:
            self.client.publish_event(
                "complete", self._start_req_id, "start", code, error,
                job_id=self._job_id)
            self._record_last(self._start_req_id, "start", code, job_id=self._job_id)
        elif self._prepare_req_id and not self._ready_sent:
            self.client.publish_event(
                "complete", self._prepare_req_id, "prepare", code, error,
                job_id=self._job_id)
            self._record_last(self._prepare_req_id, "prepare", code, job_id=self._job_id)
        if self._calibrate_req_id:
            self.client.publish_event(
                "complete", self._calibrate_req_id, "calibrate", code, error)
            self._record_last(self._calibrate_req_id, "calibrate", code)
        if self._mark_req_id:
            self.client.publish_event(
                "complete", self._mark_req_id, "mark", code, error,
                marked=[], failed=list(self._mark_ids))
            self._record_last(self._mark_req_id, "mark", code)
        if self._home_req_id:
            self.client.publish_event(
                "complete", self._home_req_id, "home", code, error)
        had_job = bool(self._start_req_id or self._prepare_req_id
                       or self._calibrate_req_id or self._mark_req_id or self._home_req_id)
        self._forget_work()
        if had_job:
            self.activity.emit(f"ERUT 작업을 이쪽에서 중단했다고 알렸습니다({code}).")
        self.refresh_status()

    # ------------------------------------------------------------ 알림
    def notify(self, code: str, name: str, text: str, **extra) -> None:
        """장애가 아닌 안내를 evt/message 로 알린다 (3S 확장 — 표준에 없다)."""
        self.client.publish_message(code, name, text, **extra)
        self.message_published.emit(code, text)
        self.activity.emit(f"ERUT 알림: {code} {text}")

    # ------------------------------------------------------------ 장애
    def raise_error(self, fields: dict) -> None:
        """장애를 `evt/error` 로 알린다 (탭2 10행). 해제면 cleared=true.

        해제는 발생 때와 **같은 code** 에 `cleared: true` 로 낸다(level·recovery 는
        발생 때 값 그대로). 옛 시험 도구가 보내는 `-CLEAR` 접미사도 해제로 읽는다.

        fields: code · name(옛 이름 message 도 읽는다) · level · recovery · detail ·
        cleared. 그 밖의 칸(예: E3001 의 probe)은 error 객체 안에 그대로 싣는다.

        level 이 stop·estop 이면 로봇을 세우고, **하던 일을 실패로 끝낸다** —
        evt/error 와 따로 evt/complete 에 5xx 를 낸다(탭4 B-2). 비상정지 때 하던
        구간은 끝난 것으로 보고 이어 가지 않는다(if-0.4). 풀린 뒤에는 브릿지가
        처음 순서(query → calibrate → prepare → start)로 새로 보낸다.
        """
        code = str(fields.get("code", "E9999")).strip()
        name = str(fields.get("name") or fields.get("message")
                   or E.NAMES.get(code, "UNKNOWN")).strip()
        cleared = bool(fields.get("cleared", False))
        if code.endswith("-CLEAR"):
            code, cleared = code[: -len("-CLEAR")], True
        if cleared:
            self._clear(code)
            return

        level = str(fields.get("level", "warning")).strip()
        recovery = str(fields.get("recovery", "manual")).strip()
        detail = fields.get("detail")
        error: dict[str, Any] = {"code": code, "name": name, "level": level,
                                 "recovery": recovery}
        if detail:
            error["detail"] = str(detail)
        known = {"code", "name", "message", "level", "recovery", "detail", "cleared"}
        error.update({k: v for k, v in fields.items() if k not in known and v is not None})
        job_id = self._job_id if self._job_id and self._job_active() and level != "warning" else ""
        self._errors[code] = error
        self.client.publish_error({**error, "cleared": False}, job_id)
        self.error_published.emit(code, name, level)
        self.activity.emit(f"장애 통보: {code} {name} ({level})")

        if level in ("stop", "estop"):
            # 장애는 로봇 자신이 아니라 차량·리프트 쪽에서도 난다 — 그때 팔이
            # 계속 벽을 훑으면 안 되므로 순회 상태와 무관하게 세운다.
            self.robot_stop_requested.emit()
            if self._mark_req_id:
                self.mark_stop_requested.emit()
            # 완료의 까닭은 이 장애의 번호 · 이름이다(탭2 8행).
            self.interrupt_active_job(500, make_error(code, name, detail or None))
            if self.sequencer.state not in _SEQ_IDLE:
                self.job_dropped.emit()
        self.refresh_status()

    def _clear(self, code: str) -> bool:
        entry = self._errors.pop(code, None)
        if entry is None:
            return False
        job_id = self._job_id if self._job_id and self._job_active() else ""
        self.client.publish_error({**entry, "cleared": True}, job_id)
        self.error_cleared.emit(code, entry["name"])
        self.activity.emit(f"장애 해제 통보: {code}")
        self.refresh_status()
        return True

    def clear_error(self, code: str) -> bool:
        """걸려 있던 장애 하나를 풀고 ERUT 에 알린다(오류 로그 화면의 '선택 해제')."""
        return self._clear(code)

    def clear_errors(self) -> list[str]:
        """걸려 있던 장애를 모두 풀고, 푼 코드를 돌려준다.

        ERUT 의 `req/reset` 과 운영자의 화면 리셋이 같은 길을 쓴다 — 조치가
        끝나면 장치가 「풀렸다」를 보내야 한다(탭5 recovery=manual).
        """
        codes = list(self._errors)
        for code in codes:
            self._clear(code)
        if codes:
            self.errors_cleared.emit(codes)
        return codes

    def error_codes(self) -> list[str]:
        """지금 걸려 있는 장애 코드 목록."""
        return list(self._errors)

    def _fault_error(self) -> dict | None:
        """작업을 멈추게 한 장애(stop · estop) — 완료의 까닭으로 싣는다. 없으면 None."""
        for entry in reversed(list(self._errors.values())):
            if entry.get("level") in ("stop", "estop"):
                return make_error(entry["code"], entry["name"], entry.get("detail"))
        return None

    def _circumference(self) -> float:
        """검사면 둘레 [mm] = π × 모재 지름. 좌표계가 서서 지름을 알 때만, 모르면 0.

        ERUT 의 x 는 이 둘레 위의 거리다(탭5 77행) — 이음매(둘레)를 넘는 x 는
        감긴 자리(x − 둘레)로 읽는다.
        """
        diameter = float(self.area_diameter() or 0.0)
        if not self._calibrated or diameter <= 0:
            return 0.0
        return math.pi * diameter

    # ------------------------------------------------------------ 도우미
    def _read_plan(self, content: dict) -> GridPlan | None:
        """요청의 `area` 로 구간 하나를 만든다 (탭1 7·8행).

            area : { start:{x,y}, end:{x,y} }   검사면 좌표 정수 mm
            scan : { pitch, speed }

        **구간 하나 = job 하나.** 크기와 위치는 area 가 정한다. 반지름·두께·
        검사장비 종류는 규격에 없는 우리 장비 값이라 RCS 설정에서 채운다
        (app.py `_fill_from_local_setup`). 예전 판의 `plan` 블록은 보지 않는다.

        `scan.pitch` 는 **구역끼리의 겹침**이다(3S 회신 2026-09-23). 구역 안의 ㄹ자
        줄 간격은 로봇이 프로브 커버(5축/8축)에 맞춰 스스로 정하고, 이 값은
        차량 이동(가로)·리프트 상승(세로) 간격에 들어간다.
        """
        try:
            area = content.get("area") or {}
            start, end = area.get("start", {}), area.get("end", {})
            x0, y0 = float(start["x"]), float(start["y"])
            x1, y1 = float(end["x"]), float(end["y"])
            scan = content.get("scan") or {}
            overlap = float(scan.get("pitch", 0) or 0)
        except (AttributeError, KeyError, TypeError, ValueError):
            return None
        width, height = abs(x1 - x0), abs(y1 - y0)
        if width <= 0 or height <= 0 or overlap < 0:
            return None
        return GridPlan(
            column_count=1, row_count=1,
            cell_width=width, cell_height=height,
            scan_overlap=0.0, pitch_x=overlap, pitch_y=overlap,
            origin_x=min(x0, x1), origin_y=min(y0, y1),
        )

    def _apply_speed(self, content: dict) -> None:
        """scan.speed [mm/s] 를 로봇 속도 비율로 바꿔 건다.

        로봇의 스캔 속도는 100 mm/s 고정이고(dus_init), 컨트롤러 비율(%)로만
        줄일 수 있다. 그래서 40 mm/s 는 40 % 다. 100 을 넘는 값은 앞에서 400 으로
        거절했다. `speed_ratio`(%)가 따로 오면 그 값을 쓴다(3S 확장).
        """
        scan = content.get("scan")
        if not isinstance(scan, dict):
            return
        if scan.get("speed_ratio") is not None:
            percent = int(_mm(scan.get("speed_ratio")))
        elif scan.get("speed") is not None:
            percent = int(round(_mm(scan.get("speed")) / SCAN_SPEED_MAX * 100))
        else:
            return
        self.speed_requested.emit(max(2, min(100, percent)))

    def _after(self, delay_ms: int, callback) -> None:
        """지연 후 한 번 실행. 타이머를 붙잡아 두어 중간에 사라지지 않게 한다."""
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(delay_ms)
        timer.timeout.connect(callback)
        timer.timeout.connect(lambda: self._pending.pop(id(timer), None))
        self._pending[id(timer)] = timer
        timer.start()

    def _on_erut_online(self, online: bool) -> None:
        """브릿지가 사라지면 하던 일을 스스로 일시정지한다(탭2 18행).

        명령하고 지켜볼 쪽이 없고 물도 못 켠다 — 기록되지 않는 헛검사를 막는다.
        다시 붙으면 브릿지가 query 로 상태를 맞춘다.
        """
        if online:
            self.activity.emit("ERUT 브릿지가 온라인입니다.")
            return
        self.activity.emit("ERUT 브릿지가 오프라인입니다. 하던 일을 일시정지합니다.")
        if self._calibrate_req_id and not self._paused_work:
            self._paused_work = "calibrate"
            self.calibration_stop_requested.emit()
        elif self._mark_req_id and not self._paused_work:
            self._paused_work = "mark"
            self.mark_pause_requested.emit()
        elif self.sequencer.state not in _SEQ_IDLE:
            self.pause_requested.emit()
        self.refresh_status()
