"""Cobot 조그 화면 위치 카드의 「이동」 · 「삭제」 (2026-11-12)."""

import pytest

from smr_operator_ui.app import OperatorWindow


def _window(qtbot, monkeypatch, poses=None):
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    window._reference_poses = dict(poses or {})
    calls: list[str] = []
    sent: list[tuple[str, list]] = []
    monkeypatch.setattr(window.ros_status, "call_command", lambda name: calls.append(name) or True)
    monkeypatch.setattr(window.ros_status, "send_pose",
                        lambda name, values: sent.append((name, list(values))) or True)
    deleted: list[str] = []
    monkeypatch.setattr("smr_operator_ui.app.delete_reference_pose",
                        lambda name: deleted.append(name) or "/tmp/x.json")
    return window, calls, sent, deleted


def test_every_card_has_move_save_and_delete(qtbot, monkeypatch) -> None:
    window, *_ = _window(qtbot, monkeypatch)
    jog = window.cobot_jog_screen
    for _text, command in jog._SAVE:
        assert jog.move_buttons[command].text() == "이동"
        assert jog.save_buttons[command].text() == "저장"
        assert jog.delete_buttons[command].text() == "삭제"
    window.close()


@pytest.mark.parametrize("command, service", (
    ("save_standby_pose", "move_standby"),
    ("save_start_pose", "move_gripper"),
))
def test_move_sends_the_node_command(qtbot, monkeypatch, command, service) -> None:
    window, calls, *_ = _window(qtbot, monkeypatch)
    window.cobot_jog_screen.move_buttons[command].click()
    assert calls == [service]
    window.close()


def test_move_home_and_assembly_use_their_existing_paths(qtbot, monkeypatch) -> None:
    window, calls, *_ = _window(qtbot, monkeypatch,
                                {"assembly_joint": {"values": [1.0] * 6}})
    window._robot_at_home = True
    window.cobot_jog_screen.move_buttons["save_home_pose"].click()
    window.cobot_jog_screen.move_buttons["save_assembly_pose"].click()
    assert calls == ["home", "move_assembly"]
    window.close()


def test_move_is_refused_while_the_robot_works(qtbot, monkeypatch) -> None:
    window, calls, *_ = _window(qtbot, monkeypatch)
    window._robot_task_state = window._TASK_STATE_RUNNING
    window.cobot_jog_screen.move_buttons["save_standby_pose"].click()
    assert calls == []
    assert "거절" in window.cobot_jog_screen.activity_label.text()
    window.close()


def test_delete_asks_then_clears_file_screen_and_robot(qtbot, monkeypatch) -> None:
    window, _calls, sent, deleted = _window(
        qtbot, monkeypatch, {"home_joint": {"values": [1.0] * 6, "saved_at": "x"}})
    jog = window.cobot_jog_screen
    jog.set_saved_pose("save_home_pose", "베이스 1.0 ...")

    jog.confirm = lambda text: False
    jog.delete_buttons["save_home_pose"].click()
    assert deleted == [] and "home_joint" in window._reference_poses, "취소하면 그대로"

    jog.confirm = lambda text: True
    jog.delete_buttons["save_home_pose"].click()
    assert deleted == ["home_joint"]
    assert "home_joint" not in window._reference_poses
    assert jog.saved_labels["save_home_pose"].text() == "저장된 값 없음"
    # 0 을 쓰면 태스크 · 노드가 펜던트 값으로 돌아간다.
    assert sent == [("home_joint", [0] * 6)]
    assert "펜던트" in jog.activity_label.text()
    window.close()


def test_delete_is_refused_while_the_task_runs(qtbot, monkeypatch) -> None:
    window, _calls, sent, deleted = _window(
        qtbot, monkeypatch, {"start_pose": {"values": [1.0] * 6}})
    window._robot_task_state = window._TASK_STATE_RUNNING
    window.cobot_jog_screen.confirm = lambda text: True
    window.cobot_jog_screen.delete_buttons["save_start_pose"].click()
    assert deleted == [] and sent == []
    window.close()


def test_reconnect_clears_robot_slots_of_deleted_positions(qtbot, monkeypatch) -> None:
    window, _calls, sent, _deleted = _window(
        qtbot, monkeypatch, {"home_joint": {"values": [1.0] * 6}})
    monkeypatch.setattr(window.ros_status, "send_value", lambda *a: True)
    window._restore_robot_settings(True)
    names = {name: values for name, values in sent}
    assert names["home_joint"] == [1.0] * 6
    for target in ("standby_joint", "start_pose", "assembly_joint"):
        assert names[target] == [0] * 6
    window.close()
