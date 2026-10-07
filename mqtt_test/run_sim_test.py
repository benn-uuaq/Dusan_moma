#!/usr/bin/env python3
"""장비 없이 전체 경로를 한 번에 확인한다.

로봇 시뮬레이터 → ROS 노드 → 운영 UI → ERUT MQTT/TPAC 까지 실제 코드를 그대로
띄우고, 시나리오를 차례로 돌린다. 실물이 없는 동안 회귀를 잡아내는 그물이다.

  rcs    RCS 단독 작업('검사 시작'): 1A 부터 마지막 셀까지 순회한다.
         원점마다 RCS 가 접촉 2초 뒤 스스로 스캔을 풀어 준다.
  erut   ERUT 표준(if-0.5): 자기소개 → calibrate(차량 한 바퀴) → prepare(차량만)
         → start(3점 → contact → 스캔, 줄 중간 pause/resume) → mark(mark_ready ↔ mark_next) → home
         → 비상정지 → reset.
  io     로봇 디지털 출력(레지스터 2)을 화면 경로로 켜고 끈다.
  tpac   TPAC 브리지가 스캔 구간 신호(DO[0..2])를 내보내는지 본다.

실행:
    python3 mqtt_test/run_sim_test.py                 # 전부 (2열 × 3행)
    python3 mqtt_test/run_sim_test.py --only rcs erut
    python3 mqtt_test/run_sim_test.py --columns 3 --rows 2

ROS 2 워크스페이스를 소싱한 셸에서 실행해야 한다:
    source /opt/ros/humble/setup.bash && source install/setup.bash
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

WS = Path(__file__).resolve().parent.parent
SIMULATOR = WS / "src/elite_robot_controller/tools/elite_robot_simulator.py"
MODBUS_PORT = 5502

sys.path.insert(0, str(WS / "operator-ui/src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 운영 설정·기록과 섞이지 않게 한다. 그대로 두면 시험이 운영자의 설정 파일
# (~/.config/smr-operator-ui/settings.json)을 덮어쓰고, 실제 데이터 폴더
# (D:/SMR/Data)에 가짜 작업기록·알람·스캔좌표를 쌓는다.
_SCRATCH = Path(tempfile.mkdtemp(prefix="smr_sim_test_"))
os.environ["SMR_SETTINGS_FILE"] = str(_SCRATCH / "settings.json")
os.environ["SMR_DATA_DIR"] = str(_SCRATCH / "data")
os.environ.pop("SMR_DATABASE_URL", None)
# ROS 도 따로 쓴다. 운영 RCS·노드(보통 도메인 0)와 같은 도메인이면 시험 UI 의
# 명령을 실제 로봇 노드가 받는다. 노드도 이 환경을 물려받는다.
os.environ["ROS_DOMAIN_ID"] = os.environ.get("SIM_TEST_ROS_DOMAIN", "77")

# 로봇 태스크가 쓰는 레지스터 (dus_*.script 참고).
STATE_REG, SEGMENT_REG, DIGITAL_OUT_REG = 290, 277, 2
TASK_REG = 500
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

    def __init__(self, window, app, modbus) -> None:
        self.window, self.app, self.modbus = window, app, modbus

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



# ---------------------------------------------------------------- 시나리오
def scenario_rcs(h: Harness, columns: int, rows: int) -> list[str]:
    """RCS 단독 작업: '검사 시작'과 같은 격자 순회로 전체 격자를 돈다.

    ERUT 작업이 아니므로 원점(290 = 7)에서 기다려 줄 쪽이 없다 — RCS 가 접촉
    뒤 CONTACT_LEAD_MS 가 지나면 스스로 scan_go 를 써서 적심 → 스캔으로 넘긴다
    (예전 사내 MC probe_ack 자리. 사내 MC 는 2026-10-07 에 뺐다).
    """
    from smr_operator_ui.services import GridPlan

    window = h.window
    visited: list[str] = []
    window.sequencer.cell_changed.connect(lambda c, r, label, n: visited.append(label))

    # RCS 가 스스로 푼 횟수를 센다(원점 도착마다 한 번).
    releases: list[int] = []
    send_value = window.ros_status.send_value

    def counting_send(name, value):
        if name == "scan_go" and int(value) == 1:
            releases.append(1)
        return send_value(name, value)

    window.ros_status.send_value = counting_send

    total = columns * rows
    log(f"\n[rcs] RCS 격자 순회: {columns}열 × {rows}행 = {total}개 셀")
    window._begin_grid_job(GridPlan(column_count=columns, row_count=rows,
                                    cell_width=600.0, cell_height=800.0,
                                    pitch_x=20.0, pitch_y=20.0), 800.0)

    seen = 0
    deadline = time.time() + 40 + total * 15
    while time.time() < deadline:
        h.pump(0.2)
        if len(visited) > seen:
            seen = len(visited)
            log(f"  진행: {visited[-1]}  [{window.sequencer.state.name}]")
        if window.sequencer.state.name == "DONE":
            break
    h.pump(1.5)
    window.ros_status.send_value = send_value

    work_area = h.register(256, 4)
    expected = [f"{c + 1}{chr(ord('A') + r)}" for c in range(columns) for r in range(rows)]

    log(f"  방문한 셀      : {' → '.join(visited)}")
    log(f"  원점 자동 해제 : {len(releases)}건")
    log(f"  레지스터 256~259: {work_area}")

    problems = []
    if visited != expected:
        problems.append(f"[rcs] 셀 순서가 다릅니다. 기대: {expected}, 실제: {visited}")
    if window.sequencer.state.name != "DONE":
        problems.append(f"[rcs] 전체 완료 상태가 아닙니다: {window.sequencer.state.name}")
    # 258(스캐너 높이)은 현장 설정값이라 고정이 아니다 — 0.1mm 단위로 실려만 가면 된다.
    if work_area[:2] != [600, 800] or work_area[3] != 20 or work_area[2] <= 0:
        problems.append(f"[rcs] 작업 영역 레지스터가 다릅니다: {work_area}")
    if len(releases) != total:
        problems.append(f"[rcs] 원점에서 셀마다 스스로 풀지 않았습니다: {len(releases)}/{total}")
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
        self.home: list[str] = []
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
        erut.publish_home = lambda state: self.home.append(state) or True
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
    if info["interface_version"] != "0.8" or "home" not in info["capabilities"]:
        problems.append(f"[erut] 자기소개가 이상합니다: {info}")

    log("[erut] calibrate — 차량(AGV)이 모재를 따라 한 바퀴 돌며 좌표계를 잡는다")
    state_before = h.register(STATE_REG)[0]
    session.handle_request("calibrate", {"req_id": "cal-1", "diameter": 1690, "height": 6000})
    if not h.wait_until(lambda: tap.event("complete", "calibrate"), timeout=60):
        return problems + ["[erut] 캘리브레이션 완료(evt/complete)가 오지 않았습니다"]
    result = tap.event("complete", "calibrate")
    log(f"  한 바퀴 {result.get('total_length_mm')} mm · 오차 {result.get('calibration_error_mm')} mm")
    if abs((result.get("total_length_mm") or 0) - 5309) > 2:
        problems.append(f"[erut] 캘리브레이션 총 둘레가 이상합니다: {result}")
    if h.register(STATE_REG)[0] != state_before:
        problems.append("[erut] 캘리브레이션 중에 로봇이 움직였습니다(3점 측정은 start 에서)")

    # if-0.5: ERUT 는 이동 명령(prepare) 전에 query 로 home 인지 묻는다.
    # 차량 캘리브레이션은 로봇을 홈에 둔 채 돈다 — evt/home 은 바뀔 때만
    # 나가므로 아무것도 안 나갔거나, 나갔다면 마지막이 home 이어야 한다.
    if tap.home and tap.home[-1] != "home":
        problems.append(f"[erut] 캘리브레이션 뒤 로봇이 홈이 아닙니다: {tap.home}")
    session.handle_request("query", {"req_id": "q-home-1"})
    home_answer = tap.responses[-1].get("home")
    log(f"  캘리브레이션 뒤 evt/home: {tap.home[-1] if tap.home else None} · query home={home_answer}")
    if home_answer != "home":
        problems.append(f"[erut] prepare 전 query 의 home 이 {home_answer} 입니다")

    log("[erut] prepare — 차량·리프트만 구간 자리로(preparing) → ready. 로봇은 홈")
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
    if tap.home and tap.home[-1] != "home":
        problems.append(f"[erut] 준비 단계에서 로봇이 홈을 벗어났습니다: {tap.home}")

    log("[erut] start — 로봇 3점 측정 → 시작점에 붙음(contact) → 적심 → 스캔")
    session.handle_request("start", {"req_id": "start-1", "job_id": "jb-erut",
                                     "surface": "outer", "area": area,
                                     "scan": {"pitch": 20, "speed": 100}})

    # 줄 한가운데서 일시정지 → 재개: 태스크를 끝내지 않고 그 자리에 세웠다가
    # (pause, 500 = 2) 그 자리에서 잇는다(play). 3점 측정부터 다시 하면 안 된다.
    if not h.wait_until(lambda: h.register(STATE_REG)[0] == 6
                        and h.register(SEGMENT_REG)[0] in (1, 3), timeout=120, poll=0.02):
        return problems + ["[erut] 스캔 중인 순간을 못 잡았습니다"]
    session.handle_request("pause", {"req_id": "pz-scan"})
    # if-0.6: paused 는 장비가 실제로 선 뒤에만 — 받자마자는 아직 running.
    right_after = tap.status[-1].get("activity") if tap.status else None
    if right_after == "paused":
        problems.append("[erut] 로봇이 서기도 전에 paused 를 알렸습니다(if-0.6)")
    if not h.wait_until(lambda: h.register(TASK_REG)[0] == 2, timeout=10):
        problems.append(f"[erut] 일시정지가 로봇 pause 로 가지 않았습니다 — 500="
                        f"{h.register(TASK_REG)[0]}")
    if not h.wait_until(lambda: tap.status and tap.status[-1].get("activity") == "paused",
                        timeout=10):
        problems.append("[erut] 로봇이 선 뒤에도 activity 가 paused 가 되지 않았습니다")
    session.handle_request("query", {"req_id": "q-paused"})
    log(f"  pause 직후 activity {right_after} → 선 뒤 paused · resumable="
        f"{tap.responses[-1].get('resumable')}")
    if tap.responses[-1].get("resumable") is not True:
        problems.append(f"[erut] 제자리에 선 일시정지인데 resumable 이 아닙니다: {tap.responses[-1]}")
    frozen = h.register(STATE_REG, 2)
    h.pump(1.5)
    still = h.register(STATE_REG, 2)
    log(f"  줄 한가운데서 일시정지 — 290/291 {frozen} → 1.5초 뒤 {still} · 500=2")
    if still != frozen:
        problems.append(f"[erut] 일시정지 중에 로봇이 계속 진행했습니다: {frozen} → {still}")
    session.handle_request("resume", {"req_id": "rs-scan"})
    states_after: list[int] = []

    def resumed_and_done() -> bool:
        states_after.append(h.register(STATE_REG)[0])
        return bool(tap.event("complete", "start"))

    if not h.wait_until(resumed_and_done, timeout=180, poll=0.02):
        return problems + ["[erut] 구간 완료(evt/complete)가 오지 않았습니다"]
    if any(state in (2, 3, 7) for state in states_after):
        problems.append("[erut] 재개가 3점 측정부터 다시 시작했습니다 — 그 자리에서 이어야 합니다")
    else:
        log("  재개 — 3점 측정 없이 멈춘 자리에서 이어 끝까지 훑었다")
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
    if "deployed" not in tap.home:
        problems.append(f"[erut] 검사 중 evt/home 이 deployed 가 된 적이 없습니다: {tap.home}")
    if not h.wait_until(lambda: tap.home and tap.home[-1] == "home", timeout=30):
        problems.append(f"[erut] 구간을 마친 뒤 홈 자세로 거두지 않았습니다: {tap.home}")
    log(f"  evt/home 흐름: {' → '.join(tap.home)}")
    if (done.get("location") or {}).get("cell") != "2A":
        problems.append(f"[erut] 완료의 구역 이름이 2A 가 아닙니다: {done.get('location')}")

    # if-0.6 탭4 D-1 3b: 멈춘 채 abort 를 받으면 작업을 버리고 idle → home.
    log("[erut] 멈춘 채 abort — 작업을 버리고 idle 이 된 뒤 home 으로 거둔다")
    session.handle_request("start", {"req_id": "start-2", "job_id": "jb-erut-2",
                                     "surface": "outer",
                                     "area": {"start": {"x": 1160, "y": 0},
                                              "end": {"x": 1760, "y": 800}},
                                     "scan": {"pitch": 20, "speed": 100}})
    if not h.wait_until(lambda: h.register(STATE_REG)[0] == 6
                        and h.register(SEGMENT_REG)[0] in (1, 3), timeout=180, poll=0.02):
        return problems + ["[erut] 두 번째 구간이 스캔에 들어가지 않았습니다"]
    session.handle_request("pause", {"req_id": "pz-2"})
    if not h.wait_until(lambda: tap.status and tap.status[-1].get("activity") == "paused",
                        timeout=10):
        problems.append("[erut] 두 번째 구간 일시정지가 paused 가 되지 않았습니다")
    session.handle_request("abort", {"req_id": "ab-2", "job_id": "jb-erut-2"})
    if not h.wait_until(lambda: tap.status and tap.status[-1].get("activity") == "idle",
                        timeout=10):
        problems.append("[erut] 멈춘 채 abort 뒤 idle 이 되지 않았습니다")
    if not h.wait_until(lambda: tap.home and tap.home[-1] == "home", timeout=60):
        problems.append(f"[erut] 멈춘 채 abort 뒤 홈으로 거두지 않았습니다: {tap.home}")
    session.handle_request("query", {"req_id": "q-after-abort"})
    answer = tap.responses[-1]
    log(f"  abort 뒤 query: activity {answer.get('activity')} · home {answer.get('home')}"
        f" · resumable {answer.get('resumable')}")
    if (answer.get("activity"), answer.get("home"), answer.get("resumable")) != ("idle", "home", False):
        problems.append(f"[erut] abort 뒤 query 가 idle·home 이 아닙니다: {answer}")

    # ERUT Q-19(10/6 16:29): start 직후(팔이 홈에서 아직 안 나갔을 때) pause → abort 하면
    # 이미 홈인 팔에 홈을 보낸다. 그래도 evt/home 이 home 으로 돌아와야 한다.
    log("[erut] start 직후 pause → abort — 홈에 있던 팔도 home 으로 돌아온다")
    session.handle_request("start", {"req_id": "start-q19", "job_id": "jb-q19",
                                     "surface": "outer",
                                     "area": {"start": {"x": 1160, "y": 0},
                                              "end": {"x": 1760, "y": 800}},
                                     "scan": {"pitch": 20, "speed": 100}})
    h.wait_until(lambda: window.sequencer.state.name == "SCANNING", timeout=60, poll=0.02)
    session.handle_request("pause", {"req_id": "pz-q19"})
    h.pump(0.5)
    session.handle_request("abort", {"req_id": "ab-q19"})
    if not h.wait_until(lambda: tap.home and tap.home[-1] == "home"
                        and session.home_state() == "home", timeout=30):
        problems.append(f"[erut] 홈에 있던 팔이 abort 뒤 deployed 로 굳었습니다: {tap.home[-4:]}")
    else:
        log("  abort 뒤 evt/home → home")

    log("[erut] mark — 점에 붙으면 mark_ready, ERUT 가 찍고 mark_next")
    h.pump(1.0)
    session.handle_request("mark", {"req_id": "mark-1", "method": "paint", "marker": "erut",
                                    "points": [{"id": "d1", "x": 900, "y": 300}]})
    if not h.wait_until(lambda: tap.event("mark_ready", "mark"), timeout=90):
        return problems + ["[erut] 마킹 자리 도착(evt/mark_ready)이 오지 않았습니다"]
    ready = tap.event("mark_ready", "mark")
    log(f"  mark_ready: 점 {ready.get('point_id')} — 로봇이 자리에서 기다린다")
    h.pump(1.0)
    if h.register(STATE_REG)[0] != 11:
        problems.append(f"[erut] 마킹 자리에서 로봇이 서 있지 않습니다(290={h.register(STATE_REG)[0]})")
    # if-0.7 마킹 ② ⑥: 기다리는 중 pause 면 그 자리에, resume 이면 mark_ready 를 다시.
    readies = sum(1 for n, _e in tap.events if n == "mark_ready")
    session.handle_request("pause", {"req_id": "pz-mark"})
    if not h.wait_until(lambda: tap.status and tap.status[-1].get("activity") == "paused",
                        timeout=10):
        problems.append("[erut] 마킹 점에서 일시정지가 paused 가 되지 않았습니다")
    if h.register(STATE_REG)[0] != 11:
        problems.append("[erut] 마킹 일시정지 중에 로봇이 자리를 떠났습니다")
    session.handle_request("resume", {"req_id": "rs-mark"})
    if not h.wait_until(lambda: sum(1 for n, _e in tap.events if n == "mark_ready") > readies,
                        timeout=10):
        problems.append("[erut] 마킹 재개 뒤 mark_ready 를 다시 내지 않았습니다")
    else:
        log("  마킹 점에서 pause → paused · resume → mark_ready 다시")
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

    # 스캔 중 비상정지 → 구간은 끝난 것(if-0.4) · 팔은 벽 앞에 펴진 채 →
    # 초기화(reset)하면 장애가 풀리고 RCS 가 스스로 팔을 홈으로 거둔다.
    log("[erut] 스캔 중 비상정지 → reset — 풀리면 펴진 팔을 스스로 홈으로")
    session.handle_request("start", {"req_id": "start-3", "job_id": "jb-erut-3",
                                     "surface": "outer",
                                     "area": {"start": {"x": 1740, "y": 0},
                                              "end": {"x": 2340, "y": 800}},
                                     "scan": {"pitch": 20, "speed": 100}})
    if not h.wait_until(lambda: h.register(STATE_REG)[0] == 6, timeout=180, poll=0.02):
        return problems + ["[erut] 세 번째 구간이 스캔에 들어가지 않았습니다"]
    session.raise_error({"code": "E1002", "message": "E_STOP", "level": "estop",
                         "recovery": "reset_required"})
    h.wait_until(lambda: h.register(TASK_REG)[0] == 3, timeout=10)
    h.pump(0.5)
    if tap.home and tap.home[-1] != "deployed":
        problems.append(f"[erut] 비상정지 뒤 팔이 펴진 채가 아닙니다(시험 전제): {tap.home}")
    session.handle_request("reset", {"req_id": "reset-1"})
    h.pump(0.5)
    cleared = [e for e in tap.errors if e["code"] == "E1002" and e["cleared"]]
    log(f"  해제 통보 {len(cleared)}회 · activity {session.activity_state()}")
    if not cleared or session.activity_state() != "idle":
        problems.append("[erut] 비상정지 해제(cleared=true)·idle 복귀가 안 됐습니다")
    if not h.wait_until(lambda: tap.home and tap.home[-1] == "home", timeout=60):
        problems.append(f"[erut] 초기화 뒤 팔을 홈으로 거두지 않았습니다: {tap.home}")
    else:
        log("  초기화 뒤 evt/home → home (스스로 거둠)")

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
    from smr_operator_ui.services.tpac_bridge.scan_signals import ScanSignalOutput

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


SCENARIOS = ("rcs", "erut", "io", "tpac")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("columns", nargs="?", type=int, default=2)
    parser.add_argument("rows", nargs="?", type=int, default=3)
    parser.add_argument("--only", nargs="+", choices=SCENARIOS, default=list(SCENARIOS))
    args = parser.parse_args()

    from PyQt6.QtWidgets import QApplication
    from pyModbusTCP.client import ModbusClient

    from smr_operator_ui.app import OperatorWindow
    from smr_operator_ui.services import RobotNodeSupervisor

    # 노드는 위에서 시뮬레이터 주소로 띄웠다. UI 가 기본값(실제 로봇 주소일 수
    # 있다)으로 노드를 하나 더 띄우지 못하게 막는다 — 노드 탐색이 늦으면
    # "아직 없다"로 보고 띄워 버린다.
    RobotNodeSupervisor.start = lambda self, already_running=False: False

    procs = Processes()
    procs.start()

    app = QApplication([])
    window = OperatorWindow(start_ros=True, start_erut=False)
    modbus = ModbusClient(host="127.0.0.1", port=MODBUS_PORT, auto_open=True)

    # ERUT 로 나가는 것은 시나리오마다 ErutTap 이 가로챈다(브로커 없이).
    harness = Harness(window, app, modbus)
    if os.environ.get("SIM_TEST_VERBOSE"):
        # RCS 진행 알림을 그대로 찍는다 — 시나리오가 어디서 멈췄는지 볼 때.
        window.main_screen.activity_shown.connect(lambda text: log(f"    · {text}"))

    log("로봇 연결 대기...")
    harness.pump(3)

    problems: list[str] = []
    try:
        if "rcs" in args.only:
            problems += scenario_rcs(harness, args.columns, args.rows)
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
