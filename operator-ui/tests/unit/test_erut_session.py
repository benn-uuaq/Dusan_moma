"""ERUT 표준 인터페이스(if-0.5) — 검사로봇 쪽 처리.

브로커 없이 돌리기 위해 `ErutClient` 자리에 발행 내용을 모으는 대역을 넣는다.
규격은 `mqtt_test/ERUT_검사로봇_MQTT_표준인터페이스_if-0.5.xlsx` 탭1~5·8~10.
"""

import pytest

from smr_operator_ui.services import JobSequencer, SequencerState
from smr_operator_ui.services.erut_session import (
    CAPABILITIES, INTERFACE_VERSION, ErutSession,
)


class FakeClient:
    """발행한 것을 모아 두는 ErutClient 대역."""

    def __init__(self) -> None:
        self.res: list[dict] = []
        self.events: list[tuple[str, dict]] = []
        self.progress: list[dict] = []
        self.status: list[dict] = []
        self.errors: list[dict] = []
        self.messages: list[dict] = []
        self.contact: list[dict] = []
        self.info: list[dict] = []
        self.home: list[str] = []
        self.is_connected = True

    class _Sig:
        def connect(self, _slot): pass
    request_received = _Sig()
    erut_online_changed = _Sig()
    connected_changed = _Sig()
    test_error_injected = _Sig()

    def publish_res(self, req_id, action, code, message, **extra):
        self.res.append({"req_id": req_id, "action": action,
                         "code": code, "message": message, **extra})
        return True

    def publish_event(self, name, req_id, action, code=200, message="OK", **extra):
        self.events.append((name, {"req_id": req_id, "action": action,
                                   "code": code, "message": message, **extra}))
        return True

    def publish_progress(self, req_id, action, **extra):
        self.progress.append({"req_id": req_id, "action": action, **extra})
        return True

    def publish_status(self, state="online", **fields):
        self.status.append({"state": state,
                            **{k: v for k, v in fields.items() if v is not None}})
        return True

    def publish_contact(self, state, job_id):
        self.contact.append({"state": state, "job_id": job_id})
        return True

    def publish_info(self, content):
        self.info.append(dict(content))
        return True

    def publish_home(self, state):
        self.home.append(state)
        return True

    def publish_message(self, code, message, text, **extra):
        self.messages.append({"code": code, "message": message, "text": text, **extra})
        return True

    def publish_error(self, code, message, level, recovery, cleared=False, **extra):
        self.errors.append({"code": code, "message": message, "level": level,
                            "recovery": recovery, "cleared": cleared, **extra})
        return True


@pytest.fixture
def session(qtbot):
    """캘리브레이션이 끝난 상태의 세션 (탭3 ② 를 지난 뒤가 기본 전제)."""
    client = FakeClient()
    seq = JobSequencer()
    s = ErutSession(client, seq)
    s._calibrated = True
    return s, client, seq


def _req(action, **content):
    content.setdefault("req_id", f"req-{action}-1")
    return action, content


AREA = {"start": {"x": 0, "y": 0}, "end": {"x": 600, "y": 800}}
SCAN = {"pitch": 20, "speed": 100}


def _prepare(s, req_id="p1", job_id="jb1", area=AREA, scan=SCAN, surface="outer", **extra):
    s.handle_request(*_req("prepare", req_id=req_id, job_id=job_id,
                           surface=surface, area=area, scan=scan, **extra))


def _start(s, req_id="s1", job_id="jb1", area=AREA, scan=SCAN):
    s.handle_request(*_req("start", req_id=req_id, job_id=job_id,
                           surface="outer", area=area, scan=scan))


def _codes(client, action):
    return [r["code"] for r in client.res if r["action"] == action]


# ---- query (탭5 61~73행) ------------------------------------------------------
def test_query_answers_with_activity_not_state(session):
    """8/12 판의 state 칸은 activity 로 이름이 바뀌었다."""
    s, client, _seq = session
    s.handle_request(*_req("query"))

    r = client.res[0]
    assert (r["code"], r["action"]) == (200, "query")
    assert r["activity"] == "idle"
    assert "state" not in r
    assert r["calibrated"] is True
    assert r["resumable"] is False


def test_query_has_no_battery_until_the_vehicle_gives_it(session):
    """배터리가 없는 장비는 칸을 빼야 한다 — 0 으로 채우면 시작을 영영 못 한다."""
    s, client, _seq = session
    s.handle_request(*_req("query"))

    assert "battery" not in client.res[0]
    assert "charging" not in client.res[0]


def test_query_lists_active_errors_as_objects(session):
    s, client, _seq = session
    s.raise_error({"code": "E2001", "message": "DRIVE_ERROR",
                   "level": "stop", "recovery": "manual"})
    s.handle_request(*_req("query"))

    r = client.res[-1]
    assert r["activity"] == "error"
    assert r["errors"] == [{"code": "E2001", "level": "stop",
                            "recovery": "manual", "message": "DRIVE_ERROR"}]


def test_query_reports_the_last_job(session):
    """완료를 놓쳤을 때 짐작하지 않도록 마지막 작업 결과를 싣는다."""
    s, client, _seq = session
    _start(s, job_id="jb9")
    s.on_job_complete()
    s.handle_request(*_req("query", req_id="q9"))

    last = client.res[-1]["last_job"]
    assert (last["job_id"], last["action"], last["code"]) == ("jb9", "start", 200)
    assert last["progress"] == 100


def test_query_reports_job_and_integer_progress_while_working(session):
    s, client, seq = session
    s.position_state = lambda: {"progress": 45.7}
    _start(s)
    seq._set_state(SequencerState.SCANNING)
    s._publish_progress()
    s.handle_request(*_req("query", req_id="q1"))

    r = client.res[-1]
    assert r["job_id"] == "jb1"
    assert r["progress"] == 45 and isinstance(r["progress"], int)


# ---- 모르는 것 · 모자란 것 (탭2 6행 · 탭10 8번) ---------------------------------
def test_unknown_action_is_501(session):
    s, client, _seq = session
    s.handle_request("teleport", {"req_id": "x1"})

    assert (client.res[-1]["code"], client.res[-1]["message"]) == (501, "NOT_IMPLEMENTED")


def test_request_without_req_id_is_400(session):
    s, client, _seq = session
    s.handle_request("query", {})

    assert client.res[-1]["code"] == 400


def test_unknown_surface_is_400(session):
    """짐작으로 다른 면을 검사하면 보고서에는 요청한 면으로 적힌다."""
    s, client, seq = session
    _prepare(s, surface="inner")

    assert client.res[-1]["code"] == 400
    assert seq.state is SequencerState.IDLE


def test_zero_size_area_is_400(session):
    s, client, _seq = session
    _prepare(s, area={"start": {"x": 0, "y": 0}, "end": {"x": 0, "y": 800}})

    assert client.res[-1]["code"] == 400


def test_speed_above_the_robot_limit_is_refused(session):
    """낼 수 없는 속도는 분명히 거절한다(탭8 45행)."""
    s, client, _seq = session
    _start(s, scan={"pitch": 20, "speed": 150})

    assert client.res[-1]["code"] == 400


def test_scan_speed_becomes_the_robot_speed_ratio(session):
    """스캔은 100 mm/s 고정이라 40 mm/s 는 40 % 로 건다."""
    s, _client, _seq = session
    ratios: list[int] = []
    s.speed_requested.connect(ratios.append)

    _prepare(s, scan={"pitch": 20, "speed": 40})

    assert ratios == [40]


def test_unknown_fields_are_ignored(session):
    s, client, _seq = session
    _prepare(s, extra_field={"anything": 1})

    assert client.res[-1]["code"] == 202


# ---- 같은 req_id (탭2 22행) -----------------------------------------------------
def test_same_req_id_is_not_run_twice(session):
    s, client, _seq = session
    jobs: list = []
    s.job_requested.connect(jobs.append)
    _prepare(s, req_id="dup")
    _prepare(s, req_id="dup")

    assert len(jobs) == 1
    assert _codes(client, "prepare") == [202, 202]


def _calibrate(s, req_id="cal-001", diameter=1690):
    s.handle_request(*_req("calibrate", req_id=req_id, diameter=diameter, height=6000))


def test_same_req_id_after_the_work_ended_runs_again(session, monkeypatch):
    """작업 정지 뒤 같은 req_id 로 다시 보낸 calibrate 는 새 요청이다(실기 2026-10-02).

    재전송 판정은 받은 지 얼마 안 됐거나(30초) 그 일이 아직 돌 때뿐이다.
    """
    s, client, _seq = session
    asked: list = []
    s.calibration_requested.connect(lambda d, h: asked.append(d))
    _calibrate(s)
    s.finish_calibration(True, 0.0, total_length_mm=5309)
    s.handle_request(*_req("abort", req_id="ab-001"))

    monkeypatch.setattr(type(s), "RETRANSMIT_WINDOW_S", 0.0)
    _calibrate(s)

    assert asked == [1690.0, 1690.0]
    assert _codes(client, "calibrate") == [202, 202]


def test_same_req_id_with_new_content_is_a_new_request(session):
    """값이 바뀌었으면 재전송이 아니다 — 다시 따진다(돌고 있으면 409)."""
    s, client, _seq = session
    asked: list = []
    s.calibration_requested.connect(lambda d, h: asked.append(d))
    _calibrate(s)
    _calibrate(s, diameter=1700)          # 앞 캘리브레이션이 아직 돈다

    assert asked == [1690.0]
    assert _codes(client, "calibrate") == [202, 409]

    s.finish_calibration(True, 0.0)
    _calibrate(s, diameter=1700)
    assert asked == [1690.0, 1700.0]


def test_a_refused_request_is_judged_again_when_resent(session):
    """캘리브레이션 전에 428 을 받은 prepare 를 캘리브레이션 뒤 그대로 다시 보내면 받는다."""
    s, client, _seq = session
    s._calibrated = False
    _prepare(s, req_id="prep-001")
    s._calibrated = True
    _prepare(s, req_id="prep-001")

    assert _codes(client, "prepare") == [428, 202]


def test_retransmission_while_the_work_runs_is_not_run_again(session, monkeypatch):
    """돌고 있는 일의 재전송은 시간이 지나도 다시 실행하지 않는다."""
    s, client, _seq = session
    asked: list = []
    s.calibration_requested.connect(lambda d, h: asked.append(d))
    _calibrate(s)
    monkeypatch.setattr(type(s), "RETRANSMIT_WINDOW_S", 0.0)
    _calibrate(s)

    assert asked == [1690.0]
    assert _codes(client, "calibrate") == [202, 202]


# ---- calibrate (탭3 ②) --------------------------------------------------------
def test_calibrate_accepts_then_completes(session):
    s, client, _seq = session
    s._calibrated = False
    asked: list[tuple] = []
    s.calibration_requested.connect(lambda d, h: asked.append((d, h)))
    s.handle_request(*_req("calibrate", diameter=2500, height=6000))

    assert client.res[0]["code"] == 202
    assert asked == [(2500.0, 6000.0)]
    assert client.events == []
    assert s.activity_state() == "calibrating"

    s.finish_calibration(True, 0.42, "잰 벽 반지름 1250.4 mm")

    name, evt = client.events[0]
    assert (name, evt["action"]) == ("complete", "calibrate")
    assert evt["origin"] == {"x": 0, "y": 0}
    assert evt["calibration_error_mm"] == 0.42
    # 총 둘레는 차량 자료가 올 때까지 싣지 않는다(선택 칸).
    assert "total_length_mm" not in evt
    s.handle_request(*_req("query", req_id="q2"))
    assert client.res[-1]["calibrated"] is True


def test_calibrate_carries_the_total_length_when_the_vehicle_gives_it(session):
    s, client, _seq = session
    s.total_length_mm = lambda: 7853.98
    s.handle_request(*_req("calibrate", diameter=2500, height=6000))
    s.finish_calibration(True, 0.3)

    assert client.events[-1][1]["total_length_mm"] == 7854


def test_calibrate_needs_the_target_size(session):
    s, client, _seq = session
    s.handle_request(*_req("calibrate", height=6000))

    assert client.res[-1]["code"] == 400


def test_failed_calibration_still_publishes_complete(session):
    s, client, _seq = session
    s.handle_request(*_req("calibrate", diameter=2500, height=6000))
    s.finish_calibration(False, 0.0, "벽 접촉이 2번뿐입니다")

    name, evt = client.events[-1]
    assert (name, evt["action"], evt["code"]) == ("complete", "calibrate", 500)
    s.handle_request(*_req("query", req_id="q3"))
    assert client.res[-1]["calibrated"] is False


def test_calibrate_is_refused_while_the_robot_works(session):
    s, client, _seq = session
    s.robot_busy = lambda: True
    s.handle_request(*_req("calibrate", diameter=2500, height=6000))

    assert client.res[-1]["code"] == 409


def test_pause_during_calibration_stops_the_lap_and_resume_continues_it(session):
    """캘리브레이션(차량 한 바퀴) 중에도 pause 를 받는다 — 그때도 차량이 움직인다."""
    s, client, _seq = session
    stops: list[int] = []
    resumes: list[int] = []
    s.calibration_stop_requested.connect(lambda: stops.append(1))
    s.calibration_resume_requested.connect(lambda: resumes.append(1))
    s.handle_request(*_req("calibrate", req_id="c1", diameter=2500, height=6000))

    s.handle_request(*_req("pause", req_id="pz"))
    assert client.res[-1]["code"] == 200
    assert stops == [1] and s.activity_state() == "paused"
    s.handle_request(*_req("query", req_id="q1"))
    assert client.res[-1]["resumable"] is True

    s.handle_request(*_req("resume", req_id="rz"))
    assert client.res[-1]["code"] == 202
    assert resumes == [1]
    assert s.activity_state() == "calibrating"
    # 완료는 처음 calibrate 의 번호로 나간다.
    s.finish_calibration(True, 0.2, total_length_mm=7853.9)
    evt = client.events[-1][1]
    assert evt["req_id"] == "c1" and evt["total_length_mm"] == 7854

def test_losing_the_frame_raises_calibration_lost(session):
    """좌표계가 무효가 되는 순간 E1003 을 낸다 — query 를 기다리지 않는다."""
    s, client, _seq = session
    s.invalidate_calibration("충전 복귀")

    err = client.errors[-1]
    assert (err["code"], err["level"], err["recovery"], err["cleared"]) == (
        "E1003", "stop", "reset_required", False)
    s.handle_request(*_req("reset", req_id="rs"))
    # 장애는 풀려도 좌표계는 다시 잡아야 한다.
    _prepare(s, req_id="p9")
    assert client.res[-1]["code"] == 428


# ---- prepare · start (탭3 ③④) -------------------------------------------------
def _ready(s, seq):
    """시퀀서가 차량·리프트를 구간 자리에 세웠다(READY)."""
    seq._set_state(SequencerState.READY)
    s.notify_prepared()


def test_prepare_moves_only_the_vehicle_then_reports_ready(session):
    """prepare 는 차량·리프트만 세운다 — 로봇 3점 측정은 start 에서 한다."""
    s, client, seq = session
    jobs: list[tuple] = []
    s.job_requested.connect(lambda plan, prepare_only: jobs.append(prepare_only))
    _prepare(s)
    assert client.res[-1]["code"] == 202
    assert jobs == [True], "준비만 — 로봇은 시작하지 않는다"
    assert s.activity_state() == "preparing"
    assert client.status[-1]["activity"] == "preparing"
    assert client.events == []

    _ready(s, seq)

    name, evt = client.events[-1]
    assert (name, evt["req_id"], evt["action"], evt["job_id"]) == (
        "ready", "p1", "prepare", "jb1")
    assert s.activity_state() == "ready"
    assert client.status[-1]["activity"] == "ready"

def test_ready_is_sent_once_per_prepare(session):
    """준비 중 멈췄다 이어 가도 ready 는 처음 prepare 의 번호로 한 번만."""
    s, client, seq = session
    _prepare(s)
    _ready(s, seq)
    s.notify_prepared()

    assert len([e for e in client.events if e[0] == "ready"]) == 1

def test_start_for_the_prepared_job_starts_the_robot(session, qtbot):
    """준비된 구간의 start → 로봇 3점 측정 → 시작점에 붙으면 잠시 뒤 스캔으로."""
    s, client, seq = session
    proceeds: list[int] = []
    gates: list[int] = []
    s.proceed_requested.connect(lambda: proceeds.append(1))
    s.scan_go_requested.connect(lambda: gates.append(1))
    _prepare(s)
    _ready(s, seq)

    _start(s)

    assert client.res[-1]["code"] == 202
    assert proceeds == [1]
    assert gates == []
    # 3점 측정을 마치고 시작점에 붙었다 — 물이 나올 틈을 두고 대기를 푼다.
    seq._set_state(SequencerState.SCANNING)
    s.notify_at_origin()
    assert gates == []
    qtbot.waitUntil(lambda: gates == [1], timeout=4000)

    s.on_job_complete()
    name, evt = client.events[-1]
    assert (name, evt["req_id"], evt["action"], evt["job_id"]) == (
        "complete", "s1", "start", "jb1")
    assert "scanned_distance_mm" in evt and "scanned_distance" not in evt
    assert "battery" not in evt


def test_abort_before_the_contact_lead_keeps_the_robot_at_the_start(session, qtbot):
    """붙은 뒤 적심 전에 abort 가 오면 대기를 풀지 않는다."""
    s, _client, seq = session
    gates: list[int] = []
    s.scan_go_requested.connect(lambda: gates.append(1))
    _start(s)
    s.notify_at_origin()
    s.handle_request(*_req("abort", req_id="ab"))

    qtbot.wait(2500)
    assert gates == []

def test_start_for_another_job_drops_the_prepared_one(session):
    """앞 구역 완료를 못 받은 채 다음 구역이 오면 준비를 버리고 새로 돈다."""
    s, client, seq = session
    proceeds: list[int] = []
    drops: list[int] = []
    jobs: list = []
    s.proceed_requested.connect(lambda: proceeds.append(1))
    s.job_dropped.connect(lambda: drops.append(1))
    s.job_requested.connect(lambda plan, prepare_only: jobs.append((plan, prepare_only)))
    _prepare(s)
    _ready(s, seq)
    s.job_dropped.connect(lambda: seq._set_state(SequencerState.STOPPED))

    _start(s, req_id="s2", job_id="jb2",
           area={"start": {"x": 580, "y": 0}, "end": {"x": 1180, "y": 800}})

    assert client.res[-1]["code"] == 202
    assert proceeds == [] and drops == [1]
    assert jobs[-1][0].origin_x == 580 and jobs[-1][1] is False
    assert s.job_id == "jb2"

def test_start_without_prepare_goes_all_the_way(session, qtbot):
    """prepare 없이 온 start 는 차량 정렬부터 스캔까지 한 번에 간다."""
    s, client, _seq = session
    jobs: list[bool] = []
    gates: list[int] = []
    s.job_requested.connect(lambda plan, prepare_only: jobs.append(prepare_only))
    s.scan_go_requested.connect(lambda: gates.append(1))
    _start(s)
    assert jobs == [False]

    s.notify_at_origin()

    qtbot.waitUntil(lambda: gates == [1], timeout=4000)
    assert not [e for e in client.events if e[0] == "ready"]

def test_prepare_is_refused_while_a_section_is_running(session):
    s, client, _seq = session
    _prepare(s, req_id="p1")
    _prepare(s, req_id="p2", job_id="jb2")

    assert _codes(client, "prepare") == [202, 409]


def test_prepare_in_ready_replaces_the_prepared_job(session):
    """ready 에서 새 prepare 가 오면 준비를 버리고 새로(탭9)."""
    s, client, seq = session
    drops: list[int] = []
    s.job_dropped.connect(lambda: drops.append(1))
    s.job_dropped.connect(lambda: seq._set_state(SequencerState.STOPPED))
    _prepare(s, req_id="p1")
    _ready(s, seq)

    _prepare(s, req_id="p2", job_id="jb2")

    assert _codes(client, "prepare") == [202, 202]
    assert drops == [1]
    assert s.activity_state() == "preparing"

def test_prepare_and_start_need_calibration(session):
    s, client, _seq = session
    s._calibrated = False
    _prepare(s)
    _start(s)

    assert _codes(client, "prepare") == [428]
    assert _codes(client, "start") == [428]


def test_area_origin_and_overlap_make_the_grid_position(session):
    """scan.pitch 는 구역끼리의 겹침이다 — 차량·리프트 간격에 들어간다."""
    s, _client, _seq = session
    jobs: list = []
    s.job_requested.connect(jobs.append)
    _prepare(s, area={"start": {"x": 1160, "y": 780}, "end": {"x": 1760, "y": 1580}},
             scan={"pitch": 20, "speed": 100})

    plan = jobs[0]
    assert (plan.origin_x, plan.origin_y) == (1160, 780)
    assert (plan.cell_width, plan.cell_height) == (600, 800)
    assert (plan.pitch_x, plan.pitch_y) == (20, 20)
    assert plan.scan_overlap == 0
    # (600-20) 간격으로 두 칸 옆, (800-20) 간격으로 한 칸 위 → 3B
    assert s._cell_label() == "3B"


def test_old_plan_block_is_ignored(session):
    """plan 대신 area 를 쓴다 — 예전 판의 plan.cell_width 가 와도 area 가 이긴다."""
    s, _client, _seq = session
    jobs: list = []
    s.job_requested.connect(jobs.append)
    _prepare(s, plan={"cell_width": 999, "cell_height": 999, "columns": 12})

    assert (jobs[0].cell_width, jobs[0].cell_height) == (600, 800)
    assert (jobs[0].column_count, jobs[0].row_count) == (1, 1)


# ---- 진행률 · 위치 (탭2 9행 + 3S 확장) --------------------------------------------
def test_progress_is_an_integer_that_never_goes_back(session):
    s, client, seq = session
    values = iter([30.9, 20, 55])
    s.position_state = lambda: {"progress": next(values), "arc_mm": 100.0, "row_mm": 40.0}
    s.motion_state = lambda: {"moved": 1160.0, "lift_height": 780.0}
    _start(s)
    seq._set_state(SequencerState.SCANNING)
    for _ in range(3):
        s._publish_progress()

    assert [p["progress"] for p in client.progress] == [30, 30, 55]
    assert all(isinstance(p["progress"], int) for p in client.progress)


def test_progress_and_complete_carry_where_the_robot_is(session):
    """좌표만으로는 모자라다 — 구역 이름·차량·리프트·로봇 위치를 같이 싣는다."""
    s, client, seq = session
    s.position_state = lambda: {"progress": 50, "arc_mm": 123.4, "row_mm": 40.0,
                                "scanned_mm": 3600.0}
    s.motion_state = lambda: {"moved": 580.0, "lift_height": 0.0}
    _start(s, area={"start": {"x": 580, "y": 0}, "end": {"x": 1180, "y": 800}})
    seq._set_state(SequencerState.SCANNING)
    s._publish_progress()

    p = client.progress[-1]
    assert p["pos"] == {"x": 703, "y": 40}
    assert p["location"] == {"cell": "2A", "vehicle_mm": 580, "lift_mm": 0,
                             "robot": {"x": 123, "y": 40}}
    assert p["activity"] == "running" and p["job_id"] == "jb1"

    s.on_job_complete()
    evt = client.events[-1][1]
    assert evt["pos"] == {"x": 703, "y": 40}
    assert evt["location"]["cell"] == "2A"
    assert evt["scanned_distance_mm"] == 3600


def test_no_progress_outside_an_erut_section(session):
    s, client, _seq = session
    s._publish_progress()

    assert client.progress == []


# ---- evt/status · evt/info · evt/contact ----------------------------------------
def test_status_keeps_state_and_activity_apart(session):
    """state 는 유언이 쓰는 칸이라 online/offline 만 — 활동은 activity 로."""
    s, client, _seq = session
    s.publish_status()

    st = client.status[-1]
    assert st["state"] == "online"
    assert st["activity"] == "idle"
    assert st["calibrated"] is True
    assert "battery" not in st


def test_status_goes_out_at_once_when_activity_changes(session):
    s, client, _seq = session
    s.publish_status()
    before = len(client.status)
    _prepare(s)

    assert len(client.status) == before + 1
    assert client.status[-1]["activity"] == "preparing"
    assert client.status[-1]["job_id"] == "jb1"


def test_status_is_not_repeated_when_nothing_changed(session):
    s, client, _seq = session
    s.publish_status()
    before = len(client.status)
    s.refresh_status()
    s.handle_request(*_req("query"))

    assert len(client.status) == before


# ---- 이동 안전 자세 (if-0.5 evt/home) --------------------------------------------
def test_home_goes_out_only_when_it_changes(session):
    """evt/home 은 붙을 때 + 바뀔 때만 — 주기 재전송이 없다(탭2 14행)."""
    s, client, _seq = session
    at_home = {"v": True}
    s.at_home = lambda: at_home["v"]
    s.publish_home()
    s.refresh_status()
    s.refresh_status()
    at_home["v"] = False
    s.refresh_status()
    at_home["v"] = True
    s.refresh_status()

    assert client.home == ["home", "deployed", "home"]


def test_unknown_home_flag_is_deployed(session):
    """모르면 위험한 쪽 — 홈 플래그를 아직 못 받았으면 deployed(탭5 130행)."""
    s, client, _seq = session
    s.at_home = lambda: None
    s.publish_home()

    assert client.home == ["deployed"]


def test_query_carries_home(session):
    """ERUT 는 이동 명령 직전에 query 의 home 으로 판단한다."""
    s, client, _seq = session
    s.at_home = lambda: True
    s.handle_request(*_req("query", req_id="q1"))
    assert client.res[-1]["home"] == "home"

    s.at_home = lambda: False
    s.handle_request(*_req("query", req_id="q2"))
    assert client.res[-1]["home"] == "deployed"


def test_status_no_longer_carries_at_home(session):
    """홈 자세는 evt/home 으로 옮겼다 — evt/status 에 같은 뜻을 두 번 싣지 않는다."""
    s, client, _seq = session
    s.at_home = lambda: True
    s.publish_status()

    assert "at_home" not in client.status[-1]


def test_info_introduces_the_device(session):
    s, client, _seq = session
    s.publish_info()

    info = client.info[-1]
    assert info["interface_version"] == INTERFACE_VERSION == "0.6"
    assert info["vendor"] == "3S" and info["device_type"] == "articulated_arm"
    assert "probe_contact" in info["capabilities"]
    assert "mark_positioning" in info["capabilities"]
    assert "home" in info["capabilities"]
    # 마커는 ERUT 것이고 배터리 자료는 아직 없다.
    assert "marking" not in CAPABILITIES
    assert "battery" not in CAPABILITIES and "charging" not in CAPABILITIES


def test_contact_goes_out_on_change_with_the_job_id(session):
    s, client, _seq = session
    _prepare(s)
    s.set_contact(False)
    s.set_contact(True)
    s.set_contact(True)
    s.set_contact(False)

    assert client.contact == [
        {"state": "detached", "job_id": "jb1"},
        {"state": "attached", "job_id": "jb1"},
        {"state": "detached", "job_id": "jb1"},
    ]
    # 2초마다 다시 보낸다.
    s._publish_contact()
    assert client.contact[-1] == {"state": "detached", "job_id": "jb1"}


def test_a_new_job_republishes_contact_even_if_unchanged(session):
    """retain 이라 앞 구역 것이 남는다 — 새 구역이면 새 job_id 로 다시 낸다."""
    s, client, _seq = session
    _prepare(s, req_id="p1", job_id="jb1")
    s.set_contact(False)
    s.handle_request(*_req("abort", req_id="ab"))
    _prepare(s, req_id="p2", job_id="jb2")
    s.set_contact(False)

    assert client.contact[-1] == {"state": "detached", "job_id": "jb2"}


# ---- pause · resume · abort -----------------------------------------------------
def test_stop_commands_are_always_accepted(session):
    """pause·abort·reset·query 는 바빠도 받는다 — 409 를 쓰지 않는다."""
    s, client, _seq = session
    _prepare(s)
    for action in ("pause", "reset", "query", "abort"):
        s.handle_request(*_req(action, req_id=f"{action}-x"))
    codes = [r["code"] for r in client.res if r["action"] != "prepare"]

    assert all(200 <= c < 300 for c in codes), codes


def test_pause_and_resume_of_a_running_section(session):
    s, client, seq = session
    s.pause_requested.connect(seq.pause)
    s.resume_requested.connect(seq.resume)
    _start(s)
    seq._plan = seq.plan or s._plan
    seq._set_state(SequencerState.SCANNING)

    s.handle_request(*_req("pause", req_id="pz"))
    assert s.activity_state() == "paused"
    s.handle_request(*_req("query", req_id="q"))
    assert client.res[-1]["resumable"] is True

    s.handle_request(*_req("resume", req_id="rz"))
    assert client.res[-1]["code"] == 202
    # 완료는 처음 start 의 req_id 로(resume 의 번호가 아니라).
    s.on_job_complete()
    assert client.events[-1][1]["req_id"] == "s1"


def _paused_section(s, seq):
    s.pause_requested.connect(seq.pause)
    s.resume_requested.connect(seq.resume)
    _start(s)
    seq._plan = seq.plan or s._plan
    seq._set_state(SequencerState.SCANNING)
    s.handle_request(*_req("pause", req_id="pz"))


def test_paused_is_reported_only_after_the_equipment_stopped(session):
    """if-0.6: 감속하는 동안은 아직 running — 선 뒤에야 paused."""
    s, client, seq = session
    settled = {"v": False}
    s.pause_settled = lambda: settled["v"]
    _paused_section(s, seq)

    assert s.activity_state() == "running"
    s.handle_request(*_req("query", req_id="q1"))
    assert client.res[-1]["resumable"] is False
    s.handle_request(*_req("resume", req_id="r1"))
    assert client.res[-1]["code"] == 409, "아직 서는 중이면 running 과 같다(탭9)"

    settled["v"] = True
    assert s.activity_state() == "paused"
    s.handle_request(*_req("query", req_id="q2"))
    assert client.res[-1]["resumable"] is True


def test_resume_after_losing_the_spot_is_412(session):
    """밀렸거나 태스크가 중지됐으면 resumable=false, resume 은 412 NOT_RESUMABLE."""
    s, client, seq = session
    resumed: list[int] = []
    s.pause_holds_position = lambda: False
    _paused_section(s, seq)
    s.resume_requested.connect(lambda: resumed.append(1))

    s.handle_request(*_req("query", req_id="q"))
    assert client.res[-1]["resumable"] is False
    s.handle_request(*_req("resume", req_id="r"))
    assert (client.res[-1]["code"], client.res[-1]["message"]) == (412, "NOT_RESUMABLE")
    assert resumed == []
    assert s.activity_state() == "paused"


def test_calibration_pause_reports_calibrating_until_the_vehicle_stops(session):
    s, _client, _seq = session
    settled = {"v": False}
    s.pause_settled = lambda: settled["v"]
    s.handle_request(*_req("calibrate", req_id="c1", diameter=1690, height=6000))
    s.handle_request(*_req("pause", req_id="pz"))

    assert s.activity_state() == "calibrating"
    settled["v"] = True
    assert s.activity_state() == "paused"


def test_resume_with_nothing_paused_is_409(session):
    s, client, _seq = session
    s.handle_request(*_req("resume"))

    assert client.res[-1]["code"] == 409


def test_abort_without_job_id_stops_whatever_is_running(session):
    s, client, _seq = session
    aborted: list[int] = []
    s.abort_requested.connect(lambda: aborted.append(1))
    s.handle_request(*_req("calibrate", req_id="c1", diameter=2500, height=6000))

    s.handle_request(*_req("abort", req_id="ab"))

    assert client.res[-1]["code"] == 200
    assert aborted == [1]
    assert s.activity_state() == "idle"
    # abort 된 일은 완료를 내지 않는다.
    assert client.events == []


def test_bridge_offline_pauses_the_section(session):
    s, _client, seq = session
    s.pause_requested.connect(seq.pause)
    _prepare(s)
    seq._set_state(SequencerState.SCANNING)

    s._on_erut_online(False)

    assert seq.state is SequencerState.PAUSED


# ---- 장애 (탭2 10행 · 탭4 B-2 · D-2) ---------------------------------------------
def test_error_is_published_with_cleared_false(session):
    s, client, _seq = session
    s.raise_error({"code": "E2001", "message": "DRIVE_ERROR",
                   "level": "warning", "recovery": "auto"})

    err = client.errors[-1]
    assert err["code"] == "E2001" and err["cleared"] is False


def test_clearing_uses_the_same_code_with_cleared_true(session):
    """code 끝에 -CLEAR 를 붙이지 않는다. level·recovery 는 발생 때 그대로."""
    s, client, _seq = session
    s.raise_error({"code": "E2001", "message": "DRIVE_ERROR",
                   "level": "stop", "recovery": "manual"})
    s.raise_error({"code": "E2001", "message": "DRIVE_ERROR", "cleared": True})

    err = client.errors[-1]
    assert (err["code"], err["cleared"], err["level"], err["recovery"]) == (
        "E2001", True, "stop", "manual")
    assert s.error_codes() == []


def test_legacy_clear_suffix_is_read_as_a_clear(session):
    s, client, _seq = session
    s.raise_error({"code": "E1002", "message": "E_STOP", "level": "estop",
                   "recovery": "reset_required"})
    s.raise_error({"code": "E1002-CLEAR", "message": "E_STOP_CLEARED"})

    assert client.errors[-1]["code"] == "E1002"
    assert client.errors[-1]["cleared"] is True


def test_warning_does_not_stop_the_work(session):
    s, client, _seq = session
    stops: list[int] = []
    s.robot_stop_requested.connect(lambda: stops.append(1))
    _prepare(s)
    s.raise_error({"code": "E5001", "message": "COUPLANT_LOW",
                   "level": "warning", "recovery": "auto"})

    assert stops == []
    assert s.activity_state() == "preparing"


def test_stop_level_fault_fails_the_section_with_5xx(session):
    """장애로 하던 일이 실패하면 evt/error 와 따로 evt/complete 에 5xx(탭4 B-2)."""
    s, client, seq = session
    drops: list[int] = []
    stops: list[int] = []
    s.job_dropped.connect(lambda: drops.append(1))
    s.robot_stop_requested.connect(lambda: stops.append(1))
    _start(s)
    seq._set_state(SequencerState.SCANNING)

    s.raise_error({"code": "E2001", "message": "DRIVE_ERROR",
                   "level": "stop", "recovery": "manual"})

    assert client.errors[-1]["job_id"] == "jb1"
    name, evt = client.events[-1]
    assert (name, evt["action"], evt["code"], evt["job_id"]) == (
        "complete", "start", 500, "jb1")
    assert stops and drops
    assert s.activity_state() == "error"
    s.handle_request(*_req("query", req_id="q"))
    assert client.res[-1]["resumable"] is False


def test_estop_ends_the_section_and_reset_returns_to_idle(session):
    """if-0.4: 비상정지 때 하던 구간은 끝난 것으로 보고 이어 가지 않는다."""
    s, client, _seq = session
    _prepare(s)
    s.raise_error({"code": "E1002", "message": "E_STOP", "level": "estop",
                   "recovery": "reset_required"})
    assert s.activity_state() == "estop"
    # 준비 실패는 evt/ready 가 아니라 evt/complete(action=prepare) 5xx 다.
    name, evt = client.events[-1]
    assert (name, evt["action"], evt["code"]) == ("complete", "prepare", 500)

    _prepare(s, req_id="p2")
    assert client.res[-1]["code"] == 423

    s.handle_request(*_req("reset", req_id="rs"))
    assert client.res[-1]["code"] == 202
    assert client.errors[-1]["cleared"] is True
    assert s.activity_state() == "idle"


def test_reset_without_faults_is_200_and_does_nothing(session):
    s, client, _seq = session
    s.handle_request(*_req("reset"))

    assert client.res[-1]["code"] == 200
    assert client.errors == []


def test_operator_clear_tells_erut(session):
    """작업자가 화면에서 풀어도 cleared=true 를 낸다 — 조치 끝은 장치가 알린다."""
    s, client, _seq = session
    s.raise_error({"code": "E2001", "message": "DRIVE_ERROR",
                   "level": "warning", "recovery": "manual"})
    assert s.clear_error("E2001") is True

    assert client.errors[-1]["cleared"] is True
    assert s.clear_error("E2001") is False


# ---- 마킹 (마커는 ERUT 것 — mark_positioning) ------------------------------------
def test_mark_hands_the_points_over(session):
    s, client, _seq = session
    points: list = []
    s.mark_requested.connect(points.append)
    s.handle_request(*_req("mark", method="paint",
                           points=[{"id": "p1", "x": 350, "y": 1200}]))

    assert client.res[-1]["code"] == 202
    assert points == [[{"id": "p1", "x": 350.0, "y": 1200.0}]]
    assert s.activity_state() == "running"


def test_mark_point_handshake(session):
    """자리에 붙으면 mark_ready, ERUT 가 찍고 mark_next 를 보내면 다음 점."""
    s, client, _seq = session
    nexts: list[tuple] = []
    s.mark_next_requested.connect(lambda pid, ok: nexts.append((pid, ok)))
    s.handle_request(*_req("mark", req_id="m1",
                           points=[{"id": "p1", "x": 350, "y": 1200}]))

    s.mark_point_ready("p1")
    name, evt = client.events[-1]
    assert (name, evt["req_id"], evt["point_id"]) == ("mark_ready", "m1", "p1")

    s.handle_request("mark_next", {"req_id": "m1", "point_id": "p1", "marked": True})
    assert client.res[-1]["code"] == 200
    assert nexts == [("p1", True)]
    # 다시 보내도(같은 번호·같은 점) 두 번 움직이지 않는다.
    s.handle_request("mark_next", {"req_id": "m1", "point_id": "p1", "marked": True})
    assert nexts == [("p1", True)]


def test_mark_next_for_another_job_is_refused(session):
    s, client, _seq = session
    s.handle_request("mark_next", {"req_id": "zz", "point_id": "p1"})

    assert client.res[-1]["code"] == 409


def test_mark_complete_is_200_even_with_some_failures(session):
    """일부가 실패해도 완료는 200 — 전부 실패했을 때만 5xx(탭3 19번)."""
    s, client, _seq = session
    s.handle_request(*_req("mark", req_id="m1", points=[
        {"id": "p1", "x": 1, "y": 1}, {"id": "p2", "x": 2, "y": 2}]))
    s.finish_mark(["p1"], ["p2"])

    evt = client.events[-1][1]
    assert (evt["code"], evt["marked"], evt["failed"]) == (200, ["p1"], ["p2"])

    s.handle_request(*_req("mark", req_id="m2", points=[{"id": "p3", "x": 3, "y": 3}]))
    s.finish_mark([], ["p3"])
    assert client.events[-1][1]["code"] == 500


def test_mark_with_the_device_marker_is_not_implemented(session):
    s, client, _seq = session
    s.handle_request(*_req("mark", marker="device",
                           points=[{"id": "p1", "x": 1, "y": 1}]))

    assert client.res[-1]["code"] == 501


def test_mark_with_broken_points_is_400(session):
    s, client, _seq = session
    s.handle_request(*_req("mark", points=[{"id": "p1", "x": "far"}]))

    assert client.res[-1]["code"] == 400


def test_pause_during_marking_asks_rcs_to_stop_it(session):
    s, _client, _seq = session
    stops: list[int] = []
    s.mark_stop_requested.connect(lambda: stops.append(1))
    s.handle_request(*_req("mark", req_id="m1", points=[{"id": "p1", "x": 1, "y": 1}]))

    s.handle_request(*_req("pause", req_id="pz"))

    assert stops == [1]


# ---- 우리 쪽에서 끊음 ------------------------------------------------------------
def test_local_stop_of_a_started_section_tells_erut(session):
    s, client, _seq = session
    _start(s)
    s.interrupt_active_job()

    name, evt = client.events[-1]
    assert (name, evt["action"], evt["code"]) == ("complete", "start", 500)


def test_local_stop_before_ready_fails_the_prepare_in_complete(session):
    """evt/ready 는 성공 전용이다 — 준비 실패는 evt/complete(action=prepare)."""
    s, client, _seq = session
    _prepare(s)
    s.interrupt_active_job()

    name, evt = client.events[-1]
    assert (name, evt["action"], evt["code"]) == ("complete", "prepare", 500)


def test_local_stop_without_an_erut_job_says_nothing(session):
    s, client, _seq = session
    s.interrupt_active_job()

    assert client.events == []


def test_no_ready_goes_out_for_a_job_erut_did_not_ask_for(session):
    s, client, _seq = session
    s.notify_at_origin()

    assert client.events == []


# ---- home (3S 확장 — 홈 확인 신호는 ERUT 가 다음 판에 넣는다) --------------------------
def test_home_is_accepted_and_completes_when_the_robot_arrives(session):
    s, client, _seq = session
    homes: list[int] = []
    s.home_requested.connect(lambda: homes.append(1))
    s.handle_request(*_req("home", req_id="h1"))

    assert client.res[-1]["code"] == 202
    assert homes == [1]
    assert s.activity_state() == "running"

    s.home_arrived()
    name, evt = client.events[-1]
    assert (name, evt["req_id"], evt["action"], evt["code"]) == ("complete", "h1", "home", 200)
    assert s.activity_state() == "idle"


def test_home_is_refused_while_the_robot_works(session):
    s, client, _seq = session
    s.robot_busy = lambda: True
    s.handle_request(*_req("home"))

    assert client.res[-1]["code"] == 409
    assert client.messages[-1]["code"] == "M1001"


def test_home_timeout_fails_in_complete(session):
    s, client, _seq = session
    s.handle_request(*_req("home", req_id="h1"))
    s.finish_home(False, "HOME_TIMEOUT")

    evt = client.events[-1][1]
    assert (evt["action"], evt["code"]) == ("home", 500)


def test_contact_outside_an_erut_section_is_detached(session):
    """캘리브레이션 3점 측정·사내 MC 작업 중 닿는 것은 ERUT 검사가 아니다 — 물을 켜지 않는다."""
    s, client, _seq = session
    s.set_contact(True)
    s.handle_request(*_req("calibrate", diameter=2500, height=6000))
    s.set_contact(True)

    assert all(c["state"] == "detached" for c in client.contact)


def test_area_wider_than_the_robot_can_reach_is_refused(session):
    """구간 가로 1000 mm 는 반지름 845 에서 현 942 mm — 로봇 한계(700)를 넘는다.

    줄여서 하면 구간 일부를 안 훑은 채 끝나므로 거절하고 최대값을 알려 준다
    (실기 2026-10-02: 호 1000 이 그대로 나가 probe_l 역기구학 오류).
    """
    s, client, _seq = session
    jobs: list = []
    s.job_requested.connect(lambda plan, prepare_only: jobs.append(plan))
    s.max_area_width = lambda: 721.0
    wide = {"start": {"x": 0, "y": 1000}, "end": {"x": 1000, "y": 1500}}

    _prepare(s, req_id="p-wide", area=wide)
    _start(s, req_id="s-wide", area=wide)

    assert jobs == []
    refused = [r for r in client.res if r["code"] == 400]
    assert [r["action"] for r in refused] == ["prepare", "start"]
    assert refused[0]["reason"] == "area_width" and refused[0]["max_width"] == 721

    _prepare(s, req_id="p-ok")             # 600 mm 는 된다
    assert len(jobs) == 1
