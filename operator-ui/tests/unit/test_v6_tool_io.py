"""밀착 센서는 툴 디지털 입력 · 물 분사는 ERUT 몫 (v6, 2026-11-12).

툴 DI0 = 5축 홀더 밀착 감지, DI1 = 8축 홀더 밀착 감지, DI2 = 밀착 리미트 감지.
"""

import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
TASK = ROOT / "robot_task" / "dusan_task_v6"
FOLDERS = ("scripts", "scripts_nosensor", "scripts_nosensor_seq")


def _code(path: pathlib.Path) -> str:
    text = path.read_text(encoding="utf-8")
    return "\n".join(re.sub(r"#.*$", "", line) for line in text.splitlines())


def test_no_script_reads_the_standard_inputs_for_the_sensors() -> None:
    for folder in FOLDERS:
        for path in (TASK / folder).glob("*.script"):
            assert "get_standard_digital_in" not in _code(path), path.name


@pytest.mark.parametrize("name", ("dus6_probe_c.script", "dus6_probe_l.script",
                                  "dus6_probe_r.script", "dus6_pass_l.script",
                                  "dus6_pass_r.script", "dus6_mark_point.script"))
def test_sensor_scripts_read_the_tool_inputs(name) -> None:
    code = _code(TASK / "scripts" / name)
    assert "get_tool_digital(di_touch)" in code and "get_tool_digital(di_limit)" in code


@pytest.mark.parametrize("folder", ("scripts", "scripts_nosensor"))
def test_init_picks_the_contact_input_by_holder(folder) -> None:
    code = _code(TASK / folder / "dus6_init.script")
    assert re.search(r"if \(eoat_type == 8\):\s*\n\s*di_touch = 1\s*\n\s*else:\s*\n\s*di_touch = 0",
                     code)
    assert re.search(r"^di_limit = 2\s*$", code, re.M)
    # 툴 입력은 RCS 가 레지스터 1(xxxxTTTTCCCCCCCC)에서 바로 읽는다 — 태스크가 따로 쓰지 않는다(2026-11-17).
    assert "pub_tool_in" not in code and "279" not in code


def test_no_water_spray_control_is_left() -> None:
    for folder in FOLDERS:
        for path in (TASK / folder).glob("*.script"):
            text = path.read_text(encoding="utf-8")
            assert "TODO: 여기에 물 분사" not in text and "물 분사 끄기" not in text, path.name
            assert "set_standard_digital_out" not in _code(path), path.name


def test_io_list_shows_the_tool_inputs_and_no_water_or_marker_outputs() -> None:
    data = json.loads((ROOT / "operator-ui" / "src" / "smr_operator_ui" / "config" / "plc_io.json")
                      .read_text(encoding="utf-8"))
    tool = {s["address"]: s for s in data["signals"] if s["address"].startswith("TDI")}
    # 레지스터 1 = xxxxTTTTCCCCCCCC — 툴 입력은 비트 8~11.
    assert all(t["bus"] == "robot_config" for t in tool.values())
    assert [tool[a]["bit"] for a in ("TDI0", "TDI1", "TDI2")] == [8, 9, 10]
    assert "5축 홀더 밀착" in tool["TDI0"]["name"] and "8축 홀더 밀착" in tool["TDI1"]["name"]
    assert "밀착 리미트" in tool["TDI2"]["name"]
    names = " ".join(s["name"] for s in data["signals"])
    assert "물 분사" not in names and "마킹기" not in names


def test_tool_inputs_reach_the_io_screen(qtbot) -> None:
    from smr_operator_ui.app import OperatorWindow
    window = OperatorWindow(start_ros=False, start_erut=False)
    qtbot.addWidget(window)
    window.ros_status.config_digital_in_changed.emit(0b101 << 8)   # 툴 T0 5축 밀착 · T2 리미트
    io = window.screens["io"]
    assert io.value_items["TDI0"].text() == "ON"
    assert io.value_items["TDI1"].text() == "OFF"
    assert io.value_items["TDI2"].text() == "ON"
    window.close()
