"""원점 대기 게이트(레지스터 267) — 지난 허가가 남으면 안 된다.

로봇은 원점에 서서 267 이 1 이 될 때까지 기다리고, 통과하면서 스스로 0 으로
지운다. 그런데 태스크는 **시작할 때 이 칸을 지우지 않는다.** 그래서 허가가
한 번 더 쓰이면(중복 회신·재시도) 다음 셀에서 로봇이 원점을 그냥 지나가
프로브 확인 없이 훑게 된다. 그래서 셀을 시작할 때 RCS 가 0 으로 지운다.
"""

from smr_operator_ui.app import OperatorWindow
from smr_operator_ui.services import SequencerState


def test_starting_a_cell_clears_the_scan_permission(qtbot) -> None:
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    sent: list[tuple[str, int]] = []
    commands: list[str] = []
    window.ros_status.send_value = lambda name, value: sent.append((name, value)) or True
    window.ros_status.call_command = lambda name: commands.append(name) or True

    window._start_robot_scan()

    assert sent[0] == ("scan_go", 0), sent
    # 허가를 지운 뒤에 stop → play 순서로 간다.
    assert commands[:2] == ["remote_control_on", "stop"]
    window.close()


def test_releasing_the_gate_sends_one(qtbot) -> None:
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    sent: list[tuple[str, int]] = []
    window.ros_status.send_value = lambda name, value: sent.append((name, value)) or True
    window._origin_waiting = True

    window._release_scan_gate()

    assert ("scan_go", 1) in sent
    window.close()


def test_zero_radius_is_refused_before_play(qtbot) -> None:
    """반지름(260)이 0 이면 로봇이 "ARC IS ZERO" 로 멈춘다 — 틀기 전에 막는다."""
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    commands: list[str] = []
    errors: list[dict] = []
    window.ros_status.send_value = lambda name, value: True
    window.ros_status.send_pose = lambda name, values: True
    window.ros_status.call_command = lambda name: commands.append(name) or True
    window.erut_session.raise_error = errors.append

    window._push_work_area_to_robot(600, 800, 30, 20, 0, 10, 0, 0, 0)
    window._start_robot_scan()

    assert commands == []
    assert errors and errors[0]["code"] == "E9304"

    window._push_work_area_to_robot(600, 800, 30, 20, 845, 10, 0, 0, 0)
    window._start_robot_scan()
    assert commands[:2] == ["remote_control_on", "stop"]
    window.close()


# ---- 일시정지 = 29999 pause, 재개 = play ------------------------------------
def _scanning_window(qtbot):
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    commands: list[str] = []
    window.ros_status.send_value = lambda name, value: True
    window.ros_status.call_command = lambda name: commands.append(name) or True
    return window, commands


def test_pause_sends_pause_and_resume_sends_play_only(qtbot) -> None:
    """줄 한가운데서도 그 자리에 서고(pause), 그 자리에서 잇는다(play)."""
    window, commands = _scanning_window(qtbot)
    window._on_robot_task_state(1)

    window._pause_robot_scan()
    assert commands == ["remote_control_on", "pause"]

    window._on_robot_task_state(2)              # 로봇이 일시정지로 섰다
    commands.clear()
    window._resume_robot_scan()
    assert commands == ["remote_control_on", "play"], "재개가 stop 을 섞었다"
    window.close()


def test_resume_after_the_task_was_stopped_restarts_the_section(qtbot) -> None:
    """그 사이 태스크가 중지됐으면 이을 자리가 없다 — 알리고 처음부터 튼다."""
    window, commands = _scanning_window(qtbot)
    window._on_robot_task_state(1)
    window._pause_robot_scan()
    window._on_robot_task_state(3)              # 펜던트에서 stop 등
    commands.clear()

    window._resume_robot_scan()

    assert commands[:2] == ["remote_control_on", "stop"]
    assert window._play_timer.isActive()
    window.close()


def test_refused_pause_falls_back_to_stop(qtbot) -> None:
    """pause 가 거절되면 로봇이 계속 훑는다 — 세우기만은 stop 으로 반드시 한다."""
    window, commands = _scanning_window(qtbot)
    window._on_robot_task_state(1)
    window._pause_robot_scan()
    commands.clear()

    window._on_pause_command_result("pause", False, "not supported in local control mode")

    assert "stop" in commands
    commands.clear()
    window._resume_robot_scan()                 # 태스크가 끝났으니 처음부터
    assert commands[:2] == ["remote_control_on", "stop"]
    window.close()


def test_pause_before_play_went_out_cancels_the_play(qtbot) -> None:
    """시작 직후(play 대기 중) 멈추면 play 를 거두고, 재개 때 처음부터 튼다."""
    window, commands = _scanning_window(qtbot)
    window._start_robot_scan()
    assert window._play_timer.isActive()
    commands.clear()

    window._pause_robot_scan()
    assert not window._play_timer.isActive()
    assert "pause" not in commands

    window._resume_robot_scan()
    assert window._play_timer.isActive()
    window.close()


def test_paused_task_blocks_vehicle_and_lift(qtbot) -> None:
    """일시정지한 팔은 벽에 붙은 채다 — 실행 중과 같이 차량·리프트를 막는다."""
    window, _commands = _scanning_window(qtbot)
    window._on_robot_task_state(2)

    assert window._robot_motion_block_reason()
    window.close()


# ---- if-0.6: 선 뒤에만 paused · 자리를 못 지키면 resumable=false ------------
def test_pause_settles_only_when_the_task_is_paused_and_the_arm_is_still(qtbot) -> None:
    window, _commands = _scanning_window(qtbot)
    window._on_robot_task_state(1)
    window._show_tcp_pose([600.0, 50.0, 300.0, 0, 0, 0])
    assert not window._pause_settled(), "태스크가 아직 돈다"

    window._on_robot_task_state(2)
    window._show_tcp_pose([600.0, 55.0, 300.0, 0, 0, 0])     # 감속하며 5 mm 더 갔다
    assert not window._pause_settled(), "팔이 아직 움직인다"

    window._tcp_moved_at -= window.STILL_FOR_S + 0.1         # 그 뒤로 가만히 있다
    assert window._pause_settled()
    window.close()


def test_stopped_task_or_pushed_arm_cannot_resume_in_place(qtbot) -> None:
    window, _commands = _scanning_window(qtbot)
    window.sequencer._resume_state = SequencerState.SCANNING
    window.sequencer._set_state(SequencerState.PAUSED)
    window._on_robot_task_state(2)
    window._show_tcp_pose([600.0, 50.0, 300.0, 0, 0, 0])
    window._tcp_moved_at -= window.STILL_FOR_S + 0.1
    window._on_pause_tick()                                  # 선 자리를 잡는다
    assert window._pause_holds_position()

    window._show_tcp_pose([600.0, 50.0, 305.0, 0, 0, 0])     # 누가 팔을 5 mm 밀었다
    assert not window._pause_holds_position()

    window._show_tcp_pose([600.0, 50.0, 300.0, 0, 0, 0])
    window._on_robot_task_state(3)                           # 펜던트에서 stop
    assert not window._pause_holds_position()
    window.close()


# ---- 초기화(reset) 때 펴진 팔을 스스로 홈으로 ---------------------------------
def _deployed_window(qtbot):
    window, commands = _scanning_window(qtbot)
    window._robot_link_up = True
    window._on_robot_task_state(3)
    window._on_robot_at_home(True)
    window._on_robot_at_home(False)          # 비상정지로 벽 앞에 선 채
    window.erut_session.raise_error({"code": "E1002", "message": "E_STOP",
                                     "level": "estop", "recovery": "reset_required"})
    commands.clear()
    return window, commands


def test_reset_folds_a_deployed_arm(qtbot) -> None:
    window, commands = _deployed_window(qtbot)

    window.erut_session.handle_request("reset", {"req_id": "rst-1"})

    assert "home" in commands
    window.close()


def test_reset_keeps_the_arm_when_a_paused_job_waits(qtbot) -> None:
    """일시정지는 제자리 — 이어 갈 작업이 있으면 접지 않는다."""
    window, commands = _deployed_window(qtbot)
    window.sequencer._resume_state = SequencerState.SCANNING
    window.sequencer._set_state(SequencerState.PAUSED)

    window._reset_alarms()                   # 화면 알람 리셋도 같은 길

    assert "home" not in commands
    window.close()


def test_reset_does_nothing_when_already_home(qtbot) -> None:
    window, commands = _deployed_window(qtbot)
    window._on_robot_at_home(True)

    window.erut_session.handle_request("reset", {"req_id": "rst-2"})

    assert "home" not in commands
    window.close()


# ---- ERUT 문의(2026-10-02 탭11) 로 고친 것 ----------------------------------
def test_progress_comes_from_register_298_not_297(qtbot) -> None:
    """Q-02: 297(스캔 경로 0.1 단위)을 진행률로 읽어 1 m 만 가도 100 이 됐다."""
    window, _commands = _scanning_window(qtbot)
    window._last_scan_state = [6, 3, 28, 100, 1, 0, 296, 1750, 11, 0]

    assert window._erut_position()["progress"] == 11
    # 줄 간격(296)은 0.1 mm — 29.6 mm 로 3줄이면 88.8 mm (정수 mm 면 90 이 됐다).
    assert window._erut_position()["row_mm"] == 88.8
    window.close()


def test_home_is_deployed_while_the_task_runs_or_the_probe_is_attached(qtbot) -> None:
    """Q-01: 276 만 믿으면 스캔 중에도 home 이 나갔다."""
    window, _commands = _scanning_window(qtbot)
    window._on_robot_task_state(3)
    window._on_robot_at_home(True)
    assert window._erut_at_home() is True

    window._on_robot_task_state(1)                       # 태스크가 돈다
    assert window._erut_at_home() is False
    window._on_robot_task_state(3)
    window.erut_session._contact = True                  # 탐촉자가 붙어 있다
    assert window._erut_at_home() is False
    window.close()


def test_home_is_deployed_until_the_fold_is_seen(qtbot) -> None:
    """Q-05: abort 뒤 홈으로 접는 중인데 query 에 home 이 나갔다(플래그가 내려가기 전)."""
    window, _commands = _scanning_window(qtbot)
    window._on_robot_task_state(3)
    window._on_robot_at_home(True)

    window._send_home()
    assert window._erut_at_home() is False, "보내자마자는 접는 중이다"
    window._on_robot_at_home(False)                      # 노드가 이동 전에 내린다
    window._on_robot_at_home(True)                       # 닿았다
    assert window._erut_at_home() is True
    window.close()


def test_robot_alarm_warning_clears_itself(qtbot) -> None:
    """Q-03: 알람 스트림은 순간 기록 — 잠잠하면 같은 code 로 cleared=true."""
    window, _commands = _scanning_window(qtbot)
    window.ROBOT_ALARM_CLEAR_MS = 50
    published: list[tuple] = []
    window.erut.publish_error = lambda code, msg, level, recovery, cleared=False, **kw: (
        published.append((code, level, recovery, cleared)) or True)

    window._handle_robot_alarm("inv_kin_singularity")
    assert published[-1] == ("E9201", "warning", "auto", False)
    qtbot.waitUntil(lambda: published[-1][3] is True, timeout=2000)
    assert "E9201" not in window.erut_session.error_codes()
    window.close()


def test_task_stopping_by_itself_during_the_scan_is_a_fault(qtbot) -> None:
    """Q-08: 역기구학 오류로 태스크가 멈췄는데 RCS 는 스캔 중인 줄 알고 기다렸다."""
    window, _commands = _scanning_window(qtbot)
    window.TASK_STOP_GRACE_MS = 10
    raised: list[dict] = []
    window.erut_session.raise_error = raised.append
    window.sequencer._set_state(SequencerState.SCANNING)
    window._scan_task_seen_running = False

    window._on_robot_task_state(3)                       # 셀 시작 때 RCS 의 stop — 무시
    qtbot.wait(50)
    assert raised == []

    window._on_robot_task_state(1)
    window._handle_robot_alarm("inv_kin_singularity")
    raised.clear()
    window._on_robot_task_state(3)                       # 스스로 멈췄다
    qtbot.waitUntil(lambda: bool(raised), timeout=2000)
    assert raised[0]["code"] == "E9202" and raised[0]["level"] == "stop"
    assert "inv_kin_singularity" in raised[0]["detail"]
    window.close()


def test_old_task_sending_pitch_in_mm_is_still_read_right(qtbot) -> None:
    """태스크를 다시 올리기 전(296 이 정수 mm)이어도 10 배 틀리지 않는다."""
    window, _commands = _scanning_window(qtbot)
    window._robot_work_area = [600, 800, 300, 20, 8450, 0, 0, 0, 0]
    window._last_scan_state = [6, 3, 28, 100, 1, 0, 30, 1750, 11, 0]      # 옛 판: 30 mm

    assert window._erut_position()["row_mm"] == 90.0
    window._last_scan_state = [6, 3, 28, 100, 1, 0, 296, 1750, 11, 0]     # 새 판: 29.6 mm
    assert window._erut_position()["row_mm"] == 88.8
    window.close()


# ---- if-0.8 · ERUT 문의 Q-18·19·24 -------------------------------------------
def test_home_sent_to_an_arm_already_home_is_confirmed(qtbot) -> None:
    """Q-19: 이미 홈인 팔에 홈을 보내면 플래그가 0 → 1 로 안 바뀐다 — 그래도 home 으로 돌아와야 한다."""
    window, _commands = _scanning_window(qtbot)
    window.HOME_SETTLE_S = 0.05
    window._on_robot_task_state(3)
    window._on_robot_at_home(True)

    window._send_home()
    assert window._erut_at_home() is False, "보내자마자는 접는 중"
    window._clear_home_pending_on_failure("home", True, "")      # 노드가 받았다
    qtbot.waitUntil(lambda: window._erut_at_home() is True, timeout=3000)
    window.close()


def test_fold_not_finished_in_time_raises_e2002_and_clears_when_home(qtbot) -> None:
    """if-0.8 E2002 HOME_FAILED — home_within_ms 안에 못 접으면 내고, home 이 되면 푼다."""
    window, _commands = _scanning_window(qtbot)
    window.HOME_SETTLE_S = 0.05
    window.HOME_WITHIN_MS_AT_FULL_SPEED = 300
    published: list[tuple] = []
    window.erut.publish_error = lambda code, msg, level, recovery, cleared=False, **kw: (
        published.append((code, msg, level, cleared)) or True)
    window._on_robot_task_state(3)
    window._on_robot_at_home(False)

    window._send_home()
    window._clear_home_pending_on_failure("home", True, "")
    qtbot.waitUntil(lambda: any(p[0] == "E2002" for p in published), timeout=3000)
    assert published[-1][:3] == ("E2002", "HOME_FAILED", "stop")

    window._on_robot_at_home(True)                               # 늦게라도 닿았다
    qtbot.waitUntil(lambda: published[-1] == ("E2002", "HOME_FAILED", "stop", True),
                    timeout=3000)
    window.close()


def test_stale_scan_state_after_abort_is_not_reported_as_contact(qtbot) -> None:
    """Q-18: 멈춘 로봇의 290(원점 대기·스캔)이 남아 있어도 새 출발 전엔 attached 가 아니다."""
    window, _commands = _scanning_window(qtbot)
    window.erut_session._start_req_id = "s1"
    window._on_robot_task_state(1)
    window._start_robot_scan()                      # 새 구역 출발
    window._track_erut_contact([6, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    assert window.erut_session._contact is not True, "옛 290 = 6 을 접촉으로 쳤다"

    window._track_erut_contact([2, 0, 0, 0, 0, 0, 0, 0, 0, 0])   # 이번 play 의 3점 측정
    window._track_erut_contact([7, 0, 0, 0, 0, 0, 0, 0, 0, 0])   # 시작점에 붙음
    assert window.erut_session._contact is True
    window._stop_robot_scan()                       # abort·정지
    assert window.erut_session._contact is False
    window.close()


def test_reset_folds_a_deployed_arm_even_without_faults(qtbot) -> None:
    """Q-24 · if-0.8: reset 은 걸린 장애가 없어도 home 으로 거둔다. 이미 home 이면 200."""
    window, commands = _scanning_window(qtbot)
    responses: list[int] = []
    window.erut.publish_res = lambda req, action, code, message, **kw: responses.append(code) or True
    window._robot_link_up = True
    window._on_robot_task_state(3)
    window._on_robot_at_home(True)

    window.erut_session.handle_request("reset", {"req_id": "r-home"})
    assert responses[-1] == 200 and "home" not in commands

    window._on_robot_at_home(False)                  # 장애 없이 펴진 채
    window.erut_session.handle_request("reset", {"req_id": "r-out"})
    assert responses[-1] == 202
    assert "home" in commands
    window.close()
