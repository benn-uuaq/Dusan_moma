"""ERUT 스캔 속도(307)는 ㄹ자 스캔 줄에만 — 화면에 보이고, 덮이지 않고, 실패를 알린다 (2026-11-11).

예전 문제:
  · RCS 속도 바는 전체 비율(레지스터 17)만 보여 ERUT 속도가 화면 어디에도 없었다.
  · 속도 바 · 설정 저장 · 재연결 복원이 307 까지 덮어 ERUT 속도가 소리 없이 풀렸다.
  · 307 을 못 보내도 「보냈습니다」라고 적었다.
"""

from smr_operator_ui.app import OperatorWindow

SCAN = {"pitch_left": 40, "pitch_right": 40, "speed_mm_s": 40, "speed_pct": 40, "overlap": 0}
JOB = {"job_id": "jb-speed", "surface": "outer", "diameter": 1300, "height": 800,
       "area": {"start": {"x": 0, "y": 0}, "end": {"x": 300, "y": 200}}, "scan": SCAN}


def _window(qtbot, monkeypatch, ok: bool = True):
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    sent: list[tuple[str, int]] = []
    monkeypatch.setattr(window.ros_status, "send_value",
                        lambda name, value: sent.append((name, int(value))) or ok)
    monkeypatch.setattr(window.ros_status, "writable", lambda name: True)
    window.erut.publish_event = lambda *a, **k: True
    window.erut.publish_res = lambda *a, **k: True
    window.erut_session._calibrated = True
    window.main_screen.orbit_view.set_target_dimensions(1.3, 0.8)
    window._push_work_area_to_robot = lambda *values: None
    return window, sent


def _prepare(window):
    window.erut_session.handle_request("prepare", {"req_id": "p-speed", **JOB})
    assert window.erut_session.has_work()


def _scan_writes(sent):
    return [value for name, value in sent if name == "scan_speed_ratio"]


def _alarms(window):
    alarms = window.cobot_manual_screen.alarm_list
    return [alarms.item(i).text() for i in range(alarms.count())]


def test_erut_speed_is_shown_under_the_speed_bar(qtbot, monkeypatch) -> None:
    window, sent = _window(qtbot, monkeypatch)
    _prepare(window)
    assert _scan_writes(sent) == [40]
    text = window.main_screen.speed_bar.scan_text()
    assert "40 %" in text and "40 mm/s" in text and "ERUT" in text
    assert window.main_screen.speed_bar.value() == 100      # 전체 비율은 그대로
    window.close()


def test_speed_bar_keeps_the_erut_scan_speed_during_a_job(qtbot, monkeypatch) -> None:
    window, sent = _window(qtbot, monkeypatch)
    _prepare(window)
    sent.clear()
    window.main_screen.speed_changed.emit(70)
    assert ("speed_ratio", 70) in sent
    assert _scan_writes(sent) == [40], "ERUT 작업 중 속도 바가 스캔 줄 속도를 덮었다"
    assert "ERUT" in window.main_screen.activity_label.text()
    window.close()


def test_speed_bar_sets_the_scan_speed_without_an_erut_job(qtbot, monkeypatch) -> None:
    window, sent = _window(qtbot, monkeypatch)
    window.main_screen.speed_changed.emit(70)
    assert _scan_writes(sent) == [70]
    assert "속도 바" in window.main_screen.speed_bar.scan_text()
    window.close()


def test_reconnect_restore_rewrites_the_erut_scan_speed(qtbot, monkeypatch) -> None:
    window, sent = _window(qtbot, monkeypatch)
    _prepare(window)
    sent.clear()
    window._restore_robot_settings(True)
    names = [name for name, _value in sent]
    assert names.index("speed_ratio") < len(names) - 1 - names[::-1].index("scan_speed_ratio")
    assert _scan_writes(sent)[-1] == 40
    window.close()


def test_play_rewrites_the_scan_speed_first(qtbot, monkeypatch) -> None:
    window, sent = _window(qtbot, monkeypatch)
    _prepare(window)
    sent.clear()
    window._schedule_play()
    window._cancel_play()
    assert _scan_writes(sent) == [40]
    window.close()


def test_failed_send_raises_an_alarm_instead_of_claiming_success(qtbot, monkeypatch) -> None:
    window, _sent = _window(qtbot, monkeypatch, ok=False)
    window.erut_session.speed_requested.emit(40)
    assert any("보내지 못했습니다" in text and "40 %" in text for text in _alarms(window))
    assert "보냈습니다" not in window.main_screen.activity_label.text()
    assert "미전송" in window.main_screen.speed_bar.scan_text()
    window.close()
