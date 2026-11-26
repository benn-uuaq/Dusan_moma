"""ERUT 시뮬레이터(mqtt_test/erut_sim.py)가 만드는 요청을 RCS 가 그대로 받는지 (if-0.13, 2026-11-24).

시뮬레이터는 RCS 코드에 기대지 않는 단독 도구라, 여기서 그 요청을 RCS 의 ErutSession 에 넣어 본다 —
원통 · 곡판 모두 calibrate 202, prepare 202(evt/ready) 여야 한다.
"""

import importlib.util
import math
import pathlib
import sys

import pytest

from smr_operator_ui.services import JobSequencer
from smr_operator_ui.services.erut_session import ErutSession

WS = pathlib.Path(__file__).resolve().parents[3]


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module                      # dataclass 가 모듈을 찾는다
    spec.loader.exec_module(module)
    return module


sim = _load("erut_sim_under_test", WS / "mqtt_test" / "erut_sim.py")
FakeClient = _load("erut_session_fakes", pathlib.Path(__file__).with_name("test_erut_session.py")).FakeClient


@pytest.fixture
def session(qtbot):
    client = FakeClient()
    return ErutSession(client, JobSequencer()), client


def _send(session, action, content):
    s, client = session
    s.handle_request(action, content)
    return [r for r in client.res if r["req_id"] == content["req_id"]][-1]


def test_cylinder_grid_goes_column_first_bottom_up_with_overlap():
    plan = sim.Plan()                                    # 원통 2500 × 6000, 2 × 2, 600 × 800, 겹침 20
    sections = plan.sections()
    assert [(s.label, s.x0, s.y0, s.x1, s.y1) for s in sections] == [
        ("A-01", 0, 0, 600, 800), ("A-02", 0, 780, 600, 1580),
        ("B-01", 580, 0, 1180, 800), ("B-02", 580, 780, 1180, 1580)]
    assert [plan.job_id(s.index) for s in sections] == [
        "jb00000001", "jb00000002", "jb00000003", "jb00000004"]
    assert plan.surface_width() == pytest.approx(math.pi * 2500)
    assert plan.problems() == []


def test_plate_sections_are_one_column_centred_on_the_plate():
    """곡판은 차량이 판 가운데 QR 앞에서 움직이지 않는다 — 열 1 개, 구역 가운데 = 판 가운데."""
    plan = sim.Plan(shape="curved_plate", width=1500, columns=3, start_x=999)
    sections = plan.sections()
    assert len(sections) == 2 and {s.column for s in sections} == {0}
    assert all((s.x0 + s.x1) / 2 == 750 for s in sections)
    assert plan.geometry() == {"shape": "curved_plate", "diameter": 2500, "height": 6000, "width": 1500}


def test_problems_are_shown_before_sending():
    plan = sim.Plan(height=1000, pitch_left=0, pitch_right=0, speed_pct=1, channels="0")
    text = " ".join(plan.problems())
    assert "모재 높이" in text and "이동높이" in text and "속도" in text and "채널" in text


@pytest.mark.parametrize("shape", ["cylinder", "curved_plate"])
def test_rcs_accepts_the_simulator_requests(session, shape):
    plan = sim.Plan(shape=shape)
    requests = sim.Requests()
    first = plan.sections()[0]
    calibrate = requests.content("calibrate", plan, first)
    assert calibrate["geometry"]["shape"] == shape
    assert _send(session, "calibrate", calibrate)["status_code"] == 202

    s, client = session
    s._calibrate_req_id = ""                 # 차량 한 바퀴 없이 원점이 잡혔다고 친다
    s._calibrated = True
    s._calibrated_geometry = dict(plan.geometry())
    prepare = requests.content("prepare", plan, first)
    assert prepare["scan"] == {"pitch_left": 30, "pitch_right": 30, "overlap": 20,
                               "speed_pct": 100, "speed_mm_s": 100.0}
    assert prepare["holder"] == {"channel_count": 5} and prepare["surface"] == "outer"
    answer = _send(session, "prepare", prepare)
    assert answer["status_code"] == 202, answer
    assert [e for e in client.events if e[0] == "ready"][-1][1]["status_code"] == 200


def test_stop_resume_and_marks_carry_the_section():
    plan = sim.Plan()
    requests = sim.Requests()
    second = plan.sections()[1]
    pause = requests.content("pause", plan, second)
    assert pause["job_id"] == "jb00000002" and pause["reason"] == "operator"
    assert requests.content("resume", plan, second)["job_id"] == "jb00000002"
    assert requests.content("abort", plan, second)["job_id"] == "jb00000002"
    mark = requests.content("mark", plan, second)
    assert all(second.x0 <= p["x"] <= second.x1 and second.y0 <= p["y"] <= second.y1 for p in mark["points"])
    ids = {requests.content("query", plan, None)["req_id"] for _ in range(3)}
    assert len(ids) == 3, "req_id 는 보낼 때마다 새 번호"
    channels_off = requests.content("start", sim.Plan(channels=""), second)
    assert "holder" not in channels_off


def test_current_values_are_saved_privately_and_loaded_back(tmp_path):
    """「현재 값 저장」(2026-11-24) — 저장소 밖 파일에 본인만 읽게(브로커 계정 · 비밀번호가 들어갈 수 있다)."""
    path = tmp_path / "sim" / "settings.json"
    values = {"host": "127.0.0.1", "port": "5532", "password": "pw", "height": "1400", "cell_h": "480",
              "rows": "3", "auto_marker": False, "section": 8}
    assert sim.save_settings(values, path) == path
    assert (path.stat().st_mode & 0o777) == 0o600
    assert sim.load_settings(path) == values
    assert sim.load_settings(tmp_path / "없음.json") == {}
    path.write_text("{깨짐", encoding="utf-8")
    assert sim.load_settings(path) == {}
    assert str(sim.SETTINGS_PATH).startswith(str(pathlib.Path.home())) or "ERUT_SIM_SETTINGS" in __import__("os").environ
