#!/usr/bin/env python3
"""장비 없이 전체 경로를 한 번에 확인한다.

로봇 시뮬레이터 → ROS 노드 → 운영 UI → MQTT/TPAC 까지 실제 코드를 그대로
띄우고, 시나리오를 차례로 돌린다. 실물이 없는 동안 회귀를 잡아내는 그물이다.

  mc     사내 MC 규격: job_cmd 로 1A 부터 마지막 셀까지 순회한다.
         원점마다 프로브 확인(probe_ack)을 보내 스캔을 풀어 준다.
  erut   ERUT 표준(if-0.4): 자기소개 → calibrate → prepare → start → mark
         (mark_ready ↔ mark_next) → home → 비상정지 → reset.
  io     로봇 디지털 출력(레지스터 2)을 화면 경로로 켜고 끈다.
  tpac   TPAC 브리지가 스캔 구간 신호(DO[0..2])를 내보내는지 본다.

실행:
    python3 mqtt_test/run_sim_test.py                 # 전부 (2열 × 3행)
    python3 mqtt_test/run_sim_test.py --only mc erut
    python3 mqtt_test/run_sim_test.py --columns 3 --rows 2

ROS 2 워크스페이스를 소싱한 셸에서 실행해야 한다:
    source /opt/ros/humble/setup.bash && source install/setup.bash
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

WS = Path(__file__).resolve().parent.parent
SIMULATOR = WS / "src/elite_robot_controller/tools/elite_robot_simulator.py"
MODBUS_PORT = 5502

sys.path.insert(0, str(WS / "operator-ui/src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 로봇 태스크가 쓰는 레지스터 (dus_*.script 참고).
STATE_REG, SEGMENT_REG, DIGITAL_OUT_REG = 290, 277, 2
STATE_AT_ORIGIN, STATE_DONE = 7, 5
#: 시뮬레이터가 벽을 입력값보다 이만큼 어긋나게 둔다 [mm]. 캘리브레이션이 잡아낸다.
WALL_ERROR_MM = 0.3


def log(message: str) -> None:
    print(message, flush=True)


class Processes:
    """시뮬레이터와 ROS 노드를 띄우고 끝나면 정리한다.

    `ros2 run`은 실제 노드를 자식으로 띄우는 래퍼라, 래퍼만 종료하면 노드가
    살아남아 다음 실행 때 포트를 물고 있게 된다. 그래서 각 프로세스를 별도
    세션으로 띄우고 **프로세스 그룹째** 정리한다.
    """

    def __init__(self) -> None:
        self.procs: list[subprocess.Popen] = []

    #: 시뮬레이터·노드 출력을 남길 파일. 비워 두면 버린다.
    log_dir = os.environ.get("SIM_TEST_LOG_DIR", "")

    def _spawn(self, args: list[str], name: str = "") -> None:
        if self.log_dir and name:
            handle = open(os.path.join(self.log_dir, f"{name}.log"), "w")
            out = err = handle
        else:
            out = err = subprocess.DEVNULL
        self.procs.append(subprocess.Popen(
            args, cwd=str(WS), stdout=out, stderr=err, start_new_session=True,
        ))

    def start(self) -> None:
        log("로봇 시뮬레이터 기동...")
        self._spawn([sys.executable, "-u", str(SIMULATOR),
                     "--wall-error", str(WALL_ERROR_MM)], name="simulator")
        time.sleep(2)

        log("ROS 제어 노드 기동...")
        self._spawn(["ros2", "run", "elite_robot_controller", "robot_control_node",
                     "--ros-args", "-p", "robot_ip:=127.0.0.1",
                     "-p", f"modbus_port:={MODBUS_PORT}"], name="node")
        time.sleep(5)

    def stop(self) -> None:
        for proc in reversed(self.procs):
            self._signal_group(proc, signal.SIGTERM)
        deadline = time.time() + 5
        for proc in reversed(self.procs):
            try:
                proc.wait(timeout=max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                pass
        # 그래도 남아 있으면 강제 종료한다.
        for proc in reversed(self.procs):
            self._signal_group(proc, signal.SIGKILL)

    @staticmethod
    def _signal_group(proc: subprocess.Popen, sig: int) -> None:
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError):
            pass


class Harness:
    """운영 UI 한 벌과 시뮬레이터 레지스터를 함께 들고 있는 시험대."""

    def __init__(self, window, app, modbus, published: list) -> None:
        self.window, self.app, self.modbus = window, app, modbus
        self.published = published
        #: 발행을 받아 볼 함수들. 브로커가 없으니 여기로 나눠 준다.
        self.listeners: list = []

    def on_publish(self, topic: str, payload) -> None:
        for listener in list(self.listeners):
            listener(topic, payload)

    def pump(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            self.app.processEvents()
            time.sleep(0.02)

    def wait_until(self, check, timeout: float, poll: float = 0.1) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            self.pump(poll)
            if check():
                return True
        return False

    def register(self, address: int, count: int = 1) -> list[int]:
        values = self.modbus.read_holding_registers(address, count)
        return list(values or [])

    def topics(self, suffix: str) -> list[dict]:
        return [p for t, p in self.published if t.endswith(suffix)]


# ---------------------------------------------------------------- 시나리오
def scenario_mc(h: Harness, columns: int, rows: int) -> list[str]:
    """사내 MC 규격: job_cmd 하나로 전체 격자를 돈다."""
    from smr_operator_ui.services import MqttTopics

    window = h.window
    visited: list[str] = []
    window.sequencer.cell_changed.connect(lambda c, r, label, n: visited.append(label))

    # 로봇이 원점에서 프로브 확인을 기다리면 MC 대신 우리가 눌러 준다.
    gates: list[bool] = []

    def on_gate(topic: str, payload: dict) -> None:
        if not topic.endswith("probe_gate"):
            return
        waiting = str(payload.get("state")) == "waiting"
        gates.append(waiting)
        if waiting:
            window.mqtt_server.command_received.emit(
                MqttTopics.PROBE_ACK, {"timestamp": "1", "pressed": True})

    h.listeners.append(on_gate)

    total = columns * rows
    log(f"\n[mc] 작업 계획 발행: {columns}열 × {rows}행 = {total}개 셀")
    window.mqtt_server.command_received.emit(MqttTopics.JOB_COMMAND, {
        "timestamp": "1", "job_id": "jb-sim",
        "job_info": {"diameter": "2500", "height": "6000",
                     "target_distance": "8560"},
        "plan": {"column_count": str(columns), "row_count": str(rows),
                 "cell_width": "600", "cell_height": "800", "overlap": "20"},
    })

    seen = 0
    deadline = time.time() + 40 + total * 12
    while time.time() < deadline:
        h.pump(0.2)
        if len(visited) > seen:
            seen = len(visited)
            log(f"  진행: {visited[-1]}  [{window.sequencer.state.name}]")
        if window.sequencer.state.name == "DONE":
            break
    h.pump(1.5)

    states = h.topics("job_state")
    work_area = h.register(256, 4)
    expected = [f"{c + 1}{chr(ord('A') + r)}" for c in range(columns) for r in range(rows)]

    log(f"  방문한 셀      : {' → '.join(visited)}")
    log(f"  job_state 발행 : {len(states)}건 (셀당 3건 = {total * 3} 기대)")
    log(f"  원점 대기 신호 : {len(gates)}건")
    log(f"  레지스터 256~259: {work_area}")

    problems = []
    if visited != expected:
        problems.append(f"[mc] 셀 순서가 다릅니다. 기대: {expected}, 실제: {visited}")
    if window.sequencer.state.name != "DONE":
        problems.append(f"[mc] 전체 완료 상태가 아닙니다: {window.sequencer.state.name}")
    if len(states) != total * 3:
        problems.append(f"[mc] job_state 발행 수가 다릅니다: {len(states)}")
    # 258(스캐너 높이)은 현장 설정값이라 고정이 아니다 — 0.1mm 단위로 실려만 가면 된다.
    if work_area[:2] != [600, 800] or work_area[3] != 20 or work_area[2] <= 0:
        problems.append(f"[mc] 작업 영역 레지스터가 다릅니다: {work_area}")
    if gates.count(True) != total:
        problems.append(f"[mc] 원점 대기가 셀마다 뜨지 않았습니다: {gates.count(True)}/{total}")
    return problems


class ErutTap:
    """ERUT 로 나가는 것을 모두 가로채 기록한다(브로커 없이)."""

    def __init__(self, window) -> None:
        self.events: list[tuple[str, dict]] = []
        self.responses: list[dict] = []
        self.status: list[dict] = []
        self.contact: list[dict] = []
        self.progress: list[dict] = []
        self.errors: list[dict] = []
        self.info: list[dict] = []
        erut = window.erut
        erut.publish_event = lambda name, req, action, code=200, message="OK", **kw: (
            self.events.append((name, {"req_id": req, "action": action, "code": code,
                                       "message": message, **kw})) or True)
        erut.publish_res = lambda req, action, code, message, **kw: (
            self.responses.append({"req_id": req, "action": action, "code": code,
                                   "message": message, **kw}) or True)
        erut.publish_progress = lambda req, action, **kw: (
            self.progress.append({"req_id": req, "action": action, **kw}) or True)
        erut.publish_status = lambda state="online", **kw: (
            self.status.append({"state": state, **kw}) or True)
        erut.publish_contact = lambda state, job_id: (
            self.contact.append({"state": state, "job_id": job_id}) or True)
        erut.publish_info = lambda content: self.info.append(content) or True
        erut.publish_error = lambda code, message, level, recovery, cleared=False, **kw: (
            self.errors.append({"code": code, "level": level, "cleared": cleared}) or True)
        erut.publish_message = lambda *a, **kw: True

    def event(self, name: str, action: str) -> dict | None:
        found = [e for n, e in self.events if n == name and e["action"] == action]
        return found[-1] if found else None

    def activities(self) -> list[str]:
        seen: list[str] = []
        for st in self.status:
            if not seen or seen[-1] != st.get("activity"):
                seen.append(st.get("activity"))
        return seen


def scenario_erut(h: Harness) -> list[str]:
    """ERUT 표준 인터페이스(if-0.4) 한 바퀴.

    자기소개 → calibrate → prepare(ready) → start(접촉·진행률·완료) →
    mark(점마다 mark_ready ↔ mark_next) → home → 비상정지 → reset.
    """
    window = h.window
    session = window.erut_session
    tap = ErutTap(window)
    problems: list[str] = []

    session.publish_info()
    info = tap.info[-1]
    log(f"\n[erut] 자기소개: 판 {info['interface_version']} · {', '.join(info['capabilities'])}")
    if info["interface_version"] != "0.4" or "probe_contact" not in info["capabilities"]:
        problems.append(f"[erut] 자기소개가 이상합니다: {info}")

    log("[erut] calibrate — 로봇이 벽을 세 번 눌러 좌표계를 잡는다")
    session.handle_request("calibrate", {"req_id": "cal-1", "diameter": 1690, "height": 6000})
    if not h.wait_until(lambda: tap.event("complete", "calibrate"), timeout=60):
        return problems + ["[erut] 캘리브레이션 완료(evt/complete)가 오지 않았습니다"]
    result = tap.event("complete", "calibrate")
    error_mm = result.get("calibration_error_mm")
    log(f"  캘리브레이션 오차: {error_mm} mm (시뮬레이터가 벽을 {WALL_ERROR_MM} mm 어긋나게 둠)")
    if error_mm is None or error_mm > 2.0:
        problems.append(f"[erut] 캘리브레이션 오차가 이상합니다: {result}")

    log("[erut] prepare — 준비 중(preparing) → 원점에서 ready")
    h.pump(1.0)
    tap.status.clear()
    area = {"start": {"x": 580, "y": 0}, "end": {"x": 1180, "y": 800}}
    session.handle_request("prepare", {
        "req_id": "prep-1", "job_id": "jb-erut", "surface": "outer",
        "area": area, "scan": {"pitch": 20, "speed": 100},
    })
    if not h.wait_until(lambda: tap.event("ready", "prepare"), timeout=90):
        return problems + ["[erut] 준비 완료(evt/ready)가 오지 않았습니다"]
    log(f"  evt/ready 받음 · activity {' → '.join(map(str, tap.activities()))}")

    log("[erut] start — 원점 대기를 풀고 스캔")
    session.handle_request("start", {"req_id": "start-1", "job_id": "jb-erut",
                                     "surface": "outer", "area": area,
                                     "scan": {"pitch": 20, "speed": 100}})
    if not h.wait_until(lambda: tap.event("complete", "start"), timeout=180):
        return problems + ["[erut] 구간 완료(evt/complete)가 오지 않았습니다"]
    done = tap.event("complete", "start")
    log(f"  구간 완료: {done.get('job_id')} · 스캔 거리 {done.get('scanned_distance_mm')} mm"
        f" · 위치 {done.get('location')}")
    acts = tap.activities()
    log(f"  activity: {' → '.join(map(str, acts))}")
    for needed in ("preparing", "ready", "running"):
        if needed not in acts:
            problems.append(f"[erut] activity 에 {needed} 가 없었습니다: {acts}")
    attached = [c for c in tap.contact if c["state"] == "attached"]
    log(f"  접촉 신호: attached {len(attached)}회 · 마지막 {tap.contact[-1] if tap.contact else None}")
    if not attached or any(c["job_id"] != "jb-erut" for c in attached) \
            or any(c["state"] == "attached" and not c["job_id"] for c in tap.contact):
        problems.append(f"[erut] 접촉(attached) 신호가 없거나 job_id 가 다릅니다: {tap.contact}")
    if tap.contact and tap.contact[-1]["state"] != "detached":
        problems.append("[erut] 스캔이 끝났는데 접촉이 attached 로 남았습니다")
    progress = [p["progress"] for p in tap.progress]
    if progress and (progress != sorted(progress)
                     or not all(isinstance(v, int) for v in progress)):
        problems.append(f"[erut] 진행률이 정수가 아니거나 뒤로 갔습니다: {progress}")
    if (done.get("location") or {}).get("cell") != "2A":
        problems.append(f"[erut] 완료의 구역 이름이 2A 가 아닙니다: {done.get('location')}")

    log("[erut] mark — 점에 붙으면 mark_ready, ERUT 가 찍고 mark_next")
    h.pump(1.0)
    session.handle_request("mark", {"req_id": "mark-1", "method": "paint",
                                    "points": [{"id": "d1", "x": 900, "y": 300}]})
    if not h.wait_until(lambda: tap.event("mark_ready", "mark"), timeout=90):
        return problems + ["[erut] 마킹 자리 도착(evt/mark_ready)이 오지 않았습니다"]
    ready = tap.event("mark_ready", "mark")
    log(f"  mark_ready: 점 {ready.get('point_id')} — 로봇이 자리에서 기다린다")
    h.pump(1.0)
    if h.register(STATE_REG)[0] != 11:
        problems.append(f"[erut] 마킹 자리에서 로봇이 서 있지 않습니다(290={h.register(STATE_REG)[0]})")
    session.handle_request("mark_next", {"req_id": "mark-1", "point_id": "d1", "marked": True})
    if not h.wait_until(lambda: tap.event("complete", "mark"), timeout=90):
        return problems + ["[erut] 마킹 완료(evt/complete)가 오지 않았습니다"]
    marked = tap.event("complete", "mark")
    log(f"  마킹 완료: marked {marked.get('marked')} failed {marked.get('failed')}")
    if marked.get("marked") != ["d1"]:
        problems.append(f"[erut] 마킹 결과가 다릅니다: {marked}")

    log("[erut] home — 홈에 닿으면 evt/complete(action=home)")
    h.pump(2.0)
    session.handle_request("home", {"req_id": "home-1"})
    if not h.wait_until(lambda: tap.event("complete", "home"), timeout=60):
        problems.append("[erut] 홈 도착(evt/complete action=home)이 오지 않았습니다")
    else:
        log(f"  홈 도착: code {tap.event('complete', 'home')['code']}")

    log("[erut] 비상정지 → reset")
    session.raise_error({"code": "E1002", "message": "E_STOP", "level": "estop",
                         "recovery": "reset_required"})
    h.pump(0.5)
    session.handle_request("reset", {"req_id": "reset-1"})
    h.pump(0.5)
    cleared = [e for e in tap.errors if e["code"] == "E1002" and e["cleared"]]
    log(f"  해제 통보 {len(cleared)}회 · activity {session.activity_state()}")
    if not cleared or session.activity_state() != "idle":
        problems.append("[erut] 비상정지 해제(cleared=true)·idle 복귀가 안 됐습니다")

    refused = [r for r in tap.responses if r["code"] >= 300]
    if refused:
        problems.append(f"[erut] 거절된 요청이 있습니다: {refused}")
    return problems


def scenario_io(h: Harness) -> list[str]:
    """로봇 디지털 출력: 화면 버튼 → ROS → 레지스터 2 의 비트."""
    screen = h.window.screens["io"]
    log("\n[io] DO1 ON → OFF")
    problems = []
    screen._request_output("DO1", True)
    if not h.wait_until(lambda: h.register(DIGITAL_OUT_REG)[0] & 0b10, timeout=10):
        problems.append("[io] DO1 을 켰는데 레지스터 2 의 비트가 서지 않았습니다")
    screen._request_output("DO1", False)
    if not h.wait_until(lambda: not (h.register(DIGITAL_OUT_REG)[0] & 0b10), timeout=10):
        problems.append("[io] DO1 을 껐는데 비트가 내려가지 않았습니다")
    # 화면 표시도 로봇 값을 따라야 한다.
    h.pump(0.5)
    if screen.value_items["DO1"].text() != "OFF":
        problems.append(f"[io] 화면 표시가 다릅니다: {screen.value_items['DO1'].text()}")
    log(f"  레지스터 2 = {h.register(DIGITAL_OUT_REG)[0]}")
    return problems


def scenario_tpac(h: Harness) -> list[str]:
    """TPAC 브리지가 스캔 구간 신호(DO[0..2])를 실제로 내보내는지."""
    from smr_operator_ui.services.tpac_bridge.scan_signals import (
        SEG_BACKWARD, SEG_FORWARD, ScanSignalOutput,
    )

    log("\n[tpac] 스캔 구간 신호 추적")
    seen: list[list[int]] = []
    output = ScanSignalOutput(lambda outputs: seen.append(outputs.bits), latch_ms=50)
    output.start()
    problems = []
    try:
        # 로봇(시뮬레이터)이 도는 동안 상태·구간을 그대로 흘려 넣는다.
        deadline = time.time() + 120
        last = None
        while time.time() < deadline:
            h.pump(0.05)
            state, segment = h.register(STATE_REG)[0], h.register(SEGMENT_REG)[0]
            output.observe(state, segment)
            if (state, segment) != last:
                last = (state, segment)
            if state == STATE_DONE:
                break
        h.pump(0.5)
    finally:
        output.stop()
    forward = [bits for bits in seen if bits == [1, 1, 0]]
    backward = [bits for bits in seen if bits == [0, 1, 0]]
    log(f"  전진 스캔 {len(forward)}회 · 후진 스캔 {len(backward)}회 · 총 {len(seen)}단계")
    if not forward:
        problems.append("[tpac] 전진 스캔 신호(1,1,0)가 한 번도 나오지 않았습니다")
    if any(bits[1] and bits[2] for bits in seen):
        problems.append("[tpac] 스캔 중에 리셋이 같이 서 있었습니다")
    return problems


SCENARIOS = ("mc", "erut", "io", "tpac")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("columns", nargs="?", type=int, default=2)
    parser.add_argument("rows", nargs="?", type=int, default=3)
    parser.add_argument("--only", nargs="+", choices=SCENARIOS, default=list(SCENARIOS))
    args = parser.parse_args()

    from PyQt6.QtWidgets import QApplication
    from pyModbusTCP.client import ModbusClient

    from smr_operator_ui.app import OperatorWindow
    from smr_operator_ui.services import MqttServer

    procs = Processes()
    procs.start()

    app = QApplication([])
    mqtt = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt, start_mqtt=False, start_ros=True,
                            start_erut=False)
    modbus = ModbusClient(host="127.0.0.1", port=MODBUS_PORT, auto_open=True)

    # 브로커 없이 돌리므로 발행 내용을 가로채 기록만 한다.
    published: list[tuple[str, dict]] = []

    harness = Harness(window, app, modbus, published)

    def capture(topic, payload, **kwargs):
        published.append((topic, payload))
        harness.on_publish(topic, payload)
        return True

    mqtt.publish = capture

    log("로봇 연결 대기...")
    harness.pump(3)

    problems: list[str] = []
    try:
        if "mc" in args.only:
            problems += scenario_mc(harness, args.columns, args.rows)
        if "tpac" in args.only:
            # 스캔이 도는 동안을 봐야 하므로 erut 구간과 겹쳐 돌린다.
            pass
        if "erut" in args.only:
            problems += scenario_erut(harness)
        if "io" in args.only:
            problems += scenario_io(harness)
        if "tpac" in args.only:
            problems += scenario_tpac_run(harness)
    finally:
        window.close()
        procs.stop()

    log("\n" + "=" * 52)
    if problems:
        log("실패:")
        for problem in problems:
            log(f"  - {problem}")
        log("=" * 52)
        return 1
    log(f"통과: {', '.join(args.only)}")
    log("=" * 52)
    return 0


def scenario_tpac_run(h: Harness) -> list[str]:
    """TPAC 신호는 로봇이 도는 동안 봐야 한다 — 셀 하나를 다시 돌린다."""
    window = h.window
    # 앞 시나리오에서 남은 스캔 허가(267)가 있으면 로봇이 원점을 그냥 지나친다.
    window.ros_status.send_value("scan_go", 0)
    h.pump(0.5)
    # 로봇은 차량 고정 확인(309)이 없으면 움직이지 않는다 — 먼저 고정한다.
    if not window._vehicle_secured():
        window.outrigger.move_to(1, " 고정")
        if not h.wait_until(window._vehicle_secured, timeout=30):
            return ["[tpac] 차량(더미) 고정이 끝나지 않았습니다"]
        h.pump(1.5)                      # 309 를 로봇에 밀어 넣을 틈을 준다
    window._start_robot_scan()
    # 원점 대기를 풀어 줘야 스캔 구간으로 넘어간다.
    if not h.wait_until(lambda: h.register(STATE_REG)[0] == STATE_AT_ORIGIN, timeout=60,
                        poll=0.05):
        return [f"[tpac] 로봇이 원점 대기(290 = 7)까지 가지 않았습니다 — "
                f"290={h.register(290)[0]} 299={h.register(299)[0]} "
                f"309={h.register(309)[0]} 266={h.register(266)[0]} "
                f"500={h.register(500)[0]}"]
    window.ros_status.send_value("scan_go", 1)
    return scenario_tpac(h)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
