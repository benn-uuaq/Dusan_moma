"""RCS <-> AGV(차량 제어 노드) 인터페이스 — 두 장짜리 요약을 만든다.

  1.노드          RCS → AGV · AGV → RCS 로 주고받는 노드(토픽 · 서비스) 이름과 칸
  2.작업별 순서    캘리브레이션 · 스캔 중 구간 이동 · 수동 작업에서 주고받는 순서

자세한 근거(요구 출처 · 정할 것 · 원주 거리 환산)는 docs/3S-AGV_PLC_연계_인터페이스_20261106.xlsx.
값을 고칠 때는 코드(src/vehicle_interfaces · operator-ui services/vehicle_*)를 먼저 고치고 다시 만든다.
실행:  python3 tools/build_rcs_agv_interface_xlsx.py
"""

import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import PatternFill

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_modbus_interface_xlsx import NOTE_FONT, rows, sheet  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "RCS_AGV_인터페이스_20261117.xlsx"

#: 정의(vehicle_interfaces)와 RCS 는 됐고 AGV 노드가 채워야 하는 것(2026-11-17)
ADD = PatternFill("solid", fgColor="FFFFF2CC")
NEW = "AGV 구현"


def add(*values):
    return ("fill", ADD, *values)


# ================================================================= 1. 노드
NODES = [
    ("section", "RCS → AGV — 서비스 (RCS 가 부르고 AGV 가 답한다)"),
    ("RCS → AGV", "<ns>/set_job", "SetJob.srv", "job_id", "string", "이동마다 새 번호(RCS-0001 …)", ""),
    ("", "", "자동 주행 한 번", "set_dist", "float64 · m",
     "이번 이동에서 차량 중심이 달릴 거리. 부호 = 방향(+ 원주 진행). RCS 가 모재 표면 거리 × 주행 배율로 환산", ""),
    ("", "", "", "set_speed", "float64 · m/s", "RCS 차량 설정 「차량 주행 속도」(기본 0.1)", ""),
    ("", "", "", "standoff", "float64 · m", "지킬 거리 — 차량 안쪽 면 ↔ 모재 표면. RCS 차량 설정 「모재 거리 유지」", ""),
    ("", "", "", "(응답) success · message", "bool · string", "false 면 이동 실패", ""),
    ("RCS → AGV", "<ns>/robot_control", "RobotControl.srv", "state", "string",
     "RUNNING = 출발 · 재개, STOP = 정지 · 일시정지, PAUSED = 캘리브레이션 주행(measure_lap) 일시정지 "
     "(그 밖의 일시정지는 STOP 후 같은 목표로 다시)", ""),
    ("", "", "출발 · 정지 · 리셋", "reset", "uint8", "1 = 오류 리셋", ""),
    ("", "", "", "job_cancel", "uint8", "1 = 작업 취소(예약 — 지금은 STOP 으로 대신)", ""),
    ("", "", "", "(응답) success · message", "bool · string", "false 면 실패(알람)", ""),
    ("RCS → AGV", "<ns>/manual_command", "ManualCommand.srv", "cmd_mv_fwd · cmd_mv_rear", "bool",
     "true 동안 전진 · 후진", ""),
    ("", "", "수동 · 아웃트리거 · 리프트", "cmd_trn_left · cmd_trn_right", "bool", "true 동안 좌 · 우회전", ""),
    ("", "", "", "cmd_outrg_set", "uint8", "아웃트리거 전체 — 1 해제 · 2 고정 · 0 없음", ""),
    ("", "", "", "cmd_man_outrg1 · 2 · 3", "uint8", "아웃트리거 개별 — 1 상승 · 2 하강 · 0 없음", ""),
    ("", "", "", "cmd_mv_lift + lift_height", "uint8 + float64 · m",
     "1 = 리프트를 lift_height(절대 높이)로. 아웃트리거 고정에서만", ""),
    ("", "", "", "cmd_init", "uint8", "1 = 원점 / 초기화", ""),
    ("", "", "", "(응답) success · message", "bool · string", "거절이면 false", ""),
    add("RCS → AGV", "<ns>/measure_lap", "MeasureLap.srv", "job_id · speed · standoff", "string · m/s · m",
        "캘리브레이션 주행 — 결과는 robot_status 의 lap_*. RCS 는 이 서비스가 있으면 쓰고, 없으면 원통만 "
        "set_job 으로 한 바퀴 거리만큼 달린다(곡판은 실패)", NEW),
    add("", "", "캘리브레이션 주행", "mode (2026-11-18)", "string",
        "LAP = 원통: 원점(QR)에서 출발해 모재를 한 바퀴 돌고 원점에 선다. "
        "ALIGN = 곡판: 한 바퀴 돌지 않는다 — 판 왼쪽 끝의 원점 QR 마커에 맞춰 정렬(거리 standoff · 방향)만 하고 선다. "
        "빈 글자 = LAP. RCS 가 모재 모양(ERUT geometry.shape · RCS 검사 대상)으로 고른다", NEW),
    add("", "", "", "expected_dist", "float64 · m",
        "LAP: RCS 가 계산한 한 바퀴 거리 = 2π(모재 반지름 + 거리 유지 + 차량 폭/2) — 안전 한도 · 참고용. ALIGN: 0", NEW),
    add("", "", "", "(응답) success · message", "bool · string", "받아들이면 바로 출발. 모르는 mode 는 거절", NEW),
    ("section", "AGV → RCS — 토픽 (AGV 가 1 Hz 이상 계속 낸다 · RCS 는 2 초 넘게 안 오면 끊김)"),
    ("AGV → RCS", "<ns>/robot_status", "RobotStatus.msg", "state", "string",
     "STOP · RUNNING · PAUSED · HOLD(아웃트리거로 고정돼 섬) · ERROR", ""),
    ("", "", "차량 상태", "hold", "string", "아웃트리거 SET(고정) · RELEASE(해제)", ""),
    ("", "", "", "error_code · error_msg", "int32 · string", "0 정상, 그 외 오류 번호 · 설명", ""),
    ("", "", "", "job_id", "string", "지금 하는 SetJob 의 job_id", ""),
    ("", "", "", "set_dist · mv_dist", "float64 · m", "목표 거리 · 이번 이동에서 달린 거리(출발 때 0)", ""),
    ("", "", "", "speed", "float64 · m/s", "지금 속도", ""),
    ("", "", "", "sen1_dist · sen2_dist", "float64 · m", "거리 센서 1 · 2", ""),
    ("", "", "", "position", "Position6D · m / rad", "x · y · z · roll · pitch · yaw", ""),
    ("", "", "", "lift_h", "float64 · m", "리프트 높이", ""),
    ("section", "AGV → RCS — robot_status 에 더한 칸 (정의 · RCS 완료, AGV 가 채운다). 못 채우면 기본값 그대로 — "
                "RCS 는 기본값을 「모름」으로 보고 조건에서 뺀다"),
    add("AGV → RCS", "<ns>/robot_status", "RobotStatus.msg", "battery_pct (기본 255)", "uint8 · %",
        "배터리 잔량 0~100. 255 = 모름 · 배터리 없음 → RCS 가 ERUT 상태 · 조회 · 완료와 상단 배터리에 싣는다", NEW),
    add("", "", "추가 칸", "charging (기본 false)", "bool", "충전 중", NEW),
    add("", "", "", "estop (기본 false)", "bool",
        "비상정지 눌림 → RCS 가 로봇 · 차량 · 작업을 세우고 ERUT E1002. 풀리면 해제", NEW),
    add("", "", "", "obstacle_stop (기본 false)", "bool", "접근 감지(안전 스캐너)로 섰다 → RCS 가 작업을 일시정지", NEW),
    add("", "", "", "level_state (기본 -1)", "int8",
        "아웃트리거 수평 1 맞음 · 0 안 맞음(로봇 검사 막음) · -1 모름", NEW),
    add("", "", "", "standoff_meas (기본 -1)", "float64 · m", "지금 잰 거리 — 차량 안쪽 면 ↔ 모재 표면. 모르면 -1", NEW),
    add("", "", "", "lap_state (기본 IDLE) · lap_path_m · lap_origin_ok · lap_error", "string · float64 m · bool · string",
        "measure_lap 진행 — IDLE · RUNNING · DONE · FAILED, 달린 거리(차량 중심, ALIGN 은 정렬하며 움직인 거리), "
        "lap_origin_ok(LAP: 원점 QR 을 다시 찾아 섰다 · ALIGN: QR 에 맞춰 섰다), 실패 까닭", NEW),
    ("section", "AGV → RCS — 버튼"),
    add("AGV → RCS", "<ns>/start_request", "std_msgs/Empty", "-", "-",
        "차량 물리 「작업 시작」 버튼이 눌렸다 → RCS 가 「계측 시작」(캘리브레이션 — 원통 한 바퀴 · 곡판 QR 정렬) "
        "순서를 돈다. 차량은 스스로 돌지 않는다", NEW),
]

# ================================================================= 2. 작업별 순서
SEQUENCES = [
    ("section", "A. 캘리브레이션(원통) — ERUT 캘리브레이션 · RCS 「계측 시작」 · 차량 「작업 시작」 버튼"),
    add("A", "0", "AGV → RCS", "start_request", "차량 버튼이 눌렸다(버튼으로 시작할 때만)", "-"),
    ("A", "1", "RCS 안", "-", "로봇이 홈이고 태스크가 안 도는지 본다 — 아니면 로봇을 홈으로 보내고 기다린다", "로봇 홈"),
    ("A", "2", "RCS → AGV", "manual_command", "cmd_mv_lift = 1, lift_height = 0 (리프트가 올라가 있을 때만)",
     "robot_status.lift_h ≈ 0 (±2 mm)"),
    ("A", "3", "RCS → AGV", "manual_command", "cmd_outrg_set = 1 해제 (고정돼 있을 때만)",
     "robot_status.hold = RELEASE"),
    ("A", "4", "RCS → AGV", "set_job", "(measure_lap 이 없을 때) set_dist = π × 모재 지름 × 주행 배율, set_speed, standoff",
     "success = true"),
    ("A", "5", "RCS → AGV", "robot_control", "state = RUNNING", "state RUNNING · mv_dist 증가"),
    ("A", "6", "AGV → RCS", "robot_status", "한 바퀴 돌고 선다",
     "STOP/HOLD 이고 |mv_dist − set_dist| ≤ 2 mm → 끝. 제한 = 차량 설정 「캘리브레이션 최대 시간」"),
    add("A", "4~6 (measure_lap 이 있으면 이쪽)", "RCS → AGV · AGV → RCS", "measure_lap · robot_status",
        "measure_lap(job_id, mode = LAP, speed, standoff, expected_dist) → lap_state RUNNING",
        "lap_state DONE · lap_origin_ok true → lap_path_m 로 끝 (FAILED · 원점 못 찾음 · 최대 시간 넘김 = 실패)"),
    ("A", "일시정지", "RCS → AGV", "robot_control",
     "measure_lap 중: state = PAUSED → 재개 RUNNING (한 바퀴를 잇는다). set_job 중: STOP → 재개 set_job(남은 거리) + RUNNING",
     "-"),
    ("A", "정지", "RCS → AGV", "robot_control", "state = STOP — 캘리브레이션을 접는다(measure_lap 이면 lap_state FAILED)",
     "-"),
    ("section", "A-2. 캘리브레이션(곡판, 2026-11-18) — 한 바퀴 돌지 않고 원점 QR 마커에 정렬만 한다. "
                "차량은 판 왼쪽 끝의 QR 앞에 놓여 있다"),
    ("A-2", "1~3", "RCS 안 · RCS → AGV", "manual_command", "A 의 1~3 과 같다 — 로봇 홈 확인, 리프트 0, 아웃트리거 해제",
     "lift_h ≈ 0 · hold = RELEASE"),
    add("A-2", "4", "RCS → AGV", "measure_lap",
        "measure_lap(job_id, mode = ALIGN, speed, standoff, expected_dist = 0) — QR 에 맞춰 거리 · 방향을 정렬한다",
        "success = true · lap_state RUNNING"),
    add("A-2", "5", "AGV → RCS", "robot_status", "QR 앞에 정렬해 선다 — 그 자리가 x = 0(판 왼쪽 끝)",
        "lap_state DONE · lap_origin_ok true → 끝 (FAILED · QR 못 맞춤 · 최대 시간 넘김 = 실패). "
        "제한 = 차량 설정 「곡판 QR 정렬 최대 시간」(기본 120 초)"),
    ("A-2", "일시정지 · 정지", "RCS → AGV", "robot_control", "A 와 같다 — PAUSED → 재개 RUNNING / STOP → lap_state FAILED",
     "-"),
    ("A-2", "없을 때", "RCS 안", "-", "measure_lap(mode ALIGN)이 없는 차량이면 곡판 원점을 잡지 못한다 — 실패로 알린다",
     "set_job 으로 대신하지 않는다"),
    ("section", "B. 스캔 중 구간 이동 — ERUT 구역 검사(start) · RCS 「검사 시작」의 구간 사이"),
    ("B", "1", "RCS 안", "-", "로봇이 홈이고 태스크가 안 도는지 본다", "로봇 홈"),
    ("B", "2", "RCS → AGV", "manual_command", "cmd_mv_lift = 1, lift_height = 0 (올라가 있을 때만)", "lift_h ≈ 0"),
    ("B", "3", "RCS → AGV", "manual_command", "cmd_outrg_set = 1 해제 (고정돼 있을 때만)", "hold = RELEASE"),
    ("B", "4", "RCS → AGV", "set_job", "set_dist = 구간 사이 표면 거리 × 주행 배율, set_speed, standoff",
     "success. 곡판: x 는 판 왼쪽 끝(QR) 0 ~ 판 가로 — 감지 않고 그대로 간다(배율은 원통과 같다)"),
    ("B", "5", "RCS → AGV", "robot_control", "state = RUNNING", "state RUNNING"),
    ("B", "6", "AGV → RCS", "robot_status", "구간 가운데에 선다", "STOP/HOLD · |mv_dist − set_dist| ≤ 2 mm"),
    ("B", "7", "RCS → AGV", "manual_command", "cmd_outrg_set = 2 고정", "hold = SET"),
    ("B", "8", "RCS → AGV", "manual_command",
     "cmd_mv_lift = 1, lift_height = 구역 높이 (팔로 닿으면 리프트는 그대로)", "lift_h 목표 ±2 mm"),
    ("B", "9", "AGV → RCS", "robot_status", "로봇 스캔 동안 그대로 서 있는다",
     "RCS 가 계속 본다 — hold SET · error_code 0 · state STOP/HOLD · estop false · level_state ≠ 0 "
     "이 아니면 로봇이 멈춰 기다린다"),
    ("B", "10", "RCS 안", "-", "스캔이 끝나면 로봇이 홈으로 → 다음 구간은 2 부터", "-"),
    ("B", "일시정지", "RCS → AGV", "robot_control · manual_command",
     "움직이던 주행 · 리프트 · 아웃트리거를 세운다(robot_control state = STOP). 재개하면 같은 목표로 다시 "
     "(set_job 남은 거리 + RUNNING / manual_command 같은 값). 로봇 스캔 중이면 로봇만 멈추고 차량에는 보내지 않는다",
     "-"),
    ("B", "정지", "RCS → AGV", "robot_control", "state = STOP — 작업을 접는다", "-"),
    ("section", "D. 언제든 — AGV 가 robot_status 로 알리면 RCS 가 하는 일"),
    add("D", "비상정지", "AGV → RCS", "robot_status", "estop = true", "RCS 가 로봇 · 차량 · 작업을 세우고 ERUT E1002. false 가 되면 해제"),
    add("D", "접근 감지", "AGV → RCS", "robot_status", "obstacle_stop = true", "RCS 가 작업을 일시정지 · 알람(재개는 사람이)"),
    add("D", "배터리", "AGV → RCS", "robot_status", "battery_pct · charging", "상단 배터리 · ERUT 상태 · 조회 · 완료에 싣는다"),
    ("D", "로봇 안전정지", "PLC ↔ 로봇", "로봇 Config IO (안전 IO, 2채널 — RCS 를 거치지 않는다)",
     "차량이 움직이는 동안 PLC 가 CI0 · CI1 중 하나라도 켜면 안전정지(비상정지 아님). 그 신호가 꺼진 뒤 CI2 · CI3 중 "
     "하나라도 켜면 안전재설정",
     "RCS 는 모니터링만 — 상단 Cobot 램프(「안전정지」 · 「재설정 대기」) · I/O 화면 · 알림. ERUT 로 안 보냄. "
     "풀릴 때까지 로봇 출발을 미룬다"),
    ("section", "C. 수동 작업 — RCS 수동 제어 화면 (로봇이 홈이고 태스크가 안 돌 때만 버튼이 켜진다)"),
    ("C", "조그", "RCS → AGV", "manual_command",
     "cmd_mv_fwd · cmd_mv_rear · cmd_trn_left · cmd_trn_right = true 를 누르는 동안 200 ms 마다 다시 — 떼면 모든 칸 0/false",
     "명령이 끊기면 AGV 가 스스로 선다"),
    ("C", "아웃트리거 전체", "RCS → AGV", "manual_command", "cmd_outrg_set = 2 고정 / 1 해제", "hold = SET / RELEASE"),
    ("C", "아웃트리거 개별", "RCS → AGV", "manual_command",
     "cmd_man_outrg1 · 2 · 3 = 1 상승 / 2 하강 — 누르는 동안 200 ms 마다, 떼면 0", "-"),
    ("C", "리프트", "RCS → AGV", "manual_command", "cmd_mv_lift = 1, lift_height = 입력 높이", "lift_h 목표 ±2 mm"),
    ("C", "원점 / 초기화", "RCS → AGV", "manual_command", "cmd_init = 1", "success"),
    ("C", "정지", "RCS → AGV", "robot_control", "state = STOP", "-"),
    ("C", "오류 리셋", "RCS → AGV", "robot_control", "reset = 1", "error_code = 0"),
]


def _fit_rows(ws, widths, start=5) -> None:
    """줄바꿈된 글이 잘리지 않게 줄 높이를 글 길이로 맞춘다(한글은 칸 너비 1.8 당 한 글자쯤)."""
    import math
    merged_rows = {rng.min_row for rng in ws.merged_cells.ranges}
    for r in range(start, ws.max_row + 1):
        if r in merged_rows:
            continue                      # 구역 제목 줄 — 가로로 합쳐져 한 줄이면 된다
        lines = 1
        for c, width in enumerate(widths, start=1):
            text = ws.cell(row=r, column=c).value
            if not isinstance(text, str) or not text:
                continue
            per_line = max(int(width / 1.8), 4)
            lines = max(lines, sum(math.ceil(len(part) / per_line) or 1 for part in text.split("\n")))
        ws.row_dimensions[r].height = max(22, 15 * lines + 6)


def main() -> None:
    wb = Workbook()
    wb.remove(wb.active)

    ws = sheet(wb, "1.노드", "RCS ↔ AGV 노드 — 이름과 칸 (2026-11-17 · 11-18 곡판 measure_lap mode)",
               "<ns> = vehicle (RCS 환경변수 SMR_VEHICLE_NS). 단위는 m · m/s · rad. 노란 줄 = 정의(vehicle_interfaces)와 "
               "RCS 는 끝났고 AGV 노드가 채우면 바로 붙는 것. 모의기(vehicle_sim)가 같은 칸을 낸다. "
               "자세한 근거: 3S-AGV_PLC_연계_인터페이스_20261106.xlsx",
               ["방향", "이름", "형식 · 용도", "칸", "타입 · 단위", "값 · 뜻", "비고"],
               [11, 22, 22, 34, 22, 70, 10])
    rows(ws, NODES, 7, left_from=2)
    _fit_rows(ws, [11, 22, 22, 34, 22, 70, 10])

    ws = sheet(wb, "2.작업별 순서", "작업별로 주고받는 순서 (2026-11-17 · 11-18 곡판 QR 정렬)",
               "위에서 아래로 차례대로. 「RCS 안」은 차량 노드와 주고받지 않는 RCS 쪽 확인이다. 제한 시간 · 실패는 "
               "RCS 알람 + ERUT E2001 DRIVE_ERROR.",
               ["작업", "순서", "방향", "노드", "보내는 값 · 하는 일", "끝 판정 · 비고"],
               [6, 14, 18, 26, 70, 52])
    rows(ws, SEQUENCES, 6, left_from=3)
    _fit_rows(ws, [6, 14, 18, 26, 70, 52])

    for ws in wb.worksheets:
        ws.sheet_view.zoomScale = 90
        ws.cell(row=2, column=1).font = NOTE_FONT
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"만들었습니다: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
