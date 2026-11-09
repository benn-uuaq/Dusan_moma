"""RCS <-> 차량(AGV · 리프트 · 아웃트리거) · 차량용 PLC 연계 인터페이스 표를 만든다.

지금 RCS 는 차량을 더미(가상)로 돌리거나 모의기(vehicle_sim)로 시험한다. 실제 차량
제어 노드 · PLC 와 붙이려면 무엇을 주고받아야 하는지 — 노드 · 토픽 · 서비스 목록,
명령과 용도, 단위, 순서, 인터락, 배터리, PLC, 확인할 것 — 를 한 파일로 정리한다.

  0. 읽는 법           범례 · 단위 · 지금 상태
  1. 노드·통신         노드 · 토픽 · 서비스 · PLC 접속 목록
  2. 메시지·서비스     필드별 타입 · 단위 · 값 · 용도 · RCS 가 쓰는 곳
  3. 원주 거리 환산     모재 표면 이동 → 차량 실제 이동 (거리 유지 입력값 · 계산기)
  4. 명령 순서         주행 · 아웃트리거 · 리프트 · 일시정지 · 조그 · ERUT 구역 · 캘리브레이션
  5. 인터락·안전       로봇 홈 · 차량 고정 확인(309) · 오류 · 끊김
  6. 배터리·충전       요청 칸과 ERUT 로 나가는 곳
  7. PLC              화면 자리표시자 신호 · 접속 설정 · 정할 것
  8. 확인·요청         차량 담당에게 확인 · 요청할 것

출처: src/vehicle_interfaces (msg · srv), src/vehicle_sim/vehicle_sim/vehicle_sim_node.py,
      operator-ui/src/smr_operator_ui/services/{vehicle_client,vehicle_adapters}.py,
      operator-ui/src/smr_operator_ui/app.py, operator-ui/.../config/plc_io.json,
      docs/vehicle_request_calibration_lap_battery.md, docs/08_amr_leveling_and_outrigger.md

값을 고칠 때는 코드를 먼저 고치고 이 스크립트를 다시 돌린다.
실행:  python3 tools/build_vehicle_interface_xlsx.py
"""

import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import PatternFill

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_modbus_interface_xlsx import (  # noqa: E402
    BOLD_BODY, BODY_FONT, BORDER, CENTER, LEFT, NOTE_FONT, rows, sheet,
)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "3S-AGV_PLC_연계_인터페이스_20261106.xlsx"

#: 상태 칸 색 — 받은 자료(색 없음) · 3S 가정(확인 필요) · 3S 요청(추가)
ASSUME = PatternFill("solid", fgColor="FFFFF2CC")
REQUEST = PatternFill("solid", fgColor="FFE2EFDA")
GIVEN = "받은 자료"
ASSUMED = "3S 가정 · 확인 필요"
REQUESTED = "3S 요청 · 추가"


def given(*values):
    return values


def assumed(*values):
    return ("fill", ASSUME, *values)


def requested(*values):
    return ("fill", REQUEST, *values)


# ================================================================= 0. 읽는 법
README = [
    ("section", "이 문서"),
    given("목적", "RCS 가 지금 가상(더미) · 모의기로만 돌리는 차량(AGV 주행 · 리프트 · 아웃트리거)과 "
          "차량용 PLC 를 실제 장비와 붙이기 위해, 무엇을 어떤 단위로 주고받는지 정리한다. "
          "차량 제어 담당과 이 표를 보고 맞춘다."),
    given("기준", "RCS 2026-11-06 판(커밋 cbe5e2f) · vehicle_interfaces 패키지(차량 담당이 준 정의를 "
          "ROS 2 로 옮긴 것) · ERUT MQTT 표준 인터페이스 if-0.12"),
    given("지금 상태", "RCS 연결 설정 「차량 제어」 = 자동(차량 상태가 들어오면 차량, 없으면 더미) / 더미 / "
          "ROS 차량 노드 고정. 실제 차량 노드는 아직 없고, 모의기(ros2 launch vehicle_sim "
          "vehicle_sim.launch.py)가 같은 인터페이스로 대신한다. PLC 는 RCS 에 통신 코드가 없다(화면 "
          "자리표시자만)."),
    ("section", "시트"),
    given("1.노드·통신", "누가 무엇을 내고 받는지 — 노드 · 토픽 · 서비스 · PLC 접속"),
    given("2.메시지·서비스", "필드별 타입 · 단위 · 값 · 용도 · RCS 가 쓰는 곳 (배터리 · 비상정지 등 요청 칸 포함)"),
    given("3.원주 거리 환산", "모재 표면 이동 s → 차량 실제 이동 L = s × (R + d + c) ÷ R, 거리 유지 입력값, 계산기"),
    given("4.명령 순서", "주행 · 아웃트리거 · 리프트 · 일시정지 · 조그 · ERUT 구역 · 캘리브레이션 — 보내는 것과 끝 판정"),
    given("5.인터락·안전", "로봇 홈(276) · 차량 고정 확인(309) · 오류 · 끊김 · 비상정지 · 수평"),
    given("6.배터리·충전", "배터리 잔량 · 충전 상태와 ERUT 로 나가는 곳"),
    given("7.PLC", "RCS 의 PLC 칸 · I/O 자리표시자 · 누가 PLC 와 통신할지"),
    given("8.확인·요청", "차량 담당에게 확인 · 요청할 것 18 가지"),
    given("RCS 차량 설정", "설정 / 진단 → 차량 설정: 차량 폭 · 모재 거리 유지(→ 주행 배율) · 리프트 한계 높이(3500) · "
          "로봇 베이스 높이(시작 위치 ↔ 베이스) · 로봇 상승 한계(베이스 위로 1600) · 차량 주행 속도(300 mm/s)"),
    ("section", "상태 칸 범례"),
    given("받은 자료", "차량 담당이 준 정의 그대로. 이름 · 타입은 글자까지 같아야 통신된다."),
    assumed("3S 가정 · 확인 필요", "자료에 없어 RCS · 모의기가 가정한 동작. 실제 차량이 다르면 RCS "
            "(services/vehicle_adapters.py)와 모의기를 같이 고친다."),
    requested("3S 요청 · 추가", "지금 인터페이스에 자리가 없어 3S 가 추가를 요청하는 것."),
    ("section", "단위"),
    given("차량 인터페이스", "거리 · 높이 m, 속도 m/s, 각도 rad (RobotStatus · SetJob · ManualCommand)"),
    given("RCS 내부 · ERUT", "mm, mm/s 정수 — RCS 가 m ↔ mm 로 바꾼다(차량 쪽은 m 그대로)."),
    given("배터리", "% 정수 0~100 (ERUT 규격이 소수를 금지)"),
]

# ================================================================= 1. 노드·통신
NODES = [
    ("section", "노드"),
    assumed("노드", "차량 제어 노드 (차량 담당)", "ROS 2 노드 — 이름 미정",
            "차량 ↔ RCS", "-", "AGV 주행 · 리프트 · 아웃트리거 제어, 상태 발행. 차량용 PLC 와 붙는 쪽"
            "(권장 — 7.PLC)", "없음. 모의기 /vehicle_sim 이 대신(패키지 vehicle_sim)"),
    given("노드", "RCS — smr_operator_ui (3S)", "ROS 2 노드 /smr_operator_ui",
          "-", "-", "차량 상태 구독 · 명령 서비스 호출, 작업 순서(시퀀서) · ERUT MQTT", "있음"),
    given("노드", "로봇 제어 노드 — robot_control_node (3S)", "ROS 2 노드 /robot_control_node",
          "로봇 ↔ RCS", "-", "협동로봇 Modbus · 대시보드. 차량과 직접 통신하지 않는다 — 홈 플래그 · "
          "차량 고정 확인만 RCS 를 거쳐 오간다(5.인터락)", "있음"),
    ("section", "차량 ↔ RCS (네임스페이스 <ns> = RCS 환경변수 SMR_VEHICLE_NS, 기본 vehicle)"),
    assumed("토픽", "<ns>/robot_status", "vehicle_interfaces/msg/RobotStatus",
            "차량 → RCS", "1 Hz 이상(모의기 10 Hz). RCS 는 2 초 넘게 안 오면 끊김",
            "차량 상태 · 위치 · 리프트 높이 · 오류. 도착 판정 · 인터락 · 화면 · ERUT 보고의 근거",
            "이름은 가정 — 확인 필요"),
    assumed("서비스", "<ns>/robot_control", "vehicle_interfaces/srv/RobotControl",
            "RCS → 차량", "요청마다", "출발 · 재개(RUNNING) · 일시정지(PAUSED) · 정지(STOP) · 오류 리셋 · "
            "작업 취소", "이름은 가정"),
    assumed("서비스", "<ns>/set_job", "vehicle_interfaces/srv/SetJob",
            "RCS → 차량", "이동마다", "자동 주행 한 번 설정(거리 · 속도)", "이름은 가정"),
    assumed("서비스", "<ns>/manual_command", "vehicle_interfaces/srv/ManualCommand",
            "RCS → 차량", "조그는 누르는 동안 200 ms 마다 다시", "수동 조그 · 아웃트리거 고정/해제 · "
            "아웃트리거 개별 · 리프트 높이 · 원점/초기화. 자동 순서의 아웃트리거 · 리프트도 이것으로 보낸다",
            "이름은 가정"),
    requested("서비스", "<ns>/measure_lap (제안)", "vehicle_interfaces/srv/MeasureLap (새로)",
              "RCS → 차량", "캘리브레이션마다", "모재를 한 바퀴 돌며 둘레를 잰다(ERUT 캘리브레이션 본체)",
              "요청 — 8.확인·요청 12"),
    ("section", "로봇 ↔ RCS (차량 인터락에 쓰는 것만)"),
    given("토픽", "robot/status/at_home", "std_msgs/Int32 (레지스터 276)", "로봇 노드 → RCS",
          "10 Hz", "로봇이 홈(접힌 자세)인가. 1 일 때만 RCS 가 차량 · 리프트 · 아웃트리거 해제를 움직인다",
          "있음"),
    given("토픽", "robot/command/scan_z_offset", "std_msgs/Int32 (레지스터 358)", "RCS → 로봇 노드",
          "구역마다 리프트를 정할 때 · 마킹 전 0", "리프트 대신 팔을 들어 스캔할 높이 [mm]. 로봇이 시작 자세를 "
          "그만큼 올려 원점부터 다시 잡으므로 TPAC 좌표는 그대로 영점부터", "있음(v6)"),
    given("토픽", "robot/command/vehicle_ready", "std_msgs/Int32 (레지스터 309)", "RCS → 로봇 노드",
          "0.5 초마다 판단, 바뀌면 · 5 초마다 다시", "차량이 굳었다(아웃트리거 고정 · 정지 · 오류 없음). "
          "1 이어야 로봇 태스크가 팔을 뻗는다", "있음"),
    ("section", "차량용 PLC"),
    assumed("접속", "차량용 PLC", "KEYENCE MC Protocol 또는 Modbus TCP · 기본 192.168.0.10:5000 · 국번 1",
            "?", "-", "비상정지 · 안전 스캐너 · 아웃트리거 접지 · 리프트 높이 · 수평(roll/pitch) 등. "
            "RCS 연결 설정에 칸만 있다", "RCS 통신 코드 없음 — 7.PLC"),
]

# ================================================================= 2. 메시지·서비스
FIELDS = [
    ("section", "RobotStatus.msg — 차량 → RCS, <ns>/robot_status"),
    given("RobotStatus", "header", "std_msgs/Header", "-", "stamp · frame_id", "상태 시각",
          "안 씀 — 끊김 판정은 받은 시각으로 한다", GIVEN),
    assumed("RobotStatus", "state", "string", "-", "STOP · RUNNING · PAUSED · HOLD · ERROR",
            "차량 상태. HOLD = 아웃트리거로 고정돼 선 상태(모의기)",
            "도착: RUNNING 을 한 번 본 뒤 STOP/HOLD. 고정 확인(309): STOP/HOLD. 수동 명령: RUNNING · "
            "PAUSED · ERROR 가 아닐 때만 받는다고 가정", "값 목록 확인 필요"),
    given("RobotStatus", "hold", "string", "-", "SET · RELEASE", "아웃트리거 고정 / 해제",
          "아웃트리거 도착 판정 · 고정 확인(309)", GIVEN),
    assumed("RobotStatus", "error_code", "int32", "-", "0 정상, 그 외 오류", "오류 번호",
            "0 이 아니면 309 = 0. state = ERROR 면 하던 이동을 실패로 보고 ERUT E2001 DRIVE_ERROR(stop)",
            "코드표 요청"),
    given("RobotStatus", "error_msg", "string", "-", "", "오류 설명", "화면 알람 · ERUT error.detail", GIVEN),
    given("RobotStatus", "job_id", "string", "-", "SetJob 의 job_id", "지금 하는 작업",
          "자기가 보낸 SetJob 의 상태만 골라 도착을 본다(job_id 같을 때만)", GIVEN),
    assumed("RobotStatus", "set_dist", "float64", "m", "", "목표 이동 거리", "표시용",
            "이번 이동 거리인지 누적인지 확인"),
    assumed("RobotStatus", "mv_dist", "float64", "m", "이번 이동에서 0 → set_dist",
            "이동 거리 — set_dist 와 같은 기준(차량 주행 거리)",
            "AMR 위치 = 출발 위치 + mv_dist. |mv_dist − set_dist| ≤ 0.002 m 면 도착",
            "이번 이동 기준(누적 아님) 가정"),
    requested("RobotStatus", "standoff_meas", "float64", "m", "", "지금 잰 거리 — 차량 안쪽 면 ↔ 모재 표면",
              "거리 유지 확인 · 화면 · 캘리브레이션(sen1/sen2 로 재는 값이면 그 칸을 쓴다)", REQUESTED),
    given("RobotStatus", "speed", "float64", "m/s", "", "현재 이동 속도", "표시용", GIVEN),
    assumed("RobotStatus", "sen1_dist / sen2_dist", "float64", "m ?", "", "센서 1 · 2 측정 거리",
            "지금 안 씀. 모재 표면까지 거리라면 캘리브레이션 둘레 계산에 쓴다",
            "뜻 · 단위 · 설치 위치 확인"),
    assumed("RobotStatus", "position (Position6D)", "x y z float64 / roll pitch yaw float64", "m / rad",
            "", "차량 6자유도 위치 · 자세", "화면 표시. roll · pitch 는 수평 확인에 쓸 수 있다",
            "Position6D 정의 · 좌표계 기준 확인"),
    given("RobotStatus", "lift_h", "float64", "m", "0 ~ 리프트 최대(모의기 3.0)", "리프트 높이",
          "리프트 도착(±2 mm) · ERUT query.lift_height · 화면", GIVEN),
    requested("RobotStatus", "battery_pct", "uint8", "%", "0~100 정수, 모르면 255", "배터리 잔량",
              "ERUT evt/status · query · complete 의 battery (6.배터리)", REQUESTED),
    requested("RobotStatus", "charging", "bool", "-", "", "충전 중", "ERUT evt/status · query 의 charging",
              REQUESTED),
    requested("RobotStatus", "estop", "bool", "-", "", "차량 비상정지 눌림",
              "ERUT 에 estop 장애로 알리고 로봇 · 작업을 세운다", REQUESTED),
    requested("RobotStatus", "obstacle_stop", "bool", "-", "", "접근 감지(라이다 · 근접 센서)로 선 상태",
              "작업을 일시정지로 보고 ERUT 에 알린다(구매사양: 설정 거리 이내 접근 시 일시정지)", REQUESTED),
    requested("RobotStatus", "level_ok (+ roll · pitch)", "bool", "- / rad", "", "아웃트리거 수평 보정 완료",
              "고정 확인(309) 조건에 더한다 — 수평이 맞을 때만 로봇 검사 허가", REQUESTED),
    requested("RobotStatus", "lap_state · lap_path_m · lap_wall_dist_m · lap_error", "string · float64",
              "- / m", "IDLE · RUNNING · DONE · FAILED", "캘리브레이션 한 바퀴 결과",
              "ERUT evt/complete(calibrate).total_length_mm", REQUESTED),
    ("section", "Position6D.msg"),
    assumed("Position6D", "x · y · z", "float64", "m", "", "위치", "표시용", "정의가 자료에 없어 일반형으로 둠"),
    assumed("Position6D", "roll · pitch · yaw", "float64", "rad", "", "자세", "표시용", "정의 확인"),
    ("section", "SetJob.srv — RCS → 차량, <ns>/set_job (자동 주행 한 번)"),
    assumed("SetJob 요청", "job_id", "string", "-", "RCS-0001, RCS-0002 … (이동마다 1씩)", "작업 식별자",
            "상태의 job_id 로 자기 이동을 가려낸다", GIVEN),
    assumed("SetJob 요청", "set_dist", "float64", "m", "부호 = 방향(+ 원주 진행 방향)",
            "이번 이동에서 **차량 중심이 달릴 거리** = 목표 − 지금 위치. RCS 가 모재 표면 거리를 차량 설정"
            "(차량 폭 · 모재 거리 유지)으로 환산해 보낸다 — 표면 200 mm 면 차량은 더 길다(3.원주 거리 환산)",
            "RCS 가 mm 를 m 로 바꿔 보낸다",
            "이번 이동 거리 가정"),
    assumed("SetJob 요청", "set_speed", "float64", "m/s", "RCS 차량 설정 「차량 주행 속도」 (기본 300 mm/s = 0.3)",
            "목표 속도", "차량 설정 화면", "차량 허용 범위 확인"),
    assumed("SetJob 요청", "offset_dist", "float64", "m", "0", "오프셋 거리", "늘 0", "뜻 확인"),
    assumed("SetJob 요청", "offset_height", "float64", "m", "0", "리프트 오프셋 높이", "늘 0", "뜻 확인"),
    assumed("SetJob 요청", "total_distance", "float64", "m", "", "작업 전체 가로(원주 방향) 크기",
            "격자 전체 = (열−1)×(폭−겹침)+폭", "뜻 확인"),
    assumed("SetJob 요청", "total_height", "float64", "m", "", "작업 전체 높이", "격자 전체 높이", "뜻 확인"),
    requested("SetJob 요청", "standoff", "float64", "m", "0 ~ 2.0", "거리 유지 목표 — 차량 안쪽 면 ↔ 모재 "
              "표면", "RCS 설정의 「모재 거리 유지」 입력값. 차량은 이 거리를 지키며 돌고, 실제 주행 거리 "
              "환산에 쓴다(3.원주 거리 환산)", REQUESTED),
    requested("SetJob 요청", "target_diameter", "float64", "m", "ERUT diameter ÷ 1000", "모재 바깥 지름",
              "실제 주행 거리 환산 · 원주 궤적 · 캘리브레이션에 쓴다", REQUESTED),
    given("SetJob 응답", "success · message", "bool · string", "-", "", "접수 여부 · 사유",
          "success 면 RobotControl(RUNNING)으로 출발시킨다. false 면 이동 실패", GIVEN),
    ("section", "RobotControl.srv — RCS → 차량, <ns>/robot_control"),
    assumed("RobotControl 요청", "state", "string", "-", "RUNNING · PAUSED · STOP · (빈 값)",
            "RUNNING = 출발 · 재개, PAUSED = 일시정지, STOP = 정지",
            "SetJob 접수 뒤 RUNNING / 작업 일시정지 중 PAUSED · 재개 RUNNING / 수동 화면 정지 · 이동 취소 STOP",
            "값 목록 확인"),
    assumed("RobotControl 요청", "reset", "uint8", "-", "1 = 오류 리셋", "오류 해제", "수동 화면 「오류 리셋」",
            ASSUMED),
    assumed("RobotControl 요청", "job_cancel", "uint8", "-", "1 = 작업 취소", "SetJob 취소", "지금 안 씀(예약)",
            ASSUMED),
    given("RobotControl 응답", "success · message", "bool · string", "-", "", "접수 여부 · 사유",
          "false 면 이동 실패(알람)", "자료에 success 가 세 번 — 하나로 둠"),
    ("section", "ManualCommand.srv — RCS → 차량, <ns>/manual_command"),
    assumed("ManualCommand 요청", "cmd_mv_fwd / cmd_mv_rear", "bool", "-", "true 동안 이동",
            "수동 전진 / 후진", "누르는 동안 200 ms 마다 다시 보내고, 떼면 모든 필드 0/false",
            "명령 유지 방식(재전송 · deadman) 확인"),
    assumed("ManualCommand 요청", "cmd_trn_left / cmd_trl_right", "bool", "-", "true 동안 회전",
            "수동 좌회전 / 우회전", "위와 같음", "cmd_trl_right 이름(오타?) 확인 — 글자까지 같아야 한다"),
    assumed("ManualCommand 요청", "cmd_outrg_set", "uint8", "-", "0 없음 · 1 해제 · 2 고정",
            "아웃트리거 전체 고정/해제", "수동 화면 + 자동 순서(주행 전 해제 · 도착 후 고정). hold 로 끝 판정",
            "자동 작업 중에도 받는지 확인"),
    given("ManualCommand 요청", "cmd_man_outrg1 · 2 · 3", "uint8", "-", "0 없음 · 1 상승 · 2 하강",
          "아웃트리거 개별 상하", "수동 화면, 누르는 동안 200 ms 마다", GIVEN),
    assumed("ManualCommand 요청", "cmd_mv_lift + lift_height", "uint8 + float64", "- / m",
            "1 = 이동, lift_height = 목표 절대 높이(0~3.0)", "리프트를 목표 높이로(아웃트리거 고정에서만)",
            "수동 화면(0~3000 mm) + 자동 순서(구역 start.y, 주행 전 0). lift_h ±2 mm 로 끝 판정",
            "자동 작업의 리프트 명령 경로 확인"),
    given("ManualCommand 요청", "cmd_init", "uint8", "-", "1 = 원점/초기화", "수동 모드 원점/초기화",
          "수동 화면 「원점/초기화」", GIVEN),
    given("ManualCommand 응답", "success · message", "bool · string", "-",
          "수동 모드가 아니면 false (예: Rejected: Not in manual mode)", "접수 여부",
          "false 면 그 동작을 실패로 본다", GIVEN),
]

# ================================================================= 3. 명령 순서
SEQUENCES = [
    ("section", "A. 자동 주행 한 번 (구역 사이 이동 · 캘리브레이션 한 바퀴의 임시 방식)"),
    assumed("A 주행", "1", "SetJob(job_id, set_dist = 목표 − 지금 [m, 차량 주행 거리 — RCS 가 표면 거리 × "
            "(R + d + c)/R 로 환산], set_speed(차량 설정), total_*)",
            "접수하면 success = true", "-", "거절 → 이동 실패(알람 · ERUT E2001)"),
    assumed("A 주행", "2", "RobotControl(state = RUNNING)", "출발. state RUNNING, job_id 그대로, mv_dist 증가",
            "-", "거절 → 이동 실패"),
    assumed("A 주행", "3", "-", "다 가면 state STOP(또는 HOLD), mv_dist ≈ set_dist",
            "RUNNING 을 본 뒤 STOP/HOLD 이고 |mv_dist − set_dist| ≤ 2 mm", "120 초 · state ERROR → 실패"),
    ("section", "B. 아웃트리거 고정 / 해제"),
    assumed("B 아웃트리거", "1", "ManualCommand(cmd_outrg_set = 2 고정 / 1 해제)",
            "고정되면 hold SET(state HOLD), 풀리면 hold RELEASE(state STOP)",
            "hold 가 목표(SET/RELEASE)가 되면 끝", "30 초 · ERROR · 거절 → 실패"),
    ("section", "C. 리프트 높이"),
    assumed("C 리프트", "1", "ManualCommand(cmd_mv_lift = 1, lift_height = 목표 m)",
            "아웃트리거 고정(hold SET)에서만 움직인다. lift_h 를 목표까지",
            "|lift_h − 목표| ≤ 2 mm", "180 초 · ERROR · 거절 → 실패"),
    ("section", "D. 일시정지 · 재개 · 정지 · 리셋"),
    assumed("D 제어", "일시정지", "RobotControl(PAUSED) — 달리는 중에 RCS 작업이 일시정지될 때",
            "그 자리에 서고 state PAUSED", "-", "-"),
    assumed("D 제어", "재개", "RobotControl(RUNNING)", "남은 거리를 이어서 간다", "A-3 과 같음", "-"),
    assumed("D 제어", "정지", "RobotControl(STOP) — 수동 화면 정지 · 이동 취소", "선다(state STOP)", "-", "-"),
    assumed("D 제어", "리셋", "RobotControl(reset = 1) — 수동 화면 오류 리셋", "오류를 풀고 STOP/HOLD 로",
            "-", "-"),
    ("section", "E. 수동 조그 · 아웃트리거 개별 (RCS 수동 제어 화면)"),
    assumed("E 조그", "누르는 동안", "ManualCommand(cmd_mv_fwd 등 = true) 200 ms 마다 다시",
            "명령이 오는 동안만 움직인다. 끊기면 스스로 선다(모의기 deadman 0.5 초)", "-",
            "로봇이 홈이 아니거나 동작 중이면 RCS 가 버튼을 잠근다"),
    assumed("E 조그", "뗄 때", "ManualCommand(모든 필드 0/false)", "선다", "-", "-"),
    ("section", "F. ERUT 구역 검사 한 번 (start) — 차량이 하는 부분"),
    assumed("F 구역", "1", "(로봇이 홈이고 태스크가 안 돌 때만 진행)", "-", "at_home = 1", "아니면 기다린다"),
    assumed("F 구역", "2", "C(리프트 0 mm) → B(해제) — 리프트가 올라가 있거나 고정돼 있으면",
            "주행 전 리프트 내리기 · 아웃트리거 풀기", "C · B 끝 판정", "C · B 와 같음"),
    assumed("F 구역", "3", "A — 구역 가로 가운데까지(원주 거리 × 한 바퀴 배율)", "주행", "A-3", "A 와 같음"),
    assumed("F 구역", "4", "B(고정) → C(리프트 높이 — 아래 규칙)",
            "고정 → 리프트. 높이 0 = 모재 아래 끝 = 리프트 최하단의 로봇 시작 위치. 구역이 팔로 닿는 "
            "높이(로봇 베이스 높이 + 로봇 상승 한계(베이스 위로, 기본 1600 mm)) 안이면 리프트는 그대로 "
            "두고 로봇이 팔을 든다(레지스터 358). 아니면 리프트를 구역 start.y 로(리프트 한계 높이, 기본 "
            "3500 mm 까지 — 넘는 만큼은 팔로)", "B · C 끝 판정",
            "최대 작업 가능 높이(로봇 베이스 높이 + 리프트 한계 + 로봇팔 상승 한계)를 넘는 구역은 RCS 가 "
            "ERUT 400 E6003 으로 미리 거절"),
    assumed("F 구역", "5", "vehicle_ready(309) = 1 → 로봇 스캔", "선 채로 있는다(움직이지 않는다)",
            "-", "차량이 움직이거나 오류면 309 = 0 → 로봇이 멈춰 기다린다"),
    ("section", "G. ERUT 캘리브레이션 = 차량 한 바퀴"),
    assumed("G 캘리브레이션", "지금(임시)", "B(해제) → A(set_dist = π × 모재 지름 × 주행 배율)",
            "그만큼 달린다(차량 중심의 한 바퀴)", "ERUT total_length_mm = 달린 거리 ÷ 주행 배율(표면 둘레)",
            "실제 한 바퀴 측정 · 원점 인식이 아니다"),
    requested("G 캘리브레이션", "요청", "MeasureLap(job_id, speed)",
              "원점(QR)에서 출발해 한 바퀴 돌고 원점에 선다. lap_path_m · lap_wall_dist_m 를 준다",
              "lap_state DONE", "lap_state FAILED + lap_error → ERUT 5xx"),
]

# ================================================================= 4. 인터락·안전
INTERLOCKS = [
    ("section", "RCS 가 지키는 규칙"),
    given("1", "차량 · 리프트를 움직이거나 아웃트리거를 풀 때", "RCS",
          "로봇 태스크가 실행 · 일시정지 중이 아니고, 로봇이 홈(at_home = 1)일 때만 보낸다. 수동 화면 "
          "버튼도 잠근다", "기다렸다가 풀리면 보낸다", "있음"),
    given("2", "vehicle_ready(309) = 1", "RCS → 로봇",
          "아웃트리거 고정 + 주행 · 리프트 · 아웃트리거 이동 명령 없음 + (실제 차량이면) 상태 수신 중 · "
          "hold SET · error_code 0 · state STOP/HOLD", "하나라도 아니면 0", "있음"),
    given("3", "로봇이 팔을 뻗을 때", "로봇 태스크(v6)",
          "309 = 1 일 때만 움직인다. 기다리면 290 = 14(RCS 알림)", "시간 초과면 299 = 8 → ERUT E9108",
          "있음"),
    given("4", "차량 상태가 끊김", "RCS", "2 초 넘게 RobotStatus 가 안 오면 끊김 — 309 = 0",
          "「자동」이면 쉬는 동안 더미로 바뀐다(작업 · 이동 중에는 안 바꾼다)", "있음"),
    given("5", "차량 오류 · 명령 거절 · 시간 초과", "RCS",
          "하던 순서를 멈추고 알람 + ERUT evt/error E2001 DRIVE_ERROR(level stop)", "-", "있음"),
    ("section", "차량 쪽에 기대하는 규칙"),
    assumed("6", "리프트", "차량", "아웃트리거 고정(hold SET)에서만 움직인다", "거절(success false)",
            "받은 자료"),
    assumed("7", "주행", "차량", "아웃트리거가 고정돼 있으면 주행을 거절하거나 먼저 푼다(RCS 는 먼저 푼다)",
            "-", ASSUMED),
    assumed("8", "수동 명령", "차량", "RUNNING · PAUSED · ERROR 에서는 받지 않는다", "success false", ASSUMED),
    requested("9", "비상정지 · 접근 감지", "차량 → RCS",
              "차량 비상정지 · 안전 스캐너 정지를 상태로 알려 준다(estop · obstacle_stop)",
              "RCS 가 로봇 · 작업을 세우고 ERUT 에 알린다", REQUESTED),
    requested("10", "수평 보정", "차량 → RCS",
              "아웃트리거 고정 뒤 IMU roll · pitch 가 허용 범위면 level_ok = true",
              "false 면 309 = 0(로봇 검사 불가)", REQUESTED),
]

# ================================================================= 5. 배터리·충전
BATTERY = [
    requested("배터리 잔량", "RobotStatus.battery_pct · uint8 · %", "0~100 정수. 모르면 · 배터리 없는 "
              "유선 급전이면 255", "255 면 ERUT 에 battery 칸을 싣지 않는다",
              "evt/status.battery · res(query).battery · evt/complete(start).battery", REQUESTED),
    requested("충전 중", "RobotStatus.charging · bool", "true / false", "그대로 옮긴다",
              "evt/status.charging · res(query).charging", REQUESTED),
    requested("주기", "RobotStatus 와 같이", "1 Hz 이상", "-", "-", REQUESTED),
    given("배터리 부족", "-", "ERUT 가 정한 최소 배터리 미만", "ERUT 가 다음 구역을 막는다",
          "구역 시작 409 E5001 LOW_BATTERY → 충전 뒤 재개", "ERUT 규격"),
    requested("충전 자리로 이동", "차량 → RCS", "차량이 스스로 충전 스테이션에 가는 기능이 있는지",
              "자리를 떴다 오면 좌표계가 무효", "evt/error E1003 CALIBRATION_LOST · calibrated = false",
              "기능 유무 확인"),
    given("ERUT 기능 목록", "evt/info capabilities", "battery · charging", "값이 오기 시작하면 RCS 가 "
          "기능 목록에 넣는다(지금은 뺐다)", "evt/info", "RCS 할 일"),
]

# ================================================================= 6. PLC
PLC = [
    ("section", "RCS 연결 설정의 PLC 칸 (값은 기본값)"),
    assumed("PLC IP · 포트", "192.168.0.10 · 5000", "-", "칸만 있다", "실제 주소 · 포트", "확인"),
    assumed("PLC 프로토콜", "KEYENCE MC Protocol / Modbus TCP", "-", "칸만 있다",
            "어느 쪽인지 · 프레임(3E/4E 등)", "확인"),
    assumed("PLC 국번", "1", "-", "칸만 있다", "국번", "확인"),
    ("section", "RCS I/O 화면 자리표시자 (config/plc_io.json — 주소는 임시)"),
    assumed("X000", "Emergency Stop", "IN", "조회만(가짜 값)", "비상정지 신호 — 차량 상태(estop)로 받는 쪽 권장",
            "주소 · 뜻 확인"),
    assumed("X001", "Safety Scanner", "IN", "조회만", "안전 스캐너 정지 — obstacle_stop", "주소 · 뜻 확인"),
    assumed("X010 · X011 · X012", "Outrigger 1 · 2 · 3 Contact", "IN", "조회만", "아웃트리거 접지 — 고정 판정",
            "주소 확인"),
    assumed("Y000", "AMR Drive Enable", "OUT", "버튼만(보내지 않음)", "주행 허가 — RCS 가 직접 쓸지 차량 노드가 쓸지",
            "주인 정하기"),
    assumed("Y010", "Lift Brake", "OUT", "버튼만", "리프트 브레이크", "주인 정하기"),
    assumed("D100", "Lift Height", "IN", "조회만", "리프트 높이 — RobotStatus.lift_h 와 같은 값", "단위 확인"),
    assumed("D110 · D111", "Roll · Pitch", "IN", "조회만", "수평 — level_ok 판단 근거", "단위 확인"),
    ("section", "정할 것"),
    requested("PLC 를 누가 읽고 쓰나", "-", "-", "-",
              "권장: 차량 제어 노드가 PLC 를 맡고 RCS 는 ROS 인터페이스만 본다(필요한 값은 RobotStatus 에). "
              "RCS 가 직접 붙어야 하면 주소표(디바이스 · 비트/워드 · 단위 · 읽기/쓰기)를 받아 RCS 에 PLC 클라이언트를 만든다",
              "결정 필요"),
]

# ================================================================= 7. 확인·요청
REQUESTS = [
    assumed("1", "이름", "토픽 · 서비스 실제 이름, 패키지 이름(vehicle_interfaces 그대로인지), 네임스페이스",
            "글자까지 같아야 통신된다", "SMR_VEHICLE_NS · 모의기 namespace 로 맞춘다", "확인"),
    assumed("2", "SetJob.set_dist", "이번 이동 거리인지, 누적 목표인지. 부호와 원주 방향(규격: 위에서 보면 반시계)",
            "도착 판정 · 위치 계산이 달라진다", "vehicle_adapters.py · 모의기", "확인"),
    assumed("3", "SetJob offset_dist · offset_height · total_distance · total_height", "뜻과 쓰임",
            "RCS 는 0 · 0 · 작업 전체 크기를 보낸다", "-", "확인"),
    assumed("4", "RobotStatus.mv_dist", "이번 이동 기준인지(출발 때 0), 누적인지", "도착 판정", "-", "확인"),
    assumed("5", "RobotStatus.state · RobotControl.state", "들어갈 수 있는 값 전부와 뜻(HOLD 포함)",
            "도착 · 고정 확인 판정", "-", "확인"),
    assumed("6", "자동 작업 중 리프트 · 아웃트리거 명령", "ManualCommand 가 수동 모드 전용이면 자동 순서에서는 "
            "무엇으로 보내나", "RCS 는 자동 순서에서도 ManualCommand 를 쓴다", "-", "확인"),
    assumed("7", "주행 전 아웃트리거", "고정된 채 SetJob/RUNNING 이 오면 차량이 먼저 푸는지, 거절하는지",
            "RCS 는 먼저 풀고 보낸다", "-", "확인"),
    assumed("8", "조그 유지 방식", "200 ms 재전송 · deadman 시간", "떼는 신호를 놓쳐도 서야 한다", "-", "확인"),
    requested("9", "error_code 표", "번호 · 뜻 · 리셋으로 풀리는지", "ERUT 장애 레벨(stop · warning) · 복구 안내",
              "ERUT E2001 detail", "요청"),
    assumed("10", "sen1_dist · sen2_dist · Position6D", "뜻 · 단위 · 좌표계 · 설치 위치", "캘리브레이션 · 수평",
            "-", "확인"),
    requested("11", "배터리 · 충전", "battery_pct · charging (6.배터리)", "ERUT 가 구역마다 배터리를 본다",
              "-", "요청"),
    requested("12", "캘리브레이션 한 바퀴", "MeasureLap 서비스 + lap_* 상태(또는 SetJob 재사용 안) · 반복 정밀도 · "
              "최대 시간 · 원점 인식(ERUT QR) · 정지 오차", "ERUT 캘리브레이션 본체 — 지금은 거리 이동으로 대신",
              "docs/vehicle_request_calibration_lap_battery.md", "요청"),
    requested("13", "비상정지 · 접근 감지 · 수평", "estop · obstacle_stop · level_ok(+허용 기울기)",
              "로봇 검사 허가 · ERUT 안전 보고", "-", "요청"),
    requested("14", "PLC", "누가 PLC 와 통신하나, RCS 가 직접 붙으면 주소표", "I/O 화면 · 인터락", "-", "결정"),
    assumed("15", "속도 · 가감속", "set_speed 허용 범위 · 기본값(RCS 0.3 m/s) · 리프트 속도", "이동 시간 · 제한 시간",
            "RCS 제한 시간 주행 120 · 리프트 180 · 아웃트리거 30 초", "확인"),
    assumed("16", "상태 발행 주기", "RobotStatus 주기(1 Hz 이상)", "RCS 는 2 초 넘게 안 오면 끊김", "-", "확인"),
    requested("17", "거리 유지 · 원주 거리 환산", "차량 폭 실측값(회전 · 주행 기준점이 폭의 가운데인지), 거리 유지 "
              "d 를 차량이 받는 방법(SetJob.standoff 추가), set_dist · mv_dist 가 차량 중심 주행 거리인지",
              "모재 표면 200 mm 이동에 차량은 (R + d + 폭/2)/R 배 더 달린다 — RCS 가 차량 설정으로 환산한다",
              "3.원주 거리 환산 · RCS 차량 설정 화면", "요청"),
    requested("18", "리프트 한계 · 로봇 베이스 높이 · 상승 한계", "리프트 최대 높이(RCS 기본 3500 mm), 리프트 "
              "최하단에서 시작 위치 ↔ 로봇 베이스 높이, 로봇 팔이 베이스 위로 닿는 높이(기본 1600 mm)",
              "구역이 베이스 높이 + 상승 한계 안이면 리프트를 안 올리고 팔로 스캔한다",
              "RCS 차량 설정 — 최대 작업 가능 높이는 RCS 가 확인(로봇 태스크에는 검사 없음)", "확인"),
]


# ================================================================= 3. 원주 거리 환산
STANDOFF_TEXT = [
    ("section", "왜 필요한가"),
    given("모재 표면 200 mm ≠ 차량 200 mm", "", "",
          "차량은 모재(검사체)에서 거리 d(차량 안쪽 면 기준, 0~2 m)를 지키며 모재를 돌고, 차량의 회전 · "
          "주행 기준점(차량 가로 길이의 중심)은 안쪽 면에서 c 만큼 더 바깥에 있다. 같은 각도를 도는 동안 "
          "모재 표면은 s 만큼, 차량 기준점은 (R + d + c)/R 배 더 간다. 그래서 검사 때문에 「200 mm 이동」을 "
          "보내도 차량은 그보다 더 달려야 한다. 캘리브레이션도 같은 중심으로 돈다."),
    ("section", "기호"),
    given("R", "", "mm", "모재 반지름 = ERUT diameter ÷ 2 (바깥 지름 기준)"),
    given("d", "", "mm", "모재 거리 유지 — 벽 ↔ 차량 센서 면(차량 바깥 끝). 0 ~ 2000. **RCS 차량 설정 입력값**"),
    given("c", "", "mm", "차량 센서 면 ↔ 회전 · 주행 기준점(차량 폭의 가운데) = **차량 폭 ÷ 2** (RCS 차량 설정 「차량 폭」)"),
    given("s", "", "mm", "모재 표면 기준 이동 거리(호 길이). RCS · ERUT 구역 좌표가 쓰는 값"),
    given("L", "", "mm", "차량 기준점이 실제로 달릴 거리"),
    ("section", "공식"),
    given("각도", "θ = s ÷ R", "rad", "모재 중심에서 본 회전 각도"),
    given("차량 실제 이동", "L = s × (R + d + c) ÷ R", "mm", "= θ × (R + d + c)"),
    given("거꾸로", "s = L × R ÷ (R + d + c)", "mm", "차량이 달린 거리를 모재 표면 기준으로 바꿀 때(mv_dist 환산)"),
    given("한 바퀴", "차량 경로 = 2π(R + d + c)", "mm", "모재 둘레 2πR 과의 비가 곧 배율 (R + d + c)/R — "
          "캘리브레이션 한 바퀴 실측으로 확인 · 보정한다"),
]

STANDOFF_AFTER = [
    ("section", "누가 환산하나 — RCS (2026-11-09)"),
    given("RCS 가 환산", "", "",
          "RCS 차량 설정 화면의 「차량 폭」 · 「모재 거리 유지」와 ERUT 모재 지름으로 배율 (R + d + 폭/2) ÷ R 을 "
          "구해, 구역 위치(모재 표면 x)에 곱한 **차량 주행 거리**를 SetJob.set_dist 로 보낸다. mv_dist 도 차량 주행 "
          "거리로 받는다. ERUT 에 내보내는 위치 · 진행 거리는 계속 모재 표면 기준이다."),
    given("캘리브레이션", "", "",
          "한 바퀴 = π × 지름 × 배율 만큼 달리고, ERUT total_length_mm 는 달린 거리 ÷ 배율(표면 둘레)로 낸다."),
    given("처음 값", "", "",
          "차량 폭 · 거리 유지가 0 이면 배율 1(예전과 같다) — 실측값을 넣어야 한다."),
    requested("차량 쪽에 요청", "", "",
              "차량이 거리 d 를 지키며 돌도록 그 값을 받을 칸(SetJob.standoff)과, 지금 잰 거리(standoff_meas)를 "
              "주는 칸. 회전 · 주행 기준점이 차량 폭의 가운데가 아니면 그 위치를 알려 준다."),
]

CALC_INPUTS = (
    ("모재 지름 D", 1300, "mm", "ERUT diameter"),
    ("거리 유지 d", 500, "mm", "벽 ↔ 차량 센서 면 (0 ~ 2000) — RCS 차량 설정 「모재 거리 유지」"),
    ("센서 면 ↔ 차량 중심 c", 400, "mm", "= 차량 폭 ÷ 2 (예: 폭 800) — RCS 차량 설정 「차량 폭」의 절반"),
    ("모재 표면 이동 s", 200, "mm", "검사 때문에 옮길 거리"),
)


def _cell(ws, row, col, value, font=None, fill=None, align=None, fmt=None):
    c = ws.cell(row=row, column=col, value=value)
    c.font, c.border = font or BODY_FONT, BORDER
    c.alignment = align or (LEFT if col >= 4 else CENTER)
    if fill is not None:
        c.fill = fill
    if fmt:
        c.number_format = fmt
    return c


def write_standoff(wb) -> None:
    ws = sheet(wb, "3.원주 거리 환산", "원주 거리 환산 — 모재 표면 이동 → 차량 실제 이동",
               "차량은 모재에서 거리 d 를 지키며 차량 가로 길이의 중심을 기준으로 돈다. 노란 칸을 바꾸면 계산기 "
               "값이 다시 계산된다(엑셀 수식).",
               ["항목", "값 · 식", "단위", "설명"], [26, 30, 8, 100])
    r = rows(ws, STANDOFF_TEXT, 4, left_from=2)

    for col in range(1, 5):
        ws.cell(row=r, column=col).fill, ws.cell(row=r, column=col).border = (
            PatternFill("solid", fgColor="FFD6E4F0"), BORDER)
    ws.cell(row=r, column=1, value="계산기 — 노란 칸을 바꾼다").font = BOLD_BODY
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
    r += 1
    # 입력 칸 주소: D · d · c · s 순서(CALC_INPUTS 와 같다).
    D_cell, d_cell, c_cell, s_cell = (f"B{r + i}" for i in range(len(CALC_INPUTS)))
    for name, value, unit, note in CALC_INPUTS:
        _cell(ws, r, 1, name)
        _cell(ws, r, 2, value, fill=ASSUME, fmt="0")
        _cell(ws, r, 3, unit)
        _cell(ws, r, 4, note)
        r += 1
    outputs = (
        ("모재 반지름 R", f"={D_cell}/2", "mm", "D ÷ 2", "0"),
        ("배율 (R + d + c) ÷ R", f"=({D_cell}/2+{d_cell}+{c_cell})/({D_cell}/2)", "-",
         "차량이 모재 표면보다 몇 배 더 달리나", "0.000"),
        ("차량 실제 이동 L", f"={s_cell}*({D_cell}/2+{d_cell}+{c_cell})/({D_cell}/2)", "mm",
         "s × 배율 — 이만큼 달려야 표면에서 s 만큼 옮겨진다", "0.0"),
        ("한 바퀴 차량 경로", f"=2*PI()*({D_cell}/2+{d_cell}+{c_cell})", "mm", "캘리브레이션 한 바퀴 예상 거리", "0"),
        ("모재 둘레", f"=PI()*{D_cell}", "mm", "π × D", "0"),
    )
    for name, formula, unit, note, fmt in outputs:
        _cell(ws, r, 1, name, font=BOLD_BODY)
        _cell(ws, r, 2, formula, fmt=fmt, font=BOLD_BODY)
        _cell(ws, r, 3, unit)
        _cell(ws, r, 4, note)
        r += 1

    for col in range(1, 5):
        ws.cell(row=r, column=col).fill, ws.cell(row=r, column=col).border = (
            PatternFill("solid", fgColor="FFD6E4F0"), BORDER)
    ws.cell(row=r, column=1, value="예 — 계산기의 D · c · s 그대로, 거리 유지 d 만 바꾸면").font = BOLD_BODY
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
    r += 1
    for col, head in enumerate(("거리 유지 d [mm]", "배율", "차량 실제 이동 L [mm]", "한 바퀴 차량 경로 [mm]"),
                               start=1):
        _cell(ws, r, col, head, font=BOLD_BODY, align=CENTER)
    r += 1
    for d in (0, 500, 1000, 1500, 2000):
        radius = f"({D_cell}/2)"
        _cell(ws, r, 1, d, fmt="0")
        _cell(ws, r, 2, f"=({radius}+A{r}+{c_cell})/{radius}", fmt="0.000", align=CENTER)
        _cell(ws, r, 3, f"={s_cell}*({radius}+A{r}+{c_cell})/{radius}", fmt="0.0", align=CENTER)
        _cell(ws, r, 4, f"=2*PI()*({radius}+A{r}+{c_cell})", fmt="0", align=CENTER)
        r += 1
    rows(ws, STANDOFF_AFTER, 4, start=r, left_from=2)


def write_readme(wb) -> None:
    ws = sheet(wb, "0.읽는 법", "RCS ↔ 차량(AGV · 리프트 · 아웃트리거) · PLC 연계 인터페이스 — 2026-11-06",
               "3S RCS 가 실제 차량 · PLC 와 붙기 위해 주고받는 노드 · 명령 · 단위를 정리했다. "
               "생성: tools/build_vehicle_interface_xlsx.py (코드를 먼저 고치고 다시 만든다).",
               ["항목", "내용"], [22, 120])
    rows(ws, README, 2, left_from=1)


def main() -> None:
    wb = Workbook()
    wb.remove(wb.active)
    write_readme(wb)

    ws = sheet(wb, "1.노드·통신", "노드 · 토픽 · 서비스 · PLC 접속",
               "누가 무엇을 내고 받는지. <ns> 는 RCS 환경변수 SMR_VEHICLE_NS(기본 vehicle) — 모의기는 "
               "파라미터 namespace 로 맞춘다.",
               ["구분", "이름", "종류 · 형식", "방향", "주기 · 시간", "용도", "지금 상태"],
               [9, 30, 30, 14, 26, 52, 26])
    rows(ws, NODES, 7, left_from=2)

    ws = sheet(wb, "2.메시지·서비스", "메시지 · 서비스 필드 — 타입 · 단위 · 값 · 용도",
               "노랑 = 3S 가정(확인 필요), 초록 = 3S 요청(추가). 필드 이름은 차량 쪽 패키지와 글자까지 같아야 한다.",
               ["인터페이스", "필드", "타입", "단위", "값 · 범위", "뜻 · 용도", "RCS 가 쓰는 곳", "상태"],
               [17, 24, 16, 8, 30, 34, 48, 22])
    rows(ws, FIELDS, 8, left_from=2)

    write_standoff(wb)

    ws = sheet(wb, "4.명령 순서", "명령 순서 — 무엇을 보내고 언제 끝났다고 보나",
               "RCS(services/vehicle_adapters.py)와 모의기(vehicle_sim)가 같은 규칙을 쓴다. 실제 차량이 "
               "다르면 두 곳을 같이 고친다.",
               ["동작", "순서", "RCS → 차량", "차량이 할 일 · 돌려줄 상태", "RCS 끝 판정", "제한 시간 · 실패"],
               [14, 10, 44, 44, 36, 34])
    rows(ws, SEQUENCES, 6, left_from=3)

    ws = sheet(wb, "5.인터락·안전", "인터락 · 안전",
               "로봇과 차량이 동시에 움직이지 않게 하는 규칙. 로봇 홈(276) · 차량 고정 확인(309)은 로봇 "
               "레지스터다(docs/3S-Elite_Modbus_인터페이스_20260916.xlsx).",
               ["번호", "언제", "누가 보나", "규칙", "어기면 · 끊기면", "상태"],
               [6, 26, 16, 60, 40, 18])
    rows(ws, INTERLOCKS, 6, left_from=2)

    ws = sheet(wb, "6.배터리·충전", "배터리 · 충전",
               "ERUT 는 구역마다 배터리를 보고 다음 구역을 정한다. 배터리가 없는 장비는 칸을 빼야 한다 "
               "— 0 으로 채우면 시작을 못 한다.",
               ["항목", "어디 · 형식", "값", "RCS 처리", "ERUT 로 나가는 곳", "상태"],
               [18, 30, 30, 36, 46, 18])
    rows(ws, BATTERY, 6, left_from=2)

    ws = sheet(wb, "7.PLC", "차량용 PLC",
               "RCS 에는 PLC 통신 코드가 없다 — 연결 설정 칸과 I/O 화면의 자리표시자뿐이다. 주소는 임시값이다.",
               ["주소 · 칸", "이름 · 값", "방향", "지금 RCS", "실제 연계 때 필요한 것", "상태"],
               [22, 30, 8, 22, 70, 16])
    rows(ws, PLC, 6, left_from=2)

    ws = sheet(wb, "8.확인·요청", "차량 담당에게 확인 · 요청할 것",
               "노랑 = 확인(가정한 것이 맞는지), 초록 = 요청(새로 필요한 것).",
               ["번호", "대상", "내용", "왜 필요한가", "RCS 쪽", "상태"],
               [6, 30, 60, 40, 40, 10])
    rows(ws, REQUESTS, 6, left_from=2)

    for ws in wb.worksheets:
        ws.sheet_view.zoomScale = 90
    wb.worksheets[0]["A3"].font = NOTE_FONT
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"만들었습니다: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
