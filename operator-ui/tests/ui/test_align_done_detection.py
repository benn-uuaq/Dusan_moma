"""QR 정렬(measure_lap ALIGN) 완료 판정 (2026-11-24 현장).

AGV 가 정렬 명령을 받자마자 lap_state DONE 을 냈는데 RCS 는 반응하지 않았다 — RUNNING 을 한 번 봐야 DONE 을
믿었다. 그 뒤 120 s 한도에 걸려 STOP 을 보냈고, FINISH 에 서 있던 AGV 가 「FINISH waits for a new job or
job_cancel」로 거절했다.
"""

from smr_operator_ui.app import OperatorWindow


def _armed(qtbot, before=None):
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    window._local_calibration = True                       # 캘리브레이션 중
    window.vehicle._last = dict(before or {"state": "FINISH", "lap_state": "IDLE", "job_id": "RCS-old"})
    sent: dict = {}
    window.vehicle.measure_lap = lambda job_id, *a, **k: sent.setdefault("job", job_id) or True
    controls: list[tuple] = []
    window.vehicle.control = lambda state="", reset=False, job_cancel=False: controls.append((state, job_cancel)) or True
    finished: list[tuple] = []
    window._finish_calibration = lambda ok, detail, *a, **k: finished.append((ok, detail))
    window.outrigger.reset(1)                              # 고정돼 있다 — 정렬 끝나면 바로 결과
    lap = {"mode": "ALIGN", "plate": False, "limit_s": 0, "circumference_mm": 0.0}
    window._calibration_lap = lap
    window._start_measured_lap(lap)
    return window, sent, controls, finished


def _status(window, **fields):
    window.vehicle._last = dict(fields)
    window._track_measured_lap(dict(fields))


def test_immediate_done_with_our_job_id_is_accepted_without_running(qtbot):
    window, sent, _controls, finished = _armed(qtbot)
    _status(window, state="FINISH", lap_state="DONE", job_id=sent["job"], lap_origin_ok=True, lap_path_m=0.0)
    assert finished and finished[0][0] is True and "QR 마커에 맞춰 정렬" in finished[0][1]
    window.close()


def test_done_without_our_job_id_counts_once_the_command_is_accepted(qtbot):
    """AGV 가 status.job_id 를 안 바꿔도(지난 작업 번호 그대로) 받아들여진 뒤의 DONE 은 이번 정렬이다."""
    window, _sent, _controls, finished = _armed(qtbot)
    _status(window, state="FINISH", lap_state="DONE", job_id="RCS-old", lap_origin_ok=True)
    assert finished == [], "받아들여졌다는 답 전에는 기다린다"
    window._on_measure_lap_result("measure_lap", True, "accepted")
    assert finished and finished[0][0] is True, "답이 오면 마지막 상태로 바로 끝낸다"
    window.close()


def test_stale_done_from_an_earlier_alignment_is_not_taken(qtbot):
    window, _sent, _controls, finished = _armed(
        qtbot, before={"state": "FINISH", "lap_state": "DONE", "job_id": "", "lap_origin_ok": True})
    window._on_measure_lap_result("measure_lap", True, "accepted")
    _status(window, state="FINISH", lap_state="DONE", job_id="", lap_origin_ok=True)
    assert finished == [], "보내기 전부터 DONE 이던 값은 이번 결과가 아니다"
    _status(window, state="RUNNING", lap_state="RUNNING", job_id="")
    _status(window, state="FINISH", lap_state="DONE", job_id="", lap_origin_ok=True)
    assert finished and finished[0][0] is True
    window.close()


def test_other_new_job_is_ignored_and_origin_not_ok_fails(qtbot):
    window, sent, _controls, finished = _armed(qtbot)
    window._on_measure_lap_result("measure_lap", True, "accepted")
    _status(window, state="RUNNING", lap_state="DONE", job_id="RCS-other", lap_origin_ok=True)
    assert finished == [], "다른 새 작업의 상태다"
    _status(window, state="FINISH", lap_state="DONE", job_id=sent["job"], lap_origin_ok=False)
    assert finished and finished[0][0] is False and "lap_origin_ok" in finished[0][1]
    window.close()


def test_timeout_and_pause_do_not_send_commands_finish_rejects(qtbot):
    """FINISH 에 서 있으면 STOP · PAUSE 를 보내지 않는다 — job_cancel 없는 STOP 은 거절된다."""
    window, _sent, controls, finished = _armed(qtbot)
    window._pause_calibration_lap()
    window._resume_calibration_lap()
    assert controls == [], "서 있는 차량에 PAUSE · RUNNING 을 보내지 않는다"
    window._measured_lap_timeout()
    assert controls == [] and finished and finished[0][0] is False
    window.close()


def test_timeout_while_aligning_stops_and_cancels_the_job(qtbot):
    window, _sent, controls, finished = _armed(qtbot)
    _status(window, state="RUNNING", lap_state="RUNNING", job_id="")
    window._pause_calibration_lap()
    window._resume_calibration_lap()
    assert controls == [("PAUSE", False), ("RUNNING", False)]
    window._measured_lap_timeout()
    assert controls[-1] == ("STOP", True), "정렬 중이면 세우고 작업도 버린다(job_cancel)"
    window.close()
