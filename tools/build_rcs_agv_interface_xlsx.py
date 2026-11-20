"""RCS <-> AGV(차량 제어 노드) 인터페이스 — 두 장짜리 요약을 만든다.

  1.노드          ROS 2 로 어떻게 붙나(노드 · 실행 환경) · 주고받는 토픽 · 서비스 · 칸 · 차량 오류 번호
  2.작업별 순서    캘리브레이션 · 스캔 중 구간 이동 · 비상정지 · 수동 작업에서 주고받는 순서

자세한 근거(요구 출처 · 정할 것 · 원주 거리 환산)는 docs/3S-AGV_PLC_연계_인터페이스_20261106.xlsx.
값을 고칠 때는 코드(operator-ui services/vehicle_* · src/vehicle_sim)를 먼저 맞추고 다시 만든다.
src/vehicle_interfaces 는 AGV 담당 판(2026-11-20)을 그대로 쓴다 — 3S 가 고치지 않는다. 같은 날 협의로
RobotControl.job_finish 한 줄만 더했다(작업 끝 → 홀딩 해제 · 아웃트리거 올림 · 이동 가능).
실행:  python3 tools/build_rcs_agv_interface_xlsx.py
"""

import math
import sys
from pathlib import Path

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_modbus_interface_xlsx import (  # noqa: E402
    BODY_FONT, BOLD_BODY, BORDER, CENTER, HEAD_FILL, HEAD_FONT, LEFT, NOTE_FONT, TITLE_FONT, rows, sheet,
)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "RCS_AGV_인터페이스_20261119.xlsx"
NS = "/vehicle"


# ================================================================= 1. 노드
SHEET1_WIDTHS = [12, 26, 34, 20, 20, 30, 56]

#: 1) 노드 구성 · 실행 환경 — (항목, 내용). 내용은 2~7 칸을 합쳐 쓴다.
SETUP = [
    ("ROS 2", "Humble. RCS PC 와 AGV PC 가 같은 네트워크 · 같은 ROS_DOMAIN_ID(기본 0) · RMW 기본(Fast DDS). "
              "방화벽에서 DDS(UDP 7400~7500 대) 를 막지 않는다"),
    ("인터페이스 패키지", "vehicle_interfaces (AGV 담당 판 2026-11-20 이 기준 — dusan_ws/src/vehicle_interfaces). RCS PC · AGV PC 가 "
                    "같은 파일을 빌드해 쓴다: colcon build --packages-select vehicle_interfaces. 칸 이름 · 타입이 글자까지 "
                    "같아야 통신된다"),
    ("AGV 노드", "AGV 담당이 만드는 차량 제어 노드(노드 이름은 자유). 아래 2) 의 토픽 · 서비스를 /vehicle/... 이름으로 "
               "연다 — robot_status · start_request 발행, robot_estop 구독, 서비스 6개 서버. PLC 통신은 이 노드 안에서만 한다"
               "(끊기면 error_code 701). "
               "예: ros2 run <AGV 패키지> <실행 파일>"),
    ("RCS 노드", "/smr_operator_ui (3S RCS PC) — robot_status · start_request 구독, robot_estop 발행, 서비스 호출(클라이언트). "
               "앞 이름 vehicle 은 RCS 환경변수 SMR_VEHICLE_NS 로 바꿀 수 있다(AGV 와 같아야 한다)"),
    ("모의기(참고 구현)", "ros2 launch vehicle_sim vehicle_sim.launch.py — AGV 노드 대신 같은 토픽 · 서비스를 연다 "
                    "(dusan_ws/src/vehicle_sim/vehicle_sim/vehicle_sim_node.py). AGV 노드도 이것과 똑같이 응답하면 된다"),
    ("QoS", "모두 ROS 2 기본값: reliable · volatile · keep last 10. 서비스는 받자마자(수 ms 안) 응답한다 — "
            "「받았다」만 답하고, 다 끝났는지는 robot_status 로 알린다"),
    ("확인 명령", "ros2 node list  /  ros2 topic list  /  ros2 topic echo /vehicle/robot_status  /  "
              "ros2 topic hz /vehicle/robot_status\n"
              "ros2 service call /vehicle/set_job vehicle_interfaces/srv/SetJob "
              "\"{job_id: 'T-1', set_dist: 0.2, set_speed: 0.1, standoff: 2.0, radius: 2.5}\"\n"
              "ros2 service call /vehicle/robot_control vehicle_interfaces/srv/RobotControl \"{state: 'RUNNING'}\"\n"
              "ros2 topic pub --once /vehicle/start_request std_msgs/msg/Empty \"{}\"  /  "
              "ros2 topic pub /vehicle/robot_estop std_msgs/msg/Bool \"{data: true}\""),
]

#: 2) 주고받는 토픽 · 서비스 — 한눈에
INTERFACE_HEAD = ["종류", "이름", "타입", "AGV 노드", "RCS 노드", "주기 · 때", "용도"]
INTERFACES = [
    ("토픽", f"{NS}/robot_status", "vehicle_interfaces/msg/RobotStatus", "발행 (publisher)", "구독 (subscriber)",
     "1 Hz 이상 계속(10 Hz 권장). 2 초 넘게 안 오면 RCS 는 끊김으로 본다", "차량 상태 · 위치 · 리프트 · 안전 · 배터리 · 캘리브레이션 결과"),
    ("토픽", f"{NS}/start_request", "std_msgs/msg/Empty", "발행", "구독",
     "차량 「작업 시작」 버튼을 누를 때 한 번", "RCS 가 「계측 시작」(캘리브레이션) 순서를 돈다 — 차량은 스스로 움직이지 않는다"),
    ("토픽", f"{NS}/robot_estop", "std_msgs/msg/Bool", "구독", "발행",
     "바뀌면 바로 + 0.5 초마다", "로봇 컨트롤러 비상정지 상태 — true 면 차량도 비상정지"),
    ("서비스", f"{NS}/set_job", "vehicle_interfaces/srv/SetJob", "서버 (service)", "호출 (client)",
     "구역 이동마다", "자동 주행 한 번 설정 — 출발은 robot_control RUNNING"),
    ("서비스", f"{NS}/robot_control", "vehicle_interfaces/srv/RobotControl", "서버", "호출",
     "출발 · 정지 · 일시정지 · 리셋 때", "출발 · 정지 · 일시정지 · 재개 · 오류 리셋(PLC 끊김이면 다시 연결) · 작업 취소"),
    ("서비스", f"{NS}/manual_command", "vehicle_interfaces/srv/ManualCommand", "서버", "호출",
     "자동 순서의 아웃트리거 때 / 수동 조그는 누르는 동안 200 ms 마다 / 수동 리프트",
     "아웃트리거 전체 고정(내리기) · 해제(올리기) · 수동 조그 · 수동 리프트(cmd_mv_lift · lift_height, 속도는 PLC) · 초기화. "
     "아웃트리거 개별은 없다(균형, 2026-11-19)"),
    ("서비스", f"{NS}/set_mv_lift", "vehicle_interfaces/srv/SetMvLift", "서버", "호출",
     "자동 동작 중 — 구역마다 · 주행 전", "리프트 목표 높이 · 속도 — 자동 동작 중(set_speed 는 auto 에서만)"),
    ("서비스", f"{NS}/measure_lap", "vehicle_interfaces/srv/MeasureLap", "서버", "호출",
     "캘리브레이션마다", "캘리브레이션 주행 — 원통 한 바퀴(LAP) · 곡판 QR 정렬(ALIGN). 결과는 robot_status 의 lap_*"),
    ("서비스", f"{NS}/reset_odometry", "vehicle_interfaces/srv/ResetOdometry", "서버", "호출",
     "RCS 수동 제어 화면 「위치 다시 맞추기」 때", "차량 위치(position x · y · yaw)를 받은 값으로 다시 맞춘다(2026-11-20)"),
]

#: 3) 칸]

#: 3) 칸 — (이름, 구분, 칸, 타입, 단위, 기본값, 값 · 뜻)
FIELD_HEAD = ["이름", "구분", "칸", "타입", "단위", "기본값", "값 · 뜻"]
FIELDS = [
    ("section", f"{NS}/robot_status — vehicle_interfaces/msg/RobotStatus (AGV 발행 → RCS 구독)"),
    ("robot_status", "msg", "header", "std_msgs/Header", "-", "-", "stamp = 보낸 시각"),
    ("", "", "state", "string", "-", "\"\"", "STOP · RUNNING · PAUSED · HOLD(아웃트리거로 고정돼 섬) · ERROR"),
    ("", "", "hold", "string", "-", "\"\"", "아웃트리거 SET(고정) · RELEASE(해제)"),
    ("", "", "error_code · error_msg", "int32 · string", "-", "0 · \"\"",
     "0 정상, 그 외 차량 오류 번호 · 설명(아래 4) 차량 오류 번호). 오류면 state = ERROR. "
     "701 = PLC 통신 끊김 — RCS 상단 「PLC」 · 연결 설정 「PLC 연결」이 이것을 본다"),
    ("", "", "job_id", "string", "-", "\"\"", "지금 하는 set_job · measure_lap 의 job_id"),
    ("", "", "set_dist · mv_dist", "float64", "m", "0", "목표 거리 · 이번 이동에서 달린 거리(출발 때 0)"),
    ("", "", "speed", "float64", "m/s", "0", "지금 속도"),
    ("", "", "sen1_dist · sen2_dist", "float64", "m", "0", "거리 센서 1 · 2"),
    ("", "", "position", "Position6D (x y z roll pitch yaw)", "m · rad", "0", "차량 위치 · 자세 — reset_odometry 로 x · y · yaw 를 다시 맞춘다"),
    ("", "", "lift_h", "float64", "m", "0", "리프트 높이"),
    ("", "", "battery_pct", "uint8", "%", "255", "배터리 잔량 0~100. 255 = 모름 · 배터리 없음"),
    ("", "", "charging", "bool", "-", "false", "충전 중"),
    ("", "", "estop", "bool", "-", "false", "차량(PLC) 비상정지 중 — 차량 버튼이든 robot_estop · 안전 회로로 건 것이든 true"),
    ("", "", "obstacle_stop", "bool", "-", "false", "접근 감지(안전 스캐너)로 섰다 → RCS 가 작업을 일시정지"),
    ("", "", "level_state", "int8", "-", "-1", "아웃트리거 수평 1 맞음 · 0 안 맞음(로봇 검사 막음) · -1 모름"),
    ("", "", "standoff_meas", "float64", "m", "-1", "지금 잰 거리 — 차량 안쪽 면 ↔ 모재 표면. 모르면 -1"),
    ("", "", "lap_state", "string", "-", "\"IDLE\"", "measure_lap 진행 — IDLE · RUNNING · DONE · FAILED"),
    ("", "", "lap_path_m", "float64", "m", "0", "이번 캘리브레이션 주행에서 차량 중심이 달린 거리(ALIGN 은 정렬하며 움직인 거리)"),
    ("", "", "lap_origin_ok", "bool", "-", "false", "LAP: 원점 QR 을 다시 찾아 섰다 · ALIGN: QR 에 맞춰 섰다"),
    ("", "", "lap_error", "string", "-", "\"\"", "FAILED 일 때 까닭"),
    ("", "※", "기본값 = 모름", "", "", "", "아직 못 채우는 칸은 기본값 그대로 둔다 — RCS 는 기본값을 「모름」으로 보고 조건에서 뺀다"),
    ("section", f"{NS}/start_request — std_msgs/msg/Empty (AGV 발행 → RCS 구독)"),
    ("start_request", "msg", "(칸 없음)", "-", "-", "-", "차량 물리 「작업 시작」 버튼이 눌렸다. 한 번 낸다"),
    ("section", f"{NS}/robot_estop — std_msgs/msg/Bool (RCS 발행 → AGV 구독)"),
    ("robot_estop", "msg", "data", "bool", "-", "false",
     "true = 로봇 컨트롤러 비상정지가 눌렸다 → 차량도 비상정지(PLC 비상정지를 건다) · 움직이는 명령 거절 · estop = true. "
     "false 가 되면 차량 리셋 규칙대로 푼다. 실제 동시 정지는 안전 회로 직결이 맡는다(2.작업별 순서 E)"),
    ("section", f"{NS}/set_job — vehicle_interfaces/srv/SetJob (RCS 호출 → AGV 서버)"),
    ("set_job", "요청", "job_id", "string", "-", "-", "이동마다 새 번호(RCS-0001 …)"),
    ("", "요청", "set_dist", "float64", "m", "-", "이번 이동에서 차량 중심이 달릴 거리. 부호 = 방향(+ 원주 진행). 모재 표면 거리 × 주행 배율(RCS 계산)"),
    ("", "요청", "set_speed", "float64", "m/s", "-", "RCS 차량 설정 「차량 주행 속도」(기본 0.1)"),
    ("", "요청", "standoff", "float64", "m", "-", "지킬 거리 — 차량 안쪽 면 ↔ 모재 표면. RCS 차량 설정 「모재 거리 유지」"),
    ("", "요청", "radius", "float64", "m", "-", "목표 대상(모재) 반지름 = 검사 대상 바깥 지름 / 2 — 보낼 때마다 지금 값. 모르면 0"),
    ("", "응답", "success · message", "bool · string", "-", "-", "받았으면 true. 출발은 하지 않는다(robot_control RUNNING 으로). 거절이면 false + 까닭"),
    ("section", f"{NS}/robot_control — vehicle_interfaces/srv/RobotControl (RCS 호출 → AGV 서버)"),
    ("robot_control", "요청", "state", "string", "-", "\"\"",
     "RUNNING = 출발 · 재개, STOP = 정지 · 일시정지, PAUSED = 캘리브레이션 주행(measure_lap) 일시정지. 빈 글자 = 상태 안 바꿈"),
    ("", "요청", "reset", "uint8", "-", "0", "1 = 오류 리셋. PLC 끊김(701) 중이면 PLC 에 다시 붙어 본다(RCS 「PLC 다시 연결」)"),
    ("", "요청", "job_cancel", "uint8", "-", "0", "1 = 작업 취소(지금은 RCS 가 STOP 으로 대신)"),
    ("", "요청", "job_finish", "uint8", "-", "0",
     "1 = 작업 끝(2026-11-20). 차량은 도착한 뒤 홀딩(브레이크)을 잡고 기다리다가, 받으면 홀딩을 풀고 아웃트리거를 올려 "
     "이동할 수 있는 상태로(hold RELEASE · STOP). RCS 는 다음 주행 전 리프트를 내린 뒤 보낸다"),
    ("", "응답", "success · message", "bool · string", "-", "-", "false 면 실패(RCS 알람)"),
    ("section", f"{NS}/manual_command — vehicle_interfaces/srv/ManualCommand (RCS 호출 → AGV 서버)"),
    ("manual_command", "요청", "cmd_mv_fwd · cmd_mv_rear", "bool", "-", "false", "true 동안 전진 · 후진(누르는 동안 200 ms 마다 다시 옴, 끊기면 선다)"),
    ("", "요청", "cmd_trn_left · cmd_trn_right", "bool", "-", "false", "true 동안 좌 · 우회전"),
    ("", "요청", "cmd_outrg_set", "uint8", "-", "0", "아웃트리거 전체 — 1 해제(올리기) · 2 고정(내리기) · 0 없음. 개별 명령은 없다"),
    ("", "요청", "cmd_mv_lift", "uint8", "-", "0", "1 = lift_height 로 리프트 이동(수동 화면). 아웃트리거 고정에서만, 속도는 PLC"),
    ("", "요청", "cmd_init", "uint8", "-", "0", "1 = 원점 / 초기화"),
    ("", "요청", "lift_height", "float64", "m", "0", "cmd_mv_lift 1 일 때 목표 높이(절대, 맨 아래 0). 도착 = lift_h 목표 ±2 mm"),
    ("", "응답", "success · message", "bool · string", "-", "-", "수동 모드가 아니면(RUNNING · PAUSED · ERROR) false"),
    ("section", f"{NS}/set_mv_lift — vehicle_interfaces/srv/SetMvLift (RCS 호출 → AGV 서버, 자동 동작 중)"),
    ("set_mv_lift", "요청", "job_id", "string", "-", "-", "명령마다 새 번호 — RCS-L0001 …"),
    ("", "요청", "set_height", "float64", "m", "-", "목표 높이(절대, 맨 아래 0). 아웃트리거 고정에서만 움직인다. 도착 = lift_h 목표 ±2 mm"),
    ("", "요청", "set_speed", "float64", "m/s", "-", "RCS 차량 설정 「리프트 속도」(기본 0.05) — auto 에서만 적용(수동은 PLC 속도)"),
    ("", "응답", "success · message", "bool · string", "-", "-", "받았으면 true. 고정 전 · 오류 · 비상정지 · 높이 범위 밖이면 false"),
    ("section", f"{NS}/measure_lap — vehicle_interfaces/srv/MeasureLap (RCS 호출 → AGV 서버)"),
    ("measure_lap", "요청", "job_id", "string", "-", "-", "캘리브레이션마다 새 번호(LAP-0001 · ALN-0001 …) — robot_status.job_id 로 돌려준다"),
    ("", "요청", "mode", "string", "-", "\"\"",
     "LAP = 원통: 원점(QR)에서 모재를 한 바퀴 돌고 원점에 선다. ALIGN = 곡판: 한 바퀴 돌지 않고 판 가운데의 QR 마커에 "
     "맞춰 정렬(거리 · 방향)만 하고 선다 — 곡판은 스캔 때도 차량이 움직이지 않는다. 빈 글자 = LAP"),
    ("", "요청", "speed", "float64", "m/s", "-", "RCS 차량 설정 「차량 주행 속도」(ALIGN 은 정렬 움직임의 상한)"),
    ("", "요청", "standoff", "float64", "m", "-", "거리 유지 — 차량 안쪽 면 ↔ 모재 표면"),
    ("", "요청", "expected_dist", "float64", "m", "-", "LAP: 한 바퀴 거리 2π(모재 반지름 + 거리 유지 + 차량 폭/2) — 안전 한도 · 참고. ALIGN: 0"),
    ("", "응답", "success · message", "bool · string", "-", "-", "받아들이면 바로 출발. 모르는 mode 는 거절. 진행 · 결과는 robot_status 의 lap_*"),
    ("section", f"{NS}/reset_odometry — vehicle_interfaces/srv/ResetOdometry (RCS 호출 → AGV 서버, 2026-11-20)"),
    ("reset_odometry", "요청", "x · y", "float64", "m", "-", "차량 위치를 이 값으로 — 수동 화면 「위치 다시 맞추기」에 작업자가 넣은 값"),
    ("", "요청", "yaw", "float64", "rad", "-", "차량 방향을 이 값으로(화면은 °, 보낼 때 rad)"),
    ("", "응답", "success · message", "bool · string", "-", "-", "바꿨으면 true. 움직이는 중이면 거절해도 된다"),
]


#: 4) 차량 오류]


#: 4) 차량 오류 — 보내는 규칙 (항목, 내용)
ERROR_RULES = [
    ("오류일 때", "error_code ≠ 0 · error_msg = 「번호 이름 — 상세」 · state = ERROR. 하던 동작을 멈추고, 풀릴 때까지 움직이는 "
              "명령(set_job · measure_lap · set_mv_lift · manual_command 의 주행 · 아웃트리거)을 거절한다"),
    ("푸는 법", "원인이 없어진 뒤 robot_control(reset = 1) 을 받으면 error_code = 0 · state = STOP(또는 HOLD). 원인이 남아 있으면 "
             "success = false 와 message 로 까닭을 답하고 오류를 그대로 둔다. RCS 는 수동 화면 「오류 리셋」 · RCS 알람 리셋 · "
             "ERUT reset 때 보낸다"),
    ("오류가 아닌 것", "비상정지 눌림 = estop · 접근 감지로 섬 = obstacle_stop · 배터리 잔량 = battery_pct · 수평 = level_state 필드로 "
                  "알린다(error_code 아님). 그 장치 자체가 고장이면 error_code(601 · 602 · 403 · 501)"),
    ("거절과 다르다", "그 명령 하나만 지금 못 하는 것(수동 모드 아님 · 아웃트리거 고정 전 리프트 등)은 서비스 응답 success = false · "
                  "message 로만 답한다 — error_code 를 쓰지 않는다"),
    ("캘리브레이션 중", "measure_lap 중에 오류가 나면 lap_state = FAILED · lap_error 에도 까닭을 쓴다(RCS 는 캘리브레이션 실패로 끝낸다)"),
    ("번호", "아래 표는 3S 제안이다 — 차량 담당이 PLC · 모터 드라이버 알람을 보고 번호 · 이름 · 뜻 · 리셋으로 풀리는지를 더하거나 "
           "고쳐 알려 준다. RCS 는 번호를 그대로 알람 · ERUT detail 에 싣는다(번호마다 다르게 동작하지 않는다)"),
]

ERROR_HEAD = ["error_code", "이름", "언제", "대역", "리셋으로 풀림", "RCS 알람 · ERUT", "비고"]
_E = "[E2001] DRIVE_ERROR"
ERRORS = [
    ("section", "1xx 주행"),
    ("101", "DRIVE_MOTOR_FAULT", "주행 모터 · 드라이버 알람(과부하 · 과전류 · 과열)", "주행", "원인 제거 뒤", _E, ""),
    ("102", "DRIVE_ENCODER_FAULT", "주행 엔코더 · 거리 측정 이상", "주행", "원인 제거 뒤", _E, ""),
    ("103", "DRIVE_TARGET_NOT_REACHED", "set_dist 만큼 못 갔다 — 막힘 · 슬립 과다", "주행", "예", _E, "mv_dist 에 실제 간 거리"),
    ("104", "STANDOFF_LOST", "거리 유지(standoff) 를 못 지킨다 — 거리 센서 값이 허용 범위 밖", "주행", "예", _E, "standoff_meas 에 잰 거리"),
    ("105", "ALIGN_FAILED", "이동 중 모재 정렬을 정해진 횟수 · 시간 안에 못 맞췄다", "주행", "예", _E, ""),
    ("section", "2xx 리프트"),
    ("201", "LIFT_MOTOR_FAULT", "리프트 모터 · 드라이버 알람", "리프트", "원인 제거 뒤", _E, ""),
    ("202", "LIFT_LIMIT", "상한 · 하한 리밋 감지", "리프트", "원인 제거 뒤", _E, ""),
    ("203", "LIFT_POSITION_ERROR", "목표 높이와 실제 높이 차이가 허용 밖 · 높이 센서 이상", "리프트", "예", _E, "lift_h 에 실제 높이"),
    ("204", "LIFT_BRAKE_FAULT", "리프트 브레이크 이상", "리프트", "원인 제거 뒤", _E, ""),
    ("section", "3xx 아웃트리거 · 수평"),
    ("301", "OUTRIGGER_SET_FAILED", "고정했는데 접지가 감지되지 않는다(시간 초과)", "아웃트리거", "예", _E, ""),
    ("302", "OUTRIGGER_RELEASE_FAILED", "해제가 끝나지 않는다", "아웃트리거", "예", _E, ""),
    ("303", "LEVEL_FAILED", "수평 보정 실패 — 허용 기울기 넘음", "아웃트리거", "예", _E, "level_state = 0 도 함께"),
    ("304", "OUTRIGGER_LOST", "고정 중에 접지가 풀렸다", "아웃트리거", "원인 제거 뒤", _E,
     "로봇 작업 중이면 RCS 가 작업을 세운다"),
    ("section", "4xx 센서 · 캘리브레이션"),
    ("401", "DISTANCE_SENSOR_FAULT", "거리 센서(sen1 · sen2) 이상 · 값 없음", "센서", "원인 제거 뒤", _E, ""),
    ("402", "QR_NOT_FOUND", "원점 QR 을 찾지 못한다(카메라 · 인식 이상)", "센서", "예", _E, "캘리브레이션 중이면 lap_state FAILED 로"),
    ("403", "IMU_FAULT", "기울기(IMU) 센서 이상 — 수평을 알 수 없다", "센서", "원인 제거 뒤", _E, ""),
    ("section", "5xx 전원"),
    ("501", "BATTERY_CRITICAL", "배터리가 위험 수준 — 더 움직이면 안 된다(낮음 경고는 battery_pct 로만)", "전원", "충전 뒤", _E, ""),
    ("502", "CHARGER_FAULT", "충전 이상", "전원", "원인 제거 뒤", _E, ""),
    ("section", "6xx 안전 장치 고장 (눌림 · 감지는 estop · obstacle_stop 필드)"),
    ("601", "SAFETY_SCANNER_FAULT", "안전 스캐너 고장 — 접근 감지를 할 수 없다", "안전", "원인 제거 뒤", _E, ""),
    ("602", "SAFETY_CIRCUIT_FAULT", "안전 회로 이상(비상정지 회로 · 로봇 안전 IO 배선)", "안전", "원인 제거 뒤", _E, ""),
    ("section", "7xx 통신 · 9xx 내부"),
    ("701", "PLC_COMM_LOST", "PLC 통신 끊김 — AGV 노드가 PLC 와 통하지 않는다", "통신", "다시 붙으면(AGV 가 스스로)", _E,
     "RCS 의 PLC 연결 표시가 이것으로 「연결 실패」. error_msg 에 까닭. reset = 1 이면 다시 붙어 본다(RCS 「PLC 다시 연결」) · "
     "스스로 다시 시도해도 된다"),
    ("702", "DRIVER_COMM_LOST", "모터 드라이버 통신 끊김", "통신", "다시 붙으면", _E, ""),
    ("901", "NODE_INTERNAL", "차량 노드 내부 오류(예외 등)", "내부", "노드 재시작", _E, ""),
]


# ================================================================= 2. 작업별 순서
SEQUENCES = [
    ("section", "A. 캘리브레이션(원통) — ERUT 캘리브레이션 · RCS 「계측 시작」 · 차량 「작업 시작」 버튼"),
    ("A", "0", "AGV → RCS", "start_request", "차량 버튼이 눌렸다(버튼으로 시작할 때만)", "-"),
    ("A", "1", "RCS 안", "-", "로봇이 홈이고 태스크가 안 도는지 본다 — 아니면 로봇을 홈으로 보내고 기다린다", "로봇 홈"),
    ("A", "2", "RCS → AGV", "set_mv_lift", "set_height = 0, set_speed (리프트가 올라가 있을 때만)",
     "robot_status.lift_h ≈ 0 (±2 mm)"),
    ("A", "3", "RCS → AGV", "robot_control", "job_finish = 1 — 앞 작업 끝: 홀딩 해제 · 아웃트리거 올림 (늘 보낸다)",
     "success · robot_status.hold = RELEASE (제한 30 초)"),
    ("A", "4", "RCS → AGV", "set_job", "(measure_lap 이 없을 때) set_dist = π × 모재 지름 × 주행 배율, set_speed, standoff, radius",
     "success = true"),
    ("A", "5", "RCS → AGV", "robot_control", "state = RUNNING", "state RUNNING · mv_dist 증가"),
    ("A", "6", "AGV → RCS", "robot_status", "한 바퀴 돌고 선다",
     "STOP/HOLD 이고 |mv_dist − set_dist| ≤ 2 mm → 끝. 제한 = 차량 설정 「캘리브레이션 최대 시간」. "
     "AGV 는 홀딩을 잡고 job_finish 를 기다린다(다음 주행 전 B-3 에서 온다)"),
    ("A", "4~6 (measure_lap 이 있으면 이쪽)", "RCS → AGV · AGV → RCS", "measure_lap · robot_status",
     "measure_lap(job_id, mode = LAP, speed, standoff, expected_dist) → lap_state RUNNING",
     "lap_state DONE · lap_origin_ok true → lap_path_m 로 끝 (FAILED · 원점 못 찾음 · 최대 시간 넘김 = 실패)"),
    ("A", "일시정지", "RCS → AGV", "robot_control",
     "measure_lap 중: state = PAUSED → 재개 RUNNING (한 바퀴를 잇는다). set_job 중: STOP → 재개 set_job(남은 거리) + RUNNING",
     "-"),
    ("A", "정지", "RCS → AGV", "robot_control", "state = STOP — 캘리브레이션을 접는다(measure_lap 이면 lap_state FAILED)",
     "-"),
    ("section", "A-2. 캘리브레이션(곡판, 2026-11-18) — 한 바퀴 돌지 않고 원점 QR 마커에 정렬만 한다. "
             "QR 은 판 가운데에 붙이고, 차량은 그 앞에 놓여 있다. 스캔 때도 차량은 움직이지 않는다"),
    ("A-2", "1~3", "RCS 안 · RCS → AGV", "set_mv_lift · robot_control", "A 의 1~3 과 같다 — 로봇 홈 확인, 리프트 0, job_finish",
     "lift_h ≈ 0 · hold = RELEASE"),
    ("A-2", "4", "RCS → AGV", "measure_lap",
     "measure_lap(job_id, mode = ALIGN, speed, standoff, expected_dist = 0) — QR 에 맞춰 거리 · 방향을 정렬한다",
     "success = true · lap_state RUNNING"),
    ("A-2", "5", "AGV → RCS", "robot_status", "QR 앞에 정렬해 선다 — 그 자리가 판 가운데(x = 판 가로 / 2)",
     "lap_state DONE · lap_origin_ok true → 끝 (FAILED · QR 못 맞춤 · 최대 시간 넘김 = 실패). "
     "제한 = 차량 설정 「곡판 QR 정렬 최대 시간」(기본 120 초)"),
    ("A-2", "일시정지 · 정지", "RCS → AGV", "robot_control", "A 와 같다 — PAUSED → 재개 RUNNING / STOP → lap_state FAILED",
     "-"),
    ("A-2", "없을 때", "RCS 안", "-", "measure_lap(mode ALIGN)이 없는 차량이면 곡판 원점을 잡지 못한다 — 실패로 알린다",
     "set_job 으로 대신하지 않는다"),
    ("section", "B. 스캔 중 구간 이동 — ERUT 구역 검사(start) · RCS 「검사 시작」의 구간 사이"),
    ("B", "1", "RCS 안", "-", "로봇이 홈이고 태스크가 안 도는지 본다", "로봇 홈"),
    ("B", "2", "RCS → AGV", "set_mv_lift", "set_height = 0, set_speed (올라가 있을 때만)", "lift_h ≈ 0"),
    ("B", "3", "RCS → AGV", "robot_control", "job_finish = 1 — 앞 자리 작업 끝: 홀딩 해제 · 아웃트리거 올림 (2026-11-20)",
     "success · hold = RELEASE (제한 30 초)"),
    ("B", "4", "RCS → AGV", "set_job", "set_dist = 구간 사이 표면 거리 × 주행 배율, set_speed, standoff, radius",
     "success. 곡판은 이 순서 2~6(리프트 내림 · job_finish · 주행)을 하지 않는다 — 차량은 QR(판 가운데)에 "
     "정렬한 자리 그대로이고 7 부터(고정 · 리프트 · 로봇) 한다"),
    ("B", "5", "RCS → AGV", "robot_control", "state = RUNNING", "state RUNNING"),
    ("B", "6", "AGV → RCS", "robot_status", "구간 가운데에 선다 — 홀딩(브레이크)을 잡고 job_finish 를 기다린다",
     "STOP/HOLD · |mv_dist − set_dist| ≤ 2 mm"),
    ("B", "7", "RCS → AGV", "manual_command", "cmd_outrg_set = 2 고정", "hold = SET"),
    ("B", "8", "RCS → AGV", "set_mv_lift",
     "set_height = 구역 높이, set_speed (팔로 닿으면 리프트는 그대로)",
     "lift_h 목표 ±2 mm. 리프트가 움직이면 PLC 가 로봇을 안전정지 — RCS 는 안전재설정 수신을 확인한 뒤 로봇을 출발시킨다(D)"),
    ("B", "9", "AGV → RCS", "robot_status", "로봇 스캔 동안 그대로 서 있는다",
     "RCS 가 계속 본다 — hold SET · error_code 0 · state STOP/HOLD · estop false · level_state ≠ 0 "
     "이 아니면 로봇이 멈춰 기다린다"),
    ("B", "10", "RCS 안", "-", "스캔이 끝나면 로봇이 홈으로 → 다음 구간은 2 부터(리프트 0 → job_finish → 주행). "
     "같은 세로 줄이면 차량은 그대로 — job_finish 없이 8 부터", "-"),
    ("B", "일시정지", "RCS → AGV", "robot_control · set_mv_lift · manual_command",
     "움직이던 주행 · 리프트 · 아웃트리거를 세운다(robot_control state = STOP). 재개하면 같은 목표로 다시 "
     "(set_job 남은 거리 + RUNNING / set_mv_lift · manual_command 같은 값). 로봇 스캔 중이면 로봇만 멈추고 차량에는 보내지 않는다",
     "-"),
    ("B", "정지", "RCS → AGV", "robot_control", "state = STOP — 작업을 접는다", "-"),
    ("section", "D. 언제든 — AGV 가 robot_status 로 알리면 RCS 가 하는 일 (비상정지는 E)"),
    ("D", "접근 감지", "AGV → RCS", "robot_status", "obstacle_stop = true", "RCS 가 작업을 일시정지 · 알람(재개는 사람이)"),
    ("D", "배터리", "AGV → RCS", "robot_status", "battery_pct · charging", "상단 배터리 · ERUT 상태 · 조회 · 완료에 싣는다"),
    ("D", "로봇 안전정지", "PLC ↔ 로봇", "로봇 Config IO (안전 IO, 2채널 — RCS 를 거치지 않는다)",
     "차량이 움직이는 동안 PLC 가 CI0 · CI1 중 하나라도 켜면 안전정지(비상정지 아님). 그 신호가 꺼진 뒤 CI2 · CI3 중 "
     "하나라도 켜면 안전재설정",
     "RCS 는 모니터링만 — 상단 Cobot 램프(「안전정지」 · 「재설정 대기」) · I/O 화면 · 알림. ERUT 로 안 보냄. "
     "풀릴 때까지 로봇 출발을 미룬다"),
    ("section", "E. 비상정지 — 로봇 컨트롤러 · PLC 어느 쪽 버튼이 눌려도 차량 · 로봇이 모두 서고 ERUT 에 알린다 (2026-11-18)"),
    ("E", "안전 회로 (먼저)", "PLC ↔ 로봇 컨트롤러", "안전 IO 직결 (2채널 — 노드 · RCS 를 거치지 않는다)",
     "PLC 비상정지 → 로봇 컨트롤러 외부 비상정지 입력, 로봇 컨트롤러 비상정지 출력 → PLC 비상정지 입력. "
     "어느 버튼이든 둘 다 바로 선다 — 통신 · RCS 가 죽어도",
     "아래 노드는 알림 · 작업 정리 · ERUT 보고용이다(ERUT R-18: MQTT 로 사람 안전을 보장하지 않는다). "
     "배선 · 핀 · 리셋 순서는 정할 것"),
    ("E", "1 PLC 버튼", "AGV → RCS", "robot_status", "estop = true",
     "RCS: 로봇 태스크 정지 · 작업 접기 · 차량 정지 · ERUT E1002 E_STOP (detail 「차량(PLC) 비상정지」)"),
    ("E", "2 로봇 컨트롤러 버튼", "로봇 노드 → RCS", "robot/status/estop", "1 (30001 is_emergency_stopped)",
     "RCS: 작업 접기 · ERUT E1002 (detail 「로봇 컨트롤러 비상정지」) · 상단 Cobot 「비상정지」"),
    ("E", "2 (이어서)", "RCS → AGV", "robot_estop", "data = true (바뀌면 바로 + 0.5 초마다)",
     "AGV: 차량 비상정지(PLC 비상정지 걸기) · 움직이는 명령 거절 · robot_status.estop = true"),
    ("E", "3 둘 다", "RCS 안", "-", "두 쪽이 함께 눌려도 ERUT E1002 는 하나 — detail 에 둘 다 적는다", "-"),
    ("E", "4 해제", "RCS → AGV · AGV → RCS", "robot_estop · robot_status",
     "버튼을 모두 풀면 RCS 가 robot_estop = false → AGV 는 차량 리셋 규칙대로 풀고 estop = false",
     "RCS 는 두 쪽이 다 풀려야 ERUT E1002 cleared. 하던 작업은 실패로 끝났다 — 다시 시작은 사람이(ERUT 는 처음 순서부터)"),
    ("section", "F. 차량 오류 — AGV 가 error_code 를 낼 때 (번호는 1.노드 4) 차량 오류 번호)"),
    ("F", "1", "AGV → RCS", "robot_status", "error_code ≠ 0 · error_msg · state = ERROR — 하던 동작을 멈추고 새 동작 명령을 거절",
     "measure_lap 중이면 lap_state = FAILED · lap_error 도"),
    ("F", "2 움직이던 중", "RCS 안", "-", "그 동작이 실패로 끝난다 → RCS 알람 [E2001] 「차량: … 차량 오류 번호 글」 · ERUT E2001 "
     "DRIVE_ERROR(stop) · 작업 접기", "캘리브레이션 주행 중이면 캘리브레이션 실패(E1004)"),
    ("F", "2 서 있음 · 작업 중", "RCS 안", "-", "구역 검사 · 마킹 · 캘리브레이션 중이면 E2001 로 작업을 세운다(로봇도 선다)",
     "예: 로봇 스캔 중 아웃트리거 접지가 풀림(304)"),
    ("F", "2 서 있음 · 쉬는 중", "RCS 안", "-", "RCS 알람 목록에만 [E2001] 「차량: 차량 오류 번호 글」",
     "ERUT 에는 안 보낸다 — 그 상태로 다음 동작을 시키면 그때 E2001"),
    ("F", "3 리셋", "RCS → AGV", "robot_control", "reset = 1 — RCS 수동 화면 「오류 리셋」 · RCS 알람 리셋 · ERUT reset 때"
     "(차량이 오류일 때만)", "원인이 없어졌으면 error_code = 0 · state STOP/HOLD. 남아 있으면 success = false + message"),
    ("F", "4", "RCS 안", "-", "하던 작업은 실패로 끝났다 — 다시 시작은 사람이(ERUT 는 처음 순서부터)", "-"),
    ("section", "G. PLC 연결 — 끊김 · 다시 연결 (2026-11-20 — PLC 전용 필드 · 서비스 없이 error_code 701 · reset 으로)"),
    ("G", "1 끊김", "AGV → RCS", "robot_status", "error_code 701 · error_msg 「701 PLC_COMM_LOST — 까닭」 · state ERROR — 하던 동작을 멈춘다",
     "RCS: 상단 PLC 「연결 실패」 · 알람 [E2001] · 작업 중이면 작업을 세운다(F 와 같음)"),
    ("G", "2 다시 연결", "RCS → AGV", "robot_control", "reset = 1 — RCS 연결 설정 「PLC 다시 연결」(701 일 때만 누른다). "
     "AGV 가 스스로 다시 시도해도 된다", "success = true(시도를 시작했다) → 붙으면 error_code 0 · state STOP/HOLD, 못 붙으면 701 그대로"),
    ("G", "3", "RCS 안", "-", "701 이 풀리면 「PLC 가 다시 연결됐습니다」. 하던 작업은 실패로 끝났다 — 다시 시작은 사람이", "-"),
    ("section", "C. 수동 작업 — RCS 수동 제어 화면 (로봇이 홈이고 태스크가 안 돌 때만 버튼이 켜진다)"),
    ("C", "조그", "RCS → AGV", "manual_command",
     "cmd_mv_fwd · cmd_mv_rear · cmd_trn_left · cmd_trn_right = true 를 누르는 동안 200 ms 마다 다시 — 떼면 모든 필드 0/false",
     "명령이 끊기면 AGV 가 스스로 선다"),
    ("C", "아웃트리거", "RCS → AGV", "manual_command", "cmd_outrg_set = 2 전체 내리기(고정) / 1 전체 올리기(해제) — "
     "균형 때문에 개별은 없다", "hold = SET / RELEASE"),
    ("C", "리프트", "RCS → AGV", "manual_command", "cmd_mv_lift = 1, lift_height = 입력 높이 (속도는 PLC)", "lift_h 목표 ±2 mm"),
    ("C", "위치 다시 맞추기", "RCS → AGV", "reset_odometry", "x · y [m] · yaw [rad] = 작업자가 넣은 값(확인 뒤)",
     "robot_status.position x · y · yaw = 그 값"),
    ("C", "원점 / 초기화", "RCS → AGV", "manual_command", "cmd_init = 1", "success"),
    ("C", "정지", "RCS → AGV", "robot_control", "state = STOP", "-"),
    ("C", "오류 리셋", "RCS → AGV", "robot_control", "reset = 1", "error_code = 0"),
]


_SHORT = {"robot_status", "start_request", "robot_estop", "set_job", "robot_control",
          "manual_command", "measure_lap", "set_mv_lift", "reset_odometry"}


def _full_names(text):
    """「노드」 칸의 짧은 이름을 /vehicle/... 전체 이름으로."""
    if not isinstance(text, str):
        return text
    return " · ".join(f"{NS}/{part}" if part in _SHORT else part for part in text.split(" · "))


def _header(ws, r, headers):
    for col, head in enumerate(headers, start=1):
        c = ws.cell(row=r, column=col, value=head)
        c.font, c.fill, c.alignment, c.border = HEAD_FONT, HEAD_FILL, CENTER, BORDER
    ws.row_dimensions[r].height = 22
    return r + 1


def _fit_rows(ws, widths, start=4) -> None:
    """줄바꿈된 글이 잘리지 않게 줄 높이를 글 길이로 맞춘다(한글은 칸 너비 1.8 당 한 글자쯤).

    가로로 합친 칸은 합친 너비로 센다. 구역 제목 줄(1 칸부터 끝까지 합침)은 한 줄이다.
    """
    spans = {}
    for rng in ws.merged_cells.ranges:
        spans[(rng.min_row, rng.min_col)] = sum(widths[rng.min_col - 1:rng.max_col])
    for r in range(start, ws.max_row + 1):
        if spans.get((r, 1)) == sum(widths):
            continue
        if ws.row_dimensions[r].height and ws.cell(row=r, column=1).fill == HEAD_FILL:
            continue
        lines = 1
        for c, width in enumerate(widths, start=1):
            text = ws.cell(row=r, column=c).value
            if not isinstance(text, str) or not text:
                continue
            width = spans.get((r, c), width)
            per_line = max(int(width / 1.8), 4)
            lines = max(lines, sum(math.ceil(len(part) / per_line) or 1 for part in text.split("\n")))
        ws.row_dimensions[r].height = max(22, 15 * lines + 6)


def _sheet1(wb) -> None:
    from openpyxl.utils import get_column_letter
    widths = SHEET1_WIDTHS
    n = len(widths)
    ws = wb.create_sheet("1.노드")
    ws.cell(row=1, column=1, value="RCS ↔ AGV — ROS 2 노드 · 토픽 · 서비스 · 차량 오류 번호 (2026-11-20, AGV 판 인터페이스)").font = TITLE_FONT
    note = ws.cell(row=2, column=1, value=(
        "AGV 노드와 RCS 노드가 ROS 2 로 바로 붙는다. 이름 앞 /vehicle 은 네임스페이스(RCS SMR_VEHICLE_NS 와 같게). "
        "단위는 m · m/s · rad. 모의기(vehicle_sim)가 같은 토픽 · 서비스 · 칸을 낸다. "
        "자세한 근거: 3S-AGV_PLC_연계_인터페이스_20261106.xlsx"))
    note.font, note.alignment = NOTE_FONT, LEFT
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=n)
    ws.row_dimensions[2].height = 30
    for col, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col)].width = width

    r = rows(ws, [("section", "1) 노드 구성 · 실행 환경")], n, start=4)
    for label, text in SETUP:
        for col in range(1, n + 1):
            c = ws.cell(row=r, column=col)
            c.border = BORDER
        c = ws.cell(row=r, column=1, value=label)
        c.font, c.alignment = BOLD_BODY, CENTER
        c = ws.cell(row=r, column=2, value=text)
        c.font, c.alignment = BODY_FONT, LEFT
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=n)
        r += 1

    r = rows(ws, [("section", "2) 주고받는 토픽 · 서비스 — 이름 앞 /vehicle 은 네임스페이스")], n, start=r + 1)
    r = _header(ws, r, INTERFACE_HEAD)
    r = rows(ws, INTERFACES, n, start=r, left_from=2)

    r = rows(ws, [("section", "3) 칸 — 토픽 메시지 · 서비스 요청 / 응답")], n, start=r + 1)
    r = _header(ws, r, FIELD_HEAD)
    r = rows(ws, FIELDS, n, start=r, left_from=3)

    r = rows(ws, [("section", "4) 차량 오류 번호 — /vehicle/robot_status.error_code (AGV → RCS, 번호표는 3S 제안)")],
             n, start=r + 1)
    for label, text in ERROR_RULES:
        for col in range(1, n + 1):
            ws.cell(row=r, column=col).border = BORDER
        c = ws.cell(row=r, column=1, value=label)
        c.font, c.alignment = BOLD_BODY, CENTER
        c = ws.cell(row=r, column=2, value=text)
        c.font, c.alignment = BODY_FONT, LEFT
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=n)
        r += 1
    r = _header(ws, r, ERROR_HEAD)
    rows(ws, ERRORS, n, start=r, left_from=2)
    _fit_rows(ws, widths)


def main() -> None:
    wb = Workbook()
    wb.remove(wb.active)
    _sheet1(wb)

    seq = [item if item and item[0] == "section"
           else tuple(_full_names(v) if i == 3 else v for i, v in enumerate(item)) for item in SEQUENCES]
    ws = sheet(wb, "2.작업별 순서", "작업별로 주고받는 순서 (2026-11-19)",
               "위에서 아래로 차례대로. 「노드」 = 1.노드 의 토픽 · 서비스 이름. 「RCS 안」은 AGV 노드와 주고받지 않는 "
               "RCS 쪽 확인이다. 제한 시간 · 실패는 RCS 알람 + ERUT E2001 DRIVE_ERROR.",
               ["작업", "순서", "방향", "노드", "보내는 값 · 하는 일", "끝 판정 · 비고"],
               [6, 14, 18, 30, 70, 52])
    rows(ws, seq, 6, left_from=3)
    _fit_rows(ws, [6, 14, 18, 30, 70, 52], start=5)

    for ws in wb.worksheets:
        ws.sheet_view.zoomScale = 90
        ws.cell(row=2, column=1).font = NOTE_FONT
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    print(f"만들었습니다: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
