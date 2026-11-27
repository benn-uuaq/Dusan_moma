"""팔 받침대(레지스터 360) 인터락 · 홈 명령 (2026-11-26).

받침대 위(360 = 2)면 차량 주행 · 아웃트리거 해제 · QR 정렬은 된다. 리프트 · 로봇 작업은 홈에서만 —
받침대 위에서 필요하면 먼저 홈으로 보낸다(노드가 받침대 되돌리기 태스크를 돌린다). 다 되돌리면
펜던트에 그 태스크가 올라가 있으니 스캔 태스크를 다시 불러 둔다.
"""

import pytest

from smr_operator_ui.app import OperatorWindow

PARKED, UNPARKING, READY = 2, 3, 4


@pytest.fixture
def window(qtbot):
    w = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(w)
    w.commands = []
    w.ros_status.send_value = lambda name, value: True
    w.ros_status.call_command = lambda name: w.commands.append(name) or True
    yield w
    w.close()


def _parked(w) -> None:
    """홈 플래그를 한 번 본 뒤(인터락이 걸린 상태) 팔을 받침대에 내려놓았다."""
    w._on_robot_task_state(3)
    w._on_robot_at_home(True)
    w._on_robot_at_home(False)
    w._on_arm_park(PARKED)


def _back_home(w) -> None:
    """노드가 되돌리기 태스크를 돌려 홈에 닿았다(276 = 1, 360 = 4, 태스크 멈춤)."""
    w._on_robot_task_state(1)
    w._on_arm_park(UNPARKING)
    w._on_robot_at_home(True)
    w._on_arm_park(READY)
    w._on_robot_task_state(3)


def test_on_the_rest_only_driving_is_allowed(window) -> None:
    _parked(window)
    assert window._robot_motion_block_reason("drive") == ""
    assert "받침대 위" in window._robot_motion_block_reason("lift")
    assert "받침대 위" in window._robot_motion_block_reason("robot")


def test_column_move_runs_at_once_on_the_rest(window) -> None:
    _parked(window)
    window.sequencer.amr_move_requested.emit(2)
    assert not window._pending_motions and window.amr.moving
    assert "home" not in window.commands, "받침대 위에서 주행하려고 팔을 되돌리지 않는다"


def test_lift_on_the_rest_first_brings_the_arm_home_then_moves(window) -> None:
    _parked(window)
    window.sequencer.lift_target_requested.emit(2480.0)

    assert window.lift.target != 2480.0, "받침대 위에서는 리프트를 움직이지 않는다"
    assert window.commands.count("home") == 1 and window._unpark_sent
    assert window._robot_wait_ms() > window.ROBOT_TASK_WAIT_MS, "되돌리기는 더 오래 기다린다"

    window.commands.clear()
    _back_home(window)
    assert window.lift.target == 2480.0
    assert "load_scan_task" in window.commands, "되돌리기 태스크 대신 스캔 태스크를 다시 올려 둔다"
    assert not window._unpark_sent


def test_scan_start_on_the_rest_unparks_first(window) -> None:
    _parked(window)
    window._work_area_ready_for_scan = lambda: True
    window._start_robot_scan()
    assert window.commands == ["home"], "받침대 위에서 스캔 태스크를 틀지 않는다 — 먼저 되돌린다"
    assert window._pending_motions

    window.commands.clear()
    _back_home(window)
    assert not window._pending_motions
    assert window.commands[:4] == ["remote_control_on", "stop", "load_scan_task", "remote_control_on"]
    assert window._play_timer.isActive(), "홈에서 스캔을 튼다"


def test_manual_screen_locks_only_the_lift_on_the_rest(window) -> None:
    _parked(window)
    manual = window.screens["manual"]
    manual._enabled = True
    window._sync_manual_interlock()
    manual._refresh_enabled()
    assert all(button.isEnabled() for button in manual.jog_buttons.values())
    assert manual.outrigger_release_button.isEnabled()
    assert not manual.lift_button.isEnabled()
    assert "리프트 이동을 잠갔습니다" in manual.notice.text()


def test_erut_sees_the_rest_as_home_but_home_request_goes_home(window) -> None:
    """거치는 RCS 안에서만 다룬다 — ERUT 에는 home 이고, ERUT 가 홈을 요청하면 받침대에서 홈으로 간다."""
    _parked(window)
    assert window._erut_at_home() is True
    window._send_home_for_erut()
    assert window.commands == ["home"] and window._unpark_sent


def test_operator_home_on_the_rest_unparks(window) -> None:
    _parked(window)
    window._request_home()
    assert window.commands == ["home"] and window._unpark_sent
    assert "받침대에서" in window.main_screen.activity_label.text()


def test_abort_on_the_rest_goes_home(window, qtbot) -> None:
    _parked(window)
    said: list[str] = []
    window.main_screen.activity_shown.connect(said.append)
    window._abort_job()
    assert any("받침대에서 들어 홈으로" in text for text in said), said
    qtbot.waitUntil(lambda: "home" in window.commands, timeout=3000)


def test_arm_leaving_the_rest_while_driving_stops_the_vehicle(window) -> None:
    _parked(window)
    window.sequencer.amr_move_requested.emit(2)
    assert window.amr.moving
    alarms = []
    window._add_alarm = lambda code, text: alarms.append(code)
    window._on_arm_park(UNPARKING)
    assert not window.amr.moving and alarms == ["E2003"]


def test_node_reply_marks_an_unpark_found_by_joints(window) -> None:
    """레지스터가 0(전원을 껐다 켬)이어도 노드는 관절값으로 받침대를 알아본다 — 응답으로 안다."""
    window._on_robot_task_state(3)
    window._on_robot_at_home(False)
    window._send_home()
    assert not window._unpark_sent
    window._note_unpark_reply("home", True, "[move_home] 받침대 되돌리기 — 팔을 받침대에서 들어 홈으로")
    assert window._unpark_sent


def test_without_park_state_the_old_interlock_is_unchanged(window) -> None:
    window._on_robot_task_state(3)
    window._on_robot_at_home(True)
    window._on_robot_at_home(False)
    assert window._robot_motion_block_reason("drive") == "로봇이 홈 위치에 있지 않습니다"
    assert window._robot_motion_block_reason("lift") == "로봇이 홈 위치에 있지 않습니다"
    window._on_robot_at_home(True)
    assert window._robot_motion_block_reason("drive") == "" == window._robot_motion_block_reason("lift")


# ---- 주행 전 자동 거치 · 「로봇 거치」 (2026-11-26) -------------------------------------
PARKING, UNTAUGHT = 1, 9


def _ready(w) -> None:
    """실제 로봇이 붙어 홈에서 쉬고 있다(인터락이 걸린 상태, 리프트 0 mm)."""
    w._robot_link_up = True
    w._on_robot_task_state(3)
    w._on_robot_at_home(True)
    w._on_arm_park(READY)


def _park_done(w) -> None:
    """노드가 받침대 태스크를 돌려 팔을 받침대 위에 내려놓았다."""
    w._on_robot_task_state(1)
    w._on_robot_at_home(False)
    w._on_arm_park(PARKING)
    w._on_arm_park(PARKED)
    w._on_robot_task_state(3)


def test_drive_parks_the_arm_first_then_drives(window) -> None:
    _ready(window)
    drove = []
    window._prepare_to_drive(lambda: drove.append(1))
    assert window.commands == ["rest"] and drove == [], "받침대에 올리기 전에는 달리지 않는다"
    assert window._robot_wait_ms() > window.ROBOT_TASK_WAIT_MS

    _park_done(window)
    assert drove == [1]
    assert window._erut_at_home() is True


def test_drive_from_the_wall_goes_home_then_parks(window) -> None:
    _ready(window)
    window._on_robot_at_home(False)                 # 팔이 벽 앞(태스크는 멈춤)
    drove = []
    window._prepare_to_drive(lambda: drove.append(1))
    assert window.commands == ["home"]
    window._on_robot_task_state(1)
    window._on_robot_at_home(True)
    window._on_robot_task_state(3)
    assert window.commands == ["home", "rest"] and drove == []
    _park_done(window)
    assert drove == [1]


def test_untaught_rest_never_drives(window) -> None:
    """무조건 거치 — 받침대 자세가 없어 못 올리면 차량을 움직이지 않고 E2002 로 멈춘다(2026-11-26)."""
    _ready(window)
    alarms = []
    window._add_alarm = lambda code, text: alarms.append(code)
    drove = []
    window._prepare_to_drive(lambda: drove.append(1))
    window._on_robot_task_state(1)
    window._on_arm_park(UNTAUGHT)                   # 펜던트에 받침대 자세가 없다
    window._on_robot_task_state(3)
    assert drove == [] and alarms[:2] == ["E9206", "E2002"]
    assert not window._pending_motions


def test_refused_rest_command_never_drives(window) -> None:
    _ready(window)
    drove = []
    window._prepare_to_drive(lambda: drove.append(1))
    window._on_rest_command_result("rest", False, "[move_rest] 받침대 태스크 task -p 실패")
    assert drove == [] and "E2002" in window.erut_session.error_codes()


def test_rest_only_at_lift_zero(window) -> None:
    """거치는 리프트 0 mm 에서만 — 다른 높이면 올리지도 달리지도 않는다."""
    _ready(window)
    window.lift.backend.reset(1200.0)
    assert window._robot_motion_block_reason("rest").startswith("리프트가 0 mm 가 아니라")
    window._request_rest()
    assert "rest" not in window.commands
    assert "리프트 0 mm 에서만" in window.main_screen.activity_label.text()


def test_no_robot_drives_as_before(window) -> None:
    drove = []
    window._prepare_to_drive(lambda: drove.append(1))
    assert drove == [1] and window.commands == []


def test_rest_button(window) -> None:
    _ready(window)
    window.main_screen.rest_button.click()
    assert window.commands == ["rest"]
    window.commands.clear()
    window._on_robot_at_home(False)                 # 벽 앞 — 홈에서만 거치한다
    window.main_screen.rest_button.click()
    assert window.commands == [] and "홈에서만" in window.main_screen.activity_label.text()


def test_no_home_command_while_the_rest_task_starts(window) -> None:
    """거치를 보내면 노드가 태스크를 틀기 전에 홈 플래그부터 내린다 — 그 틈에 홈을 보내거나 출발하면 안 된다
    (2026-11-27 현장: 거치 직후 홈을 보내고 차량이 출발했다)."""
    _ready(window)
    drove = []
    window._prepare_to_drive(lambda: drove.append(1))
    assert window.commands == ["rest"]
    window._on_robot_at_home(False)                 # 노드가 홈 플래그를 내렸다 — 태스크는 아직 안 돈다
    window._on_robot_task_state(3)
    assert window.commands == ["rest"] and drove == [], "홈을 보내지도 출발하지도 않는다"
    _park_done(window)
    assert drove == [1] and "home" not in window.commands


def test_scan_start_after_parking_reloads_the_scan_task(window) -> None:
    """거치하면 펜던트에 park 태스크가 올라간다 — 그대로 play 하면 park 태스크가 돌고 멈춘다(E9202,
    2026-11-27 현장). 스캔을 틀기 전에 스캔 태스크를 다시 불러 둔다."""
    _ready(window)
    window._prepare_to_drive(lambda: None)
    _park_done(window)
    window._on_robot_at_home(True)                  # (팔이 홈이라 되돌리기 없이) 바로 스캔을 튼다
    window._on_arm_park(READY)
    window._work_area_ready_for_scan = lambda: True
    window.commands.clear()
    window._start_robot_scan()
    assert window.commands[:3] == ["remote_control_on", "stop", "load_scan_task"]
    window.commands.clear()
    window._start_robot_scan()                      # 다음 셀 — 이미 스캔 태스크다
    assert "load_scan_task" not in window.commands
