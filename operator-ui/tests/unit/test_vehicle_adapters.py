"""차량 어댑터 시험 — 가짜 차량 클라이언트로 명령 순서와 도착 판정을 본다.

가정한 규칙(vehicle_adapters / vehicle_sim 과 같다):
  AMR     SetJob(이번 이동 거리 m, radius 모재 반지름 m) -> RUNNING -> STOP 이고 mv_dist≈set_dist 면 도착
  아웃트리거 ManualCommand(cmd_outrg_set 2/1, 전체로만) -> hold SET/RELEASE
  리프트   SetMvLift(set_mv_lift: job_id, set_height m, set_speed m/s) -> lift_h ≈ 목표 (자동 동작 중)
  작업 끝  RobotControl(job_finish = 1) -> 받아들여지고 hold RELEASE · 서 있으면 끝 (2026-11-20)
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


def test_amr_sets_a_job_then_runs_and_arrives_on_stop(car, qtbot) -> None:
    amr = VehicleAmrAdapter(car, speed_mps=0.2)
    got = _spy(amr)
    amr.move_to(700.0, " mm")
    assert car.calls[0] == ("set_job", "RCS-0001", 0.7, 0.2), "이번 이동 거리 m"
    assert len(car.calls) == 1, "작업 설정이 받아들여져야 출발한다"

    car.command_result.emit("set_job", True, "ok")
    assert car.calls[1] == ("control", "RUNNING", False, False)

    car.status(state="STOP", job_id="RCS-0001", set_dist=0.7, mv_dist=0.7)
    assert got["arrived"] == 0, "RUNNING 을 보기 전의 STOP 은 지난 상태일 수 있다"
    car.status(state="RUNNING", job_id="RCS-0001", set_dist=0.7, mv_dist=0.35)
    assert amr.position == pytest.approx(350.0)
    car.status(state="STOP", job_id="RCS-0001", set_dist=0.7, mv_dist=0.7)
    assert got["arrived"] == 1 and not amr.moving and amr.position == 700.0

    # 다음 이동은 지금 자리에서의 차이만큼 — 뒤로 가면 음수.
    amr.move_to(500.0)
    assert car.calls[-1] == ("set_job", "RCS-0002", -0.2, 0.2)


def test_amr_error_or_rejection_stops_waiting(car, qtbot) -> None:
    amr = VehicleAmrAdapter(car)
    got = _spy(amr)
    amr.move_to(300.0)
    car.command_result.emit("set_job", False, "Rejected: busy")
    assert got["problem"] and "거절" in got["problem"][0] and not amr.moving

    amr.move_to(300.0)
    car.command_result.emit("set_job", True, "ok")
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


def test_outrigger_set_and_release(car, qtbot) -> None:
    out = VehicleOutriggerAdapter(car)
    got = _spy(out)
    out.move_to(1, " 고정")
    assert car.calls[-1] == ("manual", {"cmd_outrg_set": 2})
    car.status(hold="RELEASE")
    assert got["arrived"] == 0
    car.status(hold="SET", state="HOLD")
    assert got["arrived"] == 1

    out.move_to(0)
    assert car.calls[-1] == ("manual", {"cmd_outrg_set": 1})
    car.status(hold="RELEASE")
    assert got["arrived"] == 2


def test_lift_moves_in_metres_and_reports_mm(car, qtbot) -> None:
    lift = VehicleLiftAdapter(car, speed_mps=0.04)
    got = _spy(lift)
    car.status(hold="SET", lift_h=0.0)
    lift.move_to(960.0, " mm")
    # 자동 동작 중 — 자동 서비스(manual=False)로, 차량 설정의 리프트 속도를 싣는다.
    assert car.calls[-1] == ("lift", "RCS-L0001", 0.96, 0.04)
    car.status(hold="SET", lift_h=0.5)
    assert lift.position == pytest.approx(500.0) and got["arrived"] == 0
    car.status(hold="SET", lift_h=0.9595)
    assert got["arrived"] == 1

    car.command_result.emit("set_mv_lift", False, "Rejected: outriggers not set")
    assert got["problem"] == [], "움직이지 않을 때 온 거절은 무시한다"
    lift.move_to(1500.0)
    assert car.calls[-1][1] == "RCS-L0002", "명령마다 새 job_id"
    car.command_result.emit("manual_command", False, "Rejected: busy")
    assert got["problem"] == [], "리프트는 수동 명령(manual_command) 거절을 보지 않는다"
    car.command_result.emit("set_mv_lift", False, "Rejected: outriggers not set")
    assert "outriggers not set" in got["problem"][0]


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
    amr.move_to(200.0)
    assert car.calls[0][0] == "set_job"
    assert not amr.select("dummy"), "움직이는 중에는 바꾸지 않는다"
    car.command_result.emit("set_job", True, "ok")
    car.status(state="RUNNING", job_id="RCS-0001", set_dist=0.2, mv_dist=0.1)
    car.status(state="STOP", job_id="RCS-0001", set_dist=0.2, mv_dist=0.2)
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
    """작업 끝 — 차량이 받아들이고, 홀딩을 풀고 아웃트리거를 올려(hold RELEASE) 서 있어야 끝이다."""
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
    car.command_result.emit("job_finish", True, "Job finished — hold released")
    assert got["arrived"] == 0, "아웃트리거가 아직 내려가 있다"
    car.status(hold="RELEASE", state="STOP")
    assert got["arrived"] == 1 and got["problem"] == []


def test_finish_on_an_already_released_vehicle_ends_on_the_answer(car, qtbot) -> None:
    """캘리브레이션 주행 뒤처럼 아웃트리거가 올라가 있으면 받았다는 답으로 바로 끝난다."""
    finish = VehicleFinishAdapter(car)
    got = _spy(finish)
    car.status(hold="RELEASE", state="STOP")
    finish.move_to(0)
    car.command_result.emit("job_finish", True, "Job finished — hold released")
    assert got["arrived"] == 1


def test_finish_rejected_or_error_is_a_problem(car, qtbot) -> None:
    finish = VehicleFinishAdapter(car)
    got = _spy(finish)
    car.status(hold="SET", state="HOLD")
    finish.move_to(0)
    car.command_result.emit("job_finish", False, "Rejected: moving")
    assert got["arrived"] == 0 and "Rejected: moving" in got["problem"][-1]

    finish.move_to(0)
    car.status(state="ERROR", error_code=21, error_msg="모의 오류 21", hold="SET")
    assert "차량 오류 21" in got["problem"][-1]
    finish.cancel()
    assert ("control", "STOP", False, False) not in car.calls, "기다리기만 그만두고 차량을 세우지 않는다"
