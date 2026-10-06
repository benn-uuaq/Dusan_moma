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


def test_grid_mark_point_uses_given_vehicle_lift_and_cell_coordinates(qtbot):
    """사내 MC mark_cmd: 자리가 정해져 오면 가운데 맞춤 대신 그대로 쓴다."""
    r, (amr, lift, outrigger, retractor), calls, log = _runner(qtbot)
    r.start([{"id": "m001", "amr_mm": 1402.0, "lift_mm": 120.0, "u": 250.0, "v": 35.0}], 721.0, 140.0)
    assert amr.targets == [1402.0]
    amr.arrived.emit()
    outrigger.arrived.emit()
    assert lift.targets == [120.0]
    lift.arrived.emit()
    assert calls["target"] == [(250.0, 35.0)] and calls["play"] == 1


# ---- 마킹 자리 대기 (마커는 ERUT 것) ---------------------------------------------
def _hold_runner(qtbot):
    log = []
    amr, lift = FakeAxis("차량", log), FakeAxis("리프트", log)
    outrigger, retractor = FakeAxis("고정", log), FakeAxis("복귀", log)
    releases = []
    r = MarkRunner(amr, lift, outrigger, retractor, lambda u, v: None,
                   lambda: None, lambda: None,
                   release_hold=lambda: releases.append(1))
    return r, (amr, lift, outrigger, retractor), releases


def _to_robot(axes):
    amr, lift, outrigger, _retractor = axes
    amr.arrived.emit()
    outrigger.arrived.emit()
    lift.arrived.emit()


def test_robot_waits_at_the_point_until_erut_has_marked(qtbot):
    """자리에 붙으면(290 = 11) 알리고, mark_next 가 올 때까지 풀지 않는다."""
    r, axes, releases = _hold_runner(qtbot)
    reached, done = [], []
    r.point_reached.connect(reached.append)
    r.finished.connect(lambda m, f: done.append((m, f)))
    r.start([{"id": "p1", "x": 1000.0, "y": 1200.0}], 721.0, 500.0, hold=True)
    _to_robot(axes)

    r.handle_scan_state(_state(10))
    r.handle_scan_state(_state(11))
    r.handle_scan_state(_state(11))       # 10 Hz 로 같은 값이 와도 한 번만
    assert reached == ["p1"]
    assert releases == []
    assert r.waiting_point == "p1"

    assert r.point_marked("p1", True) is True
    assert releases == [1]
    r.handle_scan_state(_state(13))
    r.handle_scan_state(_state(12))
    axes[3].arrived.emit()
    assert done == [(["p1"], [])]


def test_a_point_erut_could_not_mark_is_reported_as_failed(qtbot):
    r, axes, _releases = _hold_runner(qtbot)
    done = []
    r.finished.connect(lambda m, f: done.append((m, f)))
    r.start([{"id": "p1", "x": 1.0, "y": 1.0}], 721.0, 500.0, hold=True)
    _to_robot(axes)
    r.handle_scan_state(_state(11))
    r.point_marked("p1", False)
    r.handle_scan_state(_state(12))
    axes[3].arrived.emit()

    assert done == [([], ["p1"])]


def test_mark_next_for_a_point_we_are_not_at_is_ignored(qtbot):
    r, axes, releases = _hold_runner(qtbot)
    r.start([{"id": "p1", "x": 1.0, "y": 1.0}], 721.0, 500.0, hold=True)
    _to_robot(axes)

    assert r.point_marked("p1") is False       # 아직 자리에 안 붙었다
    r.handle_scan_state(_state(11))
    assert r.point_marked("p9") is False
    assert releases == []


def test_without_hold_the_robot_is_released_at_once(qtbot):
    """사내 MC 마킹처럼 확인할 마커가 없으면 붙자마자 푼다."""
    r, axes, releases = _hold_runner(qtbot)
    reached = []
    r.point_reached.connect(reached.append)
    r.start([{"id": "p1", "x": 1.0, "y": 1.0}], 721.0, 500.0, hold=False)
    _to_robot(axes)
    r.handle_scan_state(_state(11))

    assert releases == [1]
    assert reached == []


def test_progress_lists_the_points_not_reached(qtbot):
    r, axes, _releases = _hold_runner(qtbot)
    r.start([{"id": "p1", "x": 1.0, "y": 1.0}, {"id": "p2", "x": 2.0, "y": 2.0}],
            721.0, 500.0, hold=True)
    _to_robot(axes)
    r.handle_scan_state(_state(11))
    r.point_marked("p1", True)
    r.handle_scan_state(_state(12))

    assert r.progress() == (["p1"], ["p2"])


# ---- if-0.7 마킹 ② ⑥: 일시정지는 그 자리에, 재개면 기다리던 점을 다시 알린다 ----
def _to_point(r, axes):
    amr, lift, outrigger, _retractor = axes
    r.start([{"id": "p1", "x": 1000.0, "y": 1200.0},
             {"id": "p2", "x": 1000.0, "y": 2000.0}], 721.0, 500.0)
    amr.arrived.emit()
    outrigger.arrived.emit()
    lift.arrived.emit()


def test_pause_while_waiting_at_a_point_holds_and_resume_announces_it_again(qtbot):
    r, axes, _calls, _log = _runner(qtbot)
    reached: list[str] = []
    r.point_reached.connect(reached.append)
    _to_point(r, axes)
    r.handle_scan_state(_state(10))
    r.handle_scan_state(_state(11))                  # 자리에 붙어 기다린다
    assert reached == ["p1"]

    r.pause()
    assert r.paused and r.waiting_point == "p1"
    assert not r.point_marked("p1"), "멈춘 동안은 받지 않는다"

    r.resume()
    assert reached == ["p1", "p1"], "재개하면 기다리던 점을 다시 알린다"
    assert r.point_marked("p1")


def test_pause_while_the_robot_moves_pauses_the_robot(qtbot):
    r, axes, _calls, _log = _runner(qtbot)
    pauses: list[int] = []
    resumes: list[int] = []
    r.robot_pause_requested.connect(lambda: pauses.append(1))
    r.robot_resume_requested.connect(lambda: resumes.append(1))
    _to_point(r, axes)
    r.handle_scan_state(_state(10))                  # 로봇이 점으로 가는 중

    r.pause()
    r.resume()

    assert (pauses, resumes) == ([1], [1])


def test_arrival_during_pause_is_taken_on_resume(qtbot):
    r, (amr, lift, outrigger, retractor), _calls, log = _runner(qtbot)
    r.start([{"id": "p1", "x": 1000.0, "y": 1200.0}], 721.0, 500.0)
    r.pause()
    amr.arrived.emit()                               # 멈춘 동안 차량이 닿았다
    assert "고정" not in log

    r.resume()
    assert log[-1] == "고정"


def test_driving_to_another_spot_goes_through_the_prepare_hook(qtbot):
    """다른 자리로 갈 때는 앱이 리프트를 내리고 아웃트리거를 푼 뒤 달린다."""
    r, (amr, *_rest), _calls, log = _runner(qtbot)
    amr.position = 0.0
    prepared: list = []
    r.prepare_drive = lambda drive: (prepared.append(1), drive())
    r.start([{"id": "p1", "x": 1000.0, "y": 1200.0}], 721.0, 500.0)

    assert prepared == [1]
    assert log == ["차량"]
