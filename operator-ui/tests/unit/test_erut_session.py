"""ERUT 프로토콜 처리 테스트.

브로커 없이 돌리기 위해 `ErutClient` 자리에 발행 내용을 모으는 대역을 넣는다.
규격은 `mqtt_test/ERUT-3S_MQTT_인터페이스_*.xlsx` 탭3·탭5.
"""

import pytest

from smr_operator_ui.services import GridPlan, JobSequencer, SequencerState
from smr_operator_ui.services.erut_session import ErutSession


class FakeClient:
    """발행한 것을 모아 두는 ErutClient 대역."""

    def __init__(self) -> None:
        self.res: list[dict] = []
        self.events: list[tuple[str, dict]] = []
        self.progress: list[dict] = []
        self.status: list[tuple] = []
        self.errors: list[dict] = []
        self.is_connected = True

    # 시그널 자리 — 세션이 connect() 를 부르므로 흉내만 낸다.
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

    def publish_status(self, state, battery=None, charging=None):
        self.status.append((state, battery, charging))
        return True

    def publish_error(self, code, message, level, recovery, **extra):
        self.errors.append({"code": code, "message": message,
                            "level": level, "recovery": recovery, **extra})
        return True


@pytest.fixture
def session(qtbot):
    """캘리브레이션이 끝난 상태의 세션.

    규격 탭3 은 ① 접속 확인 → ② 캘리브레이션 → ③ 준비 → ④ 구간 검사
    순서다. prepare·start 를 보는 시험은 전부 ② 를 지난 뒤가 전제라
    여기서 미리 세워 둔다. 캘리브레이션 자체와 무효 상태의 거절을 보는
    시험만 이 값을 직접 내린다.
    """
    client = FakeClient()
    seq = JobSequencer()
    s = ErutSession(client, seq)
    s._calibrated = True
    return s, client, seq


def _req(action, **content):
    content.setdefault("req_id", f"req-{action}-1")
    return action, content


# ERUT 규격 20260818: 셀 크기는 area 가 정하고, 겹침은 셋으로 갈린다.
PLAN = {"columns": 2, "rows": 3,
        "diameter": 2500, "height": 6000, "surface_length": 9000}
AREA = {"start": {"x": 0, "y": 0}, "end": {"x": 600, "y": 800}}
SCAN = {"pitch_scan": 10, "pitch_x": 5, "pitch_y": 20, "speed": 40}


def test_query_answers_with_current_state(session):
    """query 는 응답 하나로 끝난다 (탭5: 결정 시점 전용)."""
    s, client, _seq = session
    s.handle_request(*_req("query"))

    assert len(client.res) == 1
    r = client.res[0]
    assert (r["code"], r["action"]) == (200, "query")
    assert r["state"] == "idle"
    assert r["calibrated"] is True
    assert r["resumable"] is False


def test_calibrate_accepts_then_completes(session, qtbot):
    """calibrate 는 202 로 받고 결과는 evt/complete 로만 알린다."""
    s, client, _ = session
    s._calibrated = False          # 아직 교정 전인 장치에서 시작한다
    s.handle_request(*_req("calibrate", diameter=2500, height=6000))

    assert client.res[0]["code"] == 202
    qtbot.waitUntil(lambda: bool(client.events), timeout=5000)
    name, evt = client.events[0]
    assert name == "complete"
    assert evt["action"] == "calibrate"
    assert "origin" in evt and "calibration_error_mm" in evt
    # 캘리브레이션을 마치면 query 가 calibrated=true 로 답해야 한다.
    s.handle_request(*_req("query", req_id="q2"))
    assert client.res[-1]["calibrated"] is True


def test_prepare_drives_the_robot_and_waits_for_the_origin(session):
    """prepare 는 물리 준비(프로브 측정 + 원점 복귀)를 실제로 시킨다.

    규격 탭1 이 prepare 를 "시간 걸리는 물리 준비(접근·자세·프로브)"로
    정의하고, 탭5 가 그 완료 통보를 evt/ready 로 못박아 뒀다. 그래서
    ready 는 타이머가 아니라 **로봇이 원점에 선 뒤에** 나가야 한다 —
    예전처럼 2초 뒤 흘려보내면 로봇이 아직 벽을 찾는 중인데 ERUT 가
    start 를 보내 버린다.
    """
    s, client, _seq = session
    plans = []
    s.job_requested.connect(plans.append)

    s.handle_request(*_req("prepare", job_id="jb1", area=AREA, plan=PLAN, scan=SCAN))

    assert client.res[0]["code"] == 202
    assert len(plans) == 1, "prepare 가 로봇을 움직이지 않았다"
    assert client.events == [], "원점에 닿기도 전에 ready 를 냈다"


def test_ready_goes_out_when_the_robot_parks_at_the_origin(session):
    """원점 도착이 곧 prepare 의 완료 통보다 (규격 탭5)."""
    s, client, _seq = session
    s.handle_request(*_req("prepare", job_id="jb1", area=AREA, plan=PLAN, scan=SCAN))
    s.notify_at_origin()

    name, evt = client.events[-1]
    assert (name, evt["action"], evt["job_id"]) == ("ready", "prepare", "jb1")
    # 규격 탭3 11번 그대로 — 규격에 없는 필드를 붙이지 않는다.
    assert set(evt) == {"req_id", "action", "job_id", "code", "message"}
    assert evt["message"] == "OK"


def test_later_cells_report_ready_against_the_start_request(session):
    """두 번째 구간부터는 prepare 없이 start 만 온다 (규격 탭1).

    그때도 원점에서 같은 확인을 받아야 하므로, ready 의 action 을 그때
    살아 있는 요청(start)에 맞춘다.
    """
    s, client, _seq = session
    s.handle_request(*_req("start", req_id="r-cell2", job_id="jb2",
                           area=AREA, plan=PLAN, scan=SCAN))
    s.notify_at_origin()

    name, evt = client.events[-1]
    assert (name, evt["action"]) == ("ready", "start")
    assert evt["req_id"] == "r-cell2"


def test_start_drives_the_real_sequencer(session):
    """start 만 로봇을 실제로 움직인다 — 계획이 시퀀서로 넘어가야 한다."""
    s, client, _seq = session
    plans = []
    s.job_requested.connect(plans.append)

    s.handle_request(*_req("start", job_id="jb1", area=AREA, plan=PLAN, scan=SCAN))

    assert client.res[0]["code"] == 202
    assert len(plans) == 1
    # 구간 하나 = job 하나 (규격 탭3 ④). plan.columns/rows(2x3)는 전체
    # 격자 정보라 순회 칸 수로 쓰지 않는다 — 요청 한 번에 한 칸만 한다.
    assert (plans[0].column_count, plans[0].row_count) == (1, 1)
    assert (plans[0].cell_width, plans[0].cell_height) == (600.0, 800.0)


def test_start_uses_area_when_plan_has_no_cell_size(session):
    """area 의 start·end 차이가 곧 셀 크기다. plan 에 치수가 없어도 된다."""
    s, _client, _ = session
    plans = []
    s.job_requested.connect(plans.append)

    s.handle_request(*_req(
        "start", job_id="jb1",
        area={"start": {"x": 0, "y": 1000}, "end": {"x": 700, "y": 1500}},
        plan={"columns": 4, "rows": 2}, scan=SCAN))

    assert (plans[0].cell_width, plans[0].cell_height) == (700.0, 500.0)


def test_start_is_refused_while_busy(session):
    """이미 돌고 있으면 409 BUSY 로 거절한다."""
    s, client, seq = session
    seq.start(GridPlan(1, 1, 600, 800), scan_h_mm=150)

    s.handle_request(*_req("start", job_id="jb2", area=AREA, plan=PLAN, scan=SCAN))

    assert client.res[-1]["code"] == 409


def test_resume_is_refused_when_not_paused(session):
    """일시정지 상태가 아니면 412 NOT_RESUMABLE."""
    s, client, _ = session
    s.handle_request(*_req("resume"))
    assert client.res[-1]["code"] == 412


def test_same_req_id_is_not_run_twice(session):
    """같은 req_id 를 다시 받으면 재실행하지 않고 이전 응답만 되돌린다."""
    s, client, _ = session
    starts = []
    s.job_requested.connect(starts.append)

    for _ in range(3):
        s.handle_request("start", {"req_id": "same", "job_id": "jb1",
                                   "area": AREA, "plan": PLAN, "scan": SCAN})

    assert len(starts) == 1, "같은 요청으로 로봇이 두 번 움직였다"
    assert len(client.res) == 3, "응답은 매번 돌려줘야 한다"
    assert {r["code"] for r in client.res} == {202}


def test_mark_hands_the_points_to_rcs(session):
    """mark 는 202 로 받고, 점들을 RCS(MarkRunner)에게 넘긴다 (탭3 ⑤)."""
    s, client, _ = session
    asked = []
    s.mark_requested.connect(asked.append)

    s.handle_request(*_req("mark", method="paint", points=[
        {"id": "p1", "x": 350, "y": 1200}, {"id": "p2", "x": 780, "y": 1350}]))

    assert client.res[-1]["code"] == 202
    assert asked == [[{"id": "p1", "x": 350.0, "y": 1200.0},
                      {"id": "p2", "x": 780.0, "y": 1350.0}]]
    assert client.events == [], "점을 돌기도 전에 완료를 냈다"


def test_mark_complete_reports_marked_and_failed(session):
    """다 돌면 evt/complete {marked[], failed[]} (탭5 action 표)."""
    s, client, _ = session
    s.handle_request(*_req("mark", points=[{"id": "p1", "x": 1, "y": 2}]))

    s.finish_mark(["p1"], [])

    name, evt = client.events[-1]
    assert (name, evt["action"], evt["code"]) == ("complete", "mark", 200)
    assert (evt["marked"], evt["failed"]) == (["p1"], [])


def test_mark_with_failures_is_reported_as_error(session):
    """실패한 점이 있으면 complete 를 5xx 로 낸다 (탭2: 실패도 complete)."""
    s, client, _ = session
    s.handle_request(*_req("mark", points=[{"id": "p1", "x": 1, "y": 2}]))

    s.finish_mark([], ["p1"])

    _name, evt = client.events[-1]
    assert evt["code"] == 500 and evt["failed"] == ["p1"]


def test_mark_is_refused_while_scanning_or_marking(session):
    """차량·로봇을 둘이 같이 쓸 수 없다 — 409 BUSY."""
    s, client, _ = session
    s.handle_request(*_req("mark", points=[{"id": "p1", "x": 1, "y": 2}]))
    s.handle_request("mark", {"req_id": "req-mark-2",
                              "points": [{"id": "p2", "x": 1, "y": 2}]})

    assert client.res[-1]["code"] == 409


def test_mark_with_broken_points_is_refused(session):
    """좌표가 빠진 점이 있으면 400 — 엉뚱한 데로 가지 않게."""
    s, client, _ = session
    s.handle_request(*_req("mark", points=[{"id": "p1", "x": 1}]))

    assert client.res[-1]["code"] == 400


def test_request_without_req_id_is_refused(session):
    s, client, _ = session
    s.handle_request("start", {"job_id": "jb1"})
    assert client.res[-1]["code"] == 400


def test_status_is_republished_for_liveness(session):
    """생존 감시는 evt/status 재발행으로 한다 (규격 30~60초 주기).

    LWT 는 1회성이라 프로그램이 굳은 경우를 못 잡는다. 상대는 이 갱신이
    끊기는 것으로 굳음을 판정하므로, 같은 내용이라도 다시 나가야 한다.
    """
    s, client, _seq = session
    before = len(client.status)

    s.publish_status()

    assert len(client.status) == before + 1
    state, battery, _charging = client.status[-1]
    assert state == "online"
    assert battery is not None


def test_no_telemetry_topic_exists(session):
    """규격(20260818)에 telemetry 토픽은 없다. 실수로 되살아나지 않게 막는다."""
    s, client, _seq = session
    assert not hasattr(s, "publish_telemetry")
    assert not hasattr(client, "publish_telemetry")


def test_erut_offline_pauses_the_job(session):
    """ERUT 가 사라지면 헛검사를 막으려 진행 중 작업을 멈춘다."""
    s, _client, seq = session
    paused = []
    s.pause_requested.connect(lambda: paused.append(True))
    seq.start(GridPlan(2, 2, 600, 800), scan_h_mm=150)

    s._on_erut_online(False)

    assert paused == [True]


def test_job_complete_reports_to_erut(session):
    """전체 순회가 끝나면 start 의 완료를 알린다."""
    s, client, seq = session
    s.handle_request(*_req("start", job_id="jb1", area=AREA, plan=PLAN, scan=SCAN))
    seq.start(GridPlan(2, 3, 600, 800), scan_h_mm=150)

    s.on_job_complete()

    name, evt = client.events[-1]
    assert (name, evt["action"], evt["job_id"]) == ("complete", "start", "jb1")
    assert "scanned_distance" in evt and "duration_ms" in evt


def test_error_is_published_in_spec_envelope(session):
    """evt/error 는 code·message 가 최상위, level·recovery 는 content 안이다."""
    s, client, _ = session
    s.raise_error({"code": "E2001", "message": "DRIVE_ERROR",
                   "level": "stop", "recovery": "manual",
                   "detail": "주행부 모터 과부하"})

    e = client.errors[-1]
    assert (e["code"], e["message"]) == ("E2001", "DRIVE_ERROR")
    assert (e["level"], e["recovery"]) == ("stop", "manual")
    assert e["detail"] == "주행부 모터 과부하"


def test_stop_level_error_pauses_the_job(session):
    """장애 등급이 stop/estop 이면 진행 중 작업을 멈춘다."""
    s, _client, seq = session
    paused = []
    s.pause_requested.connect(lambda: paused.append(True))
    seq.start(GridPlan(2, 2, 600, 800), scan_h_mm=150)

    s.raise_error({"code": "E1002", "message": "E_STOP",
                   "level": "estop", "recovery": "reset_required"})

    assert paused == [True]


def test_warning_level_error_does_not_pause(session):
    """경고는 화면 표시만 — 작업을 멈추지 않는다."""
    s, _client, seq = session
    paused = []
    s.pause_requested.connect(lambda: paused.append(True))
    seq.start(GridPlan(2, 2, 600, 800), scan_h_mm=150)

    s.raise_error({"code": "E5001", "message": "COUPLANT_LOW",
                   "level": "warning", "recovery": "auto"})

    assert paused == []


def test_active_errors_show_up_in_query(session):
    """걸려 있는 장애는 query 응답의 errors[] 에 담긴다."""
    s, client, _ = session
    s.raise_error({"code": "E2001", "message": "DRIVE_ERROR",
                   "level": "stop", "recovery": "manual"})

    s.handle_request(*_req("query"))
    assert client.res[-1]["errors"] == ["E2001"]
    assert client.res[-1]["state"] == "error"


def test_reset_clears_active_errors(session):
    """req/reset 이 걸려 있던 장애를 해제한다."""
    s, client, _ = session
    s.raise_error({"code": "E2001", "message": "DRIVE_ERROR",
                   "level": "stop", "recovery": "manual"})
    assert s.error_codes() == ["E2001"]

    s.handle_request(*_req("reset"))

    assert s.error_codes() == []
    assert client.res[-1]["code"] == 200


def test_clear_code_removes_the_matching_error(session):
    """`-CLEAR` 코드는 짝이 되는 장애를 목록에서 뺀다."""
    s, _client, _ = session
    s.raise_error({"code": "E2001", "message": "DRIVE_ERROR",
                   "level": "stop", "recovery": "manual"})
    s.raise_error({"code": "E2001-CLEAR", "message": "DRIVE_ERROR_CLEARED",
                   "level": "warning", "recovery": "auto"})

    assert s.error_codes() == []


def test_axis_overlaps_are_kept_apart(session):
    """가로·세로 겹침은 쓰임이 달라 섞이면 안 된다 (규격 20260818)."""
    s, _client, _ = session
    plans = []
    s.job_requested.connect(plans.append)

    s.handle_request(*_req("start", job_id="jb1", area=AREA, plan=PLAN, scan=SCAN))

    p = plans[0]
    assert p.pitch_x == 5.0         # area 사이 가로 → AMR 이동
    assert p.pitch_y == 20.0        # area 사이 세로 → 리프트 상승
    # 격자 **안** 줄 겹침은 로봇이 프로브 커버로 정한다. 여기서 안 채운다.
    assert p.scan_overlap == 0.0
    assert p.lift_pitch == 800.0 - 20.0
    assert p.column_pitch == 600.0 - 5.0


def test_scan_pitch_is_the_overlap_between_sections(session):
    """scan.pitch 는 **격자끼리의 겹침**이다 — 가로·세로에 같이 들어간다.

    격자 안에서는 겹침이 의미가 없다. 줄 간격은 로봇이 5축/8축에 맞춰
    스스로 짠다. 그래서 격자 안 줄 겹침(scan_overlap)은 비워 둔다.
    """
    s, _client, _ = session
    plans = []
    s.job_requested.connect(plans.append)

    s.handle_request(*_req("start", job_id="jb1", area=AREA,
                           scan={"pitch": 5, "speed": 40}))

    p = plans[0]
    assert (p.pitch_x, p.pitch_y) == (5.0, 5.0)
    assert p.scan_overlap == 0.0


def test_0812_payload_without_plan_block_is_accepted(session):
    """규격 20260812 의 prepare/start 에는 plan 블록이 없다 — 받아야 한다.

    예전에는 plan 을 필수로 요구해서 규격대로 보낸 prepare 를
    400 INVALID_PLAN 으로 거절했다.
    """
    s, client, _ = session
    area = {"start": {"x": 0, "y": 1000}, "end": {"x": 1000, "y": 1500}}
    s.handle_request(*_req("prepare", job_id="jb00000001", surface="outer",
                           area=area, scan={"pitch": 5, "speed": 40}))

    assert client.res[-1]["code"] == 202


def test_area_start_places_the_vehicle_and_lift(session):
    """구간 원점(area.start)이 차량·리프트를 정렬할 자리다.

    외주면에서 x 는 원주 전개 거리, y 는 높이다 (탭5).
    """
    s, _client, _ = session
    plans = []
    s.job_requested.connect(plans.append)
    area = {"start": {"x": 2000, "y": 1000}, "end": {"x": 2721, "y": 1500}}

    s.handle_request(*_req("prepare", job_id="jb1", area=area,
                           scan={"pitch": 5, "speed": 40}))

    p = plans[0]
    assert (p.origin_x, p.origin_y) == (2000.0, 1000.0)
    assert (p.cell_width, p.cell_height) == (721.0, 500.0)


def test_prepare_is_refused_while_a_section_is_running(session):
    """앞 구간이 도는 중에 온 prepare 는 409 BUSY (탭5)."""
    s, client, seq = session
    s.job_requested.connect(lambda plan: seq.start(plan, 30.0, move_first=True))
    s.handle_request(*_req("prepare", job_id="jb1", area=AREA, scan=SCAN))

    s.handle_request("prepare", {"req_id": "req-prepare-2", "job_id": "jb2",
                                 "area": AREA, "scan": SCAN})

    assert client.res[-1]["code"] == 409


def test_complete_goes_out_after_prepare_then_start(session):
    """prepare -> ready -> start 흐름에서도 구간 완료가 ERUT 로 나가야 한다.

    예전에는 start 를 원점 대기 해제로만 처리하고 그 req_id 를 기억하지
    않아서, 구간을 다 스캔해도 evt/complete 가 안 나갔다 — 규격상 ERUT 는
    complete 가 올 때까지 계속 기다린다.
    """
    s, client, seq = session
    s.job_requested.connect(lambda plan: seq.start(plan, 30.0, move_first=True))
    s.handle_request(*_req("prepare", job_id="jb1", area=AREA, scan=SCAN))
    s.notify_at_origin()
    s.handle_request("start", {"req_id": "r-start", "job_id": "jb1",
                               "area": AREA, "scan": SCAN})

    s.on_job_complete()

    done = [f for name, f in client.events if name == "complete"]
    assert done, "구간 완료(evt/complete)가 안 나갔다"
    assert (done[-1]["req_id"], done[-1]["action"]) == ("r-start", "start")
    assert {"scanned_distance", "duration_ms", "battery"} <= set(done[-1])


def test_query_reports_ready_while_waiting_at_origin(session):
    """원점에서 start 를 기다리는 동안 상태는 ready (탭5)."""
    s, client, seq = session
    s.job_requested.connect(lambda plan: seq.start(plan, 30.0, move_first=True))
    s.handle_request(*_req("prepare", job_id="jb1", area=AREA, scan=SCAN))
    s.notify_at_origin()

    s.handle_request("query", {"req_id": "q-ready"})

    assert client.res[-1]["state"] == "ready"


def test_start_while_at_origin_releases_instead_of_reporting_busy(session):
    """원점 대기 중의 start 는 대기 해제다 — BUSY 가 아니다.

    로봇이 원점에서 프로브 확인을 기다리는 동안 순회는 이미 돌고 있어서,
    평소 규칙대로 409 BUSY 를 돌려주면 로봇이 영영 풀리지 않는다.
    """
    s, client, _seq = session
    s.handle_request(*_req("prepare", job_id="jb1", area=AREA, scan=SCAN))
    s.notify_at_origin()

    released: list[int] = []
    s.scan_go_requested.connect(lambda: released.append(1))
    s.handle_request(*_req("start"))

    assert released == [1]
    codes = [r["code"] for r in client.res]
    # 규격 탭3 13번: start 의 응답은 202 ACCEPTED.
    assert 202 in codes and 409 not in codes


def test_arrival_is_published_as_a_ready_event(session):
    """도착 통보는 evt/ready 로 나가고 어느 단계인지 붙는다."""
    s, client, _seq = session
    s.handle_request(*_req("prepare", job_id="jb1", area=AREA, scan=SCAN))
    s.notify_at_origin()

    ready = [fields for name, fields in client.events if name == "ready"]
    assert ready, "도착을 알리지 않았다"
    assert ready[-1]["code"] == 200 and ready[-1]["message"] == "OK"


def test_a_second_start_after_release_is_a_normal_start(session):
    """대기를 한 번 푼 뒤에는 다시 평소 규칙으로 돌아간다."""
    s, _client, _seq = session
    s.notify_at_origin()
    s.handle_request(*_req("start"))

    released: list[int] = []
    s.scan_go_requested.connect(lambda: released.append(1))
    s.handle_request("start", {"req_id": "req-start-2"})

    assert released == [], "대기가 아닌데 해제로 처리했다"


def test_query_reports_the_lift_height(session):
    """규격 탭5: query 응답의 lift_height (리프트 있는 장치만, 표시용).

    가상 리프트라도 값이 나가야 ERUT 화면에서 지금 몇 mm 인지 보인다.
    """
    s, client, _seq = session
    s.motion_state = lambda: {"lift_height": 1600.0, "moved": 2400.0}

    s.handle_request(*_req("query"))

    assert client.res[-1]["lift_height"] == 1600.0


def test_query_omits_the_lift_height_when_there_is_none(session):
    """리프트가 없는 구성이면 그 자리를 비워 둔다 — 규격상 선택 필드다."""
    s, client, _seq = session
    s.motion_state = dict

    s.handle_request(*_req("query"))

    assert "lift_height" not in client.res[-1]


def test_progress_reports_the_amr_travel(session):
    """규격 탭5: evt/progress 의 moved (mm, 선택, 표시용)."""
    s, client, seq = session
    s.motion_state = lambda: {"lift_height": 800.0, "moved": 1200.0}
    # 세션은 계획만 넘기고 실제 순회는 app.py 가 시퀀서에 물려 준다.
    # 여기서는 그 배선을 대신한다.
    s.job_requested.connect(lambda plan: seq.start(plan, 30.0))
    s.handle_request(*_req("start", area=AREA, plan=PLAN, scan=SCAN, job_id="jb1"))
    s.on_cell_changed()

    assert client.progress, "진행률을 발행하지 않았다"
    assert client.progress[-1]["moved"] == 1200.0
    # 리프트 높이는 규격상 query 응답 자리다 — progress 에 싣지 않는다.
    assert "lift_height" not in client.progress[-1]


# ---------------------------------------------------------------------------
# 캘리브레이션 무효 상태 — 규격 탭4 D-4
# ---------------------------------------------------------------------------
def test_prepare_is_refused_without_calibration(session):
    """좌표계가 무효면 prepare 를 받지 않는다 (428).

    규격 탭4 D-4: "prepare/start를 보내도 로봇이 거절 필요". 좌표계 없이
    훑으면 스캔 좌표가 어디를 가리키는지 알 수 없다.
    """
    s, client, _seq = session
    s.invalidate_calibration("시험")

    s.handle_request(*_req("prepare", job_id="jb1", area=AREA, plan=PLAN, scan=SCAN))

    r = client.res[-1]
    assert (r["code"], r["message"]) == (428, "CALIBRATION_REQUIRED")


def test_start_is_refused_without_calibration(session):
    """start 도 같은 이유로 거절한다 — 로봇을 움직이면 안 된다."""
    s, client, _seq = session
    s.invalidate_calibration()
    plans = []
    s.job_requested.connect(plans.append)

    s.handle_request(*_req("start", job_id="jb1", area=AREA, plan=PLAN, scan=SCAN))

    assert client.res[-1]["code"] == 428
    assert plans == [], "거절해 놓고 로봇을 움직였다"


def test_origin_release_is_not_blocked_by_calibration(session):
    """원점에서 기다리는 로봇은 캘리브레이션 검사로 막지 않는다.

    그 로봇은 이미 그 구간의 좌표계로 여기까지 온 것이라, 여기서 막으면
    벽에 붙은 채로 영영 서 있게 된다. 새로 시작하는 요청만 거른다.
    """
    s, client, _seq = session
    s.handle_request(*_req("prepare", job_id="jb1", area=AREA, plan=PLAN, scan=SCAN))
    s.notify_at_origin()
    s.invalidate_calibration("도중에 무효가 됐다")

    released = []
    s.scan_go_requested.connect(lambda: released.append(1))
    s.handle_request("start", {"req_id": "r-go"})

    assert released == [1]
    assert client.res[-1]["code"] == 202


def test_recalibration_reopens_the_gate(session):
    """다시 교정하면 prepare 가 통과한다."""
    s, client, _seq = session
    s.invalidate_calibration()
    s._finish_calibrate("r-cal")

    s.handle_request(*_req("prepare", job_id="jb1", area=AREA, plan=PLAN, scan=SCAN))

    assert client.res[-1]["code"] == 202


def test_abort_clears_the_origin_wait(session):
    """abort 뒤에는 원점 대기가 풀려 query 가 ready 로 답하지 않는다."""
    s, client, seq = session
    s.job_requested.connect(lambda plan: seq.start(plan, 30.0, move_first=True))
    s.handle_request(*_req("prepare", job_id="jb1", area=AREA, scan=SCAN))
    s.notify_at_origin()

    s.handle_request(*_req("abort", job_id="jb1"))
    seq.stop()
    s.handle_request("query", {"req_id": "q-after-abort"})

    assert client.res[-1]["state"] != "ready"


def test_abort_during_marking_frees_the_next_mark(session):
    """마킹 도중 abort 하면 다음 mark 가 409 로 막히지 않는다."""
    s, client, _ = session
    s.handle_request(*_req("mark", points=[{"id": "p1", "x": 1, "y": 2}]))
    s.handle_request(*_req("abort"))

    s.handle_request("mark", {"req_id": "req-mark-2",
                              "points": [{"id": "p2", "x": 1, "y": 2}]})

    assert client.res[-1]["code"] == 202


def test_aborted_mark_sends_no_complete(session):
    """abort 된 작업은 완료를 내지 않는다 (탭4 D-3)."""
    s, client, _ = session
    s.handle_request(*_req("mark", points=[{"id": "p1", "x": 1, "y": 2}]))
    s.handle_request(*_req("abort"))

    s.finish_mark(["p1"], [])

    assert not [e for e in client.events if e[0] == "complete"]


def test_no_ready_goes_to_erut_for_a_job_it_did_not_ask_for(session):
    """RCS '검사 시작'·사내 MC 작업은 ERUT 가 시킨 게 아니다 — ready 를 안 낸다.

    req_id 가 빈 evt/ready 를 흘리면 ERUT 가 보낸 적 없는 준비 완료를 받는다.
    """
    s, client, _ = session

    s.notify_at_origin()

    assert client.events == []
    assert s.robot_state() != "ready"
