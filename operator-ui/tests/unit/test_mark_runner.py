"""MarkRunner: 마킹 점마다 차량 -> 고정 -> 리프트 -> 로봇 -> 복귀 순서."""

from PyQt6.QtCore import QObject, pyqtSignal

from smr_operator_ui.services import MarkRunner


class FakeAxis(QObject):
    """move_to 를 받으면 기록만 한다. 도착은 시험이 직접 알린다."""

    arrived = pyqtSignal()

    def __init__(self, name, log):
        super().__init__()
        self.name, self.log = name, log
        self.targets = []

    def move_to(self, target, unit="", label=""):
        self.targets.append(float(target))
        self.log.append(self.name)


def _runner(qtbot):
    log = []
    amr, lift = FakeAxis("차량", log), FakeAxis("리프트", log)
    outrigger, retractor = FakeAxis("고정", log), FakeAxis("복귀", log)
    calls = {"target": [], "play": 0, "restore": 0}

    def target(u, v):
        calls["target"].append((u, v))
        log.append("목표")

    def play():
        calls["play"] += 1
        log.append("로봇")

    def restore():
        calls["restore"] += 1

    r = MarkRunner(amr, lift, outrigger, retractor, target, play, restore)
    return r, (amr, lift, outrigger, retractor), calls, log


def _state(v):
    return [v] + [0] * 9


def test_one_point_runs_the_full_order(qtbot):
    """차량 -> 고정 -> 리프트 -> 목표·로봇 -> (홈 도착) -> 복귀."""
    r, (amr, lift, outrigger, retractor), calls, log = _runner(qtbot)
    done = []
    r.finished.connect(lambda m, f: done.append((m, f)))

    r.start([{"id": "p1", "x": 1000.0, "y": 1200.0}], 721.0, 500.0)
    amr.arrived.emit()
    outrigger.arrived.emit()
    lift.arrived.emit()
    r.handle_scan_state(_state(10))     # 로봇이 이번 점을 시작했다
    r.handle_scan_state(_state(12))     # 홈 도착 = 끝
    retractor.arrived.emit()

    assert log == ["차량", "고정", "리프트", "목표", "로봇", "복귀"]
    # 점이 격자 가운데·원점 높이에 오도록 둔다 -> 로봇 목표 (W/2, 0)
    assert amr.targets == [1000.0 - 721.0 / 2]
    assert lift.targets == [1200.0]
    assert calls["target"] == [(721.0 / 2, 0.0)]
    assert done == [(["p1"], [])]
    assert calls["restore"] == 1, "끝나고 스캔 태스크를 되돌리지 않았다"


def test_a_stale_done_state_is_not_taken_for_this_point(qtbot):
    """지난 점의 12 가 남아 있어도, 로봇이 움직인 뒤의 12 만 인정한다."""
    r, (amr, lift, outrigger, retractor), _calls, log = _runner(qtbot)
    r.start([{"id": "p1", "x": 0.0, "y": 0.0}], 721.0, 500.0)
    amr.arrived.emit()
    outrigger.arrived.emit()
    lift.arrived.emit()

    r.handle_scan_state(_state(12))     # play 직후 남아 있던 옛 값

    assert "복귀" not in log


def test_failed_point_is_reported_and_the_next_one_still_runs(qtbot):
    """벽을 못 찾은 점(290 == 9)은 실패로 적고 다음 점으로 간다."""
    r, (amr, lift, outrigger, retractor), _calls, _log = _runner(qtbot)
    done = []
    r.finished.connect(lambda m, f: done.append((m, f)))
    r.start([{"id": "p1", "x": 0.0, "y": 0.0},
             {"id": "p2", "x": 800.0, "y": 0.0}], 721.0, 500.0)

    for final in (9, 12):
        amr.arrived.emit()
        outrigger.arrived.emit()
        lift.arrived.emit()
        r.handle_scan_state(_state(10))
        r.handle_scan_state(_state(final))
        retractor.arrived.emit()

    assert done == [(["p2"], ["p1"])]


def test_cancel_mid_point_restores_the_scan_task(qtbot):
    """마킹 도중 접으면 스캔 태스크로 되돌린다 — 다음 play 가 마킹을 돌지 않게."""
    r, (amr, lift, outrigger, retractor), calls, _log = _runner(qtbot)
    done = []
    r.finished.connect(lambda m, f: done.append((m, f)))
    r.start([{"id": "p1", "x": 0.0, "y": 0.0}], 721.0, 500.0)
    amr.arrived.emit()

    r.cancel()

    assert calls["restore"] == 1
    assert done == [], "접었는데 완료를 냈다"
    assert not r.running


def test_cancel_when_idle_does_nothing(qtbot):
    """돌고 있지 않으면 태스크를 건드리지 않는다."""
    r, _axes, calls, _log = _runner(qtbot)
    r.cancel()
    assert calls["restore"] == 0
