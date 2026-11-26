"""오류 로그·로그 파일·운전 모드 저장 화면 시험.

역할 나눔: 저장 위치·보존 기간은 시스템 설정에서만 바꾸고, 로그 파일은
기록 파일 관리, 오류 로그는 활성 오류 해제 + 알람이벤트 기록, 운전 모드는
작업 조건 묶음 저장·불러오기.
"""

import math
from pathlib import Path

import pytest
from openpyxl import load_workbook

from smr_operator_ui.app import OperatorWindow
from smr_operator_ui.services import data_recorder as dr


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv("SMR_DATA_DIR", str(tmp_path / "data"))
    # 기준 위치 파일도 시험마다 따로 — 운전 모드 시험이 그리퍼 접촉 위치를 되살려 쓰면 세션 공용 파일에 남아
    # 다른 시험(차량 설정의 그리퍼 접촉 높이)이 그 값을 읽었다(2026-11-25).
    monkeypatch.setenv("SMR_ROBOT_CONFIG_DIR", str(tmp_path / "robot_config"))
    w = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(w)
    # 로봇 명령은 가로챈다(로봇 없이 알람이 쌓이지 않게).
    w.ros_status.call_command = lambda name: True
    w.ros_status.set_task_paths = lambda scan, mark: True
    yield w
    w.close()


def _texts(table, col):
    return [table.item(r, col).text() for r in range(table.rowCount())]


# ---- 오류 로그 -------------------------------------------------------------------
def test_error_log_shows_active_faults_and_records(window) -> None:
    window.erut_session.raise_error({"code": "E-PROBE-C", "message": "센터 프로브 벽 접촉 실패",
                                     "level": "stop", "recovery": "manual"})
    window.main_screen.show_activity("진행 알림 한 줄")
    window.navigate("errors")
    screen = window.screens["errors"]

    assert [screen.active_list.item(i).text() for i in range(screen.active_list.count())] == ["E-PROBE-C"]
    kinds = _texts(screen.table, 1)
    assert "장애" in kinds and "정보" not in kinds, "기본 보기는 진행 알림을 뺀다"
    screen.kind_combo.setCurrentIndex(1)            # 전체(진행 알림 포함)
    screen._load_table()
    assert "정보" in _texts(screen.table, 1)


def test_clearing_one_fault_logs_the_release(window) -> None:
    for code in ("E-PROBE-C", "E-PROBE-L"):
        window.erut_session.raise_error({"code": code, "message": code, "level": "stop",
                                         "recovery": "manual"})
    window.navigate("errors")
    screen = window.screens["errors"]
    screen.active_list.item(0).setSelected(True)
    screen._clear_selected()

    assert window.erut_session.error_codes() == ["E-PROBE-L"]
    events = window.data_recorder.read_events(screen.selected_day())
    assert ["해제", "E-PROBE-C"] in [[r[1], r[2]] for r in events]

    screen.clear_all_button.click()                 # 전체 해제 = 알람 리셋
    assert window.erut_session.error_codes() == []


def test_new_events_appear_live_and_export_to_excel(window, tmp_path) -> None:
    window.navigate("errors")
    screen = window.screens["errors"]
    before = screen.table.rowCount()
    window.cobot_manual_screen.add_alarm("시험 알람")
    assert screen.table.rowCount() == before + 1
    assert screen.table.item(0, 4).text() == "시험 알람", "최근 것이 맨 위"

    out = tmp_path / "export"
    screen.picker.chooser = lambda start: str(out)
    screen.export_button.click()
    (book,) = out.glob("*_오류로그.xlsx")
    rows = list(load_workbook(book).active.iter_rows(values_only=True))
    assert list(rows[0]) == screen.HEADERS and rows[1][4] == "시험 알람"


# ---- 로그 파일 -------------------------------------------------------------------
def test_log_files_list_export_and_delete(window, tmp_path) -> None:
    window.cobot_manual_screen.add_alarm("기록을 하나 만든다")
    window.data_recorder.comms("수신", "ERUT", "doosan/robot/req/query", "{}")
    window.navigate("logs")
    screen = window.screens["logs"]
    assert str(window.data_recorder.root) in screen.location_label.text()
    assert "보존 기간" in screen.location_label.text()
    assert sorted(_texts(screen.table, 1)) == sorted([dr.EVENTS, dr.COMMS])

    screen.table.selectRow(0)
    out = tmp_path / "copy"
    screen.picker.chooser = lambda start: str(out)
    screen.export_button.click()
    assert len(list(out.rglob("*.txt"))) == 1, "선택한 파일만 복사한다"

    screen.confirm = lambda text: False             # 취소하면 안 지운다
    screen.delete_button.click()
    assert screen.table.rowCount() == 2
    screen.confirm = lambda text: True
    screen.delete_button.click()
    assert screen.table.rowCount() == 1
    assert len(list(out.rglob("*.txt"))) == 1, "내보낸 사본은 그대로"


def test_log_files_filter_and_links(window) -> None:
    window.cobot_manual_screen.add_alarm("알람")
    window.data_recorder.comms("발신", "MC", "doosan/robot/job_state", "{}")
    window.navigate("logs")
    screen = window.screens["logs"]
    screen.kind_combo.setCurrentIndex(screen.kind_combo.findText(dr.COMMS))
    screen.refresh()
    assert _texts(screen.table, 1) == [dr.COMMS]

    opened = []
    screen.opener = opened.append
    screen.open_button.click()
    assert opened == [str(window.data_recorder.root)]

    screen.settings_button.click()                  # 위치·보존 기간은 시스템 설정에서 바꾼다
    assert window.stack.currentWidget() is window.screens["system"]


# ---- 운전 모드 저장 -------------------------------------------------------------------
def _modes(window, name="시험 모드"):
    screen = window.screens["modes"]
    screen.ask_name = lambda default: name
    screen.confirm = lambda text: True
    return screen


def test_mode_slot_saves_and_restores_job_conditions(window) -> None:
    saved = []
    real_save = window.settings_service.save
    window.settings_service.save = lambda scope, values: (saved.append((scope, dict(values))),
                                                           real_save(scope, values))
    screen = _modes(window)
    cobot = window.screens["cobot"]
    cobot.field("작업 속도").setValue(90)
    window._task_version = "dusan_v5"
    cobot.set_task_version("dusan_v5")
    window.main_screen.set_work_area(700.0, 450.0, 30.0, 15.0)

    screen._save_clicked()                           # 슬롯 1
    assert "시험 모드" in screen.slot_buttons[0].text()
    slots = [v for s, v in saved if s == "mode_slots"][-1]
    snap = slots["1"]
    assert snap["cobot"]["작업 속도"] == 90
    assert snap["robot_task"]["task_version"] == "dusan_v5"
    assert snap["work_area"]["width_mm"] == 700.0 and snap["work_area"]["overlap_mm"] == 15.0
    assert "ut" not in snap, "UT 설정은 뺐다 — 슬롯에 남으면 안 된다"
    assert "태스크 판" not in snap["cobot"]

    # 다른 값으로 바꿔 놓고 불러오면 슬롯 값으로 돌아온다.
    cobot.field("작업 속도").setValue(80)
    cobot.set_task_version("dusan_v4"); window._task_version = "dusan_v4"
    window.main_screen.set_work_area(600.0, 400.0, 30.0, 0.0)
    screen._load_clicked()
    assert cobot.field("작업 속도").value() == 90
    assert cobot.task_version() == "dusan_v5" and window._task_version == "dusan_v5"
    assert window.main_screen.rect_view.work_area()[0] == 700.0
    assert "불러왔습니다" in screen.status_label.text()


def test_mode_slot_is_not_loaded_during_a_job(window) -> None:
    screen = _modes(window)
    screen._save_clicked()
    window._start_inspection()
    window.screens["cobot"].field("작업 속도").setValue(77)
    screen._load_clicked()
    assert window.screens["cobot"].field("작업 속도").value() == 77
    assert "작업 중" in screen.status_label.text()
    window._stop_inspection()


def test_mode_slot_clear_and_restore_on_start(window) -> None:
    screen = _modes(window)
    # 켰을 때 저장소에서 온 값으로 슬롯이 채워진다.
    window._apply_stored_settings("mode_slots", {"3": {"name": "야간", "saved_at": "2026-09-15 20:00",
                                                       "cobot": {"작업 속도": 100}}, "1": {}})
    assert "야간" in screen.slot_buttons[2].text()
    assert "비어 있음" in screen.slot_buttons[0].text()

    screen._save_clicked()
    assert screen.load_button.isEnabled()
    screen.clear_button.click()
    assert "비어 있음" in screen.slot_buttons[0].text()
    assert not screen.load_button.isEnabled()
    assert "야간" in screen.slot_buttons[2].text(), "다른 슬롯은 그대로"


def test_late_startup_load_does_not_wipe_a_fresh_slot(window) -> None:
    """켤 때 불러오기가 늦게 와도 방금 저장한 슬롯을 덮지 않는다(실제로 겪음)."""
    screen = _modes(window, "방금 저장")
    screen._save_clicked()
    window._apply_stored_settings("mode_slots", {})     # 늦게 도착한 옛 값
    assert "방금 저장" in screen.slot_buttons[0].text()
    assert screen.load_button.isEnabled()


# ---- 수동 제어 (차량) -------------------------------------------------------------------
def test_manual_jog_repeats_while_held_and_stops_on_release(window, qtbot) -> None:
    screen = window.screens["manual"]
    sent = []
    screen.manual_requested.connect(sent.append)
    fwd = screen.jog_buttons["cmd_mv_fwd"]
    assert not fwd.isEnabled(), "차량 제어가 더미면 잠겨 있다"

    screen.set_available(True)
    fwd.pressed.emit()
    qtbot.wait(screen.REPEAT_MS * 2 + 80)
    fwd.released.emit()
    assert sent[0] == {"cmd_mv_fwd": True}
    assert sent.count({"cmd_mv_fwd": True}) >= 2, "누르는 동안 다시 보낸다"
    assert sent[-1] == {}, "떼면 '명령 없음'으로 멈춘다"

    # 리프트 — 화면은 높이 [m] 만 알린다. 앱이 ManualCommand(cmd_mv_lift · lift_height)로 보낸다.
    sent.clear()
    lifts = []
    screen.lift_requested.connect(lifts.append)
    screen.lift_target.setValue(1200)
    screen.lift_button.click()
    assert sent == [] and lifts == [pytest.approx(1.2)]


def test_manual_outriggers_move_together_only(window) -> None:
    """아웃트리거는 균형 때문에 전체 내리기(고정) · 올리기(해제)만 — 개별 버튼이 없다(2026-11-19)."""
    screen = window.screens["manual"]
    sent = []
    screen.manual_requested.connect(sent.append)
    screen.set_available(True)
    assert not hasattr(screen, "outrigger_buttons")
    screen.outrigger_set_button.click()
    screen.outrigger_release_button.click()
    assert sent == [{"cmd_outrg_set": 2}, {"cmd_outrg_set": 1}]


def test_manual_lift_goes_by_manual_command_at_plc_speed(window) -> None:
    """수동 화면 리프트 → ManualCommand(cmd_mv_lift 1, lift_height m) — 속도 필드 없음, PLC 가 정한다(AGV 판)."""
    calls = []
    window.vehicle.manual = lambda **fields: calls.append(fields) or True
    window.vehicle.lift = lambda *a, **k: pytest.fail("수동 리프트는 SetMvLift 로 보내지 않는다")
    window.screens["manual"].lift_requested.emit(0.8)
    assert calls == [{"cmd_mv_lift": 1, "lift_height": 0.8}]


def test_manual_odometry_reset_sends_x_y_and_yaw_in_radians(window) -> None:
    """「위치 다시 맞추기」 → ResetOdometry(x m, y m, yaw rad) — 확인을 받고, 작업 중에는 잠긴다."""
    screen = window.screens["manual"]
    calls = []
    window.vehicle.reset_odometry = lambda x, y, yaw: calls.append((x, y, yaw)) or True
    screen.confirm = lambda _text: True
    screen.set_available(True)
    screen.pose_inputs["x"].setValue(1.25)
    screen.pose_inputs["y"].setValue(-0.5)
    screen.pose_inputs["yaw"].setValue(90.0)
    screen.pose_button.click()
    assert calls == [(pytest.approx(1.25), pytest.approx(-0.5), pytest.approx(math.pi / 2))]

    screen.confirm = lambda _text: False
    screen.pose_button.click()
    assert len(calls) == 1, "확인을 거절하면 보내지 않는다"
    screen.set_interlock(True, "작업이 진행 중입니다")
    assert not screen.pose_button.isEnabled()


def test_manual_interlock_while_the_robot_works(window) -> None:
    screen = window.screens["manual"]
    screen.set_available(True)
    window._on_robot_task_state(1)                  # 로봇 태스크 실행 중
    assert not screen.jog_buttons["cmd_mv_fwd"].isEnabled()
    assert not screen.outrigger_release_button.isEnabled()
    assert not screen.lift_button.isEnabled()
    assert screen.outrigger_set_button.isEnabled() and screen.stop_button.isEnabled()
    assert "인터락" in screen.notice.text()
    window._on_robot_task_state(3)
    assert screen.jog_buttons["cmd_mv_fwd"].isEnabled()


def test_manual_status_panel(window) -> None:
    screen = window.screens["manual"]
    screen.set_status({"state": "HOLD", "hold": "SET", "error_code": 0, "job_id": "RCS-0003",
                       "mv_dist": 0.7, "set_dist": 0.7, "speed": 0.0, "sen1_dist": 0.51,
                       "sen2_dist": 0.49, "x": 2.1, "yaw": 0.0, "lift_h": 0.96})
    assert screen.lift_value.text() == "960 mm"
    assert screen.status_blocks["hold"].value_label.text() == "고정"
    assert screen.status_blocks["error"].value_label.text() == "정상"


def test_mode_slot_carries_connection_system_vehicle_and_robot_poses(window) -> None:
    """2026-11-24 — 운전 모드에 연결 · 시스템 · 차량 설정과 로봇 기준 위치도 같이 넣는다."""
    from smr_operator_ui.screens.records_screens import describe_mode
    from smr_operator_ui.services.reference_poses import load_reference_poses

    screen = _modes(window, "현장 A")
    conn, system, vehicle = (window.screens[k] for k in ("connection", "system", "vehicle"))
    conn.field("협동로봇 IP").setText("192.168.11.30")
    conn.field("PLC 포트").setValue(8501)
    conn.field("ERUT 비밀번호").setText("secret-pw")
    system.field("장비 이름").setText("SMR-A")
    vehicle.field(vehicle.SPEED_FIELD).setValue(300)
    home = {"values": [0.0, -90.0, 90.0, 0.0, 90.0, 0.0], "saved_at": "2026-11-20T10:00:00"}
    window._reference_poses = {"home_joint": dict(home)}
    sent: list[tuple] = []
    window.ros_status.send_pose = lambda target, values: sent.append((target, list(values))) or True

    screen._save_clicked()                           # 슬롯 1
    snap = window._mode_slots["1"]
    assert snap["connection"]["협동로봇 IP"] == "192.168.11.30" and snap["connection"]["PLC 포트"] == 8501
    assert snap["system"]["장비 이름"] == "SMR-A"
    assert snap["vehicle"]["차량 주행 속도"] == 300
    assert snap["reference_poses"] == {"home_joint": home}
    text = describe_mode(snap)
    assert "연결: 로봇 192.168.11.30" in text and "PLC 192.168.11.10:8501" in text
    assert "시스템: SMR-A" in text and "차량: 주행 300 mm/s" in text and "로봇 기준 위치: 홈" in text
    assert "secret-pw" not in text, "비밀번호는 보이지 않는다"

    # 바꿔 놓고 불러오면 슬롯 값으로 돌아온다 — 기준 위치는 파일 · 로봇에도.
    conn.field("협동로봇 IP").setText("10.0.0.9")
    system.field("장비 이름").setText("다른 장비")
    vehicle.field(vehicle.SPEED_FIELD).setValue(700)
    window._reference_poses = {"home_joint": {"values": [1.0] * 6, "saved_at": "x"}}
    screen._load_clicked()
    assert conn.robot_ip() == "192.168.11.30" and system.values()["장비 이름"] == "SMR-A"
    assert vehicle.speed_mm_s() == 300 and window._vehicle_amr.speed_mps == 0.3, "저장 버튼과 같이 적용"
    assert window._reference_poses["home_joint"] == home
    assert ("home_joint", home["values"]) in sent
    assert load_reference_poses()["home_joint"] == home, "저장 시각 그대로 파일에"
    assert "기준 위치 1개" in screen.status_label.text()


def test_mode_slot_with_poses_waits_for_the_robot_task_to_stop(window) -> None:
    screen = _modes(window)
    window._reference_poses = {"home_joint": {"values": [0.0] * 6, "saved_at": "s"}}
    screen._save_clicked()
    window._robot_task_running = lambda: True
    window.screens["cobot"].field("작업 속도").setValue(66)
    screen._load_clicked()
    assert window.screens["cobot"].field("작업 속도").value() == 66
    assert "로봇 태스크가 도는 중" in screen.status_label.text()


def test_manual_lift_has_stop_and_the_amr_jog_has_none(window) -> None:
    """2026-11-24 — AMR 조그는 누르는 동안만 움직여 「정지」가 없다. 리프트 카드에 「정지」만 둔다
    (올리기 · 내리기는 같은 날 뺐다 — 높이 입력 + 「이 높이로 이동」은 그대로)."""
    screen = window.screens["manual"]
    screen.set_available(True)
    assert not hasattr(screen, "lift_up_button") and not hasattr(screen, "lift_down_button")
    jog_card = screen.jog_buttons["cmd_mv_fwd"].parentWidget()
    assert screen.stop_button.parentWidget() is not jog_card, "정지는 AMR 조그 카드에 없다"
    assert screen.stop_button.parentWidget() is screen.lift_button.parentWidget(), "리프트 카드에 있다"
    lifts, stops = [], []
    screen.lift_requested.connect(lifts.append)
    screen.stop_requested.connect(lambda: stops.append(1))
    screen.lift_target.setValue(700)
    screen.lift_button.click()
    screen.stop_button.click()
    assert lifts == [pytest.approx(0.7)] and stops == [1]
    screen.set_interlock(True, "로봇이 홈 위치가 아님")
    assert not screen.lift_button.isEnabled()
    assert screen.stop_button.isEnabled(), "인터락이어도 정지는 누를 수 있다"


def test_outrigger_hold_lamp_follows_the_vehicle_status(window) -> None:
    screen = window.screens["manual"]
    screen.set_available(True)
    screen.set_status({"state": "HOLD", "hold": "SET"})
    assert screen.hold_lamp.property("lamp") == "on" and screen.hold_text.text() == "고정됨 — HOLD"
    screen.set_status({"state": "STOP", "hold": "RELEASE"})
    assert screen.hold_lamp.property("lamp") == "off" and screen.hold_text.text() == "해제됨"
    screen.set_available(False, "차량 상태가 들어오지 않습니다")
    assert screen.hold_lamp.property("lamp") == "unknown"


# ---- 운전 모드 — 모재마다 다른 기준 위치 4개 (2026-11-25 검토) --------------------------------
POSES_A = {"home_joint": [0.0, -90.0, 90.0, 0.0, 90.0, 0.0],
           "standby_joint": [10.0, -80.0, 80.0, 0.0, 90.0, 0.0],
           "start_pose": [-900.5, -23.7, -343.6, 86.3, -0.3, -89.8],
           "assembly_joint": [20.0, -70.0, 70.0, 0.0, 90.0, 0.0]}


def test_mode_slot_restores_all_four_reference_poses(window) -> None:
    """받침대 높이가 다른 모재마다 슬롯에 저장 — 홈 · 스캔 대기 · 그리퍼 접촉 · 조립 4개가 파일 · 화면 · 로봇에 돌아온다."""
    from smr_operator_ui.services.reference_poses import load_reference_poses

    screen = _modes(window, "모재 A (받침대 높음)")
    window._reference_poses = {k: {"values": list(v), "saved_at": "2026-11-20T10:00:00"} for k, v in POSES_A.items()}
    sent: list[tuple] = []
    window.ros_status.send_pose = lambda target, values: sent.append((target, list(values))) or True
    screen._save_clicked()
    assert set(window._mode_slots["1"]["reference_poses"]) == set(POSES_A)

    window._reference_poses = {k: {"values": [1.0] * 6, "saved_at": "다른 모재"} for k in POSES_A}
    screen._load_clicked()
    assert {k: v["values"] for k, v in window._reference_poses.items()} == POSES_A
    assert sorted(t for t, _ in sent) == sorted(POSES_A), "4개 모두 로봇(노드)으로 보낸다"
    saved = load_reference_poses()
    assert {k: saved[k]["values"] for k in POSES_A} == POSES_A, "기준 위치 파일에도"
    assert window.screens["vehicle"].contact_height_mm() == -343.6, "그리퍼 접촉 높이도 그 모재 값으로"
    vehicle = window.screens["vehicle"]
    box = vehicle.field(vehicle.CONTACT_FIELD)
    assert box.value() == -344 and not box.isEnabled(), "차량 설정 칸에 그 값이 들어가고 잠긴다"
    for command, (target, _labels) in window.POSE_TARGETS.items():
        assert window.cobot_jog_screen.saved_labels[command].text(), f"{target} 카드에 값이 보인다"


def test_mode_slot_warns_about_reference_poses_it_does_not_have(window) -> None:
    """빠진 위치는 불러와도 앞 모재의 값이 남는다 — 저장할 때 · 불러올 때 알린다(2026-11-25)."""
    from smr_operator_ui.screens.records_screens import describe_mode

    screen = _modes(window, "모재 B")
    window.ros_status.send_pose = lambda target, values: True
    without_contact = {k: v for k, v in POSES_A.items() if k != "start_pose"}
    window._reference_poses = {k: {"values": list(v), "saved_at": "s"} for k, v in without_contact.items()}
    screen._save_clicked()
    text = screen.status_label.text()
    assert "저장 안 된 기준 위치: 그리퍼 접촉" in text and screen.status_label.objectName() == "StatusWarn"
    assert "없음(불러와도 지금 값 유지): 그리퍼 접촉" in describe_mode(window._mode_slots["1"])

    window._reference_poses["start_pose"] = {"values": list(POSES_A["start_pose"]), "saved_at": "모재 A"}
    screen._load_clicked()
    assert "슬롯에 없어 지금 값을 그대로 둔 위치: 그리퍼 접촉" in screen.status_label.text()
    assert window._reference_poses["start_pose"]["saved_at"] == "모재 A", "지우지 않는다(티칭값 보호)"


def test_mode_slot_summary_shows_the_gripper_contact_height(window) -> None:
    from smr_operator_ui.screens.records_screens import describe_mode

    screen = _modes(window, "모재 A")
    window._reference_poses = {k: {"values": list(v), "saved_at": "s"} for k, v in POSES_A.items()}
    screen._save_clicked()
    assert "기준 위치 4개 포함" in screen.status_label.text()
    assert "그리퍼 접촉(z -343.6 mm)" in describe_mode(window._mode_slots["1"])


# ---- 지금 운전 모드 표시 · 켤 때 다시 불러오기 (2026-11-25) ---------------------------------
def test_main_screen_shows_the_current_mode(window) -> None:
    screen = _modes(window, "모재 A")
    screen._save_clicked()                            # 슬롯 1 — 지금 설정을 슬롯으로 = 지금 운전 모드
    assert window.main_screen.mode_label.text() == "슬롯 1 · 모재 A"
    screen.slot_buttons[1].click()
    screen.ask_name = lambda default: "모재 B"
    screen._save_clicked()                            # 슬롯 2
    screen.slot_buttons[0].click()
    screen._load_clicked()
    assert window.main_screen.mode_label.text() == "슬롯 1 · 모재 A"
    screen.clear_button.click()
    assert window.main_screen.mode_label.text().startswith("없음")


def test_last_mode_is_loaded_again_when_the_program_starts(qtbot, tmp_path, monkeypatch) -> None:
    """껐다 켜면 마지막 운전 모드를 다시 불러온다 — 그 슬롯의 값(작업 속도 · 기준 위치)이 돌아온다."""
    import json

    settings = tmp_path / "settings.json"
    monkeypatch.setenv("SMR_SETTINGS_FILE", str(settings))
    monkeypatch.setenv("SMR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SMR_ROBOT_CONFIG_DIR", str(tmp_path / "robot_config"))
    first = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(first)
    first.ros_status.send_pose = lambda target, values: True
    first.screens["cobot"].field("작업 속도").setValue(73)
    first._reference_poses = {"start_pose": {"values": list(POSES_A["start_pose"]), "saved_at": "s"}}
    modes = _modes(first, "모재 A")
    modes._save_clicked()

    def stored():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data
    qtbot.waitUntil(lambda: stored().get("mode_current", {}).get("slot") == 1
                    and "1" in stored().get("mode_slots", {}), timeout=5000)
    first.close()

    # 껐다 켠 사이 다른 값으로 바뀌어 있었다(파일에 남은 개별 설정).
    data = stored()
    data.setdefault("cobot", {})["작업 속도"] = 40
    settings.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "robot_config").mkdir(exist_ok=True)

    second = OperatorWindow(start_ros=False, start_erut=False, restore_mode=True)
    qtbot.addWidget(second)
    second.ros_status.send_pose = lambda target, values: True
    qtbot.waitUntil(lambda: second.main_screen.mode_label.text() == "슬롯 1 · 모재 A", timeout=5000)
    assert second.screens["cobot"].field("작업 속도").value() == 73, "슬롯 값이 돌아온다"
    assert second._reference_poses["start_pose"]["values"] == POSES_A["start_pose"]
    second.close()
