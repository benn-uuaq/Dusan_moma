"""차량 어댑터 시험 — 가짜 차량 클라이언트로 명령 순서와 도착 판정을 본다.

규칙은 AGV 담당 제어 노드(src/steer_agv, 2026-11-23)와 같다(vehicle_adapters · vehicle_sim):
  AMR     SetJob(새 job_id, 이번 이동 거리 m, radius) -> 차량이 STOP/PAUSE 면 RUNNING -> HOLD 이고 mv≈set 이면 도착
          일시정지 PAUSE(작업 유지) → 재개 RUNNING, 정지 STOP + job_cancel
  아웃트리거 고정 = 거리 0 SetJob + RUNNING → HOLD(hold SET), 해제 = FINISH → hold RELEASE
  리프트   SetMvLift(lift_control: job_id, set_height m, set_speed m/s) -> lift_h ≈ 목표
  작업 끝  RobotControl(state FINISH) -> 받아들여지고 hold RELEASE · 서 있으면 끝
"""

import pytest
from PyQt6.QtCore import QObject, pyqtSignal

from smr_operator_ui.services.motion_adapters import DummyMotionAdapter, SwitchableMotion
from smr_operator_ui.services.vehicle_adapters import (
    VehicleAmrAdapter, VehicleFinishAdapter, VehicleLiftAdapter, VehicleOutriggerAdapter,
)


class FakeVehicle(QObject):
    status_changed = pyqtSignal(dict)
    command_result = pyqtSignal(str, bool, str)

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple] = []
        self.last_status: dict = {}
        self.accept = True

    def set_job(self, job_id, set_dist, set_speed, standoff=0.0, radius=0.0):
        self.calls.append(("set_job", job_id, round(set_dist, 6), set_speed))
        self.standoff = standoff
        self.radius = radius
        return True

    def control(self, state="", reset=False, job_cancel=False):
        self.calls.append(("control", state, reset, job_cancel))
        return True

    def manual(self, **fields):
        self.calls.append(("manual", fields))
        return True

    def lift(self, job_id, height_m, speed_mps):
        self.calls.append(("lift", job_id, round(height_m, 6), speed_mps))
        return True

    def finish_job(self):
        self.calls.append(("finish",))
        return True

    def new_job_id(self):
        self.jobs = getattr(self, "jobs", 0) + 1
        return f"RCS-{self.jobs:04d}"

    def status(self, **values):
        base = {"state": "STOP", "hold": "RELEASE", "error_code": 0, "error_msg": "",
                "job_id": "", "set_dist": 0.0, "mv_dist": 0.0, "lift_h": 0.0}
        base.update(values)
        self.last_status = base
        self.status_changed.emit(dict(base))


@pytest.fixture
def car(qtbot):
    return FakeVehicle()


def _spy(adapter):
    got = {"arrived": 0, "problem": []}
    adapter.arrived.connect(lambda: got.__setitem__("arrived", got["arrived"] + 1))
    adapter.problem.connect(got["problem"].append)
    return got


def test_amr_sets_a_job_then_runs_and_arrives_on_hold(car, qtbot) -> None:
    amr = VehicleAmrAdapter(car, speed_mps=0.2)
    got = _spy(amr)
    car.status(state="STOP")
    amr.move_to(700.0, " mm")
    assert car.calls[0] == ("set_job", "RCS-0001", 0.7, 0.2), "이번 이동 거리 m"
    assert len(car.calls) == 1, "작업 등록이 받아들여져야 출발한다"

    car.command_result.emit("set_job", True, "accepted")
    assert car.calls[1] == ("control", "RUNNING", False, False)

    car.status(state="HOLD", hold="SET", job_id="RCS-0001", set_dist=0.7, mv_dist=0.7)
    assert got["arrived"] == 0, "RUNNING 을 보기 전의 HOLD 는 지난 상태일 수 있다"
    car.status(state="RUNNING", job_id="RCS-0001", set_dist=0.7, mv_dist=0.35)
    assert amr.position == pytest.approx(350.0)
    car.status(state="STOP", job_id="RCS-0001", set_dist=0.7, mv_dist=0.7)
    assert got["arrived"] == 0, "차량은 도착하면 HOLD(아웃트리거 고정)한다 — STOP 은 아직이다"
    car.status(state="HOLD", hold="SET", job_id="RCS-0001", set_dist=0.7, mv_dist=0.6965)
    assert got["arrived"] == 1 and not amr.moving and amr.position == 700.0, "5 mm 안이면 도착"

    # 다음 이동은 지금 자리에서의 차이만큼 — 뒤로 가면 음수. job_id 는 늘 새 번호.
    amr.move_to(500.0)
    assert car.calls[-1] == ("set_job", "RCS-0002", -0.2, 0.2)


def test_amr_waits_for_stop_after_registering_in_finish(car, qtbot) -> None:
    """FINISH 에서 작업을 등록하면 차량이 곧 STOP 이 된다 — 그 전에 RUNNING 을 보내면 거절된다."""
    amr = VehicleAmrAdapter(car)
    car.status(state="FINISH", hold="RELEASE")
    amr.move_to(300.0)
    car.command_result.emit("set_job", True, "accepted")
    assert car.calls[-1][0] == "set_job", "아직 FINISH — RUNNING 을 미룬다"
    car.status(state="STOP", hold="RELEASE", job_id="RCS-0001", set_dist=0.3)
    assert car.calls[-1] == ("control", "RUNNING", False, False)
    car.status(state="STOP", hold="RELEASE", job_id="RCS-0001", set_dist=0.3)
    assert [c for c in car.calls if c[:2] == ("control", "RUNNING")] == [("control", "RUNNING", False, False)]


def test_amr_refuses_to_start_while_the_outriggers_hold(car, qtbot) -> None:
    amr = VehicleAmrAdapter(car)
    got = _spy(amr)
    car.status(state="HOLD", hold="SET")
    amr.move_to(300.0)
    car.command_result.emit("set_job", True, "accepted")
    assert "FINISH" in got["problem"][0] and ("control", "RUNNING", False, False) not in car.calls


def test_amr_pause_keeps_the_job_and_resume_runs_it_again(car, qtbot) -> None:
    """일시정지 = PAUSE(차량에 작업이 남는다), 재개 = 같은 목표로 move_to → 새 SetJob 없이 RUNNING."""
    amr = VehicleAmrAdapter(car)
    got = _spy(amr)
    car.status(state="STOP")
    amr.move_to(400.0)
    car.command_result.emit("set_job", True, "accepted")
    car.status(state="RUNNING", job_id="RCS-0001", set_dist=0.4, mv_dist=0.1)
    amr.pause()
    assert car.calls[-1] == ("control", "PAUSE", False, False)
    assert not amr.moving and amr.paused
    car.status(state="PAUSE", job_id="RCS-0001", set_dist=0.4, mv_dist=0.1)

    amr.move_to(400.0, label="재개")
    assert car.calls[-1] == ("control", "RUNNING", False, False), "새 SetJob 을 보내지 않는다"
    assert sum(1 for c in car.calls if c[0] == "set_job") == 1
    car.status(state="RUNNING", job_id="RCS-0001", set_dist=0.4, mv_dist=0.3)
    car.status(state="HOLD", hold="SET", job_id="RCS-0001", set_dist=0.4, mv_dist=0.4)
    assert got["arrived"] == 1 and not amr.paused


def test_amr_stop_cancels_the_job_even_when_paused(car, qtbot) -> None:
    amr = VehicleAmrAdapter(car)
    car.status(state="STOP")
    amr.move_to(400.0)
    car.command_result.emit("set_job", True, "accepted")
    amr.cancel()
    assert car.calls[-1] == ("control", "STOP", False, True), "정지 = STOP + job_cancel"
    amr.move_to(400.0)
    car.command_result.emit("set_job", True, "accepted")
    amr.pause()
    amr.cancel()
    assert car.calls[-1] == ("control", "STOP", False, True) and not amr.paused
    amr.cancel()
    assert car.calls[-1] == ("control", "STOP", False, True)
    assert sum(1 for c in car.calls if c == ("control", "STOP", False, True)) == 2, "할 일이 없으면 보내지 않는다"


def test_amr_error_or_rejection_stops_waiting(car, qtbot) -> None:
    amr = VehicleAmrAdapter(car)
    got = _spy(amr)
    amr.move_to(300.0)
    car.command_result.emit("set_job", False, "busy, missing or duplicate job_id")
    assert got["problem"] and "거절" in got["problem"][0] and not amr.moving

    amr.move_to(300.0)
    car.command_result.emit("set_job", True, "accepted")
    car.status(state="ERROR", error_code=21, error_msg="모터 과부하", job_id="RCS-0002")
    assert "차량 오류 21" in got["problem"][-1]
    assert got["arrived"] == 0


def test_amr_timeout(car, qtbot) -> None:
    amr = VehicleAmrAdapter(car, speed_mps=10.0)     # 달릴 시간이 거의 없게 — 제한 ≈ TIMEOUT_MS
    amr.TIMEOUT_MS = 50
    got = _spy(amr)
    amr.move_to(100.0)
    qtbot.waitUntil(lambda: bool(got["problem"]), timeout=1000)
    assert "초 안에" in got["problem"][0]


def test_outrigger_set_by_a_zero_job_and_release_by_finish(car, qtbot) -> None:
    """자동 모드에는 아웃트리거만 내리는 명령이 없다 — 거리 0 작업이 도착하면 차량이 HOLD 한다."""
    out = VehicleOutriggerAdapter(car)
    out.job_values = lambda: (0.1, 2.0, 2.5)
    got = _spy(out)
    car.status(state="STOP", hold="RELEASE")
    out.move_to(1, " 고정")
    assert car.calls[-1] == ("set_job", "RCS-0001", 0.0, 0.1) and car.radius == 2.5
    car.command_result.emit("set_job", True, "accepted")
    assert car.calls[-1] == ("control", "RUNNING", False, False)
    car.status(state="RUNNING", hold="RELEASE", job_id="RCS-0001")
    assert got["arrived"] == 0
    car.status(state="HOLD", hold="SET", job_id="RCS-0001")
    assert got["arrived"] == 1 and out.position == 1.0

    out.move_to(1)
    qtbot.waitUntil(lambda: got["arrived"] == 2, timeout=1000)
    assert car.calls[-1] == ("control", "RUNNING", False, False), "이미 고정이면 보내지 않는다"

    out.move_to(0)
    assert car.calls[-1] == ("finish",)
    car.status(state="FINISH", hold="RELEASE")
    assert got["arrived"] == 3 and out.position == 0.0
    car.command_result.emit("finish", False, "FINISH requires stationary automatic mode and idle lift")
    assert got["problem"] == [], "끝난 뒤에 온 거절은 무시한다"


def test_lift_moves_in_metres_and_reports_mm(car, qtbot) -> None:
    lift = VehicleLiftAdapter(car, speed_mps=0.04)
    got = _spy(lift)
    car.status(hold="SET", lift_h=0.0)
    lift.move_to(960.0, " mm")
    # 자동 동작 중 — lift_control 로, 차량 설정의 리프트 속도를 싣는다.
    assert car.calls[-1] == ("lift", "RCS-L0001", 0.96, 0.04)
    car.status(hold="SET", lift_h=0.5)
    assert lift.position == pytest.approx(500.0) and got["arrived"] == 0
    car.status(hold="SET", lift_h=0.9575)
    assert got["arrived"] == 1, "5 mm 안이면 도착(AGV 노드는 3 mm 안에서 리프트 작업을 끝낸다)"

    car.command_result.emit("lift_control", False, "lift requires stopped and secured vehicle")
    assert got["problem"] == [], "움직이지 않을 때 온 거절은 무시한다"
    lift.move_to(1500.0)
    assert car.calls[-1][1] == "RCS-L0002", "명령마다 새 job_id"
    car.command_result.emit("manual_command", False, "manual mode required")
    assert got["problem"] == [], "리프트는 수동 명령(manual_command) 거절을 보지 않는다"
    car.command_result.emit("lift_control", False, "lift height out of range")
    assert "out of range" in got["problem"][0]


def test_switchable_motion_forwards_only_the_active_backend(car, qtbot) -> None:
    dummy = DummyMotionAdapter("AMR", travel_ms=10)
    real = VehicleAmrAdapter(car)
    amr = SwitchableMotion({"dummy": dummy, "vehicle": real}, "dummy")
    arrived = []
    amr.arrived.connect(lambda: arrived.append(amr.active))

    amr.move_to(100.0)
    qtbot.waitUntil(lambda: arrived == ["dummy"], timeout=1000)
    assert car.calls == [], "더미일 때 차량에 명령이 가면 안 된다"

    assert amr.select("vehicle")
    car.status(state="STOP")
    amr.move_to(200.0)
    assert car.calls[0][0] == "set_job"
    assert not amr.select("dummy"), "움직이는 중에는 바꾸지 않는다"
    car.command_result.emit("set_job", True, "accepted")
    car.status(state="RUNNING", job_id="RCS-0001", set_dist=0.2, mv_dist=0.1)
    amr.pause()
    assert car.calls[-1] == ("control", "PAUSE", False, False), "실제 차량의 일시정지는 PAUSE"
    car.status(state="PAUSE", job_id="RCS-0001", set_dist=0.2, mv_dist=0.1)
    amr.move_to(200.0)
    assert car.calls[-1] == ("control", "RUNNING", False, False)
    car.status(state="RUNNING", job_id="RCS-0001", set_dist=0.2, mv_dist=0.15)
    car.status(state="HOLD", hold="SET", job_id="RCS-0001", set_dist=0.2, mv_dist=0.2)
    assert arrived == ["dummy", "vehicle"]
    assert not amr.select("nope")


def test_amr_time_limit_grows_with_the_distance(car) -> None:
    """기본 0.1 m/s 로 한 바퀴(캘리브레이션)처럼 멀리 가면 고정 120 초로는 모자란다(2026-11-12)."""
    amr = VehicleAmrAdapter(car)
    assert amr.speed_mps == 0.1                       # 차량 주행 속도 기본 0.1 m/s
    amr.move_to(20000.0)                              # 20 m — 달릴 시간 200 초
    assert amr._timer.interval() == 120_000 + int(200_000 * amr.TRAVEL_MARGIN)
    # SetJob 은 거리 · 속도 · 거리 유지만 — 격자 · 크기(예전 total_* · offset_*)는 RCS 가 관리한다.
    assert car.calls[-1] == ("set_job", "RCS-0001", 20.0, 0.1)


def test_amr_explicit_time_limit_and_standoff(car) -> None:
    """캘리브레이션 한 바퀴는 차량 설정의 최대 시간을 그대로 쓰고, 거리 유지를 SetJob 에 싣는다."""
    amr = VehicleAmrAdapter(car)
    amr.standoff_m = 2.0
    amr.move_to(20000.0, timeout_ms=310_000)
    assert amr._timer.interval() == 310_000
    assert car.standoff == 2.0


def test_amr_sends_the_target_radius_each_time(car) -> None:
    """SetJob.radius = 목표 대상(모재) 반지름 [m] — 보낼 때마다 묻는다(AGV 판 2026-11-20)."""
    amr = VehicleAmrAdapter(car)
    amr.move_to(100.0)
    assert car.radius == 0.0, "모르면 0"
    radius = {"m": 2.5}
    amr.radius_source = lambda: radius["m"]
    amr.reset(0.0)
    amr.move_to(100.0)
    assert car.radius == 2.5
    radius["m"] = 3.0                      # 검사 대상 지름이 바뀌면 다음 이동부터
    amr.reset(0.0)
    amr.move_to(100.0)
    assert car.radius == 3.0


def test_finish_waits_for_the_hold_release_after_it_is_accepted(car, qtbot) -> None:
    """작업 끝 — 차량이 받아들이고, 홀딩을 풀고 아웃트리거를 올려 state FINISH · hold RELEASE 를 보고해야 끝이다."""
    finish = VehicleFinishAdapter(car)
    got = _spy(finish)
    car.status(hold="SET", state="HOLD")
    finish.move_to(0)
    assert car.calls == [("finish",)]
    car.status(hold="RELEASE", state="STOP")
    assert got["arrived"] == 0, "받아들였다는 답 전에는 끝으로 보지 않는다"
    car.status(hold="SET", state="HOLD")
    car.command_result.emit("robot_control", True, "Done")
    assert got["arrived"] == 0, "다른 robot_control 결과는 작업 끝이 아니다"
    car.command_result.emit("finish", True, "accepted")
    assert got["arrived"] == 0, "아웃트리거가 아직 내려가 있다"
    car.status(hold="RELEASE", state="STOP")
    assert got["arrived"] == 0, "FINISH 를 보고하기 전의 옛 상태일 수 있다"
    car.status(hold="RELEASE", state="FINISH")
    assert got["arrived"] == 1 and got["problem"] == []


def test_finish_waits_for_the_finish_state_even_when_released(car, qtbot) -> None:
    """아웃트리거가 이미 올라가 있어도 차량이 FINISH 를 보고할 때까지 기다린다 — 옛 STOP 으로 다음 작업을
    출발시키면 차량이 아직 FINISH 라 RUNNING 을 거절한다."""
    finish = VehicleFinishAdapter(car)
    got = _spy(finish)
    car.status(hold="RELEASE", state="STOP")
    finish.move_to(0)
    car.command_result.emit("finish", True, "accepted")
    assert got["arrived"] == 0
    car.status(hold="RELEASE", state="FINISH")
    assert got["arrived"] == 1
    finish.move_to(0)                                   # 이미 FINISH — 받았다는 답으로 끝난다
    car.command_result.emit("finish", True, "accepted")
    assert got["arrived"] == 2


def test_finish_rejected_or_error_is_a_problem(car, qtbot) -> None:
    finish = VehicleFinishAdapter(car)
    got = _spy(finish)
    car.status(hold="SET", state="HOLD")
    finish.move_to(0)
    car.command_result.emit("finish", False, "FINISH requires stationary automatic mode and idle lift")
    assert got["arrived"] == 0 and "idle lift" in got["problem"][-1]

    finish.move_to(0)
    car.status(state="ERROR", error_code=21, error_msg="모의 오류 21", hold="SET")
    assert "차량 오류 21" in got["problem"][-1]
    finish.cancel()
    assert ("control", "STOP", False, False) not in car.calls, "기다리기만 그만두고 차량을 세우지 않는다"


# ---- 정지 · 실패 때 차량에 작업이 남지 않게 (2026-11-24 검토) --------------------------------
CANCEL = ("control", "STOP", False, True)


def test_timeout_while_driving_stops_and_cancels_the_job(car, qtbot) -> None:
    """예전에는 시간 초과로 포기해도 차량에 아무것도 안 보냈다 — 차량이 계속 가거나 다음 SetJob 이 busy 로 거절됐다."""
    amr = VehicleAmrAdapter(car)
    got = _spy(amr)
    car.status(state="STOP")
    amr.move_to(500.0)
    car.command_result.emit("set_job", True, "accepted")
    car.status(state="RUNNING", job_id="RCS-0001", set_dist=0.5, mv_dist=0.1)
    amr._on_timeout()
    assert got["problem"] and car.calls[-1] == CANCEL
    amr.cancel()                                        # 이어지는 정지 · ERUT 정지 — 두 번 보내지 않는다
    assert car.calls.count(CANCEL) == 1


def test_stop_before_the_set_job_answer_cancels_once_it_is_registered(car, qtbot) -> None:
    amr = VehicleAmrAdapter(car)
    car.status(state="STOP")
    amr.move_to(500.0)
    amr.cancel()                                        # 답 전에 정지
    assert CANCEL not in car.calls, "아직 등록 전 — 보낼 것이 없다"
    car.command_result.emit("set_job", True, "accepted")
    assert car.calls[-1] == CANCEL, "등록되자마자 버린다"
    assert ("control", "RUNNING", False, False) not in car.calls

    amr2 = VehicleAmrAdapter(car)
    amr2.move_to(300.0)
    amr2.cancel()
    car.command_result.emit("set_job", False, "set_job requires idle automatic mode")
    assert car.calls.count(CANCEL) == 1, "거절된 작업은 버릴 것이 없다"


def test_start_refused_after_registering_drops_the_registered_job(car, qtbot) -> None:
    """작업은 등록됐는데 출발이 거절되면(RUNNING 실패) 그 작업을 버린다 — HOLD 면 보내지 않는다(FINISH 가 끝낸다)."""
    amr = VehicleAmrAdapter(car)
    car.status(state="STOP")
    amr.move_to(500.0)
    car.command_result.emit("set_job", True, "accepted")
    car.command_result.emit("robot_control", False, "release outriggers and stop or pause before RUNNING")
    assert car.calls[-1] == CANCEL

    hold = VehicleAmrAdapter(car)
    car.status(state="HOLD", hold="SET")
    hold.move_to(800.0)
    car.command_result.emit("set_job", True, "accepted")
    assert car.calls.count(CANCEL) == 1, "HOLD 에는 job_cancel 의 STOP 을 보내지 않는다"


def test_outrigger_stop_cancels_the_zero_job_but_not_a_finish(car, qtbot) -> None:
    out = VehicleOutriggerAdapter(car)
    car.status(state="STOP", hold="RELEASE")
    out.move_to(1, " 고정")
    car.command_result.emit("set_job", True, "accepted")
    car.status(state="RUNNING", hold="RELEASE", job_id="RCS-0001")
    out.cancel()
    assert car.calls[-1] == CANCEL, "고정용 거리 0 작업도 버린다(예전에는 job_cancel 없는 STOP)"

    car.status(state="HOLD", hold="SET")
    out.move_to(0, " 해제")                              # FINISH 를 기다리는 중
    calls = len(car.calls)
    out.cancel()
    assert len(car.calls) == calls, "FINISH 는 차량이 마저 한다 — STOP 을 보내지 않는다(FINISH 에 STOP 은 거절)"


def test_stop_after_arrival_sends_nothing(car, qtbot) -> None:
    amr = VehicleAmrAdapter(car)
    car.status(state="STOP")
    amr.move_to(300.0)
    car.command_result.emit("set_job", True, "accepted")
    car.status(state="RUNNING", job_id="RCS-0001", set_dist=0.3, mv_dist=0.2)
    car.status(state="HOLD", hold="SET", job_id="RCS-0001", set_dist=0.3, mv_dist=0.3)
    calls = len(car.calls)
    amr.cancel()
    assert len(car.calls) == calls, "도착해 HOLD — 끝난 작업이라 보낼 것이 없다"


# ---- 리프트 도착 = lift_state FINISH 를 받은 뒤 높이 비교 (2026-11-24) --------------------------
def _lift(car):
    lift = VehicleLiftAdapter(car, speed_mps=0.05)
    car.status(state="HOLD", hold="SET", lift_h=0.0, lift_state="FINISH")   # 지난 명령의 FINISH
    return lift, _spy(lift)


def test_lift_arrives_on_finish_signal_then_height_check(car, qtbot) -> None:
    lift, got = _lift(car)
    lift.move_to(780.0, " mm")
    car.status(state="HOLD", hold="SET", lift_h=0.0, lift_state="FINISH")
    assert got["arrived"] == 0 and not got["problem"], "RUNNING 전의 FINISH 는 지난 명령 것 — 기다린다"
    car.status(state="HOLD", hold="SET", lift_h=0.4, lift_state="RUNNING")
    car.status(state="HOLD", hold="SET", lift_h=0.78, lift_state="RUNNING")
    assert got["arrived"] == 0, "높이가 닿아도 도착 신호(FINISH) 전에는 끝내지 않는다"
    car.status(state="HOLD", hold="SET", lift_h=0.7785, lift_state="FINISH")
    assert got["arrived"] == 1 and not lift.moving


def test_lift_finish_with_a_wrong_height_fails_with_both_heights(car, qtbot) -> None:
    lift, got = _lift(car)
    lift.move_to(780.0, " mm")
    car.status(state="HOLD", hold="SET", lift_h=0.3, lift_state="RUNNING")
    car.status(state="HOLD", hold="SET", lift_h=0.768, lift_state="FINISH")
    assert got["arrived"] == 0
    assert got["problem"] and "높이 768 mm · 목표 780 mm" in got["problem"][0] and "12 mm" in got["problem"][0]


def test_lift_pause_then_finish_and_stop_before_target(car, qtbot) -> None:
    """lift_state 는 STOP · RUNNING · PAUSE · FINISH(AGV 측과 정함). PAUSE 도 이번 명령이 시작된 표시다."""
    lift, got = _lift(car)
    lift.move_to(780.0, " mm")
    car.status(state="HOLD", hold="SET", lift_h=0.2, lift_state="PAUSE")
    car.status(state="HOLD", hold="SET", lift_h=0.6, lift_state="FINISH")
    assert got["problem"] and "높이 600 mm · 목표 780 mm" in got["problem"][0], "PAUSE 를 봤으니 이번 FINISH 다"

    lift2, got2 = _lift(car)
    lift2.move_to(500.0, " mm")
    car.status(state="HOLD", hold="SET", lift_h=0.1, lift_state="RUNNING")
    car.status(state="HOLD", hold="SET", lift_h=0.1, lift_state="STOP")
    assert got2["problem"] and "목표 전에 멈췄습니다" in got2["problem"][0]


def test_lift_without_lift_state_uses_the_height_only(car, qtbot) -> None:
    lift = VehicleLiftAdapter(car, speed_mps=0.05)
    got = _spy(lift)
    car.status(state="HOLD", hold="SET", lift_h=0.0)
    lift.move_to(780.0, " mm")
    car.status(state="HOLD", hold="SET", lift_h=0.778)
    assert got["arrived"] == 1, "lift_state 를 안 채우는 AGV — 예전처럼 ±5 mm"
