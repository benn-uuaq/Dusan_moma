"""사내 MC 격자 지정 마킹 — mqtt_job_sim 이 보내는 mark_cmd 를 RCS 가 받아 돈다.

  doosan/robot/req/mark_cmd   {mark_id, cell{column,row}, point{x,y}, plan{...}}
  doosan/robot/mark_state     {mark_id, cell, state: executing/completed/failed/rejected}
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from smr_operator_ui.app import OperatorWindow
from smr_operator_ui.services.mqtt_server import MqttPayloadError, MqttTopics, validate_command_payload

SIM_PATH = Path(__file__).resolve().parents[3] / "mqtt_test" / "mqtt_job_sim.py"
PLAN = {"column_count": "3", "row_count": "4", "cell_width": "721", "cell_height": "140",
        "overlap": "20", "radius": "834.6", "thickness": "10", "eoat": "5"}


def _payload(column="2", row="C", x="250", y="35", mark_id="m001", **plan):
    import time
    return {"timestamp": str(int(time.time() * 1000)), "mark_id": mark_id,
            "cell": {"column": column, "row": row}, "point": {"x": x, "y": y},
            "plan": {**PLAN, **plan}}


@pytest.fixture
def window(qtbot, monkeypatch):
    w = OperatorWindow(start_mqtt=False, start_ros=False)
    qtbot.addWidget(w)
    w.ros_status.call_command = lambda name: True
    w.ros_status.set_task_paths = lambda scan, mark: True
    states, starts, areas = [], [], []
    w.mqtt_server.publish_mark_state = lambda mark_id, state, cell="", detail="": states.append(
        (mark_id, state, cell, detail)) or True
    monkeypatch.setattr(w.mark_runner, "start", lambda points, cw, ch: starts.append((points, cw, ch)))
    monkeypatch.setattr(w, "_send_work_area", lambda *a, **k: areas.append(a))
    w.test_log = (states, starts, areas)
    yield w
    w.close()


def test_payload_format_is_validated() -> None:
    validate_command_payload(MqttTopics.MARK_COMMAND, _payload())
    bad = _payload()
    del bad["point"]["y"]
    with pytest.raises(MqttPayloadError):
        validate_command_payload(MqttTopics.MARK_COMMAND, bad)
    assert MqttTopics.MARK_COMMAND in MqttTopics.COMMANDS
    assert MqttTopics.MARK_STATE in MqttTopics.STATUSES


def test_mark_goes_to_the_column_row_and_cell_coordinate(window) -> None:
    states, starts, areas = window.test_log
    window._handle_mqtt_command(MqttTopics.MARK_COMMAND, _payload(column="2", row="C", x="250", y="35"))
    (points, cw, ch), = starts
    # 스캔 순회와 같은 계산: 열 간격 = 721 - 20, 행 간격 = 140 - 20
    assert points == [{"id": "m001", "amr_mm": 701.0, "lift_mm": 240.0, "u": 250.0, "v": 35.0}]
    assert (cw, ch) == (721.0, 140.0)
    assert areas and areas[0][:2] == (721.0, 140.0), "로봇 마킹 태스크가 쓸 격자 크기를 먼저 보낸다"
    assert states == [("m001", "executing", "2C", "")]

    window._mc_mark = ("m001", "2C")
    window._finish_mqtt_mark(["m001"], [])
    assert states[-1] == ("m001", "completed", "2C", "")


@pytest.mark.parametrize("column,row,x,y,why", (
    ("4", "A", "0", "0", "열"), ("1", "E", "0", "0", "행"),
    ("1", "A", "722", "0", "좌표"), ("1", "A", "0", "-1", "좌표")))
def test_out_of_range_marks_are_rejected(window, column, row, x, y, why) -> None:
    states, starts, _ = window.test_log
    window._handle_mqtt_command(MqttTopics.MARK_COMMAND, _payload(column=column, row=row, x=x, y=y))
    assert starts == []
    assert states and states[-1][1] == "rejected" and why in states[-1][3]


def test_mark_is_rejected_while_a_job_runs(window) -> None:
    states, starts, _ = window.test_log
    window._start_inspection()
    window._handle_mqtt_command(MqttTopics.MARK_COMMAND, _payload())
    assert starts == [] and states[-1][1] == "rejected"
    window._stop_inspection()


def test_the_simulator_payload_is_accepted_by_rcs(window, qtbot) -> None:
    """mqtt_job_sim 의 마킹 패널이 실제로 만드는 payload 를 그대로 넣어 본다."""
    spec = importlib.util.spec_from_file_location("mqtt_job_sim_under_test", SIM_PATH)
    sim = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = sim
    spec.loader.exec_module(sim)
    try:
        root = sim.tk.Tk()
    except sim.tk.TclError:
        pytest.skip("화면(DISPLAY)이 없어 Tk 를 띄울 수 없습니다")
    try:
        cls = next(v for v in vars(sim).values() if isinstance(v, type) and hasattr(v, "_publish_mark_command"))
        app = cls(root)
        sent = []
        app._publish = lambda topic, payload: sent.append((topic, payload))
        app.column_count_var.set("3"); app.row_count_var.set("4")
        app.cell_width_var.set("721"); app.cell_height_var.set("140")
        app.mark_column_var.set("3"); app.mark_row_var.set("D")
        app.mark_x_var.set("700"); app.mark_y_var.set("140")
        app._publish_mark_command()
        (topic, payload), = sent
        assert topic == sim.MARK_COMMAND == MqttTopics.MARK_COMMAND
        assert app.mark_id_var.get() == "m002", "다음 번호로 올린다"
        app._handle_mark_state({"mark_id": "m001", "cell": "3D", "state": "completed"})
        assert "완료" in app.mark_state_var.get()
    finally:
        root.destroy()

    validate_command_payload(MqttTopics.MARK_COMMAND, payload)
    states, starts, _ = window.test_log
    window._handle_mqtt_command(MqttTopics.MARK_COMMAND, payload)
    (points, _, _), = starts
    assert points[0]["amr_mm"] == 2 * 701.0 and points[0]["lift_mm"] == 3 * 120.0
    assert (points[0]["u"], points[0]["v"]) == (700.0, 140.0)
