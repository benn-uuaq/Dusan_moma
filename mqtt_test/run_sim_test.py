#!/usr/bin/env python3
"""격자 순회 전체 파이프라인을 장비 없이 한 번에 검증한다.

로봇 시뮬레이터 → ROS 노드 → 운영 UI → MQTT까지 실제 코드를 그대로 띄우고,
`job_cmd`를 넣은 뒤 `1A`부터 마지막 셀까지 자동으로 도는지 확인한다.
로봇의 스캔 완료 신호는 시뮬레이터의 Modbus 레지스터를 직접 써서 흉내 낸다.

실행:
    python3 mqtt_test/run_sim_test.py            # 2열 × 3행
    python3 mqtt_test/run_sim_test.py 3 2        # 3열 × 2행

ROS 2 워크스페이스를 소싱한 셸에서 실행해야 한다:
    source /opt/ros/humble/setup.bash && source install/setup.bash
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

WS = Path(__file__).resolve().parent.parent
SIMULATOR = WS / "src/elite_robot_controller/tools/elite_robot_simulator.py"
MODBUS_PORT = 5502

sys.path.insert(0, str(WS / "operator-ui/src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 로봇 태스크가 레지스터 290/295에 쓰는 값 (dus_*.script 참고).
STATE_REG, FINISHED_REG = 290, 295
SCANNING, DONE = 4, 5


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

    def _spawn(self, args: list[str]) -> None:
        self.procs.append(subprocess.Popen(
            args, cwd=str(WS),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        ))

    def start(self) -> None:
        log("로봇 시뮬레이터 기동...")
        self._spawn([sys.executable, str(SIMULATOR)])
        time.sleep(2)

        log("ROS 제어 노드 기동...")
        self._spawn(["ros2", "run", "elite_robot_controller", "robot_control_node",
                     "--ros-args", "-p", "robot_ip:=127.0.0.1",
                     "-p", f"modbus_port:={MODBUS_PORT}"])
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


def main() -> int:
    columns = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    rows = int(sys.argv[2]) if len(sys.argv) > 2 else 3

    from PyQt6.QtWidgets import QApplication
    from pyModbusTCP.client import ModbusClient

    from smr_operator_ui.app import OperatorWindow
    from smr_operator_ui.services import MqttServer, MqttTopics

    procs = Processes()
    procs.start()

    app = QApplication([])
    mqtt = MqttServer()
    window = OperatorWindow(mqtt_server=mqtt, start_mqtt=False, start_ros=True)
    modbus = ModbusClient(host="127.0.0.1", port=MODBUS_PORT, auto_open=True)

    # 브로커 없이 돌리므로 발행 내용을 가로채 기록만 한다.
    published: list[tuple[str, dict]] = []
    mqtt.publish = lambda topic, payload, **kw: published.append((topic, payload)) or True

    visited: list[str] = []
    window.sequencer.cell_changed.connect(lambda c, r, label, n: visited.append(label))

    def pump(seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.02)

    log("로봇 연결 대기...")
    pump(3)

    total = columns * rows
    log(f"\n작업 계획 발행: {columns}열 × {rows}행 = {total}개 셀\n")
    mqtt.command_received.emit(MqttTopics.JOB_COMMAND, {
        "timestamp": "1", "job_id": "jb-sim",
        "job_info": {"diameter": "2500", "height": "6000",
                     "target_distance": "8560"},
        "plan": {"column_count": str(columns), "row_count": str(rows),
                 "cell_width": "600", "cell_height": "800", "overlap": "20"},
    })

    # 시뮬레이터가 play를 받으면 스스로 스캔 태스크를 돌며 레지스터를
    # 채우므로, 여기서는 셀이 넘어가는 것을 지켜보기만 한다.
    seen = 0
    deadline = time.time() + 30 + total * 10
    while time.time() < deadline:
        pump(0.2)
        if len(visited) > seen:
            seen = len(visited)
            log(f"  진행: {visited[-1]}  [{window.sequencer.state.name}]")
        if window.sequencer.state.name == "DONE":
            break
    pump(1.5)

    states = [p for t, p in published if t.endswith("job_state")]
    tcps = [p for t, p in published if t.endswith("/tcp")]
    work_area = modbus.read_holding_registers(256, 4)

    log("\n" + "=" * 52)
    log(f"방문한 셀      : {' → '.join(visited)}")
    log(f"시퀀서 상태    : {window.sequencer.state.name}")
    log(f"job_state 발행 : {len(states)}건 (셀당 3건 = {total * 3} 기대)")
    log(f"tcp 발행       : {len(tcps)}건 (격자 이름 부착)")
    log(f"레지스터 256~259: {work_area}  (셀 가로/세로/스캐너/겹침)")
    log("=" * 52)

    expected = [f"{c + 1}{chr(ord('A') + r)}" for c in range(columns) for r in range(rows)]
    problems = []
    if visited != expected:
        problems.append(f"셀 순서가 다릅니다. 기대: {expected}")
    if window.sequencer.state.name != "DONE":
        problems.append(f"전체 완료 상태가 아닙니다: {window.sequencer.state.name}")
    if len(states) != total * 3:
        problems.append(f"job_state 발행 수가 다릅니다: {len(states)}")
    if work_area != [600, 800, 150, 20]:
        problems.append(f"작업 영역 레지스터가 다릅니다: {work_area}")

    window.close()
    procs.stop()

    if problems:
        log("\n실패:")
        for problem in problems:
            log(f"  - {problem}")
        return 1
    log("\n통과: 전체 격자를 순서대로 스캔했습니다.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
