"""차량(AGV 노드) 거절을 작업자 안내로 (2026-11-24).

현장: 수동 제어 버튼이 안 먹었다 — PLC 가 자동 모드(DM001 = 2)라 AGV 노드가 「manual mode required」로
모두 거절했고, RCS 는 영어 원문만 진행 알림에 0.2 s 마다 쏟았다. (PLC 로는 아무것도 쓰지 않았다 — AGV 노드의
PLC 기록에 True/False 쓰기는 없다. 기록에 보이는 True/False 는 받은 ROS 요청을 적은 것이다.)
"""

from smr_operator_ui.app import OperatorWindow
from smr_operator_ui.services.vehicle_client import explain_reject


def test_known_rejections_are_explained_and_unknown_ones_kept():
    assert "수동 모드가 아닙니다" in explain_reject("manual mode required")
    assert "「주행 수동」(1)" in explain_reject("drive requires DRIVE Manual mode and released outriggers")
    assert "PLC 명령 쓰기" in explain_reject("PLC writes disabled")
    assert explain_reject("something new") == "something new"


def test_manual_rejection_shows_on_the_manual_screen_once_per_burst(qtbot):
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    said: list[str] = []
    window.main_screen.show_activity = said.append
    manual = window.screens["manual"]
    for _ in range(5):                                  # 조그를 누르고 있는 동안 0.2 s 마다 다시 보낸다
        window.vehicle.command_result.emit("manual_command", False, "manual mode required")
    assert manual.result_label.isVisibleTo(manual)
    assert "차량이 거절했습니다" in manual.result_label.text() and "수동 모드가 아닙니다" in manual.result_label.text()
    assert len(said) == 1 and "(manual mode required)" in said[0], "같은 거절은 한 번만 알린다"
    window.vehicle.command_result.emit("manual_command", True, "accepted")
    assert not manual.result_label.isVisibleTo(manual), "받아들이면 지운다"
    window.close()


def test_lift_range_is_explained_in_mm_from_the_lift_limit():
    """입력이 mm 라 안내도 mm — 상한은 차량 설정 「리프트 한계 높이」(실제 5000 mm, 2026-11-24)."""
    assert explain_reject("lift height out of range", 5000) == "리프트 높이가 허용 범위(0 ~ 5,000 mm)를 벗어났습니다"
    assert "0 ~ 5,000 mm" in explain_reject("lift height out of range"), "모르면 실제 한계 5000"
    assert "200 mm/s" in explain_reject("automatic lift speed out of range")
    assert " m)" not in explain_reject("lift height out of range", 5000)


def test_manual_lift_inside_the_limit_but_refused_points_at_an_old_agv_node(qtbot):
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    vehicle = window.screens["vehicle"]
    vehicle.field(vehicle.LIFT_MAX_FIELD).setValue(5000)
    window.vehicle.manual_lift = lambda height_m: True
    window._manual_lift(3.0)                            # 3000 mm — 한계 안
    window.vehicle.command_result.emit("manual_command", False, "lift height out of range")
    text = window.screens["manual"].result_label.text()
    assert "0 ~ 5,000 mm" in text and "요청 3,000 mm" in text and "옛 상한" in text
    window._manual_lift(6.0)                            # 6000 mm — 한계 밖
    window.vehicle.command_result.emit("manual_command", False, "lift height out of range")
    text = window.screens["manual"].result_label.text()
    assert "요청 6,000 mm" in text and "옛 상한" not in text
    window.close()
