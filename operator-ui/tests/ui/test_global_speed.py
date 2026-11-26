"""메인 화면 속도 바 = 전체 속도 — 코봇 · 차량 주행 · 리프트에 같은 비율 (2026-11-26)."""

from smr_operator_ui.app import OperatorWindow


def test_speed_bar_scales_vehicle_and_lift_speeds(qtbot) -> None:
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    sent = []
    window.ros_status.send_value = lambda name, value: sent.append((name, value)) or True
    amr, lift = window._vehicle_amr, window._vehicle_lift
    amr.speed_mps, lift.speed_mps = 0.2, 0.05

    window._apply_speed_ratio(50, source="속도 바")
    assert ("speed_ratio", 50) in sent, "코봇에도 그대로 간다"
    assert amr.effective_speed_mps == 0.1
    assert amr.job_values()[0] == 0.1, "SetJob 주행 속도에 비율이 걸린다"
    assert lift.speed_scale == 0.5

    window._show_speed_scale(30)                         # 펜던트에서 바꾼 실제 비율도 따른다
    assert abs(amr.effective_speed_mps - 0.06) < 1e-9
    window._show_speed_scale(0)                          # 범위 밖(값 없음)은 무시
    assert abs(amr.effective_speed_mps - 0.06) < 1e-9
    window.close()


def test_speed_bar_shows_only_the_percent(qtbot) -> None:
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    bar = window.main_screen.speed_bar
    window.show()
    assert not bar.mms_label.isVisible() and not bar.scan_label.isVisible()
    assert bar.value_label.isVisible()
    window.close()
