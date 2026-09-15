"""차량 어댑터 시험 — 가짜 차량 클라이언트로 명령 순서와 도착 판정을 본다.

가정한 규칙(vehicle_adapters / vehicle_sim 과 같다):
  AMR     SetJob(이번 이동 거리 m) -> RUNNING -> STOP 이고 mv_dist≈set_dist 면 도착
  아웃트리거 ManualCommand(cmd_outrg_set 2/1) -> hold SET/RELEASE
  리프트   ManualCommand(cmd_mv_lift=1, lift_height m) -> lift_h ≈ 목표
"""

import pytest
from PyQt6.QtCore import QObject, pyqtSignal

from smr_operator_ui.services.motion_adapters import DummyMotionAdapter, SwitchableMotion
from smr_operator_ui.services.vehicle_adapters import (
    VehicleAmrAdapter, VehicleLiftAdapter, VehicleOutriggerAdapter,
)


class FakeVehicle(QObject):
    status_changed = pyqtSignal(dict)
    command_result = pyqtSignal(str, bool, str)

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple] = []
        self.last_status: dict = {}
        self.accept = True

    def set_job(self, job_id, set_dist, set_speed, **kw):
        self.calls.append(("set_job", job_id, round(set_dist, 6), set_speed))
        return True

    def control(self, state="", reset=False, job_cancel=False):
        self.calls.append(("control", state, reset, job_cancel))
        return True

    def manual(self, **fields):
        self.calls.append(("manual", fields))
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
    amr = VehicleAmrAdapter(car)
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
    lift = VehicleLiftAdapter(car)
    got = _spy(lift)
    car.status(hold="SET", lift_h=0.0)
    lift.move_to(960.0, " mm")
    assert car.calls[-1] == ("manual", {"cmd_mv_lift": 1, "lift_height": 0.96})
    car.status(hold="SET", lift_h=0.5)
    assert lift.position == pytest.approx(500.0) and got["arrived"] == 0
    car.status(hold="SET", lift_h=0.9595)
    assert got["arrived"] == 1

    car.command_result.emit("manual_command", False, "Rejected: outriggers not set")
    assert got["problem"] == [], "움직이지 않을 때 온 거절은 무시한다"
    lift.move_to(1500.0)
    car.command_result.emit("manual_command", False, "Rejected: outriggers not set")
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
