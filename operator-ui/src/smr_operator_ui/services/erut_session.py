"""ERUT 요청 9개를 처리하는 프로토콜 계층.

**지금은 로봇만 진짜다.** 실제 장비가 붙어 있는 것은 협동로봇뿐이라,
`start` 계열만 `JobSequencer` 를 통해 로봇을 실제로 움직이고 나머지
(캘리브레이션·마킹·배터리)는 시험용으로 답만 돌려준다. 어디까지가 진짜인지는
`REAL` / `TEST` 주석으로 표시해 두었다 — 실장비가 붙을 때 그 자리만 바꾸면 된다.

흐름은 `mqtt_test/ERUT-3S_MQTT_인터페이스_*.xlsx` 탭3(정상 시나리오)을 따른다.

    query     → res 200 (상태 전체)
    calibrate → res 202 → evt/complete 200 (origin, calibration_error_mm)
    prepare   → res 202 → evt/ready 200
    start     → res 202 → evt/progress … → evt/complete 200
    pause/resume/abort → res 200 (즉시)
    reset     → res 200 → state idle
    mark      → res 202 → evt/complete 200 (marked[], failed[])
"""

from __future__ import annotations

import time
from typing import Any, Callable

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from smr_operator_ui.services.erut_client import ErutClient
from smr_operator_ui.services.job_sequencer import GridPlan, SequencerState

# 시험용 값. 실장비가 붙으면 실제 측정값으로 바꾼다.
TEST_BATTERY = 85
TEST_CHARGING = False
TEST_CALIBRATION_ERROR_MM = 0.4
# 캘리브레이션·마킹은 실제 동작이 없어 이 시간 뒤 완료로 답한다.
TEST_WORK_MS = 1500
# evt/status 재발행 주기. 규격(탭5)이 30~60초라 그 안쪽으로 잡는다.
STATUS_PERIOD_MS = 30_000

# 시퀀서 상태 → ERUT state (탭5 값 정의의 7값)
_STATE_MAP = {
    SequencerState.IDLE: "idle",
    SequencerState.SECURING: "running",
    SequencerState.LEVELING: "running",
    SequencerState.MOVING_LIFT: "running",
    SequencerState.SCANNING: "running",
    SequencerState.RETRACTING: "running",
    SequencerState.MOVING_AMR: "running",
    SequencerState.PAUSED: "paused",
    SequencerState.DONE: "idle",
    SequencerState.STOPPED: "idle",
}


class ErutSession(QObject):
    """ERUT 요청을 받아 로봇(진짜)과 시험용 응답으로 나눠 처리한다."""

    activity = pyqtSignal(str)
    # 작업 계획이 확정되어 순회를 시작해 달라는 요청 (app.py 가 시퀀서에 넘긴다)
    job_requested = pyqtSignal(object)      # GridPlan
    pause_requested = pyqtSignal()
    resume_requested = pyqtSignal()
    abort_requested = pyqtSignal()
    speed_requested = pyqtSignal(int)
    # 장애로 로봇을 세워 달라는 요청. 순회(시퀀서)와 별개로 나간다 —
    # 작업 중이 아니어도(수동 조작 중이어도) 비상 장애는 로봇을 세워야 한다.
    robot_stop_requested = pyqtSignal()
    # 로봇이 원점에서 멈춰 기다리는 것을 풀어 달라는 요청 (레지스터 267).
    # ERUT 가 도착 통보(evt/ready, stage=at_origin)를 받고 "작업 시작"을
    # 다시 보내면 여기로 온다.
    scan_go_requested = pyqtSignal()
    # 마킹 점들을 돌아 달라는 요청. [{id, x, y}, ...] (검사면 좌표 mm)
    mark_requested = pyqtSignal(list)

    def __init__(self, client: ErutClient, sequencer,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.client = client
        self.sequencer = sequencer
        self._calibrated = False
        self._job_id = ""
        self._start_req_id = ""
        self._started_at = 0.0
        # 로봇이 원점에 도착해 시작 신호를 기다리는 중인가.
        self._at_origin = False
        # 가상 차량·리프트 값을 주는 함수. app.py 가 물려 준다.
        # 규격 탭5 의 query.lift_height 와 progress.moved 자리를 채운다.
        self.motion_state: Callable[[], dict] = dict
        # 원점 도착 시 evt/ready 를 낼 prepare 요청의 req_id.
        self._ready_req_id = ""
        # 마킹 중인 요청의 req_id 와 진행 여부.
        self._mark_req_id = ""
        self._marking = False
        # 같은 req_id 를 다시 받으면 재실행하지 않고 이전 응답을 되돌려준다(탭2 중요사항).
        self._handled: dict[str, tuple[int, str]] = {}
        self._pending: dict[str, QTimer] = {}

        client.request_received.connect(self.handle_request)
        client.erut_online_changed.connect(self._on_erut_online)
        # 접속은 비동기라 start() 시점엔 아직 붙기 전이다. 그때 발행하면
        # 그냥 버려지고, 브로커에는 지난번 LWT 의 offline 이 남아 있게 된다.
        # 그래서 실제로 붙은 뒤에 online 을 올린다.
        client.connected_changed.connect(self._on_broker_connected)
        client.test_error_injected.connect(self.raise_error)

        # 상시 상태 발행 — evt/status 를 주기적으로 다시 낸다(규격 30~60초).
        # LWT 는 1회성이라 프로그램이 굳은 경우를 못 잡는데, 이 갱신이
        # 끊기는 것으로 상대가 굳음을 판정한다.
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(STATUS_PERIOD_MS)
        self._status_timer.timeout.connect(self.publish_status)

    # ------------------------------------------------------------ 상태
    def start(self) -> None:
        """상시 발행을 시작한다. online 통보는 접속이 붙은 뒤에 나간다."""
        if self.client.is_connected:
            self.publish_status()
        self._status_timer.start()

    def _on_broker_connected(self, connected: bool) -> None:
        if connected:
            self.publish_status()

    def stop(self) -> None:
        self._status_timer.stop()

    def publish_status(self) -> None:
        """접속 생존 상태를 다시 낸다. 규격상 접속 시 + 30~60초 주기."""
        self.client.publish_status("online", TEST_BATTERY, TEST_CHARGING)

    def robot_state(self) -> str:
        """지금 장치 상태를 ERUT 의 7값으로 돌려준다.

        원점에서 start 를 기다리는 동안은 `ready` 다 — 탭5 "검사 준비 완료
        (start 대기)". 시퀀서는 이때 이미 스캔 단계라 그대로 두면 running
        으로 나간다.
        """
        if self._at_origin:
            return "ready"
        if self._marking:
            return "running"         # 마킹도 장치가 움직이는 작업이다
        return _STATE_MAP.get(self.sequencer.state, "idle")

    # ------------------------------------------------------------ 요청 처리
    def handle_request(self, action: str, content: dict) -> None:
        req_id = str(content.get("req_id", "")).strip()
        if not req_id:
            self.activity.emit(f"ERUT {action}: req_id 가 없어 거절했습니다.")
            self.client.publish_res("", action, 400, "MISSING_REQ_ID")
            return

        # 같은 요청을 다시 받으면 재실행하지 않고 이전 응답만 되풀이한다.
        if req_id in self._handled:
            code, message = self._handled[req_id]
            self.client.publish_res(req_id, action, code, message)
            self.activity.emit(f"ERUT {action}: 이미 처리한 req_id — 이전 응답 재발행")
            return

        handler = getattr(self, f"_do_{action}", None)
        if handler is None:
            self._reply(req_id, action, 400, "UNKNOWN_ACTION")
            return
        handler(req_id, content)

    def _reply(self, req_id: str, action: str, code: int, message: str,
               **extra) -> None:
        self._handled[req_id] = (code, message)
        self.client.publish_res(req_id, action, code, message, **extra)

    def _needs_calibration(self, req_id: str, action: str) -> bool:
        """캘리브레이션이 무효면 요청을 거절하고 True 를 돌려준다.

        규격 탭4 D-4: 위치 이탈·재배치로 좌표계가 무효해지면
        `calibrated=false` 이고, 그 상태에서는 **prepare/start 를 보내도
        로봇이 거절해야 한다**. 좌표계 없이 훑으면 스캔 좌표가 어디를
        가리키는지 알 수 없어서, 받아 놓고 엉뚱한 데를 검사하는 것보다
        거절하는 편이 낫다.

        428 은 탭5 코드 표의 4xx 대역이다 — 즉시 실패이고 ERUT 는 같은
        요청을 재시도하지 않는다. 대신 재캘리브레이션을 안내한다.
        """
        if self._calibrated:
            return False
        self._reply(req_id, action, 428, "CALIBRATION_REQUIRED")
        self.activity.emit(
            f"ERUT {action} 거절 — 캘리브레이션이 무효합니다(428). 재캘리브레이션이 필요합니다.")
        return True

    def invalidate_calibration(self, reason: str = "") -> None:
        """좌표계를 무효로 돌린다 (규격 탭4 D-4).

        충전·교정룸 복귀나 재도킹처럼 로봇이 자리를 옮기면 원점이 더는
        맞지 않는다. 그 신호가 아직 없어서 자동으로 부르는 곳은 없고,
        도킹·복귀 경로가 생기면 거기서 부르면 된다.
        """
        if not self._calibrated:
            return
        self._calibrated = False
        note = f" — {reason}" if reason else ""
        self.activity.emit(f"캘리브레이션을 무효로 표시했습니다{note}.")

    # ---- query : REAL (상태) + TEST (배터리) --------------------------------
    def _do_query(self, req_id: str, content: dict) -> None:
        seq = self.sequencer
        extra: dict[str, Any] = {
            "state": "error" if self.error_codes() else self.robot_state(),
            "resumable": seq.state is SequencerState.PAUSED,
            "calibrated": self._calibrated,
            "battery": TEST_BATTERY,          # TEST
            "charging": TEST_CHARGING,        # TEST
        }
        if self._job_id:
            extra["job_id"] = self._job_id
        if self.error_codes():
            extra["errors"] = self.error_codes()
        # 규격 탭5: lift_height 는 "리프트 있는 장치만, 화면 표시용" 선택 필드다.
        lift = self.motion_state().get("lift_height")
        if lift is not None:
            extra["lift_height"] = lift
        plan = seq.plan
        if plan is not None and seq.state not in (
            SequencerState.IDLE, SequencerState.DONE, SequencerState.STOPPED
        ):
            extra["progress"] = int(seq.cell_ordinal() / (plan.total_cells or 1) * 100)
        # query 는 응답 하나로 끝난다. 재실행 금지 대상이 아니라 기록하지 않는다.
        self.client.publish_res(req_id, "query", 200, "OK", **extra)

    # ---- calibrate : TEST ---------------------------------------------------
    def _do_calibrate(self, req_id: str, content: dict) -> None:
        self._reply(req_id, "calibrate", 202, "ACCEPTED")
        self.activity.emit("ERUT 캘리브레이션 요청 (시험용 — 실제 원점 교정 없음)")
        self._after(TEST_WORK_MS, lambda: self._finish_calibrate(req_id))

    def _finish_calibrate(self, req_id: str) -> None:
        self._calibrated = True
        self.client.publish_event(
            "complete", req_id, "calibrate",
            origin={"x": 0, "y": 0},                        # TEST
            calibration_error_mm=TEST_CALIBRATION_ERROR_MM,  # TEST
        )
        self.activity.emit("ERUT 캘리브레이션 완료를 발행했습니다 (시험용).")

    # ---- prepare : REAL — 물리 준비를 실제로 시킨다 --------------------------
    def _do_prepare(self, req_id: str, content: dict) -> None:
        """구간 검사 준비. 규격 탭1: "시간 걸리는 물리 준비(접근·자세·프로브)".

        우리 로봇에게 그 준비란 3점 프로브 측정 + 원점 복귀다. 그게 끝나
        원점에 서면(레지스터 290 = 7) `evt/ready` 를 낸다 — 규격 탭5 에서
        prepare 의 완료 통보가 evt/ready 로 못박혀 있고, 탭1 은 그것을
        "start 를 보내도 되는 시점을 알려주는 **게이트**"라고 부른다.

        예전에는 여기서 타이머로 2초 뒤에 ready 를 흘려보냈다(시험용). 그러면
        로봇이 아직 벽을 찾는 중인데도 ERUT 가 start 를 보내 버린다.

        **구간마다 온다고 본다.** 규격 탭1 은 prepare 를 "최초 1회"로 적어
        뒀지만, 구간마다 원점에서 프로브 확인을 받아야 하므로 협력사에
        구간마다 보내 달라고 요청했다. 그 전까지도 멈추지 않도록, start 만
        오는 경우의 대비책을 _do_start 에 남겨 둔다.
        """
        if self._needs_calibration(req_id, "prepare"):
            return
        # 앞 구간이 아직 도는 중이면 받지 않는다(탭5 409 BUSY). 받으면
        # 도는 중인 구간을 버리고 새 구간으로 갈아타 버린다.
        if self.sequencer.state not in (
            SequencerState.IDLE, SequencerState.DONE, SequencerState.STOPPED
        ):
            self._reply(req_id, "prepare", 409, "BUSY")
            return
        plan = self._read_plan(content)
        if plan is None:
            self._reply(req_id, "prepare", 400, "INVALID_PLAN")
            return
        self._job_id = str(content.get("job_id", "")).strip()
        self._plan = plan
        self._apply_speed(content)
        self._reply(req_id, "prepare", 202, "ACCEPTED")
        # 원점에 도착하면 이 req_id 로 ready 를 낸다.
        self._ready_req_id = req_id
        self._at_origin = False
        self.activity.emit(
            f"ERUT 검사 준비 ({self._job_id}) — 프로브 측정 후 원점까지 갑니다.")
        self.job_requested.emit(plan)

    # ---- start : REAL — 로봇이 실제로 움직인다 -------------------------------
    def _do_start(self, req_id: str, content: dict) -> None:
        # 로봇이 원점에서 프로브 확인을 기다리는 중이면, 이 start 는 새
        # 작업이 아니라 **그 대기를 푸는 신호**다. 계획을 다시 볼 것도,
        # 순회 상태를 볼 것도 없다 — 이미 돌고 있는 작업의 한가운데다.
        # (계획 검사보다 먼저 본다. 해제 신호에는 plan 이 안 실려 온다.)
        if self._at_origin:
            self._at_origin = False
            # 규격 탭3 13번: start 의 응답은 **202 ACCEPTED** ("작업을
            # 시작했다, 결과는 complete 로"). 예전에는 200 으로 답했다.
            self._reply(req_id, "start", 202, "ACCEPTED")
            # 이 start 가 이 구간의 완료·진행률을 받을 요청이다. 여기서
            # 기억해 두지 않으면 progress 도 **complete 도 안 나가서** ERUT
            # 가 영영 기다린다(예전 동작).
            self._start_req_id = req_id
            self._started_at = time.time()
            self._job_id = str(content.get("job_id", "")).strip() or self._job_id
            self.activity.emit("ERUT 작업 시작 — 적심 후 스캔으로 넘어갑니다.")
            self.scan_go_requested.emit()
            return

        # 캘리브레이션 검사는 **게이트 해제 다음**이다. 이미 원점에서 기다리는
        # 로봇은 그 구간의 좌표계로 여기까지 온 것이라, 여기서 막으면 벽에
        # 붙은 채로 영영 서 있게 된다. 새로 시작하는 요청만 거른다.
        if self._needs_calibration(req_id, "start"):
            return

        # 여기부터는 **대비책**이다. 구간마다 prepare 가 오는 것이 정해진
        # 흐름이고(그러면 위 게이트 해제로 끝난다), 이 아래는 prepare 없이
        # start 만 왔을 때 작업이 멈추지 않게 하려고 남겨 둔 길이다.
        plan = self._read_plan(content) or getattr(self, "_plan", None)
        if plan is None:
            self._reply(req_id, "start", 400, "NO_PLAN")
            return
        if self.sequencer.state not in (
            SequencerState.IDLE, SequencerState.DONE, SequencerState.STOPPED
        ):
            self._reply(req_id, "start", 409, "BUSY")
            return

        self._job_id = str(content.get("job_id", "")).strip() or self._job_id
        self._start_req_id = req_id
        self._started_at = time.time()
        self._apply_speed(content)
        self._reply(req_id, "start", 202, "ACCEPTED")
        self.activity.emit(f"ERUT 구간 검사 시작 ({self._job_id}) — 로봇 실제 동작")
        self.job_requested.emit(plan)

    # ---- 원점 도착 <-> 스캔 시작 손짓 ---------------------------------------
    def notify_at_origin(self) -> None:
        """로봇이 원점에 도착해 멈춰 섰다고 알린다 (REAL).

        규격 탭5 대로 **prepare 의 완료 통보(evt/ready)** 다. 프로브가 벽에
        제대로 붙었는지는 ERUT 쪽이 판단하므로, 로봇은 원점에서 멈춰 서고
        (레지스터 290 = 7) 우리는 그 사실만 알린다. ERUT 가 프로브 눌림을
        확인하고 `req/start` 를 보내면 그때 적심(비비기) -> 스캔으로 간다.

        구간마다 prepare 가 오면 action 은 늘 "prepare" 다. prepare 없이
        start 만 온 구간에서는(대비책 경로) 그때 살아 있는 start 요청에
        맞춰 낸다 — 어느 쪽이든 원점 확인은 건너뛰지 않는다.

        프로브 눌림 수치는 주고받지 않는다. ERUT 가 자기 UT 회로로 눌림을
        보고 판단한 뒤 보내는 `req/start` 자체가 확인이다.
        """
        # ERUT 가 시킨 작업이 아니면(RCS '검사 시작'·사내 MC job_cmd) ERUT
        # 에 알릴 요청이 없다. req_id 가 빈 evt/ready 를 흘리면 ERUT 가 자기가
        # 보낸 적 없는 준비 완료를 받는다 — 그래서 아무것도 안 한다. 그때
        # 원점 대기는 MC 쪽 probe_ack 으로 푼다.
        if not self._ready_req_id and not self._start_req_id:
            return
        self._at_origin = True
        if self._ready_req_id:
            req_id, action = self._ready_req_id, "prepare"
        else:
            req_id, action = self._start_req_id, "start"
        # 규격 탭3 11번 그대로 낸다: code 200, message "OK",
        # content = {req_id, action, job_id}. 예전에는 message 를
        # "AT_ORIGIN_WAITING_PROBE_CHECK" 로 덮어쓰고 stage·cell 을 붙였는데,
        # ERUT 가 message == "OK" 로 판정하면 준비 완료로 안 볼 수 있다.
        self.client.publish_event("ready", req_id, action, job_id=self._job_id)
        self._ready_req_id = ""
        self.activity.emit("ERUT 에 원점 도착(evt/ready)을 알렸습니다 — start 대기.")

    def clear_at_origin(self) -> None:
        """로봇이 대기에서 풀렸다. 다음 도착까지 초기화한다."""
        self._at_origin = False

    def on_cell_changed(self, *_args) -> None:
        """셀이 넘어갈 때마다 진행률을 알린다 (REAL)."""
        if not self._start_req_id:
            return
        seq = self.sequencer
        plan = seq.plan
        if plan is None:
            return
        motion = self.motion_state()
        self.client.publish_progress(
            self._start_req_id, "start",
            job_id=self._job_id,
            progress=int(seq.cell_ordinal() / (plan.total_cells or 1) * 100),
            state=self.robot_state(),
            # 규격 탭2: moved 는 "mm — 선택, 표시용". 가상 차량 이동 거리다.
            # 규격에 없는 필드(cell, lift_height)는 넣지 않는다 — 리프트
            # 높이는 규격상 query 응답 자리다.
            moved=motion.get("moved", 0.0),
        )

    def on_job_complete(self) -> None:
        """전체 격자를 다 돌면 완료를 알린다 (REAL)."""
        if not self._start_req_id:
            return
        req_id, self._start_req_id = self._start_req_id, ""
        plan = self.sequencer.plan
        cells = plan.total_cells if plan else 0
        self.client.publish_event(
            "complete", req_id, "start",
            job_id=self._job_id,
            scanned_distance=int(cells * (plan.cell_width if plan else 0)),
            duration_ms=int((time.time() - self._started_at) * 1000),
            battery=TEST_BATTERY,     # TEST
        )
        self.activity.emit("ERUT 구간 검사 완료를 발행했습니다.")

    # ---- pause / resume / abort : REAL --------------------------------------
    def _do_pause(self, req_id: str, content: dict) -> None:
        self._reply(req_id, "pause", 200, "OK")
        self.pause_requested.emit()

    def _do_resume(self, req_id: str, content: dict) -> None:
        if self.sequencer.state is not SequencerState.PAUSED:
            self._reply(req_id, "resume", 412, "NOT_RESUMABLE")
            return
        self._reply(req_id, "resume", 200, "OK")
        self.resume_requested.emit()

    def _do_abort(self, req_id: str, content: dict) -> None:
        """작업자 검사 종료 (탭4 D-3). 200 으로 바로 답하고 끝낸다.

        abort 된 job 은 완료(evt/complete)를 내지 않는다 — 규격 그대로.
        원점 대기·마킹 표시도 여기서 내린다. 안 내리면 query 가 계속 ready
        로 답하거나, 다음 mark 가 409 BUSY 로 막힌다.
        """
        self._reply(req_id, "abort", 200, "OK")
        self._start_req_id = ""
        self._ready_req_id = ""
        self._at_origin = False
        self._mark_req_id = ""
        self._marking = False
        self.abort_requested.emit()

    # ---- reset : TEST (장애 수집이 아직 없다) --------------------------------
    def _do_reset(self, req_id: str, content: dict) -> None:
        """장애 해제. 걸려 있던 코드를 지우고 idle 을 알린다."""
        cleared = self.clear_errors()
        self._reply(req_id, "reset", 200, "OK")
        self.client.publish_status("online", TEST_BATTERY, TEST_CHARGING)
        if cleared:
            self.activity.emit(f"ERUT 리셋 — 장애 해제: {', '.join(cleared)}")
        else:
            self.activity.emit("ERUT 리셋 요청 (해제할 장애 없음)")

    # ---- mark : TEST --------------------------------------------------------
    def _do_mark(self, req_id: str, content: dict) -> None:
        """결함 자리 마킹 (규격 탭3 ⑤). 점마다 차량·리프트·로봇이 실제로 간다.

        점은 {id, x, y} — area 와 같은 검사면 좌표다. 받으면 202 로 답하고
        RCS(MarkRunner)가 점을 차례로 돈다. 다 끝나면 finish_mark() 가
        evt/complete {marked[], failed[]} 를 낸다(탭5 action 표).

        마킹 동작 자체(스프레이/마커)는 아직 TODO 다 — 로봇은 그 자리에
        붙었다가 홈으로 돌아온다.
        """
        points = content.get("points")
        if not isinstance(points, list) or not points:
            self._reply(req_id, "mark", 400, "INVALID_POINTS")
            return
        clean = []
        for pt in points:
            try:
                clean.append({"id": str(pt["id"]), "x": float(pt["x"]),
                              "y": float(pt["y"])})
            except (KeyError, TypeError, ValueError):
                self._reply(req_id, "mark", 400, "INVALID_POINTS")
                return
        if self._needs_calibration(req_id, "mark"):
            return
        # 스캔 구간이 도는 중이면 받지 않는다 — 차량·로봇을 둘이 같이 쓸 수 없다.
        if self.sequencer.state not in (
            SequencerState.IDLE, SequencerState.DONE, SequencerState.STOPPED
        ) or self._marking:
            self._reply(req_id, "mark", 409, "BUSY")
            return
        self._reply(req_id, "mark", 202, "ACCEPTED")
        self._mark_req_id = req_id
        self._marking = True
        self.activity.emit(f"ERUT 마킹 요청 {len(clean)}점 — 차례로 이동합니다.")
        self.mark_requested.emit(clean)

    def finish_mark(self, marked: list, failed: list) -> None:
        """마킹을 다 돌았다. 규격대로 evt/complete 를 낸다."""
        req_id, self._mark_req_id = self._mark_req_id, ""
        self._marking = False
        if not req_id:
            return
        code, message = (200, "OK") if not failed else (500, "INTERNAL_ERROR")
        self.client.publish_event("complete", req_id, "mark", code=code,
                                  message=message, marked=list(marked),
                                  failed=list(failed))
        self.activity.emit(
            f"ERUT 마킹 완료를 발행했습니다 (성공 {len(marked)}, 실패 {len(failed)}).")

    # ------------------------------------------------------------ 장애
    def raise_error(self, fields: dict) -> None:
        """장애를 규격대로 `evt/error` 로 알린다.

        지금은 시험 도구(`mqtt_test/alarm_sim.py`)가 넣어 준 것만 처리한다.
        실제 장애 수집(PLC·로봇 알람)이 붙으면 그쪽에서 같은 함수를 부르면
        되고, 나가는 메시지 형식은 바뀌지 않는다.

        `level` 이 `stop`/`estop` 이면 진행 중 작업을 멈춘다 — 화면만 띄우고
        계속 돌면 장애 상황에서 헛검사를 하게 된다.
        """
        code = str(fields.get("code", "E9999")).strip()
        message = str(fields.get("message", "UNKNOWN")).strip()
        level = str(fields.get("level", "warning")).strip()
        recovery = str(fields.get("recovery", "manual")).strip()

        extra: dict[str, Any] = {}
        detail = fields.get("detail")
        if detail:
            extra["detail"] = str(detail)
        if self._job_id and level != "warning":
            extra["job_id"] = self._job_id

        self._errors = [c for c in getattr(self, "_errors", []) if c != code]
        if code.endswith("-CLEAR"):
            # 해제 통보. 걸려 있던 코드를 목록에서 뺀다.
            base = code[: -len("-CLEAR")]
            self._errors = [c for c in self._errors if c != base]
        else:
            self._errors.append(code)

        self.client.publish_error(code, message, level, recovery, **extra)
        self.activity.emit(f"장애 통보: {code} {message} ({level})")

        if level in ("stop", "estop"):
            # 로봇은 순회 상태와 무관하게 세운다. 장애는 로봇 자신이 아니라
            # 차량·리프트·배터리 쪽에서도 나며, 그때 로봇 팔이 계속 벽을
            # 훑고 있으면 안 된다. 순회가 돌고 있지 않아도(수동 조작 중이어도)
            # 마찬가지다.
            self.robot_stop_requested.emit()
            if self.sequencer.state not in (
                SequencerState.IDLE, SequencerState.DONE, SequencerState.STOPPED
            ):
                self.pause_requested.emit()

    def clear_errors(self) -> list[str]:
        """걸려 있던 장애 코드를 모두 지우고, 지운 코드를 돌려준다.

        ERUT 가 `req/reset` 을 보내면 `_do_reset` 이 하는 일과 같다. 운영자가
        화면에서 직접 지울 수 있어야 해서(현장에서 스테이션 조작을 기다릴 수
        없다) 같은 동작을 밖에서도 부를 수 있게 뺐다.
        """
        cleared = self.error_codes()
        self._errors = []
        return cleared

    def error_codes(self) -> list[str]:
        """지금 걸려 있는 장애 코드 목록."""
        return list(getattr(self, "_errors", []))

    # ------------------------------------------------------------ 도우미
    def _read_plan(self, content: dict) -> GridPlan | None:
        """요청에서 격자 계획을 만든다 (ERUT 규격 20260818).

            area : { start:{x,y}, end:{x,y} }  → 셀 하나의 사각형
            plan : { columns, rows, diameter, height, surface_length }
            scan : { pitch_scan, pitch_x, pitch_y, speed }

        셀 크기는 **`area` 의 start·end 차이**로 정한다. `plan` 에
        `cell_width`/`cell_height` 가 오면 그쪽을 우선한다(옛 규격 호환).

        겹침 세 개는 쓰임이 달라 따로 담는다 — `pitch_scan` 은 로봇이
        area 안에서 쓰고, `pitch_x`/`pitch_y` 는 우리가 AMR·리프트를
        얼마나 움직일지 정하는 데 쓴다.
        """
        # **구간 하나 = job 하나** (규격 탭3 ④). 요청 하나로 격자 한 칸만
        # 스캔한다. 다음 구간은 ERUT 가 새 prepare/start 로 다시 준다.
        #
        # `plan.columns/rows` 는 **전체 격자** 정보라 여기서 순회 칸 수로
        # 쓰면 안 된다 — 예전에는 그렇게 써서 20260818 판 예시(12 x 6)면
        # 요청 한 번에 72 칸을 다 돌고 나서야 complete 를 냈다. ERUT 는
        # 구간마다 complete 를 기다린다.
        #
        # `plan` 블록 자체도 **필수가 아니다.** 규격의 start 와 20260812 판
        # prepare 에는 없다. 칸의 크기와 위치는 `area` 가 정한다.
        plan = content.get("plan")
        if not isinstance(plan, dict):
            plan = {}
        try:
            columns = rows = 1
            area = content.get("area") or {}
            start, end = area.get("start", {}), area.get("end", {})
            x0, y0 = float(start["x"]), float(start["y"])
            x1, y1 = float(end["x"]), float(end["y"])
            width = plan.get("cell_width", abs(x1 - x0))
            height = plan.get("cell_height", abs(y1 - y0))
            width, height = float(width), float(height)
            # 이 구간의 원점 — 검사면 좌표(탭5: 외주면 x=원주 전개, y=높이).
            # 차량은 x 로, 리프트는 y 로 정렬한다.
            origin_x, origin_y = min(x0, x1), min(y0, y1)

            scan = content.get("scan") or {}
            # `scan.pitch` 는 **격자끼리의 겹침(오버랩)** 이다. 격자 안에서는
            # 겹침이 의미가 없다 — 줄 간격은 로봇이 5축/8축 프로브 커버에
            # 맞춰 스스로 ㄹ자를 짠다(dus_init.script). 그래서 이 값은 가로·
            # 세로 격자간 겹침에 넣고 격자 안 줄 겹침(scan_overlap)은 비운다.
            # (ERUT 화면의 "오버랩 간격" 한 칸과 같은 값이다.)
            #
            # 20260818 판처럼 pitch_x/pitch_y 가 따로 오면 그 값을 쓴다.
            single = float(scan.get("pitch", scan.get("pitch_scan", 0)) or 0)
            pitch_x = float(scan.get("pitch_x", single) or 0)
            pitch_y = float(scan.get("pitch_y", single) or 0)
            scan_overlap = 0.0

            # 로봇이 호를 계산하는 데 필요한 값. plan 블록에 온다.
            # radius 는 **훑는 면**의 반지름이다 — 안쪽/바깥쪽 중 실제로
            # 스캔하는 면 기준으로 보내야 한다(두께를 더하고 빼는 판단은
            # 보내는 쪽 몫이다). 없으면 0 -> 평면으로 보고 직선 스캔.
            radius = float(plan.get("radius", 0) or 0)
            thickness = float(plan.get("thickness", 0) or 0)
            # 검사장비(EOAT) 종류 — 프로브 축 수 5(X형) 또는 8(직사각).
            eoat_probes = int(float(plan.get("eoat", 0) or 0))

            if columns <= 0 or rows <= 0 or width <= 0 or height <= 0:
                return None
        except (KeyError, TypeError, ValueError):
            return None
        return GridPlan(
            column_count=columns, row_count=rows,
            cell_width=width, cell_height=height,
            scan_overlap=scan_overlap, pitch_x=pitch_x, pitch_y=pitch_y,
            radius=radius, thickness=thickness, eoat_probes=eoat_probes,
            origin_x=origin_x, origin_y=origin_y,
        )

    def _apply_speed(self, content: dict) -> None:
        scan = content.get("scan")
        if not isinstance(scan, dict):
            return
        ratio = scan.get("speed_ratio")
        if ratio is None:
            return
        try:
            percent = int(ratio)
        except (TypeError, ValueError):
            return
        if 2 <= percent <= 100:
            self.speed_requested.emit(percent)

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
        """ERUT 가 사라지면 헛검사를 막기 위해 진행 중 작업을 멈춘다."""
        if online:
            self.activity.emit("ERUT 가 온라인입니다.")
            return
        self.activity.emit("ERUT 가 오프라인입니다. 진행 중 작업을 일시정지합니다.")
        if self.sequencer.state not in (
            SequencerState.IDLE, SequencerState.DONE, SequencerState.STOPPED
        ):
            self.pause_requested.emit()
